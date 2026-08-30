"""
LinkedIn Jobs — публичный guest-эндпоинт, без авторизации и без JobSpy.

Почему не JobSpy (замер 06.08.2026): он геокодит строку локации и валидирует её по
своему списку стран — падает с `Invalid country string` на Казахстане, Сербии и
Армении, а "Georgia" понимает как штат США (в выдаче Augusta, GA). Для поиска по
странам релокации он непригоден.

Guest API принимает `geoId` — числовой идентификатор региона, поэтому страна
задаётся однозначно. Все geoId в settings.yaml проверены так: сделан запрос по
geoId и прочитаны локации вакансий, которые он реально вернул.

Ограничения:
- Карточка поиска НЕ содержит описания — его тянем отдельным запросом на вакансию.
  Это главный расход времени, поэтому есть `max_descriptions`.
- При 429 парсер делает паузу с ростом, после `_MAX_429` подряд — сдаётся и
  возвращает то, что успел собрать (пустой список вместо падения пайплайна).
"""
import html
import logging
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

from ...models import Job
from ..base import BaseParser
from ..normalize import clean_description, detect_remote

logger = logging.getLogger(__name__)

_CONFIG = Path(__file__).parent.parent.parent.parent / "config" / "settings.yaml"

_SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
_POSTING = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/"
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}
_MAX_429 = 3          # подряд, после чего считаем себя забаненными на этот цикл
_PAGE = 10            # столько карточек отдаёт одна страница выдачи

# geoId «весь мир». Нужен для слежки за компанией: фильтр `f_C` сам по себе гостевым
# эндпоинтом не принимается — на запрос без keywords/geoId приходит пустой ответ
# (26 байт). Проверено 15.08.2026: `f_C=13336409&geoId=92000000` отдаёт ровно
# вакансии Binance и ничего больше.
_WORLDWIDE_GEO = "92000000"

_RE_URL = re.compile(r'href="(https://[a-z.]*linkedin\.com/jobs/view/[^"?]+)')
_RE_TITLE = re.compile(r'base-search-card__title"[^>]*>\s*([^<]+)')
_RE_COMPANY = re.compile(r'base-search-card__subtitle"[^>]*>\s*(?:<a[^>]*>)?\s*([^<]+)')
_RE_LOCATION = re.compile(r'job-search-card__location"[^>]*>\s*([^<]+)')
_RE_JOB_ID = re.compile(r'-(\d+)$')
_RE_TAG = re.compile(r"<[^>]+>")
_RE_WS = re.compile(r"\s+")


_WATCH_BUCKET = "__watch__"   # служебная метка страны для вакансий watch-компаний


def _round_robin(cards: list[dict]) -> list[dict]:
    """Раздаёт бюджет описаний ПО КРУГУ между странами, а не по порядку сбора.

    Зачем. Описания качаются подряд и обрываются на `max_descriptions`, а карточки
    складывались в порядке обхода стран. Боевой прогон 30.08.2026: собрано 1005
    карточек, потолок 400 — и его целиком съели первые четыре страны (Кипр, ОАЭ,
    Греция, Сербия). Семь стран из одиннадцати не получили НИ ОДНОГО описания,
    то есть до скоринга не дошли вовсе. Проблему создала не сама страна, а порядок:
    после разбиения OR-запроса на четыре карточек стало втрое больше, а потолок
    остался прежним.

    Теперь: watch-компании идут первыми целиком (они редкие, их терять нельзя),
    дальше страны чередуются по одной вакансии. Если потолок снова окажется ниже
    числа карточек, недобор равномерно размажется по всем странам вместо того,
    чтобы обнулить хвост списка.
    """
    watch = [c for c in cards if c.get("geo") == _WATCH_BUCKET]
    buckets: dict[str, list[dict]] = {}
    for c in cards:
        if c.get("geo") != _WATCH_BUCKET:
            buckets.setdefault(c.get("geo", "?"), []).append(c)

    out = list(watch)
    while any(buckets.values()):
        for geo in list(buckets):
            if buckets[geo]:
                out.append(buckets[geo].pop(0))
    return out


def _clean(text: str) -> str:
    # unescape ПОСЛЕ вырезания тегов (см. _description), иначе экранированный
    # `&lt;script&gt;` превратится в настоящий тег уже после чистки.
    return _RE_WS.sub(" ", html.unescape(text or "")).strip()


