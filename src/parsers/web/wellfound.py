"""
WellFound (AngelList) — требует авторизацию для полного доступа.
Этот парсер читает публичную страницу без авторизации (ограниченный список).
"""
import hashlib
import logging
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ..base import BaseParser
from ...models import Job

logger = logging.getLogger(__name__)
_URL = "https://wellfound.com/jobs"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


class WellFoundParser(BaseParser):
    name = "wellfound"

    def parse(self) -> list[Job]:
        try:
            resp = requests.get(_URL, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("WellFound request failed: %s", e)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []

        for link in soup.select("a[href*='/jobs/']"):
            try:
                title = link.get_text(strip=True)
                url = link["href"]
                if not url.startswith("http"):
                    url = f"https://wellfound.com{url}"
                uid = hashlib.md5(url.encode()).hexdigest()[:12]
                if title and len(title) > 5 and "/jobs/" in url:
                    jobs.append(Job(
                        id=f"wf_{uid}",
                        title=title,
                        company="",
                        description=title,
                        url=url,
                        source="wellfound.com",
                        is_remote=True,
                        published_at=datetime.utcnow(),
                    ))
            except Exception as e:
                logger.debug("WellFound parse skip: %s", e)

        seen = set()
        unique = []
        for j in jobs:
            if j.id not in seen:
                seen.add(j.id)
                unique.append(j)
        return unique
