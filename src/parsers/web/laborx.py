"""LaborX — парсит HTML через p.name-row (title + company в одном блоке)."""
import hashlib
import logging
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from ..base import BaseParser
from ...models import Job

logger = logging.getLogger(__name__)
_URL = "https://laborx.com/vacancies"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class LaborXParser(BaseParser):
    name = "laborx"

    def parse(self) -> list[Job]:
        try:
            resp = requests.get(_URL, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("LaborX request failed: %s", e)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []
        seen: set[str] = set()

        # p.name-row содержит: <a.name-link href="/vacancies/[slug]">Title</a> at <a.name-link>Company</a>
        for row in soup.select("p.name-row"):
            try:
                links = row.select("a.name-link")
                if not links:
                    continue

                job_link = links[0]
                href = job_link.get("href", "")
                if not href or "/vacancies/" not in href or href in seen:
                    continue
                seen.add(href)

                url = f"https://laborx.com{href}" if not href.startswith("http") else href
                title = job_link.get_text(strip=True)
                company = links[1].get_text(strip=True) if len(links) > 1 else ""

                if not title:
                    continue

                uid = hashlib.md5(href.encode()).hexdigest()[:12]
                jobs.append(Job(
                    id=f"lbx_{uid}",
                    title=title,
                    company=company,
                    description=f"{title} at {company}".strip(". "),
                    url=url,
                    source="laborx.com",
                    is_remote=True,
                    published_at=datetime.now(timezone.utc),
                ))
            except Exception as e:
                logger.debug("LaborX parse skip: %s", e)

        return jobs
