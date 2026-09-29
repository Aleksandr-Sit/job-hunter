"""Межпрогонный дедуп (29.09.2026).

Аудит 28.09.2026: `dedupe_jobs` сравнивал вакансии только внутри прогона.
1. Копия из следующего прогона приходила второй карточкой: hh — та же компания
   и должность в другом городе («Тетрика» ×14 за месяц), Telegram — репост в
   другом канале («межбиржевая торговля» ×7).
2. Копию, отброшенную дедупом, помечали провизорным отказом, и смена критериев
   её воскрешала: «Аккаунт-менеджер» 22.09 → вторая карточка 25.09.

Оба дефекта молчаливые (карточки просто приходят лишний раз), поэтому под тестом
на уровне двух настоящих прогонов `_run_pipeline` с подменёнными парсером, AI и
Telegram.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src import scheduler, storage
from src.matcher.pre_filter import job_dedupe_key, split_duplicates
from src.models import Job, MatchResult

_PASS = {"role": "crypto_ops", "passed_gate": True, "score": 80,
         "recommend": True, "reasons": []}

_ACC_MANAGER = (
    "Требуется Аккаунт-менеджер в криптовалютный процессинг. Ведение пула "
    "клиентов, сопровождение сделок, работа с мерчантами. Удалённо, полный день."
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.db")
    storage.init_db()
    return storage


def _hh(jid, city, company="Онлайн-школа Тетрика", title="Менеджер по продажам"):
    return Job(id=jid, title=title, company=company, description="rss",
               url=f"https://hh.ru/vacancy/{jid}", source="hh.ru", location=city)


def _tg(jid, channel, text=_ACC_MANAGER, title="Требуется Аккаунт-менеджер"):
    return Job(id=jid, title=title, company=f"@{channel}", description=text,
               url=f"https://t.me/{channel}/1", source=f"telegram:{channel}")


def _dup_of(jid):
    with storage.get_conn() as conn:
        row = conn.execute("SELECT dup_of FROM seen_jobs WHERE id=?", (jid,)).fetchone()
    return row["dup_of"] if row else None


class _Parser:
    name = "fake"

    def __init__(self, state):
        self.state = state

    def parse(self):
        return list(self.state["jobs"])


@pytest.fixture()
def pipe(db, monkeypatch):
    """Прогон как в бою, но парсер, AI и Telegram подменены.

    state["jobs"]     — что «вернули парсеры» в этом прогоне;
    state["scores"]   — балл AI по id (по умолчанию 80);
    state["to_ai"]    — id, дошедшие до AI, по прогонам;
    state["fail_send"]— id, которые Telegram «не принял».
    """
    state = {"jobs": [], "scores": {}, "to_ai": [], "fail_send": set(),
             "sent": [], "pf": "v1"}
    monkeypatch.setattr(scheduler, "_load_config", lambda: {
        "matching": {"threshold": 55, "batch_size": 5},
        "hh_enrich": {"enabled": False},
        "scheduler": {"dedupe_window_days": 30},
    })
    monkeypatch.setattr(scheduler, "_build_parsers", lambda cfg: [_Parser(state)])
    monkeypatch.setattr(scheduler, "_prefilter_version", lambda: state["pf"])
    monkeypatch.setattr(scheduler, "score_job", lambda j: {"best": _PASS, "all": [_PASS]})

    def fake_match(jobs, threshold, batch_size):
        state["to_ai"].append([j.id for j in jobs])
        storage.mark_seen_batch(jobs)          # как match_jobs: вердикт после AI
        out = []
        for j in jobs:
            score = state["scores"].get(j.id, 80)
            if score >= threshold:
                out.append((j, MatchResult(j.id, score, [], [], "")))
        return out

    def fake_send(pairs):
        ok = [j for j, _ in pairs if j.id not in state["fail_send"]]
        state["sent"].extend(j.id for j in ok)
        return ok

    monkeypatch.setattr(scheduler, "match_jobs", fake_match)
    monkeypatch.setattr(scheduler, "send_jobs_batch", fake_send)
    monkeypatch.setattr(scheduler, "send_text", lambda *a, **k: True)
    monkeypatch.setattr(scheduler, "send_daily_summary", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "_warn_if_provider_switched", lambda: None)

    def run(jobs):
        state["jobs"] = jobs
        before = len(state["to_ai"])
        scheduler._run_pipeline()
        return state["to_ai"][before] if len(state["to_ai"]) > before else []

    state["run"] = run
    return state


class TestKeyAndStorage:
    def test_hh_key_ignores_city(self):
        assert job_dedupe_key(_hh("1", "Самара")) == job_dedupe_key(_hh("2", "Москва"))

    def test_tg_key_ignores_channel_and_price_prefix(self):
        a = _tg("tg_a", "cryptoheadhunter")
        b = _tg("tg_b", "workingincrypto", text="2474 \n#Вакансия\n\n" + _ACC_MANAGER)
        assert job_dedupe_key(a) == job_dedupe_key(b)

    def test_split_reports_representative(self):
        a, b, c = _hh("1", "Самара"), _hh("2", "Москва"), _hh("3", "Сочи", company="Точка")
        kept, dropped = split_duplicates([a, b, c])
        assert kept == [a, c]
        assert dropped == [(b, a)]

    def test_known_keys_respect_window(self, db):
        db.remember_dedupe_keys([("k_old", "j1"), ("k_new", "j2")])
        stale = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        with db.get_conn() as conn:
            conn.execute("UPDATE dedupe_keys SET created_at=? WHERE key='k_old'", (stale,))
        assert db.known_dedupe_keys(["k_old", "k_new", "k_none"], max_age_days=30) == \
            {"k_new": "j2"}

    def test_resend_refreshes_key(self, db):
        db.remember_dedupe_keys([("k", "j1")])
        stale = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        with db.get_conn() as conn:
            conn.execute("UPDATE dedupe_keys SET created_at=?", (stale,))
        db.remember_dedupe_keys([("k", "j9")])
        assert db.known_dedupe_keys(["k"], max_age_days=30) == {"k": "j9"}

    def test_duplicate_is_final_under_any_prefilter_version(self, db):
        db.mark_prefilter_seen([_hh("copy", "Москва")], "v1")   # был провизорный
        db.mark_duplicate_seen([(_hh("copy", "Москва"), "orig")])
        db.mark_prefilter_seen([_hh("copy", "Москва")], "v2")   # не понижает
        assert db.is_seen_batch(["copy"], prefilter_version="v3") == {"copy"}
        assert _dup_of("copy") == "orig"


class TestCrossRun:
    def test_hh_same_vacancy_in_another_city_next_run(self, pipe):
        """«Тетрика» ×14: та же компания и должность, другой город и id."""
        assert pipe["run"]([_hh("hh_samara", "Самара")]) == ["hh_samara"]
        assert pipe["sent"] == ["hh_samara"]

        assert pipe["run"]([_hh("hh_moscow", "Москва")]) == [], "копия дошла до AI"
        assert pipe["sent"] == ["hh_samara"]
        assert _dup_of("hh_moscow") == "hh_samara"
        assert "hh_moscow" not in {j.id for j in storage.pool_load()[0]}

    def test_tg_repost_in_another_channel_next_day(self, pipe):
        """«Аккаунт-менеджер»: @cryptoheadhunter, потом репост с ценой в шапке."""
        pipe["run"]([_tg("tg_1", "cryptoheadhunter")])
        repost = _tg("tg_2", "workingincrypto", text="2474 \n#Вакансия\n\n" + _ACC_MANAGER)
        assert pipe["run"]([repost]) == []
        assert pipe["sent"] == ["tg_1"]
        assert _dup_of("tg_2") == "tg_1"

    def test_tg_same_title_different_text_is_not_a_copy(self, pipe):
        pipe["run"]([_tg("tg_1", "cryptoheadhunter")])
        other = _tg("tg_2", "workingincrypto",
                    text="Требуется Аккаунт-менеджер в OTC-деск. Сопровождение "
                         "институциональных клиентов, офис в Дубае.")
        assert pipe["run"]([other]) == ["tg_2"]

    def test_run_copy_does_not_resurrect_after_criteria_change(self, pipe):
        """Копия, схлопнутая внутри прогона, раньше получала провизорный отказ и
        после смены criteria.yaml шла в AI отдельной вакансией. Представителю AI
        дал 30 — ключа нет, поэтому держит только финальная пометка копии."""
        pipe["scores"]["tg_a"] = 30
        first = pipe["run"]([_tg("tg_a", "cryptoheadhunter"),
                             _tg("tg_b", "workingincrypto")])
        assert first == ["tg_a"]
        assert _dup_of("tg_b") == "tg_a"

        pipe["pf"] = "v2"                               # сменились критерии
        assert pipe["run"]([_tg("tg_a", "cryptoheadhunter"),
                            _tg("tg_b", "workingincrypto")]) == []
        assert pipe["sent"] == []

    def test_ai_reject_does_not_block_copy(self, pipe):
        """«Свой в Альфе» 30 → 72: копия отклонённой вакансии оценивается заново."""
        company, title = "Альфа-Банк", "Менеджер по продажам (проект Свой в Альфе)"
        pipe["scores"]["hh_1"] = 30
        pipe["run"]([_hh("hh_1", "Москва", company, title)])
        pipe["scores"]["hh_2"] = 72
        assert pipe["run"]([_hh("hh_2", "Самара", company, title)]) == ["hh_2"]
        assert pipe["sent"] == ["hh_2"]

    def test_undelivered_card_does_not_take_the_key(self, pipe):
        pipe["fail_send"].add("hh_1")
        pipe["run"]([_hh("hh_1", "Самара")])
        assert pipe["sent"] == []
        assert pipe["run"]([_hh("hh_2", "Москва")]) == ["hh_2"]
        assert pipe["sent"] == ["hh_2"]

    def test_same_title_other_employer_is_not_a_copy(self, pipe):
        pipe["run"]([_hh("hh_1", "Самара", company="Точка Банк")])
        assert pipe["run"]([_hh("hh_2", "Самара", company="Кастор")]) == ["hh_2"]

    def test_copy_after_window_is_sent_again(self, pipe):
        pipe["run"]([_hh("hh_1", "Самара")])
        stale = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        with storage.get_conn() as conn:
            conn.execute("UPDATE dedupe_keys SET created_at=?", (stale,))
        assert pipe["run"]([_hh("hh_2", "Москва")]) == ["hh_2"]
