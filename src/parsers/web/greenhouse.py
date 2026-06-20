"""
Greenhouse ATS — публичный JSON API без авторизации.
API: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from ..base import BaseParser
from ...models import Job

logger = logging.getLogger(__name__)

_API_BASE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json",
}
_CONFIG = Path(__file__).parent.parent.parent.parent / "config" / "settings.yaml"


class GreenhouseParser(BaseParser):
    name = "greenhouse"

    def __init__(self) -> None:
        cfg = yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))
        parser_cfg = cfg.get("parsers", {}).get("greenhouse", {})
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
                logger.info("Greenhouse %s: %d jobs", name, len(fetched))
                jobs.extend(fetched)
            except Exception as e:
                logger.warning("Greenhouse %s failed: %s", name, e)
        return jobs

    def _fetch_company(self, slug: str, company_name: str) -> list[Job]:
        url = _API_BASE.format(slug=slug)
        resp = requests.get(url, headers=_HEADERS, params={"content": "true"}, timeout=20)
        if resp.status_code == 404:
            logger.warning("Greenhouse: %s not found (404)", slug)
            return []
        resp.raise_for_status()
        data = resp.json()

        jobs = []
        for item in data.get("jobs", []):
            try:
                jobs.append(self._parse_item(item, company_name))
            except Exception as e:
                logger.debug("Greenhouse item error (%s): %s", slug, e)
        return jobs

    def _parse_item(self, item: dict, company_name: str) -> Job:
        location = ""
        locs = item.get("offices") or item.get("location") or []
        if isinstance(locs, list) and locs:
            location = locs[0].get("name", "")
        elif isinstance(locs, dict):
            location = locs.get("name", "")

        updated_at = item.get("updated_at") or item.get("created_at") or ""
        try:
            published_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except Exception:
            published_at = None

        # Content field contains job description HTML
        content = item.get("content", "") or ""
        if len(content) > 2000:
            content = content[:2000]

        is_remote = any(
            w in (location + content).lower()
            for w in ["remote", "anywhere", "удалённо", "удаленно"]
        )

        return Job(
            id=f"gh_{item['id']}",
            title=item.get("title", ""),
            company=company_name,
            description=content,
            url=item.get("absolute_url", ""),
            source="greenhouse",
            is_remote=is_remote,
            location=location,
            published_at=published_at,
        )


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")
    logging.basicConfig(level=logging.INFO)
    parser = GreenhouseParser()
    jobs = parser.parse()
    print(f"Found {len(jobs)} jobs from Greenhouse")
    for j in jobs[:10]:
        print(f"  {j.title} @ {j.company} | {j.location} | {j.url}")
