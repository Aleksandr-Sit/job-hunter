"""
Ashby ATS — публичный JSON API без авторизации.
API: https://api.ashbyhq.com/posting-api/job-board/{slug}
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import yaml

from ..base import BaseParser
from ...models import Job

logger = logging.getLogger(__name__)

_API_BASE = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json",
}
_CONFIG = Path(__file__).parent.parent.parent.parent / "config" / "settings.yaml"


class AshbyParser(BaseParser):
    name = "ashby"

    def __init__(self) -> None:
        cfg = yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))
        parser_cfg = cfg.get("parsers", {}).get("ashby", {})
        self.enabled = parser_cfg.get("enabled", True)
        self.companies: list[dict] = parser_cfg.get("companies", [])

    def parse(self) -> list[Job]:
        if not self.enabled:
            return []
        jobs: list[Job] = []
        for company in self.companies:
            slug = company.get("slug", "")
            name = company.get("name", slug)
            try:
                fetched = self._fetch_company(slug, name)
                logger.info("Ashby %s: %d jobs", name, len(fetched))
                jobs.extend(fetched)
            except Exception as e:
                logger.warning("Ashby %s failed: %s", name, e)
        return jobs

    def _fetch_company(self, slug: str, company_name: str) -> list[Job]:
        url = _API_BASE.format(slug=slug)
        session = requests.Session()
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
        session.mount("https://", HTTPAdapter(max_retries=retry))
        resp = session.get(url, headers=_HEADERS, timeout=20)
        if resp.status_code == 404:
            logger.warning("Ashby: %s not found (404)", slug)
            return []
        resp.raise_for_status()
        data = resp.json()

        jobs = []
        for item in data.get("jobs", []):
            if not item.get("isListed", True):
                continue
            try:
                jobs.append(self._parse_item(item, company_name))
            except Exception as e:
                logger.debug("Ashby item error (%s): %s", slug, e)
        return jobs

    def _parse_item(self, item: dict, company_name: str) -> Job:
        location = item.get("location") or ""
        workplace = item.get("workplaceType", "") or ""

        try:
            published_at = datetime.fromisoformat(item["publishedAt"].replace("Z", "+00:00"))
        except Exception:
            published_at = None

        description = item.get("descriptionPlain") or ""
        if len(description) > 2000:
            description = description[:2000]

        is_remote = (
            item.get("isRemote", False)
            or workplace.lower() == "remote"
            or any(w in (location + description).lower() for w in ["remote", "anywhere", "удалённо", "удаленно"])
        )

        return Job(
            id=f"ab_{item['id']}",
            title=item.get("title", ""),
            company=company_name,
            description=description,
            url=item.get("jobUrl", "") or item.get("applyUrl", ""),
            source="ashby",
            is_remote=is_remote,
            location=location,
            published_at=published_at,
        )


if __name__ == "__main__":
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")
    logging.basicConfig(level=logging.INFO)
    parser = AshbyParser()
    jobs = parser.parse()
    print(f"Found {len(jobs)} jobs from Ashby")
    for j in jobs[:10]:
        print(f"  {j.title} @ {j.company} | {j.location} | {j.url}")
