"""Русские заголовки раздела «желательно»: «Будет преимуществом», «Будет плюсом».

Боевой случай 19.09.2026 — ARBI Pay, Operations Manager: 58 баллов при пороге 42,
срезана гейтом иностранного языка за тайский под заголовком «Будет преимуществом».
Та же фраза бывает оговоркой к пункту — такая не должна гасить соседние требования.
"""
import pytest

from src.matcher.pre_filter import CRITERIA, _in_optional_section, _n, _required_hits


def _optional(text: str, word: str) -> bool:
    return _in_optional_section(text, text.index(word))


@pytest.mark.parametrize("text, word", [
    ("Требования:\n▫️\nопыт от года\n▪️\nБудет преимуществом:\n▫️\nзнание тайского", "тайского"),
    ("Требования:\nопыт\nБудет плюсом\nзнание китайского", "китайского"),
    ("Требования:\nопыт\n▪️\nПлюсом будет:\n▫️\nиспанский язык", "испанский"),
    ("Будет преимуществом: Опыт в Web3; тайский язык", "тайский"),
    ("знание теории; Будет плюсом: понимание геймдизайна, турецкий", "турецкий"),
])
def test_header_makes_language_optional(text, word):
    assert _optional(text, word)


@pytest.mark.parametrize("text, word", [
    ("Опыт в поддержке будет плюсом, но не обязателен.\nТребуется знание китайского",
     "китайского"),
    ("Опыт в YouTube будет преимуществом. Свободный турецкий язык", "турецкий"),
    ("• Будет плюсом понимание съемок. Обязательно: казахский язык", "казахский"),
    ("Опыт KOL будет преимуществом, но не является обязательным. Грузинский обязателен",
     "Грузинский"),
])
def test_inline_remark_does_not_hide_requirements(text, word):
    assert not _optional(text, word)


def test_required_header_after_optional_wins():
    text = "Будет плюсом:\nопыт в Web3\nТребования:\nсвободный тайский"
    assert not _optional(text, "тайский")


# Весовой штраф english_penalty — та же граница, что у гейта. До 18.10.2026 он
# считал фразы по всему тексту: xHuman, hh 138458820, «Разговорный английский язык»
# под «Будет плюсом:» → −14…−18 во всех ролях.
@pytest.mark.parametrize("text", [
    "Требования: опыт B2B от 2 лет.\nБудет плюсом:\nОпыт в SaaS.\nРазговорный английский язык.",
    "Кого мы ищем: опыт продаж. Будет плюсом: разговорный английский язык. Что мы предлагаем",
    "Опыт CRM. Разговорный английский будет плюсом.",
    "Requirements: 2 years in support.\nNice to have:\n- Fluent English\n- Crypto",
    "Желательно:\nсвободный английский",
])
def test_english_penalty_skips_optional(text):
    assert _required_hits(CRITERIA["english_penalty"], _n(text))[0] == 0


@pytest.mark.parametrize("text", [
    "Требования: свободный английский, опыт от 2 лет.",
    "Requirements: fluent English. Nice to have: crypto experience.",
    "Будет плюсом: опыт в CRM.\nТребования:\nразговорный английский",
    "Fluent English is required; additional languages are a plus.",
])
def test_english_penalty_keeps_requirements(text):
    assert _required_hits(CRITERIA["english_penalty"], _n(text))[0] > 0
