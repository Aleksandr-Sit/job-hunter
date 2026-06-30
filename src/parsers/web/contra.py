"""Contra.com — freelance platform."""
import hashlib
import logging
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from ..base import BaseParser
from ...models import Job

logger = logging.getLogger(__name__)
_URL = "https://contra.com/opportunity/all"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JobHunterBot/1.0)"}


class ContraParser(BaseParser):
    name = "contra"

    def parse(self) -> list[Job]:
        try:
            resp = requests.get(_URL, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("Contra request failed: %s", e)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []

        for link in soup.select("a[href*='/opportunity/']"):
            try:
                title = link.get_text(strip=True)
                url = link["href"]
                if not url.startswith("http"):
                    url = f"https://contra.com{url}"
                uid = hashlib.md5(url.encode()).hexdigest()[:12]
                if title and len(title) > 5:
                    jobs.append(Job(
                        id=f"ctr_{uid}",
                        title=title,
                        company="",
                        description=title,
                        url=url,
                        source="contra.com",
                        is_remote=True,
                        published_at=datetime.now(timezone.utc),
                    ))
            except Exception as e:
                logger.debug("Contra parse skip: %s", e)

        seen = set()
        unique = []
        for j in jobs:
            if j.id not in seen:
                seen.add(j.id)
                unique.append(j)
        return unique
