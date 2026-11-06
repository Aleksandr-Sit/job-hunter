"""Согласованные предупреждения видны даже при пропуске со стороны AI."""
import pytest

from src.bot.formatter import format_job_message
from src.matcher.pre_filter import listing_warnings
from src.models import Job, MatchResult


def job(description="", **kwargs):
    return Job(id="test", title="AI-интегратор", company="Example", url="u",
               source="example", description=description, **kwargs)


def test_unknown_salary_survives_two_other_ai_warnings():
    j = job()
    m = MatchResult("test", 80, [], ["Первое", "Второе", "Третье"], "")
    text = format_job_message(j, m)
    assert "Доход не подтверждён" in text
    assert "Первое" in text and "Второе" in text


@pytest.mark.parametrize("note", [
    "зарплата не указана – доход необходимо уточнить",
    "Зарплата не указана, уточнить условия оплаты.",
    "Доход не подтверждён: зарплата не указана, уточнить условия оплаты.",
])
def test_unknown_salary_is_not_repeated(note):
    m = MatchResult("test", 78, [], [note, "Офис в Дубае, потребуется переезд"], "")
    text = format_job_message(job(), m)
    assert text.count("\n· ") == 2
    assert text.count("зарплата не указана") == 1
    assert "Офис в Дубае, потребуется переезд" in text


def test_salary_duplicate_keeps_other_facts_in_same_note():
    m = MatchResult("test", 78, [], [
        "зарплата не указана; офис в Дубае",
        "Оклад 50000, остальное — премия по KPI",
    ], "")
    text = format_job_message(job(), m)
    assert text.count("зарплата не указана") == 1
    assert "офис в Дубае" in text
    assert "Оклад 50000, остальное — премия по KPI" in text


def test_ai_warnings_are_not_repeated_or_replaced_with_filler():
    m = MatchResult("test", 78, [], ["Офис в Дубае", "офис в Дубае.", "Третье"], "")
    text = format_job_message(job(salary_min=100000), m)
    assert text.count("\n· ") == 1
    assert "Третье" not in text


def test_variable_pay_in_omitted_middle_is_visible():
    j = job("a" * 900 + " квартальная премия по KPI " + "b" * 900,
            salary_min=75000, salary_max=90000, salary_currency="RUB")
    assert "KPI" not in j.to_text()
    assert any("Доход не подтверждён" in note for note in listing_warnings(j))


@pytest.mark.parametrize("pay", ["performance bonus", "sales commission", "variable pay"])
def test_english_variable_pay_is_flagged(pay):
    assert any("Доход не подтверждён" in note
               for note in listing_warnings(job(pay, salary_min=120000)))


@pytest.mark.parametrize("desc", [
    "График (hh): SIX_ON_ONE_OFF\nЧасы в день (hh): HOURS_12",
    "График 6/1, смены по 12 часов",
    "12-hour shifts, график 6/1",
])
def test_workload_warnings_are_not_lost(desc):
    notes = listing_warnings(job(desc, salary_min=130000))
    assert any("6/1" in note for note in notes)
    assert any("10–12" in note for note in notes)


def test_standard_day_has_no_invented_drawbacks():
    assert listing_warnings(job("График 5/2, 8 часов", salary_min=130000)) == []


def test_night_option_requires_confirmation_of_daytime_only():
    notes = listing_warnings(job("Работа ночью (hh): да. Можно выбрать дневные смены."))
    assert any("отсутствие обязательных ночей" in note for note in notes)
