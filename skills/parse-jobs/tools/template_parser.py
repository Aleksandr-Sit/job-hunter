"""
Шаблон нового парсера. Скопируй в src/parsers/web/<name>.py и замени:
- PREFIX → короткий префикс источника (2-4 символа)
- SITE_NAME → название сайта
- BASE_URL → URL страницы с вакансиями
"""
import hashlib
import logging
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# Подними уровень импорта при копировании в src/parsers/web/
from ..base import BaseParser
from ...models import Job

logger = logging.getLogger(__name__)
PREFIX = "xxx"
SITE_NAME = "example.com"
BASE_URL = "https://example.com/jobs"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JobHunterBot/1.0)"}


class TemplateParser(BaseParser):
    name = PREFIX

    def parse(self) -> list[Job]:
        try:
            resp = requests.get(BASE_URL, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("%s request failed: %s", SITE_NAME, e)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        jobs = []

        # TODO: замени селектор на реальный
        for card in soup.select(".job-card"):
            try:
                jobs.append(_parse_card(card))
            except Exception as e:
                logger.debug("%s parse skip: %s", SITE_NAME, e)

        return jobs


def _parse_card(card) -> Job:
    # TODO: замени селекторы
    title_el = card.select_one("h2, h3, .title")
    company_el = card.select_one(".company")
    link_el = card.select_one("a[href]")

    title = title_el.get_text(strip=True) if title_el else ""
    company = company_el.get_text(strip=True) if company_el else ""
    url = link_el["href"] if link_el else ""
    if url and not url.startswith("http"):
        url = f"https://{SITE_NAME}{url}"

    uid = hashlib.md5(url.encode()).hexdigest()[:12]
    return Job(
        id=f"{PREFIX}_{uid}",
        title=title,
        company=company,
        description=f"{title} at {company}",
        url=url,
        source=SITE_NAME,
        is_remote=True,
        published_at=datetime.utcnow(),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = TemplateParser()
    jobs = parser.parse()
    print(f"Found {len(jobs)} jobs from {SITE_NAME}")
    for j in jobs[:5]:
        print(f"  {j.title} @ {j.company} — {j.url}")
