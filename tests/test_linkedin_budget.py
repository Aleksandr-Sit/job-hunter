"""Бюджет описаний LinkedIn раздаётся по кругу, а не по порядку сбора.

Повод — боевой прогон 30.08.2026: собрано 1005 карточек, потолок описаний 400,
и его целиком съели первые четыре страны. Семь стран из одиннадцати не получили
ни одного описания и до скоринга не дошли. Проблему создал порядок обхода:
карточки складывались странами подряд, а описания качались с начала списка.
"""
from src.parsers.web.linkedin import _WATCH_BUCKET, _round_robin


def _card(cid: str, geo: str) -> dict:
    return {"id": cid, "title": f"job {cid}", "geo": geo}


class TestRoundRobin:
    def test_every_geo_gets_a_slot_within_the_budget(self):
        """Главный случай: карточек больше потолка — страны не должны обнуляться."""
        cards = [_card(f"{g}-{i}", g) for g in "ABCDE" for i in range(50)]
        order = _round_robin(cards)
        within_budget = {c["geo"] for c in order[:20]}
        assert within_budget == set("ABCDE"), (
            f"в первые 20 попали не все страны: {within_budget}")

    def test_old_behaviour_would_have_starved_tail(self):
        """Контроль: порядок сбора действительно обнулял хвост — фиксируем разницу."""
        cards = [_card(f"{g}-{i}", g) for g in "ABCDE" for i in range(50)]
        naive = {c["geo"] for c in cards[:20]}
        assert naive == {"A"}, "тест устарел: порядок сбора изменился"

    def test_watch_companies_go_first_and_whole(self):
        """Вакансии отслеживаемых компаний редкие — терять их нельзя."""
        cards = ([_card(f"w{i}", _WATCH_BUCKET) for i in range(3)]
                 + [_card(f"{g}-{i}", g) for g in "AB" for i in range(10)])
        order = _round_robin(cards)
        assert [c["id"] for c in order[:3]] == ["w0", "w1", "w2"]

    def test_nothing_is_lost_or_duplicated(self):
        cards = [_card(f"{g}-{i}", g) for g in "ABC" for i in range(7)]
        order = _round_robin(cards)
        assert len(order) == len(cards)
        assert {c["id"] for c in order} == {c["id"] for c in cards}

    def test_uneven_buckets_do_not_break(self):
        """Стран с разным числом вакансий — обычный случай, не должен падать."""
        cards = ([_card(f"A-{i}", "A") for i in range(1)]
                 + [_card(f"B-{i}", "B") for i in range(9)])
        order = _round_robin(cards)
        assert len(order) == 10
        assert order[0]["geo"] == "A" and order[1]["geo"] == "B"

    def test_empty_input(self):
        assert _round_robin([]) == []
