"""
Greenhouse ATS — публичный JSON API без авторизации.
API: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
"""
import logging
from datetime import datetime
from pathlib import Path

import requests
import yaml
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...models import Job
from ..base import BaseParser
from ..normalize import clean_description, detect_remote

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
        session = requests.Session()
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
        session.mount("https://", HTTPAdapter(max_retries=retry))
        resp = session.get(url, headers=_HEADERS, params={"content": "true"}, timeout=20)
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
        # `location` — фактическое место работы («New York, New York»).
        # `offices` — организационная единица и часто НЕ локация: у Gemini там
        # «Gemini North America». Раньше offices читался первым, и в карточке
        # вместо города показывался регион (найдено 05.08.2026).
        location = ""
        loc = item.get("location")
        if isinstance(loc, dict):
            location = loc.get("name", "") or ""
        if not location:
            offices = item.get("offices") or []
            if isinstance(offices, list) and offices:
                location = offices[0].get("name", "") or ""
            elif isinstance(offices, dict):
                location = offices.get("name", "") or ""

        updated_at = item.get("updated_at") or item.get("created_at") or ""
        try:
            published_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except Exception:
            published_at = None

        # Content field contains job description HTML
        content = clean_description(item.get("content", ""))

        # Раньше is_remote ставился по слову «remote» ГДЕ УГОДНО в тексте — и
        # вакансия в офисе Нью-Йорка («flexibility of remote work» в блоке про
        # культуру компании) помечалась как удалённая. Теперь: либо локация прямо
        # говорит remote, либо в тексте есть явная формулировка формата.
        is_remote = detect_remote(location=location, description=content)

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
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")
    logging.basicConfig(level=logging.INFO)
    parser = GreenhouseParser()
    jobs = parser.parse()
    print(f"Found {len(jobs)} jobs from Greenhouse")
    for j in jobs[:10]:
        print(f"  {j.title} @ {j.company} | {j.location} | {j.url}")
