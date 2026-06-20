"""CryptoJobsList — парсит Next.js SSR JSON из <script> тега на главной странице."""
import json
import logging
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from ..base import BaseParser
from ...models import Job

logger = logging.getLogger(__name__)
_URL = "https://cryptojobslist.com"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class CryptoJobListParser(BaseParser):
    name = "cryptojoblist"

    def parse(self) -> list[Job]:
        try:
            resp = requests.get(_URL, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("CryptoJobsList request failed: %s", e)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        raw_jobs = self._extract_jobs(soup)
        logger.debug("CryptoJobsList: %d raw jobs in page JSON", len(raw_jobs))

        jobs = []
        for item in raw_jobs:
            try:
                jobs.append(self._parse_item(item))
            except Exception as e:
                logger.warning("CryptoJobsList parse error: %s", e)
        return jobs

    def _extract_jobs(self, soup) -> list[dict]:
        for script in soup.find_all("script"):
            text = script.get_text()
            if "pageProps" in text and "jobTitle" in text:
                try:
                    data = json.loads(text)
                    return data["props"]["pageProps"]["jobs"]
                except (json.JSONDecodeError, KeyError):
                    pass
        return []

    def _parse_item(self, item: dict) -> Job:
        salary = item.get("salary") or {}
        sal_min = salary.get("minValue")
        sal_max = salary.get("maxValue")
        currency = salary.get("currency", "USD")

        # Годовая зарплата → месячная
        if salary.get("unitText") == "YEAR":
            if sal_min:
                sal_min = sal_min // 12
            if sal_max:
                sal_max = sal_max // 12

        slug = item.get("seoSlug", "")
        url = f"{_URL}/{slug}" if slug else _URL

        # Описание — из JSONLD если есть, иначе собираем из полей
        desc = ""
        jsonld_raw = item.get("jobPostingJSONLD", "")
        if jsonld_raw and isinstance(jsonld_raw, str):
            try:
                desc = json.loads(jsonld_raw).get("description", "")
            except json.JSONDecodeError:
                pass
        if not desc:
            loc = item.get("jobLocation", "")
            tags_str = ", ".join(item.get("tags", []))
            desc = (
                f"{item.get('jobTitle', '')} at {item.get('companyName', '')}. "
                f"Location: {loc}. Tags: {tags_str}"
            )

        published_raw = item.get("publishedAt", "")
        try:
            published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            published_at = datetime.now(timezone.utc)

        return Job(
            id=f"cjl_{item.get('id', slug[:20])}",
            title=item.get("jobTitle", ""),
            company=item.get("companyName", ""),
            description=desc[:3000],
            url=url,
            source="cryptojobslist.com",
            salary_min=sal_min,
            salary_max=sal_max,
            salary_currency=currency,
            is_remote=bool(item.get("remote")),
            published_at=published_at,
            tags=item.get("tags", []),
        )
