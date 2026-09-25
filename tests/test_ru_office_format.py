"""Офис в РФ по ПОЛЮ формата работы hh, а не по словам в тексте.

Повод 25.09.2026: RSS hh отдаёт только город, а дообогащение брало лишь блок
описания. Поэтому отделения ВТБ получали +10 за «удалённая консультация» в тексте,
а офис РТ-ИБ у м. «Южная» не штрафовался вовсе. Решение владельца: удалёнка в
приоритете, офис в другом городе — штраф (исключительный оффер он хочет видеть),
офис в Самаре — отдельный вес.
"""
import pytest

from src.matcher.pre_filter import score_vacancy
from src.parsers import hh_enrich as he

_TEXT = ("Менеджер по работе с клиентами в SaaS. Удержание действующих клиентов, "
         "CRM, удалённая консультация клиентов.\n")


def _page(formats: bytes) -> bytes:
    return b'<script>{"x":1,"workFormats":' + formats + b',"y":2}</script>'


@pytest.mark.parametrize("formats,expected", [
    (b'["REMOTE"]', "Формат работы (hh): REMOTE\n"),
    (b'["ON_SITE","REMOTE","HYBRID"]', "Формат работы (hh): ON_SITE, REMOTE, HYBRID\n"),
    (b'[]', ""),
])
def test_format_note_from_page(formats, expected):
    assert he._format_note(_page(formats)) == expected


def test_format_note_from_escaped_page():
    """Так поле лежит в живой странице: JSON в атрибуте, кавычки = &#34;.
    Рядом — вложенная форма и справочник всех форматов, их брать нельзя."""
    page = (b'&#34;workFormats&#34;:[{&#34;workFormatsElement&#34;:[&#34;ON_SITE&#34;]}],'
            b'&#34;workFormats&#34;:[&#34;ON_SITE&#34;],&#34;x&#34;:[],'
            b'&#34;workFormats&#34;:[{&#34;id&#34;:&#34;REMOTE&#34;,&#34;text&#34;:&#34;r&#34;}]')
    assert he._format_note(page) == "Формат работы (hh): ON_SITE\n"


def test_format_note_absent_field():
    assert he._format_note(b"<html>no field</html>") == ""


def _score(formats: bytes | None, city: str) -> dict:
    note = he._format_note(_page(formats)) if formats else ""
    return score_vacancy("Аккаунт-менеджер", f"{note}{_TEXT}Location: {city}",
                         "sales_remote")


def test_remote_field_keeps_remote_bonus():
    r = _score(b'["REMOTE","HYBRID"]', "Москва")
    assert "remote" in r["reasons"]
    assert not any("офис" in x for x in r["reasons"])


@pytest.mark.parametrize("formats", [b'["ON_SITE"]', b'["HYBRID"]'])
def test_office_in_other_city_is_penalised_not_rejected(formats):
    remote = _score(b'["REMOTE"]', "Москва")
    office = _score(formats, "Москва")
    assert "remote" not in office["reasons"], "слово «удалённая» в тексте — не формат"
    assert "офис/гибрид в другом городе РФ, удалёнки нет" in office["reasons"]
    assert office["passed_gate"] and office["score"] < remote["score"]


def test_home_city_office_is_softer_than_other_city():
    home = _score(b'["ON_SITE"]', "Самара")
    other = _score(b'["ON_SITE"]', "Казань")
    assert home["score"] > other["score"]
    assert any("Самаре" in x for x in home["reasons"])


def test_relocation_city_office_is_not_ru_office():
    r = _score(b'["ON_SITE"]', "Ереван")
    assert not any("РФ" in x for x in r["reasons"])


def test_without_field_behaviour_unchanged():
    """Не дообогащённая вакансия (формата нет) скорится как раньше."""
    r = _score(None, "Москва")
    assert "remote" in r["reasons"]
