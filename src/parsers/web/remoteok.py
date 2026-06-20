"""RemoteOK — бесплатный JSON API."""
import logging
from datetime import datetime, timezone

import requests

from ..base import BaseParser
from ...models import Job

logger = logging.getLogger(__name__)
_API_URL = "https://remoteok.com/api"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, */*",
    "Referer": "https://remoteok.com/",
}
_TIMEOUT = 30
_RETRIES = 2


class RemoteOKParser(BaseParser):
    name = "remoteok"

    def parse(self) -> list[Job]:
        last_err = None
        for attempt in range(_RETRIES):
            try:
                resp = requests.get(_API_URL, headers=_HEADERS, timeout=_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                break
            except requests.RequestException as e:
                last_err = e
                logger.warning("RemoteOK attempt %d failed: %s", attempt + 1, e)
        else:
            logger.error("RemoteOK request failed after %d attempts: %s", _RETRIES, last_err)
            return []

        # Первый элемент — мета-данные, пропускаем
        jobs = []
        for item in data[1:]:
            try:
                jobs.append(self._parse_item(item))
            except Exception as e:
                logger.warning("RemoteOK parse error: %s", e)
        return jobs

    def _parse_item(self, item: dict) -> Job:
        epoch = item.get("epoch", 0)
        published_at = datetime.fromtimestamp(epoch, tz=timezone.utc) if epoch else None

        salary_str = item.get("salary", "") or ""
        sal_min = sal_max = None
        if salary_str:
            parts = [p.strip().replace("$", "").replace(",", "").replace("k", "000")
                     for p in salary_str.split("–")]
            try:
                sal_min = int(float(parts[0]))
                sal_max = int(float(parts[-1]))
            except (ValueError, IndexError):
                pass

        return Job(
            id=f"rok_{item['id']}",
            title=item.get("position", ""),
            company=item.get("company", ""),
            description=item.get("description", "")[:3000],
            url=item.get("url", ""),
            source="remoteok.com",
            salary_min=sal_min,
            salary_max=sal_max,
            salary_currency="USD",
            is_remote=True,
            published_at=published_at,
            tags=item.get("tags", []),
        )
