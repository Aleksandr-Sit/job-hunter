"""remote3.co — Web3 remote jobs."""
import hashlib
import logging
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from ..base import BaseParser
from ...models import Job

logger = logging.getLogger(__name__)
_URL = "https://remote3.co/web3-jobs"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class Remote3Parser(BaseParser):
    name = "remote3"

    def parse(self) -> list[Job]:
        try:
            resp = requests.get(_URL, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("remote3.co request failed: %s", e)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []
        seen: set[str] = set()

        # Карточка: <a href="/remote-jobs/[slug]">
        #   <p class="body-small text-tertiary-white">Company</p>
        #   <h2 class="JobListingItem_jobTitle__...">Title</h2>
        for a in soup.select("a[href*='/remote-jobs/']"):
            try:
                href = a.get("href", "")
                if not href or href in seen:
                    continue
                seen.add(href)

                url = f"https://remote3.co{href}" if not href.startswith("http") else href

                title_el = a.select_one("h2")
                company_el = a.select_one("p.body-small")

                title = title_el.get_text(strip=True) if title_el else ""
                company = company_el.get_text(strip=True) if company_el else ""

                if not title or len(title) < 3:
                    continue

                uid = hashlib.md5(href.encode()).hexdigest()[:12]
                jobs.append(Job(
                    id=f"r3_{uid}",
                    title=title,
                    company=company,
                    description=f"{title} at {company}".strip(". "),
                    url=url,
                    source="remote3.co",
                    is_remote=True,
                    published_at=datetime.now(timezone.utc),
                ))
            except Exception as e:
                logger.debug("remote3 parse skip: %s", e)

        return jobs
