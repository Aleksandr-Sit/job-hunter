"""web3.career — scraping."""
import hashlib
import logging
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from ..base import BaseParser
from ...models import Job

logger = logging.getLogger(__name__)
_URL = "https://web3.career"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JobHunterBot/1.0)"}


class Web3CareerParser(BaseParser):
    name = "web3career"

    def parse(self) -> list[Job]:
        try:
            resp = requests.get(_URL, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("web3.career request failed: %s", e)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []
        for card in soup.select("tr.job_seen_beacon, table.jobs tr[data-jobid], .job-card, article.job"):
            try:
                jobs.append(self._parse_card(card))
            except Exception as e:
                logger.debug("web3.career parse skip: %s", e)

        # Fallback: ищем любые ссылки с /job/ в пути
        if not jobs:
            for link in soup.select("a[href*='/job/']"):
                try:
                    title = link.get_text(strip=True)
                    url = link["href"]
                    if not url.startswith("http"):
                        url = f"{_URL}{url}"
                    uid = hashlib.md5(url.encode()).hexdigest()[:12]
                    if title:
                        jobs.append(Job(
                            id=f"w3c_{uid}",
                            title=title,
                            company="",
                            description=title,
                            url=url,
                            source="web3.career",
                            is_remote=True,
                            published_at=datetime.now(timezone.utc),
                        ))
                except Exception:
                    pass
        return jobs

    def _parse_card(self, card) -> Job:
        title_el = card.select_one("h2, h3, .job-title, td.title")
        company_el = card.select_one(".company, .company-name, td.company")
        link_el = card.select_one("a[href]")
        tags_els = card.select(".tag, .badge, .skill")

        title = title_el.get_text(strip=True) if title_el else ""
        company = company_el.get_text(strip=True) if company_el else ""
        url = link_el["href"] if link_el else ""
        if url and not url.startswith("http"):
            url = f"{_URL}{url}"
        tags = [t.get_text(strip=True) for t in tags_els]

        uid = hashlib.md5(url.encode()).hexdigest()[:12]
        return Job(
            id=f"w3c_{uid}",
            title=title,
            company=company,
            description=f"{title} at {company}. Tags: {', '.join(tags)}",
            url=url,
            source="web3.career",
            is_remote=True,
            published_at=datetime.now(timezone.utc),
            tags=tags,
        )
