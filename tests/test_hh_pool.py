"""Пул непросмотренных вакансий HH и почасовой сбор (25.09.2026).

Аудит потерь показал: окно RSS — 20 свежих на запрос, и у 22 запросов оно
переполняется быстрее ночного разрыва между прогонами. Всё сверх окна и всё,
отложенное потолком пересмотра, выпадало из выдачи и терялось молча. Пул держит
такие вакансии до вердикта; ломается тоже молча, поэтому под тестом.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import requests

from src import scheduler, storage
from src.models import Job
from src.parsers import hh_parser


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.db")
    storage.init_db()
    return storage


def _job(jid, source="hh.ru", watch=False):
    j = Job(id=jid, title=f"Вакансия {jid}", company="ACME", description="rss",
            url=f"https://hh.ru/vacancy/{jid}", source=source,
            published_at=datetime(2026, 9, 25, 6, tzinfo=timezone.utc),
            tags=["a"])
    if watch:
        j.raw["employer_watch"] = True
    return j


class TestPoolStorage:
    def test_roundtrip_keeps_fields(self, db):
        db.pool_add([_job("hh_1", watch=True)])
        jobs, expired = db.pool_load()
        assert expired == 0
        (j,) = jobs
        assert j.id == "hh_1" and j.source == "hh.ru" and j.tags == ["a"]
        assert j.published_at == datetime(2026, 9, 25, 6, tzinfo=timezone.utc)
        assert j.raw["employer_watch"] is True

    def test_add_is_idempotent(self, db):
        assert db.pool_add([_job("hh_1"), _job("hh_2")]) == 2
        assert db.pool_add([_job("hh_1"), _job("hh_3")]) == 1
        assert db.pool_size() == 3

    def test_expired_are_dropped_and_counted(self, db):
        db.pool_add([_job("old"), _job("new")])
        stale = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
        with db.get_conn() as conn:
            conn.execute("UPDATE hh_pool SET added_at=? WHERE id='old'", (stale,))
        jobs, expired = db.pool_load(max_age_days=14)
        assert expired == 1
        assert [j.id for j in jobs] == ["new"]

    def test_prune_removes_only_decided(self, db):
        db.pool_add([_job("verdict"), _job("reject_now"), _job("reject_old"), _job("waiting")])
        db.mark_seen_batch([_job("verdict")])
        db.mark_prefilter_seen([_job("reject_now")], "v2")
        db.mark_prefilter_seen([_job("reject_old")], "v1")
        assert db.pool_prune_seen(prefilter_version="v2") == 2
        left = {j.id for j in db.pool_load()[0]}
        # отказ под старым отпечатком переоткрыт сменой критериев — ждёт пересмотра
        assert left == {"reject_old", "waiting"}


class TestRssRetry:
    def _rss(self):
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        resp.content = b"<rss><channel></channel></rss>"
        return resp

    def test_single_timeout_is_retried(self, monkeypatch):
        calls = []

        def get(self, *a, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise requests.ReadTimeout("hh тормозит")
            return TestRssRetry._rss(None)

        monkeypatch.setattr(requests.Session, "get", get)
        monkeypatch.setattr(hh_parser, "_RSS_RETRY_PAUSE", 0)
        p = hh_parser.HHParser.__new__(hh_parser.HHParser)
        p.cfg = {"area": 113}
        assert p._fetch_query("AI automation") == []
        assert len(calls) == 2

    def test_gives_up_after_attempts(self, monkeypatch):
        calls = []

        def get(self, *a, **kw):
            calls.append(1)
            raise requests.ConnectionError("лежит")

        monkeypatch.setattr(requests.Session, "get", get)
        monkeypatch.setattr(hh_parser, "_RSS_RETRY_PAUSE", 0)
        p = hh_parser.HHParser.__new__(hh_parser.HHParser)
        p.cfg = {"area": 113}
        assert p._fetch_query("AI automation") == []
        assert len(calls) == hh_parser._RSS_ATTEMPTS


class TestHarvest:
    def test_harvest_uses_date_pass_only_and_dedupes(self, monkeypatch):
        seen_calls = []

        def fetch(self, query, by_date=True, employer_id=None):
            seen_calls.append((query, by_date, employer_id))
            return [_job("hh_1"), _job(f"hh_{query}")]

        monkeypatch.setattr(hh_parser.HHParser, "_fetch_query", fetch)
        p = hh_parser.HHParser.__new__(hh_parser.HHParser)
        p.cfg = {"search_queries": ["a", "b"], "employer_ids": [{"id": 1, "name": "X"}]}
        ids = sorted(j.id for j in p.harvest())
        assert ids == ["hh_1", "hh_a", "hh_b"]
        assert all(by_date and emp is None for _, by_date, emp in seen_calls)

    def test_harvest_puts_only_unseen_into_pool(self, db, monkeypatch):
        db.mark_seen_batch([_job("hh_old")])
        monkeypatch.setattr(hh_parser.HHParser, "__init__", lambda self: None)
        monkeypatch.setattr(hh_parser.HHParser, "harvest",
                            lambda self: [_job("hh_old"), _job("hh_new")])
        monkeypatch.setattr(scheduler, "_prefilter_version", lambda: "v1")
        scheduler.harvest_hh()
        assert {j.id for j in db.pool_load()[0]} == {"hh_new"}

    def test_harvest_skipped_while_main_run_holds_lock(self, db, monkeypatch):
        called = []
        monkeypatch.setattr(hh_parser.HHParser, "__init__", lambda self: None)
        monkeypatch.setattr(hh_parser.HHParser, "harvest",
                            lambda self: called.append(1) or [])
        with scheduler._RUN_LOCK:
            scheduler.harvest_hh()
        assert called == []

    def test_harvest_failure_releases_lock(self, db, monkeypatch):
        monkeypatch.setattr(hh_parser.HHParser, "__init__", lambda self: None)
        monkeypatch.setattr(hh_parser.HHParser, "harvest",
                            MagicMock(side_effect=RuntimeError("hh лёг")))
        scheduler.harvest_hh()   # не бросает наружу
        assert scheduler._RUN_LOCK.acquire(blocking=False)
        scheduler._RUN_LOCK.release()


class TestMainRunUsesPool:
    def test_pool_jobs_join_fresh_output_without_duplicates(self, db):
        db.pool_add([_job("hh_fresh"), _job("hh_missed")])
        fresh = _job("hh_fresh")
        fresh.title = "свежая версия"
        merged = scheduler._merge_hh_pool([fresh, _job("gh_1", source="greenhouse")], 14)
        by_id = {j.id: j for j in merged}
        assert sorted(by_id) == ["gh_1", "hh_fresh", "hh_missed"]
        assert by_id["hh_fresh"].title == "свежая версия"
        assert len(merged) == 3

    def test_run_once_prunes_pool_even_if_pipeline_fails(self, db, monkeypatch):
        db.pool_add([_job("done"), _job("deferred")])
        db.mark_seen_batch([_job("done")])
        monkeypatch.setattr(scheduler, "_prefilter_version", lambda: "v1")
        monkeypatch.setattr(scheduler, "_run_pipeline",
                            MagicMock(side_effect=RuntimeError("упал")))
        with pytest.raises(RuntimeError):
            scheduler.run_once()
        assert {j.id for j in db.pool_load()[0]} == {"deferred"}
        assert scheduler._RUN_LOCK.acquire(blocking=False)
        scheduler._RUN_LOCK.release()
