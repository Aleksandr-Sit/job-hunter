"""Разгрузка очереди пересмотра HH, 18.10.2026.

Аудит: 17 широких запросов давали ~95% притока, очередь была общей и «свежие
первыми», поэтому вакансии узких запросов умирали по сроку наравне с «Продавец @
Магнит», а 4 179 городских копий пересматривались по одной. Тест держит: запрос
может быть словарём {text, field: name, priority: low}; вакансия несёт метку
запроса (и не теряет её при слиянии с пулом); очередь — слежка → обычные → low,
внутри high-заголовки раньше; копии пересматриваются одной, Самара — представитель.
"""
import logging
from datetime import datetime, timedelta, timezone

import pytest

from src import scheduler
from src.models import Job
from src.parsers.hh_parser import HHParser, _query_params, query_label

_NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


def _job(jid, title=None, company="ACME", location="Москва", hours_ago=0.0,
         queries=None, watch=False):
    j = Job(id=jid, title=title or f"Вакансия {jid}", company=company,
            location=location, description="rss", url=f"https://hh.ru/vacancy/{jid}",
            source="hh.ru", published_at=_NOW - timedelta(hours=hours_ago))
    if queries is not None:
        j.raw["hh_queries"] = list(queries)
    if watch:
        j.raw["employer_watch"] = True
    return j


class TestQueryFormat:
    def test_plain_string(self):
        assert query_label("казначей") == "казначей"
        assert _query_params("казначей") == {"text": "казначей"}

    def test_name_field_narrows_and_gets_own_label(self):
        entry = {"text": "менеджер по продажам IT", "field": "name"}
        assert query_label(entry) == "менеджер по продажам IT [название]"
        assert _query_params(entry) == {"text": "менеджер по продажам IT",
                                        "search_field": "name"}

    def test_dict_without_field_searches_whole_text(self):
        entry = {"text": "служба поддержки", "priority": "low"}
        assert query_label(entry) == "служба поддержки"
        assert _query_params(entry) == {"text": "служба поддержки"}

    def test_employer_watch_has_no_text(self):
        assert _query_params(None) == {}

    def test_low_priority_queries_from_config(self):
        cfg = {"parsers": {"hh": {"search_queries": [
            "казначей",
            {"text": "специалист технической поддержки", "field": "name",
             "priority": "low"},
            {"text": "Automation engineer", "field": "name"},
        ]}}}
        assert scheduler._low_priority_queries(cfg) == frozenset(
            {"специалист технической поддержки [название]"})


class TestQueryTags:
    @staticmethod
    def _parser(monkeypatch, by_query, second_pass=False):
        p = HHParser()
        p.cfg = {"search_queries": ["казначей", {"text": "P2P", "field": "name"}],
                 "second_pass": second_pass, "employer_ids": []}
        seen_queries = []

        def fake(query, by_date=True, employer_id=None):
            seen_queries.append(query)
            ids = by_query.get(query_label(query), [])
            return [Job(id=i, title="T", company="C", description="", url="u",
                        source="hh.ru") for i in ids]
        monkeypatch.setattr(p, "_fetch_query", fake)
        return p, seen_queries

    def test_parse_tags_each_vacancy_with_all_queries(self, monkeypatch):
        p, seen_queries = self._parser(monkeypatch, {
            "казначей": ["hh_1", "hh_2"], "P2P [название]": ["hh_2"]})
        jobs = {j.id: j for j in p.parse()}
        assert jobs["hh_1"].raw["hh_queries"] == ["казначей"]
        assert jobs["hh_2"].raw["hh_queries"] == ["казначей", "P2P [название]"]
        # словарь уходит в _fetch_query как есть — параметры строит _query_params
        assert {"text": "P2P", "field": "name"} in seen_queries

    def test_harvest_tags_too(self, monkeypatch):
        p, _ = self._parser(monkeypatch, {
            "казначей": ["hh_1"], "P2P [название]": ["hh_1", "hh_3"]})
        jobs = {j.id: j for j in p.harvest()}
        assert jobs["hh_1"].raw["hh_queries"] == ["казначей", "P2P [название]"]
        assert jobs["hh_3"].raw["hh_queries"] == ["P2P [название]"]


class TestPoolMerge:
    def test_pool_tags_merged_into_fresh_version(self, monkeypatch):
        fresh = _job("hh_1", queries=["служба поддержки"])
        pooled = _job("hh_1", queries=["P2P [название]", "служба поддержки"])
        other = _job("hh_2", queries=["казначей"])
        monkeypatch.setattr(scheduler.storage, "pool_load",
                            lambda max_age_days: ([pooled, other], 0))
        merged = {j.id: j for j in scheduler._merge_hh_pool([fresh], 14)}
        assert merged["hh_1"] is fresh
        assert fresh.raw["hh_queries"] == ["служба поддержки", "P2P [название]"]
        assert merged["hh_2"] is other

    def test_untagged_pool_version_does_not_crash(self, monkeypatch):
        fresh = _job("hh_1")
        monkeypatch.setattr(scheduler.storage, "pool_load",
                            lambda max_age_days: ([_job("hh_1")], 0))
        scheduler._merge_hh_pool([fresh], 14)
        assert not fresh.raw.get("hh_queries")


