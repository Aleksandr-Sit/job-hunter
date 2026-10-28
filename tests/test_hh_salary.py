"""Зарплата hh: «от/до» и актуальная сумма со страницы, 18.10.2026.

Повод — Ozon Банк «Специалист удаленной службы поддержки» (hh 138573342): в
карточке «90,000 RUB», на странице «до 75 000 ₽ до вычета налогов». Две причины:
RSS-версия из пула старше страницы (пул хранит первую увиденную), и «до N»
разбиралось как минимум — карточка печатала потолок как фикс.
"""
from unittest.mock import patch

import pytest

from src.bot.formatter import _meta_line
from src.models import Job, salary_range
from src.parsers import hh_enrich as he
from src.parsers.hh_parser import HHParser

RSS_DESC = ("Вакансия компании: Ozon Банк\nСоздана: 17.10.2026\nРегион: Самара\n"
            "Предполагаемый уровень месячного дохода: до 90 000 ₽")


def _page(comp: str) -> bytes:
    """Фрагмент страницы hh: JSON лежит в атрибуте, кавычки — &#34;."""
    return (f'<div data-qa="x" data-state="{{&#34;compensation&#34;:{comp}}}">'
            ).replace('"', "&#34;").replace("&#34;x&#34;", '"x"').encode()


def _job():
    return Job(id="hh_138573342", title="Специалист удаленной службы поддержки",
               company="Ozon Банк", description=RSS_DESC, location="Самара",
               url="https://hh.ru/vacancy/138573342", source="hh.ru",
               salary_max=90000, salary_currency="RUB")


class TestParseSalary:
    @pytest.mark.parametrize("line, expected", [
        ("Предполагаемый уровень месячного дохода: до 90 000 ₽", (None, 90000, "RUB")),
        ("Предполагаемый уровень месячного дохода: от 60 000 до 90 000 ₽", (60000, 90000, "RUB")),
        ("Предполагаемый уровень месячного дохода: от 50\xa0000 ₽", (50000, None, "RUB")),
        ("Предполагаемый уровень месячного дохода: до 2 500 $", (None, 2500, "USD")),
    ])
    def test_from_to(self, line, expected):
        assert HHParser._parse_salary(None, line) == expected


class TestCompensation:
    def test_upper_bound_gross(self):
        html = _page('{"to":75000,"currencyCode":"RUR","gross":true,"mode":"MONTH"}')
        assert he._compensation(html) == {"from": None, "to": 75000,
                                          "currency": "RUB", "gross": True, "mode": "MONTH"}

    def test_range_net(self):
        html = _page('{"from":120000,"to":150000,"currencyCode":"RUR","gross":false}')
        c = he._compensation(html)
        assert (c["from"], c["to"], c["gross"]) == (120000, 150000, False)

    def test_no_salary(self):
        assert he._compensation(b"<html>no salary</html>") is None
        assert he._compensation(_page("{}")) is None

    def test_rss_line_replaced_not_duplicated(self):
        j = _job()
        he._apply_compensation(j, {"from": None, "to": 75000, "currency": "RUB",
                                   "gross": True})
        assert (j.salary_min, j.salary_max) == (None, 75000)
        assert "90 000" not in j.description
        assert "Зарплата (hh): до 75 000 RUB, до вычета налогов" in j.description
        assert j.description.startswith("Вакансия компании: Ozon Банк")
        assert j.raw["salary_gross"] is True

    def test_line_added_when_rss_had_none(self):
        j = _job()
        j.description = "Вакансия компании: X\nРегион: Самара"
        he._apply_compensation(j, {"from": 80000, "to": None, "currency": "RUB",
                                   "gross": False})
        assert j.description.startswith("Зарплата (hh): от 80 000 RUB, на руки\n")


class TestEnrichUpdatesSalary:
    FULL = "Полное описание вакансии. " * 30

    def test_page_salary_wins_over_pool_version(self):
        comp = {"from": None, "to": 75000, "currency": "RUB", "gross": True}
        j = _job()
        with patch.object(he, "_fetch", return_value=(self.FULL, 800_000, comp)), \
                patch.object(he.time, "sleep"):
            he.enrich([j])
        assert j.salary_max == 75000 and "90 000" not in j.description

    def test_old_two_value_fetch_still_works(self):
        j = _job()
        with patch.object(he, "_fetch", return_value=(self.FULL, 800_000)), \
                patch.object(he.time, "sleep"):
            he.enrich([j])
        assert j.salary_max == 90000 and self.FULL.strip() in j.description


class TestCard:
    @pytest.mark.parametrize("lo, hi, text", [
        (None, 75000, "до 75,000"), (90000, None, "от 90,000"),
        (60000, 90000, "60,000–90,000"), (None, None, ""),
    ])
    def test_range(self, lo, hi, text):
        assert salary_range(lo, hi) == text

    def test_meta_line_shows_bound_and_tax(self):
        j = _job()
        j.salary_max, j.raw["salary_gross"] = 75000, True
        assert "💰 до 75,000 RUB до вычета" in _meta_line(j)

    def test_ai_text_has_bound(self):
        assert "Salary: до 90000 RUB" in _job().to_text()
