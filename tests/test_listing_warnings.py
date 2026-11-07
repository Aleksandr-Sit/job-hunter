"""Согласованные предупреждения видны даже при пропуске со стороны AI."""
import pytest

from src.bot.formatter import format_job_message
from src.matcher.pre_filter import listing_warnings
from src.models import Job, MatchResult


def job(description="", **kwargs):
    return Job(id="test", title="AI-интегратор", company="Example", url="u",
               source="example", description=description, **kwargs)


def test_unknown_salary_adds_no_warning():
    j = job()
    m = MatchResult("test", 80, [], ["Первое", "Второе", "Третье"], "")
    text = format_job_message(j, m)
    assert "Доход не подтверждён" not in text
    assert "Первое" in text and "Второе" in text


@pytest.mark.parametrize("note", [
    "зарплата не указана – доход необходимо уточнить",
    "Зарплата не указана, уточнить условия оплаты.",
    "Доход не подтверждён: зарплата не указана, уточнить условия оплаты.",
    "в объявлении не указана зарплата",
    "Зарплата не указана в вакансии",
    "требуется уточнение условий оплаты",
    "Необходимо уточнение зарплаты.",
])
def test_unknown_salary_is_not_repeated(note):
    m = MatchResult("test", 78, [], [note, "Офис в Дубае, потребуется переезд"], "")
    text = format_job_message(job(), m)
    assert text.count("\n· ") == 1
    assert "зарплата не указана" not in text
    assert "Офис в Дубае, потребуется переезд" in text


def test_salary_duplicate_keeps_other_facts_in_same_note():
    m = MatchResult("test", 78, [], [
        "зарплата не указана; офис в Дубае",
        "Оклад 50000, остальное — премия по KPI",
    ], "")
    text = format_job_message(job(), m)
    assert "зарплата не указана" not in text
    assert "офис в Дубае" in text
    assert "Оклад 50000, остальное — премия по KPI" in text


def test_pay_clarification_keeps_specific_conditions():
    m = MatchResult("test", 68, [], [
        "требуется уточнение условий оплаты; требуется опыт Buildroot/Yocto",
        "требуется уточнение условий оплаты на испытательном сроке",
    ], "")
    text = format_job_message(job(), m)
    assert "требуется опыт Buildroot/Yocto" in text
    assert "требуется уточнение условий оплаты на испытательном сроке" in text
    assert text.count("\n· ") == 2


def test_ai_warnings_are_not_repeated_or_replaced_with_filler():
    m = MatchResult("test", 78, [], ["Офис в Дубае", "офис в Дубае.", "Третье"], "")
    text = format_job_message(job(salary_min=100000), m)
    assert text.count("\n· ") == 1
    assert "Третье" not in text


def test_variable_pay_in_omitted_middle_is_visible():
    j = job("a" * 900 + " квартальная премия по KPI " + "b" * 900,
            salary_min=75000, salary_max=90000, salary_currency="RUB")
    assert "KPI" not in j.to_text()
    assert any("доход включает переменную часть" in note for note in listing_warnings(j))


def test_variable_pay_screenshot_keeps_amounts_without_generic_duplicate():
    j = job("Оклад и бонус по KPI", salary_max=120000)
    detail = "Оклад 50 000 ₽ + бонус до 70 000 ₽ – чистый доход может быть ниже требований"
    m = MatchResult("test", 72, [], [detail,
        "Комиссионные/премии не гарантированы, необходимо уточнить реальный размер дохода"], "")
    text = format_job_message(j, m)
    assert detail in text
    assert "Комиссионные/премии не гарантированы" not in text
    assert text.count("\n· ") == 2


def test_variable_pay_duplicate_keeps_unrelated_clause():
    m = MatchResult("test", 72, [], [
        "Премии не гарантированы; требуется опыт Zendesk",
        "Бонус 70 000 зависит от KPI",
    ], "")
    text = format_job_message(job("Премия по KPI", salary_min=50000), m)
    assert "требуется опыт Zendesk" in text
    assert "Бонус 70 000 зависит от KPI" in text
    assert "Премии не гарантированы" not in text


@pytest.mark.parametrize("pay", ["performance bonus", "sales commission", "variable pay"])
def test_english_variable_pay_is_flagged(pay):
    assert any("доход включает переменную часть" in note
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


@pytest.mark.parametrize("note", [
    "Формат работы (офис/удалёнка) не уточнён, требуется уточнение",
    "Уровень английского не указан",
    "Нет информации о графике",
    "Условия релокации не раскрыты",
])
def test_missing_information_is_omitted_from_all_sections(note):
    m = MatchResult("test", 72, [note], [note], note)
    text = format_job_message(job(), m)
    assert note not in text
    assert "Почему подходит" not in text
    assert "Учесть" not in text
    assert "💬" not in text


def test_unknown_salary_has_no_automatic_warning():
    assert listing_warnings(job()) == []


def test_night_option_requires_confirmation_of_daytime_only():
    notes = listing_warnings(job("Работа ночью (hh): да. Можно выбрать дневные смены."))
    assert any("отсутствие обязательных ночей" in note for note in notes)