class LinkedInParser(BaseParser):
    name = "linkedin"

    def __init__(self) -> None:
        cfg = yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))
        p = cfg.get("parsers", {}).get("linkedin", {})
        self.enabled: bool = p.get("enabled", False)
        self.queries: list[str] = p.get("queries", [])
        self.geos: list[dict] = p.get("geos", [])
        self.pages_per_query: int = p.get("pages_per_query", 3)
        self.hours_old: int = p.get("hours_old", 168)
        self.max_descriptions: int = p.get("max_descriptions", 400)
        self.delay: float = p.get("delay_seconds", 0.7)
        self.watch_companies: list[dict] = p.get("watch_companies", [])
        self._strikes = 0

    # ── HTTP ──────────────────────────────────────────────────────────────────
    def _get(self, url: str) -> str:
        """Возвращает тело ответа или "" — сеть не должна ронять пайплайн."""
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers=_HEADERS)
                with urllib.request.urlopen(req, timeout=25) as r:
                    self._strikes = 0
                    return r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    self._strikes += 1
                    if self._strikes >= _MAX_429:
                        logger.warning("LinkedIn: 429 подряд %d раз — прекращаем цикл",
                                       self._strikes)
                        return ""
                    time.sleep(15 * (attempt + 1))
                    continue
                logger.debug("LinkedIn HTTP %s: %s", e.code, url[:90])
                return ""
            except Exception as e:
                logger.debug("LinkedIn ошибка сети: %s", e)
                time.sleep(3)
        return ""

    def _blocked(self) -> bool:
        return self._strikes >= _MAX_429

    # ── Парсинг ───────────────────────────────────────────────────────────────
    def _cards(self, query: str, geo_id: str, start: int) -> list[dict]:
        url = (f"{_SEARCH}?keywords={urllib.parse.quote(query)}&geoId={geo_id}"
               f"&f_TPR=r{self.hours_old * 3600}&start={start}")
        return self._parse_cards(self._get(url))

    @staticmethod
    def _parse_cards(html: str) -> list[dict]:
        """Разбор выдачи. Общий для поиска по странам и слежки за компанией —
        формат карточки один и тот же, дублировать разбор нельзя."""
        out: list[dict] = []
        for block in html.split("<li>")[1:]:
            m_url = _RE_URL.search(block)
            m_title = _RE_TITLE.search(block)
            if not (m_url and m_title):
                continue
            job_url = m_url.group(1)
            m_id = _RE_JOB_ID.search(job_url)
            m_company = _RE_COMPANY.search(block)
            m_loc = _RE_LOCATION.search(block)
            out.append({
                "id": m_id.group(1) if m_id else job_url,
                "url": job_url,
                "title": _clean(m_title.group(1)),
                "company": _clean(m_company.group(1)) if m_company else "",
                "location": _clean(m_loc.group(1)) if m_loc else "",
            })
        return out

    def _company_cards(self, company_id: str, start: int) -> list[dict]:
        """Все вакансии одной компании. Без фильтра по свежести (`f_TPR`) намеренно:
        отслеживаются компании, которые публикуются редко, и пропустить открытие
        из-за окна в неделю дороже, чем разок увидеть старую вакансию — повторы
        всё равно снимет seen-гейт."""
        url = (f"{_SEARCH}?f_C={urllib.parse.quote(str(company_id))}"
               f"&geoId={_WORLDWIDE_GEO}&start={start}")
        return self._parse_cards(self._get(url))

    def _description(self, job_id: str) -> str:
        body = self._get(_POSTING + job_id)
        return _clean(_RE_TAG.sub(" ", body)) if body else ""

    # ── Основной проход ───────────────────────────────────────────────────────
    def parse(self) -> list[Job]:
        if not self.enabled:
            return []
        if not (self.geos and self.queries) and not self.watch_companies:
            logger.warning("LinkedIn: не заданы ни queries/geos, ни watch_companies")
            return []

        found: dict[str, dict] = {}

        # Отслеживаемые компании идут ПЕРВЫМИ. Порядок важен: описания качаются в
        # порядке вставки и обрываются на max_descriptions (400) — в хвосте очереди
        # редкая вакансия из watch-списка просто не доехала бы до скоринга.
        for comp in self.watch_companies:
            cid, cname = str(comp.get("id", "")), comp.get("name", "?")
            if not cid or self._blocked():
                continue
            before = len(found)
            for page in range(self.pages_per_query):
                cards = self._company_cards(cid, page * _PAGE)
                for c in cards:
                    c["geo"] = _WATCH_BUCKET
                    found.setdefault(c["id"], c)
                time.sleep(self.delay)
                if len(cards) < _PAGE:
                    break
            logger.info("LinkedIn watch %s: %d вакансий", cname, len(found) - before)

        for geo in self.geos:
            geo_id, geo_name = str(geo.get("id", "")), geo.get("name", "?")
            if not geo_id:
                continue
            before = len(found)
            for query in self.queries:
                for page in range(self.pages_per_query):
                    if self._blocked():
                        break
                    cards = self._cards(query, geo_id, page * _PAGE)
                    for c in cards:
                        c["geo"] = geo_name
                        found.setdefault(c["id"], c)
                    time.sleep(self.delay)
                    if len(cards) < _PAGE:
                        break        # выдача кончилась — дальше страниц нет
                if self._blocked():
                    break
            logger.info("LinkedIn %s: +%d уникальных", geo_name, len(found) - before)
            if self._blocked():
                break

        # Описания — отдельный запрос на вакансию, самая дорогая часть прохода
        jobs: list[Job] = []
        for i, c in enumerate(_round_robin(list(found.values()))):
            if i >= self.max_descriptions or self._blocked():
                logger.info("LinkedIn: остановились на %d вакансиях из %d",
                            i, len(found))
                break
            desc = self._description(c["id"])
            time.sleep(self.delay)
            if not desc:
                continue
            jobs.append(Job(
                id=f"li_{c['id']}",
                title=c["title"],
                company=c["company"],
                description=clean_description(desc),
                url=c["url"],
                source="linkedin.com",
                location=c["location"],
                is_remote=detect_remote(location=c["location"], description=desc),
            ))

        logger.info("LinkedIn: %d вакансий итого", len(jobs))
        return jobs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = LinkedInParser()
    result = parser.parse()
    print(f"\nНайдено: {len(result)} вакансий")
    for j in result[:10]:
        print(f"  {j.title} @ {j.company} | {j.location} | remote={j.is_remote}")
