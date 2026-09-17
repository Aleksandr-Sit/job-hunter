"""Очередь пересмотра отказов HH: порядок, потолок, отложенные.

Ревизия 17.09.2026: потолок пересмотра (220) упирался в каждом прогоне, слежка за
работодателями шла в конце выдачи HH и срезалась, а всё за потолком помечалось
увиденным до следующей смены критериев. Теперь слежка идёт первой, дальше свежие,
а не влезшие в потолок возвращаются в очередь, а не хоронятся.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src import scheduler
from src.models import Job

_NOW = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)


def _job(jid, hours_ago=0.0, watch=False, published=True):
    j = Job(id=jid, title=f"Вакансия {jid}", company="ACME", description="rss",
            url=f"https://hh.ru/vacancy/{jid}", source="hh.ru",
            published_at=_NOW - timedelta(hours=hours_ago) if published else None)
    if watch:
        j.raw["employer_watch"] = True
    return j


class _Stats:
    def __init__(self, healthy=True, enriched=0):
        self.healthy = healthy
        self.enriched = enriched


@pytest.fixture()
def no_network(monkeypatch):
    """enrich подменён: дописывает описание тем, чей id не в `fail`; score_job — отказ."""
    state = {"fail": set(), "healthy": True, "enriched_ids": []}

    def fake_enrich(jobs, limit=120):
        n = 0
        for j in list(jobs)[:limit]:
            if j.id in state["fail"]:
                continue
            j.description += "\nполное описание"
            state["enriched_ids"].append(j.id)
            n += 1
        return _Stats(healthy=state["healthy"], enriched=n)

    monkeypatch.setattr("src.parsers.hh_enrich.enrich", fake_enrich)
    monkeypatch.setattr(scheduler, "score_job", lambda j: {"best": {
        "passed_gate": False, "recommend": False, "role": None, "reasons": []}})
    return state


class TestRescueOrder:
    def test_watch_first_even_from_tail(self):
        jobs = [_job("a", 1), _job("b", 2), _job("w", 50, watch=True)]
        assert [j.id for j in scheduler._rescue_order(jobs)][0] == "w"

    def test_fresh_before_old_and_undated_last(self):
        jobs = [_job("old", 48), _job("none", published=False), _job("new", 1)]
        assert [j.id for j in scheduler._rescue_order(jobs)] == ["new", "old", "none"]

    def test_naive_and_aware_dates_do_not_crash(self):
        naive = _job("naive")
        naive.published_at = datetime(2026, 9, 17, 11)
        scheduler._rescue_order([naive, _job("aware", 3)])


class TestRescueDeferred:
    def test_tail_over_limit_is_deferred_not_reviewed(self, no_network):
        jobs = [_job(f"j{i}", hours_ago=i) for i in range(5)]
        recovered, deferred = scheduler._rescue_hh_rejects(jobs, limit=3)
        assert recovered == []
        assert [j.id for j in deferred] == ["j3", "j4"]
        assert no_network["enriched_ids"] == ["j0", "j1", "j2"]

    def test_watched_reviewed_before_limit_cuts(self, no_network):
        jobs = [_job(f"j{i}", hours_ago=i) for i in range(3)] + [_job("w", 99, watch=True)]
        _, deferred = scheduler._rescue_hh_rejects(jobs, limit=2)
        assert "w" in no_network["enriched_ids"]
        assert {j.id for j in deferred} == {"j1", "j2"}

    def test_blocked_hh_defers_unfetched(self, no_network):
        no_network["healthy"] = False
        no_network["fail"] = {"j0", "j2"}
        jobs = [_job(f"j{i}", hours_ago=i) for i in range(4)]
        _, deferred = scheduler._rescue_hh_rejects(jobs, limit=3)
        assert {j.id for j in deferred} == {"j0", "j2", "j3"}

    def test_single_failure_in_healthy_run_is_not_deferred(self, no_network):
        no_network["fail"] = {"j1"}
        jobs = [_job(f"j{i}", hours_ago=i) for i in range(3)]
        _, deferred = scheduler._rescue_hh_rejects(jobs, limit=3)
        assert deferred == []

    def test_recovered_returned(self, no_network, monkeypatch):
        monkeypatch.setattr(scheduler, "score_job", lambda j: {"best": {
            "passed_gate": True, "recommend": True, "role": "crypto_ops",
            "reasons": ["ok"]}})
        recovered, deferred = scheduler._rescue_hh_rejects([_job("a")], limit=5)
        assert [j.id for j in recovered] == ["a"] and deferred == []
        assert recovered[0].match_role == "crypto_ops"

    def test_empty(self, no_network):
        assert scheduler._rescue_hh_rejects([], limit=5) == ([], [])


class TestWatchFlag:
    def test_flag_set_on_vacancy_already_found_by_query(self, monkeypatch):
        from src.parsers.hh_parser import HHParser
        p = HHParser()
        p.cfg = {"search_queries": ["казначей"], "second_pass": False,
                 "employer_ids": [{"id": 12354017, "name": "ТАУ Сервис"}]}
        shared = [Job(id="hh_1", title="Казначей", company="ТАУ Сервис",
                      description="", url="u", source="hh.ru")]
        only_watch = Job(id="hh_2", title="Бэк-офис", company="ТАУ Сервис",
                         description="", url="u", source="hh.ru")

        def fake(query, by_date=True, employer_id=None):
            if employer_id:
                return [Job(**{**shared[0].__dict__, "raw": {}}), only_watch]
            return shared
        monkeypatch.setattr(p, "_fetch_query", fake)

        jobs = {j.id: j for j in p.parse()}
        assert set(jobs) == {"hh_1", "hh_2"}
        assert jobs["hh_1"].raw.get("employer_watch") is True
        assert jobs["hh_2"].raw.get("employer_watch") is True

    def test_query_only_vacancy_not_flagged(self, monkeypatch):
        from src.parsers.hh_parser import HHParser
        p = HHParser()
        p.cfg = {"search_queries": ["казначей"], "second_pass": False, "employer_ids": []}
        monkeypatch.setattr(p, "_fetch_query", lambda *a, **k: [
            Job(id="hh_3", title="Казначей", company="X", description="", url="u",
                source="hh.ru")])
        assert not p.parse()[0].raw.get("employer_watch")
