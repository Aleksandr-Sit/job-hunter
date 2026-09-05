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


def _fetch(url: str, session: requests.Session | None = None) -> tuple[str, int]:
    """(описание, сколько байт скачано). Пустая строка — не получилось.

    Никогда не бросает: отказ HH не должен ронять прогон — бот продолжит
    работать на короткой выжимке, как работал до этого.
    """
    m = _VACANCY_ID.search(url or "")
    if not m:
        return "", 0
    sess = session or requests.Session()
    try:
        r = sess.get(f"https://hh.ru/vacancy/{m.group(1)}",
                     headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        size = len(r.content)
        if r.status_code != 200:
            logger.debug("HH enrich: %s -> HTTP %s", m.group(1), r.status_code)
            return "", size
        return _extract(r.content), size
    except Exception as e:
        logger.debug("HH enrich: %s -> %s", m.group(1), str(e)[:100])
        return "", 0


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
        full, size = _fetch(job.url, session)
        stats.downloaded += size
        if not full:
            stats.failed += 1
            continue
        stats.chars += len(full)
        # Выжимку сохраняем: в ней регион и зарплата, которых нет в теле описания.
        job.description = f"{job.description}\n{full}"[:MAX_DESCRIPTION_CHARS]
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
