"""Надёжность AI-скоринга: кривой ответ модели не теряет вакансии и не роняет прогон.

Ревизия 17.09.2026 нашла три спящих отказа:
- `"score": null` давал TypeError, который ловился не всеми ветками, и прогон падал;
- батч помечался «увиденным» целиком, даже если модель вернула не все id, —
  выпавшая вакансия исчезала навсегда без единой оценки;
- падение стартового прогона роняло контейнер, а `restart: unless-stopped`
  поднимал его заново (карусель перезапусков).
"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from src import scheduler
from src.matcher import cerebras_matcher as m
from src.models import Job, MatchResult


def _jobs(n=3):
    return [Job(id=f"j{i}", title="Support Specialist", company="Acme",
                description="crypto support", url=f"u{i}", source="test")
            for i in range(n)]


def _client_returning(payload) -> MagicMock:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    client = MagicMock()
    client.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content=text))]
    return client


def _item(jid, score=70):
    return {"id": jid, "score": score, "why_fits": [], "watch_out": [],
            "recommendation": "ok"}


class TestMalformedItems:
    @pytest.mark.parametrize("bad", [
        {"id": "j1", "score": None},                 # TypeError: int(None)
        {"id": "j1"},                                # KeyError
        {"id": "j1", "score": "высокий"},            # ValueError
        "просто строка вместо объекта",              # AttributeError/TypeError
        None,
    ])
    def test_bad_item_does_not_kill_the_batch(self, bad):
        client = _client_returning([_item("j0"), bad, _item("j2")])
        out = m.match_batch(_jobs(3), client=client, provider=m._PROVIDER_REGISTRY["openrouter"])
        assert [r.job_id for r in out] == ["j0", "j2"]

    def test_all_items_broken_returns_empty_not_none(self):
        client = _client_returning([{"id": "j0", "score": None}])
        out = m.match_batch(_jobs(1), client=client, provider=m._PROVIDER_REGISTRY["openrouter"])
        assert out == []

    def test_non_json_answer_is_a_batch_failure(self):
        client = _client_returning("модель ответила прозой")
        assert m.match_batch(_jobs(1), client=client,
                             provider=m._PROVIDER_REGISTRY["openrouter"]) is None


class TestPartialBatchMarking:
    """seen ставится только вернувшимся id — остальные вернутся следующим прогоном."""

    @staticmethod
    def _run(results, jobs, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER_ORDER", "openrouter")
        monkeypatch.setenv("OPENROUTER_API_KEY", "live")
        marked = []
        with patch.object(m, "match_batch", return_value=results), \
             patch.object(m, "_get_client"), \
             patch.object(m.storage, "get_cached_match", return_value=None), \
             patch.object(m.storage, "mark_seen_batch",
                          side_effect=lambda js: marked.extend(j.id for j in js)), \
             patch.object(m.storage, "save_match"), \
             patch.object(m, "_MATCHES_JSONL", m.Path(os.devnull)):
            out = m.match_jobs(jobs, threshold=55, batch_size=3)
        return out, marked

    def test_missing_id_is_not_marked_seen(self, monkeypatch):
        jobs = _jobs(3)
        results = [MatchResult(job_id="j0", score=70, why_fits=[], watch_out=[],
                               recommendation=""),
                   MatchResult(job_id="j2", score=40, why_fits=[], watch_out=[],
                               recommendation="")]
        out, marked = self._run(results, jobs, monkeypatch)
        assert marked == ["j0", "j2"], "j1 не оценена — помечать её нельзя"
        assert [j.id for j, _ in out] == ["j0"]
        assert m.last_run_stats["unscored"] == 1

    def test_empty_result_marks_nobody(self, monkeypatch):
        out, marked = self._run([], _jobs(2), monkeypatch)
        assert out == [] and marked == []

    def test_full_batch_is_marked(self, monkeypatch):
        jobs = _jobs(2)
        results = [MatchResult(job_id=j.id, score=70, why_fits=[], watch_out=[],
                               recommendation="") for j in jobs]
        out, marked = self._run(results, jobs, monkeypatch)
        assert marked == ["j0", "j1"] and m.last_run_stats["unscored"] == 0


class TestStartupRunGuard:
    def test_failed_first_run_does_not_kill_the_container(self, monkeypatch):
        sent = []
        monkeypatch.setattr(scheduler, "_load_config",
                            lambda: {"scheduler": {"cron": "0 6,10,14 * * *"}})
        monkeypatch.setattr(scheduler, "_wait_for_network", lambda timeout=180: None)
        monkeypatch.setattr(scheduler, "run_listener", lambda: None)
        monkeypatch.setattr(scheduler, "send_text", lambda t: sent.append(t))
        monkeypatch.setattr(scheduler, "BlockingScheduler", MagicMock())
        monkeypatch.setattr(scheduler, "run_once",
                            MagicMock(side_effect=RuntimeError("HH упал")))

        scheduler.main()   # не должно бросить наружу

        assert any("Стартовый прогон упал" in t for t in sent)

    def test_alert_failure_does_not_kill_the_container(self, monkeypatch):
        monkeypatch.setattr(scheduler, "_load_config",
                            lambda: {"scheduler": {"cron": "0 6 * * *"}})
        monkeypatch.setattr(scheduler, "_wait_for_network", lambda timeout=180: None)
        monkeypatch.setattr(scheduler, "run_listener", lambda: None)
        monkeypatch.setattr(scheduler, "BlockingScheduler", MagicMock())
        monkeypatch.setattr(scheduler, "run_once",
                            MagicMock(side_effect=RuntimeError("HH упал")))
        def flaky_send(text):
            if "Стартовый прогон упал" in text:
                raise RuntimeError("Telegram лёг")
        monkeypatch.setattr(scheduler, "send_text", flaky_send)

        scheduler.main()   # алерт не ушёл, но контейнер жив
