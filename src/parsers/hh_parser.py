"""
HH.ru parser via public RSS feed — без OAuth, без регистрации.
RSS endpoint: hh.ru/search/vacancy/rss?text=...&area=113
"""
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from email.utils import parsedate_to_datetime

import requests
import yaml
from bs4 import BeautifulSoup

from .base import BaseParser
from ..models import Job

logger = logging.getLogger(__name__)

_CONFIG = Path(__file__).parent.parent.parent / "config" / "settings.yaml"
_RSS_URL = "https://hh.ru/search/vacancy/rss"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# Паттерны для извлечения зарплаты из текста описания
_SALARY_RE = re.compile(
    r"(?:от\s*)?([\d\s]+)\s*(?:до\s*([\d\s]+))?\s*(руб|rub|\$|usd|usdt|eur)?",
    re.IGNORECASE,
)


class HHParser(BaseParser):
    name = "hh"

    def __init__(self) -> None:
        cfg = yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))
        self.cfg = cfg["parsers"]["hh"]

    def parse(self) -> list[Job]:
        if not self.cfg.get("enabled", True):
            return []

        seen: set[str] = set()
        jobs: list[Job] = []
        for query in self.cfg.get("search_queries", ["developer"]):
            for job in self._fetch_query(query):
                if job.id not in seen:
                    seen.add(job.id)
                    jobs.append(job)
        return jobs

    def _fetch_query(self, query: str) -> list[Job]:
        params = {
            "text": query,
            "area": self.cfg.get("area", 113),
            "per_page": 50,
            "order_by": "publication_time",
        }
        if not self.cfg.get("only_with_salary", False) is True:
            pass  # RSS не поддерживает этот фильтр, пропускаем

        try:
            # trust_env=False: hh.ru доступен напрямую из России,
            # через международный прокси — блокируется (451)
            session = requests.Session()
            session.trust_env = False
            resp = session.get(_RSS_URL, params=params, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("HH.ru RSS request failed: %s", e)
            return []

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            logger.error("HH.ru RSS parse error: %s", e)
            return []

        jobs = []
        for item in root.findall(".//item"):
            try:
                jobs.append(self._parse_item(item))
            except Exception as e:
                logger.warning("Failed to parse HH RSS item: %s", e)
        return jobs

    def _parse_item(self, item: ET.Element) -> Job:
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub_date_raw = item.findtext("pubDate") or ""
        desc_html = item.findtext("description") or ""

        # ID из URL вакансии
        vacancy_id = link.rstrip("/").rsplit("/", 1)[-1]

        # Дата публикации
        try:
            published_at = parsedate_to_datetime(pub_date_raw)
        except Exception:
            try:
                published_at = datetime.fromisoformat(pub_date_raw)
            except Exception:
                published_at = None

        # Описание + компания + город из HTML
        soup = BeautifulSoup(desc_html, "html.parser")
        desc_text = soup.get_text(separator="\n", strip=True)

        company = ""
        location = ""
        salary_min = None
        salary_max = None
        salary_currency = "RUB"

        # Формат RSS-описания (поля через |):
        # "Вакансия компании: X|Создана: Y|Регион: Z|Предполагаемый уровень...: от N до M $"
        for line in desc_text.splitlines():
            line = line.strip()
            if not line:
                continue
            low = line.lower()
            if "вакансия компании:" in low:
                company = line.split(":", 1)[-1].strip()
            elif "регион:" in low or "город:" in low:
                location = line.split(":", 1)[-1].strip()
            elif "уровень" in low or "доход" in low or "зарплата:" in low:
                if "не указан" not in low:
                    salary_min, salary_max, salary_currency = self._parse_salary(line)

        is_remote = any(
            w in desc_text.lower()
            for w in ["удалённо", "удаленно", "remote", "дистанционно"]
        )

        return Job(
            id=f"hh_{vacancy_id}",
            title=title,
            company=company,
            description=desc_text[:2000],
            url=link,
            source="hh.ru",
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            location=location,
            is_remote=is_remote,
            published_at=published_at,
        )

    def _parse_salary(self, text: str) -> tuple:
        # \xa0 — неразрывный пробел в числах на HH.ru (например "3\xa0000")
        text = text.replace("\xa0", " ")
        nums = re.findall(r"[\d\s]+", text)
        nums = [int(n.replace(" ", "")) for n in nums if n.strip() and int(n.replace(" ", "")) > 100]
        sal_min = nums[0] if nums else None
        sal_max = nums[1] if len(nums) > 1 else None

        text_low = text.lower()
        if "$" in text or "usd" in text_low:
            currency = "USD"
        elif "eur" in text_low:
            currency = "EUR"
        elif "usdt" in text_low:
            currency = "USDT"
        else:
            currency = "RUB"

        return sal_min, sal_max, currency


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / ".env")
    logging.basicConfig(level=logging.INFO)
    parser = HHParser()
    jobs = parser.parse()
    print(f"Found {len(jobs)} jobs from HH.ru")
    for j in jobs[:5]:
        print(f"  [{j.salary_min}–{j.salary_max} {j.salary_currency}] {j.title} @ {j.company} | {j.location}")
