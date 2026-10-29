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

import html
import re

from bs4 import BeautifulSoup

from ..models import _AI_HEAD_CHARS, _AI_TAIL_CHARS, MAX_DESCRIPTION_CHARS

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


def html_to_text(raw: str | None) -> str:
    """HTML (в том числе экранированный) → читаемый текст. Вызывать ДО обрезки.

    Greenhouse отдаёт описание ЭКРАНИРОВАННЫМ: проверено 17.09.2026 — 25 из 25
    вакансий Bitpanda начинались с `&lt;div…`. Разметка уходила в гейты как есть
    и съедала лимит обрезки, из-за чего требования (они стоят в конце) до гейтов
    не доезжали. Lever держит HTML в `lists[].content`, RemoteOK — в описании.

    Порядок обязателен: сначала распаковать сущности, потом разобрать теги, и
    только затем `clean_description` — иначе обрезка режет разметку, а не текст.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    unescaped = html.unescape(text)
    if "<" not in unescaped:
        return unescaped
    return BeautifulSoup(unescaped, "html.parser").get_text(separator="\n", strip=True)


def clean_inline(text: str | None) -> str:
    """Однострочное поле (заголовок, компания, город) → чистый текст.

    hh отдаёт заголовок в RSS с ДВОЙНЫМ экранированием: 01.10.2026 в Telegram
    пришло «Главный специалист по&nbsp;финансовому мониторингу». До этого то же
    самое чинили по одному парсеру (LinkedIn, RemoteOK, Remote3 — 277 заголовков
    с `&amp;` в базе), и следующий источник снова приносил сущности. Поэтому
    распаковываем до упора (не больше трёх слоёв) и схлопываем пробелы —
    неразрывный `\\xa0` `split()` тоже считает пробелом.
    """
    s = text or ""
    for _ in range(3):
        unescaped = html.unescape(s)
        if unescaped == s:
            break
        s = unescaped
    return " ".join(s.split())


def clean_job_fields(job) -> None:
    """Чистит видимые поля вакансии на месте. Вызывается один раз для ВСЕХ
    источников сразу после сбора (`scheduler.run_once`), а не в каждом парсере."""
    job.title = clean_inline(job.title)
    job.company = clean_inline(job.company)
    if job.location:
        job.location = clean_inline(job.location)


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


# Что из выброшенной середины описания AI обязан увидеть: язык и право на работу.
_HIDDEN_REQUIREMENT = re.compile(
    r'english|английск|'
    r'work\s+(?:permit|authori[sz]ation|visa)|right\s+to\s+work|eligible\s+to\s+work|'
    r'citizen\w*|passport|гражданств\w*|разрешени\w*\s+на\s+работу',
    re.IGNORECASE,
)
# Граница пункта требований. LinkedIn склеивает пункты списка без точек, поэтому
# фрагмент дополнительно ограничен длиной.
_FRAGMENT_BREAK = re.compile(r'[.!?;•·\n]')
_FRAGMENT_SPAN = 120
_MAX_FRAGMENTS = 3


def hidden_requirements_note(description: str | None) -> str:
    """Строки про язык и право на работу из СЕРЕДИНЫ описания — для промпта AI.

    AI получает 700 символов с начала и 800 с конца (`_sample_description`), а
    требования часто стоят посередине. Workster 29.10.2026: «Russian C1 and very
    good English» на 960-м символе из 1817, и AI написал «уровень английского не
    указан». В батче 18.10 упоминание английского было ТОЛЬКО в середине у 444 из
    1276 вакансий LinkedIn и 246 из 1078 Greenhouse. Пересылать всё описание
    дорого; пересылаем только эти фрагменты, как `schedule_note` — смены.
    """
    text = description or ""
    if len(text) <= _AI_HEAD_CHARS + _AI_TAIL_CHARS:
        return ""
    lo, hi = _AI_HEAD_CHARS, len(text) - _AI_TAIL_CHARS
    fragments: list[str] = []
    covered = -1
    for m in _HIDDEN_REQUIREMENT.finditer(text, lo, hi):
        if m.start() < covered:          # уже внутри предыдущего фрагмента
            continue
        s = max(0, m.start() - _FRAGMENT_SPAN)
        e = min(len(text), m.end() + _FRAGMENT_SPAN)
        left = list(_FRAGMENT_BREAK.finditer(text, s, m.start()))
        if left:
            s = left[-1].end()
        right = _FRAGMENT_BREAK.search(text, m.end(), e)
        if right:
            e = right.start()
        fragments.append(" ".join(text[s:e].split()))
        covered = e
        if len(fragments) == _MAX_FRAGMENTS:
            break
    if not fragments:
        return ""
    quoted = " | ".join(f"«{f}»" for f in fragments)
    return f"\nRequirements from the omitted middle of the description: {quoted}"


def onsite_note(workplace_type: str | None) -> str:
    """Строка-пометка для описания, если ATS явно говорит про офис/гибрид.

    Скоринг читает ТЕКСТ, а не поля источника, поэтому формат нужно донести
    до него текстом — иначе гибрид выглядит как обычная вакансия.
    """
    wp = (workplace_type or "").strip().lower()
    if wp in _ONSITE_WORKPLACE:
        return f"\nWork format: {workplace_type} (on-site presence required)"
    return ""