class TestRescueOrderTiers:
    LOW = frozenset({"служба поддержки"})

    def test_watch_then_normal_then_low(self):
        jobs = [_job("low", queries=["служба поддержки"], hours_ago=0),
                _job("norm", queries=["казначей"], hours_ago=5),
                _job("w", hours_ago=50, watch=True)]
        order = [j.id for j in scheduler._rescue_order(jobs, self.LOW)]
        assert order == ["w", "norm", "low"]

    def test_found_also_by_normal_query_is_not_low(self):
        jobs = [_job("low", queries=["служба поддержки"], hours_ago=0),
                _job("both", queries=["служба поддержки", "казначей"], hours_ago=9)]
        assert [j.id for j in scheduler._rescue_order(jobs, self.LOW)] == ["both", "low"]

    def test_untagged_counts_as_normal(self):
        jobs = [_job("low", queries=["служба поддержки"], hours_ago=0),
                _job("old_pool", hours_ago=9)]
        assert [j.id for j in scheduler._rescue_order(jobs, self.LOW)] == ["old_pool", "low"]

    def test_high_title_before_fresher_low_title(self):
        jobs = [_job("plain", hours_ago=0),
                _job("crypto", title="Специалист поддержки криптобиржи", hours_ago=9)]
        assert [j.id for j in scheduler._rescue_order(jobs)] == ["crypto", "plain"]


class TestGroupCopies:
    def test_copies_grouped_first_member_is_rep(self):
        a1 = _job("a1", title="Продавец", company="Магнит", location="Москва")
        b = _job("b", title="Казначей", company="ТАУ Сервис")
        a2 = _job("a2", title="Продавец", company="Магнит", location="Казань")
        reps, copies = scheduler._group_copies([a1, b, a2])
        assert [j.id for j in reps] == ["a1", "b"]
        assert [j.id for j in copies["a1"]] == ["a2"] and copies["b"] == []

    def test_samara_copy_is_representative(self):
        msk = _job("msk", title="Оператор чата", company="X", location="Москва")
        smr = _job("smr", title="Оператор чата", company="X", location="Самара")
        reps, copies = scheduler._group_copies([msk, smr])
        assert [j.id for j in reps] == ["smr"]
        assert [j.id for j in copies["smr"]] == ["msk"]


class _Stats:
    healthy = True

    def __init__(self, enriched):
        self.enriched = enriched


@pytest.fixture()
def fake_enrich(monkeypatch):
    seen = []

    def enrich(jobs, limit=120):
        for j in jobs:
            j.description += "\nполное описание"
            seen.append(j.id)
        return _Stats(len(jobs))
    monkeypatch.setattr("src.parsers.hh_enrich.enrich", enrich)
    return seen


def _verdict(passing_ids):
    def score(j):
        ok = j.id in passing_ids
        return {"best": {"passed_gate": ok, "recommend": ok,
                         "role": "support_fintech" if ok else None, "reasons": []}}
    return score


class TestRescueCopies:
    def test_copies_of_recovered_returned_as_pairs(self, fake_enrich, monkeypatch):
        monkeypatch.setattr(scheduler, "score_job", _verdict({"a1"}))
        a1 = _job("a1", title="Оператор чата", company="X", location="Москва")
        a2 = _job("a2", title="Оператор чата", company="X", location="Казань")
        recovered, deferred, copies = scheduler._rescue_hh_rejects([a1, a2], limit=5)
        assert fake_enrich == ["a1"]          # копия не качалась
        assert [j.id for j in recovered] == ["a1"] and deferred == []
        assert [(c.id, r.id) for c, r in copies] == [("a2", "a1")]

    def test_copies_of_deferred_deferred_together(self, fake_enrich, monkeypatch):
        monkeypatch.setattr(scheduler, "score_job", _verdict(set()))
        jobs = [_job("x", hours_ago=0),
                _job("y1", title="Вакансия y", company="Y", hours_ago=5),
                _job("y2", title="Вакансия y", company="Y", hours_ago=6)]
        _, deferred, copies = scheduler._rescue_hh_rejects(jobs, limit=1)
        assert {j.id for j in deferred} == {"y1", "y2"} and copies == []

    def test_log_line_by_query(self, fake_enrich, monkeypatch, caplog):
        monkeypatch.setattr(scheduler, "score_job", _verdict(set()))
        jobs = [_job("a", queries=["служба поддержки"]),
                _job("b", queries=["служба поддержки", "казначей"]),
                _job("c")]
        with caplog.at_level(logging.INFO, logger=scheduler.logger.name):
            scheduler._rescue_hh_rejects(jobs, limit=5,
                                         low_queries=frozenset({"служба поддержки"}))
        line = next(r.getMessage() for r in caplog.records
                    if r.getMessage().startswith("HH очередь по запросам"))
        assert "«служба поддержки» (низк.) 2" in line
        assert "«казначей» 1" in line and line.endswith("без метки 1")
