"""Пул карточек LinkedIn и ротация стран (14.10.2026).

Прогоны 14.10.2026: поиск по 11 странам ~38 мин из 45, описаний 15–19 из ~1700
карточек, остальные выбрасывались. Теперь прогон ищет несколько стран, карточки
ждут описания в пуле li_pool, описания докачиваются и между прогонами.
Ломается молча (вакансии просто не доходят), поэтому под тестом.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src import scheduler, storage
from src.parsers.web.linkedin import _WATCH_BUCKET, LinkedInParser


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.db")
    storage.init_db()
    return storage


def _card(cid, title="Customer Support Specialist", geo="Кипр"):
    return {"id": cid, "title": title, "company": "ACME", "location": "Limassol",
            "url": f"https://www.linkedin.com/jobs/view/{cid}", "geo": geo}


def _parser(monkeypatch, geos=("Кипр",), descriptions=None):
    p = LinkedInParser.__new__(LinkedInParser)
    p.enabled = True
    p.queries = ["support"]
    p.geos = [{"id": str(i + 1), "name": g} for i, g in enumerate(geos)]
    p.pages_per_query, p.hours_old = 1, 168
    p.delay = p.search_delay = 0
    p.max_minutes, p.max_descriptions = 45, 1000
    p._strikes = 0
    p.watch_companies = []
    p.geos_per_run, p.pool_max_age_days = 0, 7
    p.skip_seen = False
    descriptions = descriptions if descriptions is not None else {}
    monkeypatch.setattr(p, "_description", lambda cid: descriptions.get(cid, ""))
    monkeypatch.setattr("src.parsers.web.linkedin.time.sleep", lambda s: None)
    return p


class TestPoolStorage:
    def test_add_pending_ready_roundtrip(self, db):
        assert db.li_pool_add([_card("1"), _card("2")], {"1": "high", "2": "low"}) == 2
        assert db.li_pool_add([_card("1")], {"1": "low"}) == 0      # лежащую не трогаем
        pending, expired = db.li_pool_pending()
        assert expired == 0
        assert {(c["id"], c["tier"]) for c in pending} == {("1", "high"), ("2", "low")}
        db.li_pool_set_description("1", "text")
        assert [c["id"] for c, _ in db.li_pool_ready()] == ["1"]
        assert [c["id"] for c in db.li_pool_pending()[0]] == ["2"]

    def test_expired_cards_dropped_and_counted(self, db):
        db.li_pool_add([_card("old")], {"old": "high"})
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        with db.get_conn() as conn:
            conn.execute("UPDATE li_pool SET added_at = ?", (old,))
        pending, expired = db.li_pool_pending(max_age_days=7)
        assert (pending, expired) == ([], 1)

    def test_failed_description_gets_three_tries(self, db):
        """Пустой ответ — и закрытая вакансия, и серия 429: не выбрасываем сразу."""
        db.li_pool_add([_card("1")], {"1": "high"})
        db.li_pool_fail("1")
        db.li_pool_fail("1")
        assert len(db.li_pool_pending()[0]) == 1
        db.li_pool_fail("1")
        assert db.li_pool_pending()[0] == []

    def test_prune_removes_only_judged(self, db):
        db.li_pool_add([_card("1"), _card("2")], {"1": "high", "2": "high"})
        db.mark_seen_batch([type("J", (), {"id": "li_1", "source": "linkedin.com",
                                           "title": "t", "url": "u"})()])
        assert db.li_pool_prune_seen() == 1
        assert [c["id"] for c in db.li_pool_pending()[0]] == ["2"]


class TestGeoRotation:
    def test_never_searched_first_then_oldest(self, db):
        names = ["A", "B", "C", "D"]
        assert db.li_geo_oldest(names, 2) == ["A", "B"]
        db.li_geo_mark("A")
        db.li_geo_mark("B")
        assert db.li_geo_oldest(names, 2) == ["C", "D"]
        db.li_geo_mark("C")
        db.li_geo_mark("D")
        assert db.li_geo_oldest(names, 2) == ["A", "B"]

    def test_parse_searches_only_picked_geos(self, db, monkeypatch):
        p = _parser(monkeypatch, geos=("A", "B", "C"))
        p.geos_per_run = 2
        searched = []
        monkeypatch.setattr(p, "_cards", lambda q, g, s: searched.append(g) or [])
        p.parse()
        assert searched == ["1", "2"]
        searched.clear()
        p.parse()
        assert set(searched) == {"3", "1"}   # C ни разу + самая давняя A


class TestParseThroughPool:
    def test_cards_go_to_pool_and_return_with_description(self, db, monkeypatch):
        p = _parser(monkeypatch, descriptions={"1": "We need support in Russian."})
        monkeypatch.setattr(p, "_cards", lambda q, g, s: [
            _card("1"), _card("2", title="Senior Software Engineer")])
        jobs = p.parse()
        assert [j.id for j in jobs] == ["li_1"]
        # dev-заголовок (drop) в пул не попал; карточка без описания ждёт попытки
        assert db.li_pool_pending()[0] == []

    def test_undone_cards_wait_for_harvest(self, db, monkeypatch):
        p = _parser(monkeypatch, descriptions={"1": "d1", "2": "d2"})
        p.max_descriptions = 1               # прогон успел одно описание
        monkeypatch.setattr(p, "_cards", lambda q, g, s: [_card("1"), _card("2")])
        assert len(p.parse()) == 1
        assert len(db.li_pool_pending()[0]) == 1
        assert p.harvest(minutes=5) == 1     # докачка между прогонами
        assert len(db.li_pool_ready()) == 2

    def test_ready_cards_survive_until_verdict(self, db, monkeypatch):
        """Упал AI-батч — вакансия не теряется: следующий прогон отдаст её снова."""
        p = _parser(monkeypatch, descriptions={"1": "d1"})
        monkeypatch.setattr(p, "_cards", lambda q, g, s: [_card("1")])
        assert [j.id for j in p.parse()] == ["li_1"]
        monkeypatch.setattr(p, "_cards", lambda q, g, s: [])
        assert [j.id for j in p.parse()] == ["li_1"]

    def test_high_before_low_and_watch_low_last(self, db, monkeypatch):
        order = []
        p = _parser(monkeypatch)
        monkeypatch.setattr(p, "_description", lambda cid: order.append(cid) or "d")
        p.watch_companies = [{"name": "Chainalysis", "id": "10607336"}]
        monkeypatch.setattr(p, "_company_cards", lambda cid, s: [
            {**_card("w-hr", "Director, HRBP - GTM"), "geo": _WATCH_BUCKET}])
        monkeypatch.setattr(p, "_cards", lambda q, g, s: [
            _card("a-low", "Data Analyst"), _card("a-high", "AML Analyst")])
        p.parse()
        assert order == ["a-high", "a-low", "w-hr"]


class TestSchedulerHooks:
    def test_harvest_skipped_while_run_holds_lock(self, db, monkeypatch):
        called = []
        monkeypatch.setattr("src.parsers.web.linkedin.LinkedInParser.harvest",
                            lambda self, minutes: called.append(minutes))
        with scheduler._RUN_LOCK:
            scheduler.harvest_linkedin()
        assert called == []

    def test_prune_li_pool_does_not_raise_on_db_error(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("db locked")
        monkeypatch.setattr(storage, "li_pool_prune_seen", boom)
        scheduler._prune_li_pool()           # только лог, без исключения
