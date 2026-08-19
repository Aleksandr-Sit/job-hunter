"""Единая нормализация данных вакансии для ВСЕХ парсеров.

Зачем модуль: одни и те же баги находились в разных парсерах по очереди —
обрезка описания прятала требования от гейтов (Bybit, 05.08), поле локации
подменялось организационным регионом (Gemini, 05.08), формат работы читался
из недостоверного флага (Ashby: workplaceType=Hybrid и isRemote=true
одновременно, 05.08). Правка в одном парсере не чинила остальные.

Теперь правила живут здесь, а парсеры их вызывают. Меняем логику — меняется
у всех источников сразу.
"""
from __future__ import annotations

from ..models import MAX_DESCRIPTION_CHARS

# Локация, которая сама говорит «удалённо» — тогда города в ней нет.
_REMOTE_LOCATION_WORDS = (
    "remote", "anywhere", "worldwide", "distributed",
    "удалён", "удален", "дистанцион",
)

# Форматы работы из ATS: единственный достоверный источник, когда поле есть.
_ONSITE_WORKPLACE = ("hybrid", "onsite", "on-site", "on site", "in office", "office")


def clean_description(text: str | None) -> str:
    """Единая обрезка описания.

    Лимит держим большим: требования (языки, гражданство, опыт, формат) в
    длинных вакансиях стоят В КОНЦЕ, и короткая обрезка прятала их от гейтов.
    Описания живут только в памяти прогона, в БД не пишутся.
    """
    return (text or "")[:MAX_DESCRIPTION_CHARS]


def is_remote_location(location: str | None) -> bool:
    """Локация сама заявляет удалённый формат."""
    return any(w in (location or "").lower() for w in _REMOTE_LOCATION_WORDS)


def detect_remote(
    location: str | None = "",
    description: str | None = "",
    workplace_type: str | None = None,
    explicit_flag: bool | None = None,
) -> bool:
    """Единое определение удалённого формата по приоритету достоверности.

    1. `workplace_type` из ATS (Remote / Hybrid / OnSite) — самый надёжный.
       Ashby отдаёт его корректно, при этом его же `isRemote` врёт: у Elliptic
       26 из 28 вакансий были Hybrid и isRemote=true одновременно.
    2. Локация вида «Remote» / «Worldwide».
    3. ЯВНАЯ формулировка формата в тексте («fully remote», «полностью удалённо»).
       Одиночное слово «remote» сигналом НЕ считается — оно встречается в
       «remote troubleshooting», «remote access», «flexibility of remote work»
       у офисных вакансий.
    4. Флаг источника — только если ничего выше не сработало.
    """
    from ..matcher.pre_filter import _EXPLICIT_REMOTE  # локальный импорт: циклы

    wp = (workplace_type or "").strip().lower()
    if wp:
        return wp == "remote"

    if is_remote_location(location):
        return True

    if _EXPLICIT_REMOTE.search(description or ""):
        return True

    return bool(explicit_flag)


def schedule_note(description: str | None) -> str:
    """Пометка про смены для промпта AI, если они найдены в ПОЛНОМ описании.

    Нужна по той же причине, что и `onsite_note`, только острее: в промпт уходит
    начало и конец описания, а середина выбрасывается (`_sample_description`).
    У вакансии SOFTSWISS 19.08.2026 фраза «2–4 night shifts per month» стояла на
    позиции 2183 из 3245 — ровно в выброшенной середине, и модель про ночные
    смены не знала вообще, сколько её об этом ни инструктируй.
    """
    from ..matcher.pre_filter import _n, _night_shift_mode  # локальный импорт: циклы

    mode = _night_shift_mode(_n(description or ""))
    if mode == "core":
        return "\nSchedule: NIGHT SHIFTS are part of the regular working pattern."
    if mode == "occasional":
        return "\nSchedule: the listing mentions occasional NIGHT SHIFTS (a few per month)."
    if mode == "shift":
        return "\nSchedule: rotating/shift work (no night shifts stated)."
    return ""


def onsite_note(workplace_type: str | None) -> str:
    """Строка-пометка для описания, если ATS явно говорит про офис/гибрид.

    Скоринг читает ТЕКСТ, а не поля источника, поэтому формат нужно донести
    до него текстом — иначе гибрид выглядит как обычная вакансия.
    """
    wp = (workplace_type or "").strip().lower()
    if wp in _ONSITE_WORKPLACE:
        return f"\nWork format: {workplace_type} (on-site presence required)"
    return ""
