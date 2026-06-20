"""
Lever ATS — публичный JSON API без авторизации.
API: https://api.lever.co/v0/postings/{company}?mode=json
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from ..base import BaseParser
from ...models import Job

logger = logging.getLogger(__name__)

_API_BASE = "https://api.lever.co/v0/postings/{slug}"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json",
}
_CONFIG = Path(__file__).parent.parent.parent.parent / "config" / "settings.yaml"


class LeverParser(BaseParser):
    name = "lever"

    def __init__(self) -> None:
        cfg = yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))
        parser_cfg = cfg.get("parsers", {}).get("lever", {})
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
                logger.info("Lever %s: %d jobs", name, len(fetched))
                jobs.extend(fetched)
            except Exception as e:
                logger.warning("Lever %s failed: %s", name, e)
        return jobs

    def _fetch_company(self, slug: str, company_name: str) -> list[Job]:
        url = _API_BASE.format(slug=slug)
        resp = requests.get(url, headers=_HEADERS, params={"mode": "json"}, timeout=20)
        if resp.status_code == 404:
            logger.warning("Lever: %s not found (404)", slug)
            return []
        resp.raise_for_status()
        data = resp.json()

        jobs = []
        for item in data:
            try:
                jobs.append(self._parse_item(item, company_name))
            except Exception as e:
                logger.debug("Lever item error (%s): %s", slug, e)
        return jobs

    def _parse_item(self, item: dict, company_name: str) -> Job:
        location = item.get("categories", {}).get("location", "") or ""
        commitment = item.get("categories", {}).get("commitment", "") or ""
        team = item.get("categories", {}).get("team", "") or ""

        # createdAt — Unix timestamp в миллисекундах
        created_ms = item.get("createdAt", 0) or 0
        try:
            published_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
        except Exception:
            published_at = None

        # Описание: text.description + lists
        text_block = item.get("text", "") or ""
        lists = item.get("lists", []) or []
        description_parts = [text_block]
        for lst in lists:
            description_parts.append(lst.get("text", "") + "\n" + lst.get("content", ""))
        description = "\n\n".join(p for p in description_parts if p).strip()
        if len(description) > 2000:
            description = description[:2000]

        is_remote = any(
            w in (location + commitment + description).lower()
            for w in ["remote", "anywhere", "удалённо", "удаленно"]
        )

        return Job(
            id=f"lv_{item['id']}",
            title=item.get("text", "") or "",
            company=company_name,
            description=description,
            url=item.get("hostedUrl", "") or item.get("applyUrl", ""),
            source="lever",
            is_remote=is_remote,
            location=location,
            tags=[team, commitment] if (team or commitment) else [],
            published_at=published_at,
        )


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")
    logging.basicConfig(level=logging.INFO)
    parser = LeverParser()
    jobs = parser.parse()
    print(f"Found {len(jobs)} jobs from Lever")
    for j in jobs[:10]:
        print(f"  {j.title} @ {j.company} | {j.location} | {j.url}")
