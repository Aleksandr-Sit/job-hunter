"""Сортировка карточек LinkedIn по заголовку до скачивания описаний.

Повод — прогоны 14.10.2026: ~1700 карточек, за 45 минут стадии 19 и 15 описаний,
и все ушли на Chainalysis из watch-списка (инженеры, HR, юристы). Ни одной
вакансии из стран до скоринга не дошло.
"""
from src.matcher.title_triage import title_triage
from src.parsers.web.linkedin import _WATCH_BUCKET, _triage_queue


class TestTitleTriage:
    def test_dev_and_executive_titles_dropped(self):
        for t in ("Senior Software Engineer, Graphs", "Sr. Backend Engineer (Remote)", "CEO"):
            assert title_triage(t) == "drop", t

    def test_foreign_language_title_dropped(self):
        assert title_triage("Deployment Strategist (Mandarin Speaking)") == "drop"

    def test_role_words_high(self):
        for t in ("Crypto Operations Analyst", "AML Analyst", "QA Engineer (Manual)",
                  "Payments Operations Specialist", "Account Manager, Russian-speaking"):
            assert title_triage(t) == "high", t

    def test_domain_word_without_role_word_high(self):
        """Замер: «Crypto Specialist» ушёл в Telegram с 85, ролевого слова в нём нет."""
        assert title_triage("Crypto Specialist") == "high"

    def test_russian_mention_high(self):
        assert title_triage("CS Specialist (Russian)") == "high"

    def test_unclear_titles_low_not_dropped(self):
        for t in ("Director, HRBP - GTM", "Recruiter, GTM - AMER", "Data Analyst"):
            assert title_triage(t) == "low", t


def _card(cid, title, geo):
    return {"id": cid, "title": title, "company": "X", "geo": geo}


class TestTriageQueue:
    def _cards(self):
        return [
            _card("w-hr", "Director, HRBP - GTM", _WATCH_BUCKET),
            _card("w-eng", "Staff Software Engineer, Graphs", _WATCH_BUCKET),
            _card("w-crypto", "Deployment Strategist - Crypto Investigations", _WATCH_BUCKET),
            _card("a-low", "Data Analyst", "A"),
            _card("a-high", "Customer Support Specialist", "A"),
            _card("b-high", "AML Analyst", "B"),
        ]

    def test_order_high_then_geo_low_then_watch_low(self):
        queue, sizes = _triage_queue(self._cards())
        ids = [c["id"] for c in queue]
        assert ids == ["w-crypto", "a-high", "b-high", "a-low", "w-hr"]
        assert sizes == {"high": 3, "low": 2, "drop": 1}

    def test_drop_not_queued_rest_not_lost(self):
        queue, _ = _triage_queue(self._cards())
        assert {c["id"] for c in queue} == {c["id"] for c in self._cards()} - {"w-eng"}

    def test_empty(self):
        assert _triage_queue([]) == ([], {"high": 0, "low": 0, "drop": 0})
