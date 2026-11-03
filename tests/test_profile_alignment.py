"""Согласованные направления, дневные смены и отдельная география HH."""
from unittest.mock import MagicMock, patch

import pytest

from src.matcher.pre_filter import score_job, score_vacancy
from src.models import Job
from src.parsers.hh_parser import HHParser, query_label


@pytest.mark.parametrize("title", [
    "Специалист по внедрению", "Менеджер по внедрению IT-продукта",
    "Внедренец", "Implementation Specialist", "Customer Onboarding Specialist",
])
def test_it_implementation_is_a_target(title):
    result = score_job(Job(id="i", title=title, company="Example", url="u",
                          source="test", is_remote=True,
                          description="Разработчик SaaS. Внедрение программного продукта, обучение клиентов. Полностью удалённая работа."))
    assert result["best"]["passed_gate"]
    assert result["best"]["recommend"]


def test_implementation_does_not_admit_unrelated_industry():
    result = score_vacancy("Специалист по внедрению", "Работа с оборудованием на заводе.",
                           "support_fintech")
    assert not result["passed_gate"]


@pytest.mark.parametrize("schedule", [
    "График 2/2, дневные смены", "График 6/1, смены по 12 часов", "График 5/2",
])
def test_daytime_work_is_not_excluded(schedule):
    result = score_vacancy("Аккаунт-менеджер", "SaaS, CRM. " + schedule, "sales_remote")
    assert result["passed_gate"]


def test_daytime_mention_does_not_cancel_mandatory_nights():
    result = score_vacancy("Аккаунт-менеджер",
                           "SaaS. Можно работать днём, но обязательны 2 ночные смены в месяц.",
                           "sales_remote")
    assert not result["passed_gate"]


@pytest.mark.parametrize("query,expected", [
    ("crypto", 113),
    ({"text": "crypto", "field": "name", "area": [236, 213, 40]}, [236, 213, 40]),
])
def test_hh_sends_query_specific_areas_without_changing_default(query, expected):
    parser = HHParser.__new__(HHParser)
    parser.cfg = {"area": 113}
    session = MagicMock()
    session.get.return_value.content = b"<rss><channel/></rss>"
    with patch("src.parsers.hh_parser.requests.Session", return_value=session):
        assert parser._fetch_query(query) == []
    params = session.get.call_args.kwargs["params"]
    assert params["area"] == expected
    assert params["order_by"] == "publication_time"
    assert parser.cfg["area"] == 113


def test_hh_query_labels_keep_geographies_distinct():
    assert query_label({"text": "crypto", "area": [236]}) != query_label("crypto")


def test_hh_night_flag_without_required_nights_is_allowed_and_warned():
    from src.bot.formatter import format_job_message
    from src.models import MatchResult
    j = Job(id="night", title="Customer Success Manager", company="Example", url="u",
            source="hh.ru", salary_min=130000, salary_currency="RUB",
            description="SaaS, CRM. Работа ночью (hh): да\nГрафик (hh): FIVE_ON_TWO_OFF\n",
            raw={"hh_full": True})
    assert score_job(j)["best"]["recommend"]
    text = format_job_message(j, MatchResult(j.id, 80, [], [], ""))
    assert "подтвердить отсутствие обязательных ночей" in text


@pytest.mark.parametrize("schedule", [
    "График 2/2, вечерние смены с 16:00 до 00:00 по GMT+3",
    "График работы с 23:00 до 07:00", "Working hours 01:00–09:00",
])
def test_explicit_overnight_hours_are_rejected(schedule):
    r = score_vacancy("Customer Success Manager", "SaaS, CRM. " + schedule, "sales_remote")
    assert not r["passed_gate"]
    assert "ночные" in r["reasons"][0]


@pytest.mark.parametrize("schedule", [
    "График 2/2 с 08:00 до 20:00", "Рабочие часы с 09:00 до 18:00",
    "Время доставки отчёта 16:00–00:00", "Начало рабочего дня с 07:30 до 10:30",
])
def test_daytime_hours_and_non_schedule_times_are_kept(schedule):
    r = score_vacancy("Customer Success Manager", "SaaS, CRM. " + schedule, "sales_remote")
    assert r["passed_gate"]


@pytest.mark.parametrize("city", ["Актобе", "Тараз", "Караганда", "Кутаиси"])
def test_office_in_approved_country_is_not_limited_to_capital(city):
    j = Job(id="geo", title="Customer Success Manager", company="Example", url="u",
            source="hh.ru", location=city,
            description="SaaS, CRM. Формат работы (hh): ON_SITE\n",
            raw={"hh_full": True})
    r = score_job(j)["best"]
    assert r["passed_gate"]
    assert r["recommend"]


@pytest.mark.parametrize("city", ["Москва", "Ульяновск", "Милан"])
def test_country_reference_does_not_allow_unapproved_offices(city):
    j = Job(id="geo", title="Customer Success Manager", company="Example", url="u",
            source="hh.ru", location=city,
            description="SaaS, CRM. Формат работы (hh): ON_SITE\n",
            raw={"hh_full": True})
    assert not score_job(j)["best"]["passed_gate"]


@pytest.mark.parametrize("option", [
    "по согласованию возможны смежные смены день/ночь",
    "по согласованию возможны ночные смены",
])
def test_optional_overnight_shift_does_not_hide_daytime_option(option):
    from src.matcher.pre_filter import schedule_warnings
    text = ("SaaS. График работы: 2/2 с 09:00 до 21:00 (МСК); "
            f"{option} — 15:00–03:00 (МСК).")
    assert score_vacancy("Customer Success Manager", text, "sales_remote")["passed_gate"]
    assert any("отсутствие обязательных ночей" in note for note in schedule_warnings(text))


def test_salary_by_agreement_does_not_make_nights_optional():
    text = "SaaS. Оплата по согласованию. График 2/2 с 16:00 до 00:00."
    assert not score_vacancy("Customer Success Manager", text, "sales_remote")["passed_gate"]
