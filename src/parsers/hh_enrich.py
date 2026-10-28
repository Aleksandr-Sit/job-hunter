"""Дообогащение вакансий с HH.ru полным описанием.

Зачем. RSS отдаёт только выжимку: замер 19.08.2026 на 849 вакансиях — медиана
описания **125 символов**, максимум 252. У вакансии Контура это были четыре
строки метаданных (компания, дата, регион, доход) и ни слова о содержании
работы. Из-за этого весь HH скорился вслепую по заголовку: вакансии получали
почти одинаковый балл ~50 и не различались между собой, а контентные фильтры
(ночные смены, офис, английский) на них не работали в принципе.

Замер выигрыша на 56 вакансиях, прошедших предфильтр:
  описание 121 -> 2809 символов (медиана);
  7 из 56 отсеялись, включая две с ночными сменами как режимом;
  оставшиеся разъехались по баллам: AML-специалист 50 -> 90,
  AI-инженер 50 -> 90, тогда как «Комплаенс-специалист» упал 50 -> 32.

Цена: 0.62 c и 782 КБ на страницу, то есть ~60 c и ~48 МБ на прогон.

Почему не через API. `api.hh.ru` отдаёт с VPS **403 при любом User-Agent**
(проверены три варианта, включая браузерный) — датацентровые адреса режутся.
Обычная страница вакансии при этом открывается, HTTP 200.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Iterable

import requests
from bs4 import BeautifulSoup

from ..models import MAX_DESCRIPTION_CHARS, Job

logger = logging.getLogger(__name__)

_VACANCY_ID = re.compile(r"/vacancy/(\d+)")

# Разметка HH. Порядок = порядок надёжности; если первый селектор исчезнет,
# остальные дадут шанс не потерять описание молча.
_SELECTORS = (
    "[data-qa=vacancy-description]",
    "[data-qa=vacancy-section] .vacancy-description",
    ".vacancy-description",
)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_TIMEOUT = 20
# Пауза между запросами. 0.25 с держались, пока страниц было ~7 за прогон, но на
# объёме упираются в антибот hh.ru: замер 05.09.2026 — при 0.25 с ровно с 50-й
# страницы hh.ru отдаёт HTTP 200 БЕЗ текста вакансии (мягкая заглушка, не ошибка),
# извлечение падает до 22%, и `healthy` поднимает ложную тревогу про вёрстку.
# При 0.8 с — 100 страниц из 100 без единого сбоя, при 1.5 с — 70 из 70.
# Берём 0.8 с: это потолок ТЕМПА, а не количества, поэтому замедление снимает его
# полностью. Заодно чинит латентный баг — боевой `limit: 180` при 0.25 с тоже
# развалился бы на 50-й странице, просто до него ни разу не доходило.
_PAUSE = 0.8
_MIN_USEFUL = 300      # ниже этого считаем, что описание не извлеклось

# Формат работы — структурное поле страницы (REMOTE / HYBRID / ON_SITE / FIELD_WORK),
# в тексте описания его нет. RSS отдаёт только город, а слово «удалённо» в тексте
# встречается и у офисных вакансий («удалённая консультация клиентов»), поэтому до
# 25.09.2026 отделения ВТБ получали +10 за remote, а офис РТ-ИБ у м. «Южная» — ни
# одного штрафа. Поле проверено на 5 страницах против их «Формат работы: …».
_WORK_FORMATS = re.compile(rb'"workFormats":\s*\[((?:\s*"[A-Z_]+"\s*,?)*)\]')
WORK_FORMAT_LABEL = "Формат работы (hh):"

# Порог «тихого отказа»: если доля успешных извлечений упала ниже — вёрстка HH
# сменилась. Без этой проверки поломка выглядит как обычный день: ошибок в логе
# нет, описания просто снова короткие, и бот молча возвращается к слепому
# скорингу. Ровно такой тихий отказ 18.08.2026 стоил суток.
_HEALTH_MIN_RATE = 0.5


class EnrichStats:
    """Итог прогона дообогащения — для лога и алерта."""

    def __init__(self) -> None:
        self.attempted = 0
        self.enriched = 0
        self.failed = 0
        self.downloaded = 0   # реально скачано байт (страницы ~780 КБ)
        self.chars = 0        # извлечено полезного текста

    @property
    def success_rate(self) -> float:
        return self.enriched / self.attempted if self.attempted else 1.0

    @property
    def healthy(self) -> bool:
        """False — похоже, сломалось извлечение, а не просто попались плохие страницы."""
        return self.attempted < 5 or self.success_rate >= _HEALTH_MIN_RATE

    def __str__(self) -> str:
        return (f"попыток {self.attempted}, дообогащено {self.enriched}, "
                f"неудач {self.failed}, скачано {self.downloaded / 1e6:.0f} МБ, "
                f"извлечено {self.chars / 1000:.0f} тыс. символов")


def _extract(html: bytes) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for sel in _SELECTORS:
        node = soup.select_one(sel)
        if node:
            text = node.get_text(" ", strip=True)
            if len(text) >= _MIN_USEFUL:
                return text
    return ""


def _format_note(html: bytes) -> str:
    """Строка «Формат работы (hh): REMOTE, HYBRID» или пусто, если поля нет.

    Ставится В НАЧАЛО описания: хвост режется по MAX_DESCRIPTION_CHARS.

    ⚠️ В сырой странице JSON лежит в атрибуте, кавычки экранированы как `&#34;`.
    Без раскодирования поле не находилось ни на одной из 1126 страниц (замер
    25.09.2026). Ищем только ПЛОСКИЙ список строк: вложенный `[{"workFormatsElement"`
    и справочник `[{"id":"ON_SITE","text":…}]` со всеми вариантами им не являются."""
    html = html.replace(b"&#34;", b'"').replace(b"&quot;", b'"')
    m = _WORK_FORMATS.search(html)
    if not m:
        return ""
    formats = re.findall(rb"[A-Z_]+", m.group(1))
    if not formats:
        return ""
    return f"{WORK_FORMAT_LABEL} {', '.join(f.decode() for f in formats)}\n"


# График, часы и ночные смены — структурные поля той же страницы (26.10.2026).
# В тексте описания их часто нет: из 41 сменной вакансии, ушедшей в Telegram за
# 22.09–26.10, бот по тексту видел 17, остальные 24 — только здесь. OsteoFace
# (hh 137767979) — «График: 6/1» в поле, а в тексте ни слова; владелец откликнулся,
# не зная о шестидневке.
#
# Строки пишутся КОДАМИ hh (TWO_ON_TWO_OFF, HOURS_12), а не «2/2» и «ночные смены»:
# текстовые regex предфильтра приняли бы подпись за фразу работодателя
# («график (hh): 2/2» — это `_SHIFT_SCHEDULE`, «ночные смены (hh)» — `_NIGHT_CORE`,
# причём без поправки на FLEXIBLE). Человеческие подписи — в `schedule_summary`.
# Предфильтр ищет эти подписи в нижнем регистре (`pre_filter._HH_SCHEDULE` и др.).
SCHEDULE_LABEL = "График (hh):"
HOURS_LABEL = "Часы в день (hh):"
NIGHT_LABEL = "Работа ночью (hh):"


def _field(page: bytes, key: bytes) -> list[str]:
    """Коды поля hh: плоский список, иначе вложенный `[{"<key>Element":[…]}]`.

    Справочник `[{"id":"SIX_ON_ONE_OFF","text":"6/1"},…]` со ВСЕМИ вариантами под
    тем же ключом сюда не попадает: после `[` в нём стоит `{`, а не строка.
    Логика проверена выгрузкой 837 страниц (data/scans/schedule_audit_tools)."""
    m = re.search(rb'"' + key + rb'":\s*\[((?:\s*"[A-Z0-9_]+"\s*,?)*)\]', page)
    if m and m.group(1).strip():
        return [x.decode() for x in re.findall(rb"[A-Z0-9_]+", m.group(1))]
    m = re.search(rb'"' + key + rb'":\s*\[\{"' + key + rb'Element":\s*\[((?:\s*"[A-Z0-9_]+"\s*,?)*)\]',
                  page)
    if m:
        return [x.decode() for x in re.findall(rb"[A-Z0-9_]+", m.group(1))]
    return []


def _schedule_note(html: bytes) -> str:
    """Строки графика hh для начала описания или пусто, если полей нет.

    «Работа ночью» пишется только при `nightShifts: true`: «нет» в описании ничего
    не добавляет, а места в начале описания мало (в промпт AI идут первые 700 символов)."""
    html = html.replace(b"&#34;", b'"').replace(b"&quot;", b'"')
    lines = []
    days = _field(html, b"workScheduleByDays")
    if days:
        lines.append(f"{SCHEDULE_LABEL} {', '.join(days)}")
    hours = _field(html, b"workingHours")
    if hours:
        lines.append(f"{HOURS_LABEL} {', '.join(hours)}")
    night = re.search(rb'"nightShifts":\s*(true|false)', html)
    if night and night.group(1) == b"true":
        lines.append(f"{NIGHT_LABEL} да")
    return "".join(f"{line}\n" for line in lines)


# Подписи для карточки Telegram: владелец читает «6/1», а не SIX_ON_ONE_OFF.
_DAYS_RU = {
    "SIX_ON_ONE_OFF": "6/1", "FIVE_ON_TWO_OFF": "5/2", "FOUR_ON_FOUR_OFF": "4/4",
    "FOUR_ON_THREE_OFF": "4/3", "FOUR_ON_TWO_OFF": "4/2", "THREE_ON_THREE_OFF": "3/3",
    "THREE_ON_TWO_OFF": "3/2", "TWO_ON_TWO_OFF": "2/2", "TWO_ON_ONE_OFF": "2/1",
    "ONE_ON_THREE_OFF": "1/3", "ONE_ON_TWO_OFF": "1/2", "WEEKEND": "по выходным",
    "FLEXIBLE": "свободный", "OTHER": "другой",
}


def _hours_ru(code: str) -> str:
    if code.startswith("HOURS_"):
        return f"{code[6:]} ч"
    return {"FLEXIBLE": "по договорённости", "OTHER": "другое"}.get(code, code)


def schedule_summary(description: str | None) -> str:
    """«6/1 · 8 ч или по договорённости · есть ночные смены» по строкам hh в
    описании, или пусто (вакансия не с hh или поля не заполнены)."""
    text = description or ""
    parts = []

    def codes(label: str) -> list[str]:
        m = re.search(re.escape(label) + r"\s*([A-Z0-9_, ]+)", text)
        return re.findall(r"[A-Z0-9_]+", m.group(1)) if m else []

    days = codes(SCHEDULE_LABEL)
    if days:
        parts.append(" или ".join(_DAYS_RU.get(d, d) for d in days))
    hours = codes(HOURS_LABEL)
    if hours:
        parts.append(" или ".join(_hours_ru(h) for h in hours))
    if f"{NIGHT_LABEL} да" in text:
        parts.append("есть ночные смены")
    return " · ".join(parts)


# Зарплата со страницы — 18.10.2026. RSS-версия вакансии лежит в пуле до 14 дней
# (`storage.pool_add` — INSERT OR IGNORE, первая увиденная), и если работодатель
# снизил сумму, бот слал старую: Ozon Банк «до 90 000» в карточке при «до 75 000»
# на странице. Страница — актуальная, и только она знает gross/net. Поле одно на
# вакансию (на странице встречается дважды, оба раза её же).
_COMPENSATION = re.compile(rb'"compensation":\{([^{}]*)\}')
_CURRENCY = {"RUR": "RUB", "RUB": "RUB", "USD": "USD", "EUR": "EUR", "KZT": "KZT"}
SALARY_LABEL = "Зарплата (hh):"
# Строка дохода из RSS-выжимки: её заменяем, иначе AI видит обе суммы
_RSS_SALARY_LINE = re.compile(r"^[^\n]*(?:уровень[^\n]*дохода|зарплата:)[^\n]*$",
                              re.IGNORECASE | re.MULTILINE)


def _compensation(html: bytes) -> dict | None:
    """{"from", "to", "currency", "gross"} или None, если зарплата не указана."""
    html = html.replace(b"&#34;", b'"').replace(b"&quot;", b'"')
    m = _COMPENSATION.search(html)
    if not m:
        return None
    body = m.group(1).decode("utf-8", "replace")

    def num(key):
        v = re.search(rf'"{key}":(\d+)', body)
        return int(v.group(1)) if v else None

    lo, hi = num("from"), num("to")
    if not (lo or hi):
        return None
    cur = re.search(r'"currencyCode":"([A-Z]+)"', body)
    gross = re.search(r'"gross":(true|false)', body)
    # MONTH / SHIFT / HOUR / FLY_IN_FLY_OUT: пол зарплаты (pre_filter) — только помесячно
    mode = re.search(r'"mode":"([A-Z_]+)"', body)
    return {"from": lo, "to": hi,
            "currency": _CURRENCY.get(cur.group(1), cur.group(1)) if cur else "RUB",
            "gross": None if not gross else gross.group(1) == "true",
            "mode": mode.group(1) if mode else None}


def _apply_compensation(job: Job, comp: dict) -> None:
    """Поля зарплаты и строка дохода в описании — по странице, а не по RSS."""
    from ..models import salary_range
    job.salary_min, job.salary_max = comp["from"], comp["to"]
    job.salary_currency = comp["currency"]
    if comp["gross"] is not None:
        job.raw["salary_gross"] = comp["gross"]
    if comp.get("mode"):
        job.raw["salary_mode"] = comp["mode"]
    tax = {True: ", до вычета налогов", False: ", на руки"}.get(comp["gross"], "")
    line = f"{SALARY_LABEL} {salary_range(comp['from'], comp['to'], sep=' ')} {comp['currency']}{tax}"
    desc, n = _RSS_SALARY_LINE.subn(line, job.description or "", count=1)
    job.description = desc if n else f"{line}\n{desc}"


def _fetch(url: str, session: requests.Session | None = None
           ) -> tuple[str, int, dict | None]:
    """(описание, сколько байт скачано, зарплата со страницы). Пустая строка —
    не получилось.

    Никогда не бросает: отказ HH не должен ронять прогон — бот продолжит
    работать на короткой выжимке, как работал до этого.
    """
    m = _VACANCY_ID.search(url or "")
    if not m:
        return "", 0, None
    sess = session or requests.Session()
    try:
        r = sess.get(f"https://hh.ru/vacancy/{m.group(1)}",
                     headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        size = len(r.content)
        if r.status_code != 200:
            logger.debug("HH enrich: %s -> HTTP %s", m.group(1), r.status_code)
            return "", size, None
        text = _extract(r.content)
        if not text:
            return "", size, None
        # График — первым: за строкой «График (hh)» идёт ещё строка hh, а не текст
        # работодателя («…: OTHER\n2/2 …» дало бы `_SHIFT_SCHEDULE` ложную фразу).
        return (_schedule_note(r.content) + _format_note(r.content) + text,
                size, _compensation(r.content))
    except Exception as e:
        logger.debug("HH enrich: %s -> %s", m.group(1), str(e)[:100])
        return "", 0, None


def fetch_description(url: str, session: requests.Session | None = None) -> str:
    """Полное описание вакансии по её URL. Пустая строка — не получилось."""
    return _fetch(url, session)[0]


def enrich(jobs: Iterable[Job], limit: int = 120) -> EnrichStats:
    """Дописывает полное описание вакансиям с hh.ru. Меняет Job на месте.

    `limit` — предохранитель: если предфильтр вдруг пропустит сотни вакансий,
    прогон не должен уйти в получасовое скачивание.
    """
    stats = EnrichStats()
    targets = [j for j in jobs if (j.source or "") == "hh.ru"][:limit]
    if not targets:
        return stats

    session = requests.Session()
    for i, job in enumerate(targets):
        if i:
            time.sleep(_PAUSE)
        stats.attempted += 1
        full, size, *page = _fetch(job.url, session)
        stats.downloaded += size
        if not full:
            stats.failed += 1
            continue
        stats.chars += len(full)
        if page and page[0]:
            _apply_compensation(job, page[0])
        # Выжимку сохраняем: в ней регион (и зарплата, если со страницы не взялась).
        job.description = f"{job.description}\n{full}"[:MAX_DESCRIPTION_CHARS]
        # Полный текст есть — правила направления (pre_filter.direction_gate) судят по нему
        job.raw["hh_full"] = True
        stats.enriched += 1

    logger.info("HH дообогащение: %s", stats)
    if not stats.healthy:
        logger.error(
            "HH дообогащение: извлечено только %.0f%% (%d из %d). Две причины, и "
            "различать их по логу нельзя — hh.ru в обоих случаях отвечает 200: "
            "(1) антибот на объёме (тогда помогает больший _PAUSE), "
            "(2) сменилась вёрстка (тогда — селекторы в hh_enrich._SELECTORS).",
            stats.success_rate * 100, stats.enriched, stats.attempted)
    return stats
