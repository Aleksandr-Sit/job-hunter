"""Habr Career — парсит серверный HTML (.vacancy-card) по набору РФ-крипто-запросов.

Даёт РФ-крипто/финтех-вакансии, которых нет на ATS-бордах (Greenhouse/Lever/Ashby)
и часто нет на HH: МТС Финтех, Bitbanker и т.п. (RU_CRYPTO_MARKET_MAP). Проба
22.07: серверный HTML, .vacancy-card__title a = тайтл+ссылка, a[href*=/companies/]
= компания, .vacancy-card__skills = навыки. Описание собираем из тайтла+навыков+
локации — полного текста в карточке нет (как у laborx/linkedin), но роль/домен
несёт тайтл, а RU-термины criteria (цфа/блокчейн/крипто) их ловят.
"""
import hashlib
import logging
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from ..base import BaseParser
from ...models import Job

logger = logging.getLogger(__name__)

_BASE = "https://career.habr.com"
_URL = f"{_BASE}/vacancies"
# РФ-крипто/финтех-запросы. Habr q= ищет по тайтлу/описанию; pre_filter отфильтрует
# роль/домен (dev-роли отсекутся код-гейтом). Дедуп по vacancy id.
_QUERIES = ["blockchain", "криптовалюта", "web3", "цфа", "цифровой рубль", "crypto"]
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru,en-US;q=0.9,en;q=0.8",
}


class HabrCareerParser(BaseParser):
    name = "habr"

    def parse(self) -> list[Job]:
        jobs: list[Job] = []
        seen: set[str] = set()

        for q in _QUERIES:
            try:
                resp = requests.get(_URL, params={"q": q, "type": "all"},
                                    headers=_HEADERS, timeout=20)
                resp.raise_for_status()
            except requests.RequestException as e:
                logger.warning("Habr Career q=%s request failed: %s", q, e)
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            for card in soup.select(".vacancy-card"):
                try:
                    ta = card.select_one(".vacancy-card__title a")
                    if not ta:
                        continue
                    href = ta.get("href", "")
                    vid = href.rstrip("/").split("/")[-1]
                    if not vid or vid in seen:
                        continue
                    seen.add(vid)

                    title = ta.get_text(" ", strip=True)
                    if not title:
                        continue
                    url = href if href.startswith("http") else f"{_BASE}{href}"

                    comp = card.select_one('a[href*="/companies/"]')
                    company = comp.get_text(" ", strip=True) if comp else ""

                    skills_el = card.select_one(".vacancy-card__skills")
                    skills = skills_el.get_text(" ", strip=True) if skills_el else ""
                    meta_el = card.select_one(".vacancy-card__meta")
                    location = meta_el.get_text(" ", strip=True) if meta_el else ""

                    desc = title
                    if skills:
                        desc += f". Навыки: {skills}"
                    if location:
                        desc += f". {location}"

                    is_remote = "удал" in (location + " " + skills).lower()

                    jobs.append(Job(
                        id=f"habr_{vid}",
                        title=title,
                        company=company,
                        description=desc,
                        url=url,
                        source="career.habr.com",
                        location=location or None,
                        is_remote=is_remote,
                        published_at=datetime.now(timezone.utc),
                    ))
                except Exception as e:  # noqa: BLE001
                    logger.debug("Habr Career card skip: %s", e)

            time.sleep(0.5)  # вежливо к серверу между запросами

        logger.info("Habr Career: %d вакансий итого", len(jobs))
        return jobs
