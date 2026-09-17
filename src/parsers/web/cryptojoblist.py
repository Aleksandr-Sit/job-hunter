"""CryptoJobsList — парсит Next.js SSR JSON из <script> тега на главной странице."""
import json
import logging
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from ...models import Job
from ..base import BaseParser
from ..normalize import clean_description

logger = logging.getLogger(__name__)
_URL = "https://cryptojobslist.com"
_DESC_PAUSE = 0.3     # пауза между страницами вакансий (25 за прогон)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class CryptoJobListParser(BaseParser):
    name = "cryptojoblist"

    # Контейнер описания на странице вакансии: `JobView_description__<хэш>`.
    # Хэш меняется при пересборке сайта, поэтому ищем по началу имени класса.
    _DESC_CLASS = re.compile(r"^JobView_description")
    # Подвал страницы: «Related Salaries in Web3 … Salary developer Salary manager»
    # — это слова ролей и доменов, которыми легко обмануть гейты. Режем.
    _FOOTER_MARKERS = ("Related Salaries", "Related Locations")

    def __init__(self) -> None:
        self._session = requests.Session()

    def _fetch_description(self, url: str) -> str:
        """Полное описание со страницы вакансии. Пустая строка — не получилось.

        Главная страница отдаёт только метаданные (проверено 17.09.2026: ни у
        одной из 25 вакансий нет `jobPostingJSONLD`), поэтому описание тянем
        отдельным запросом. Цена замерена там же: ~100 КБ и 0.08 с на страницу,
        то есть ~2.5 МБ и ~8 с на прогон вместе с паузами.

        Никогда не бросает: отказ сайта не должен ронять парсер.
        """
        if not url or "/jobs/" not in url:
            return ""
        try:
            time.sleep(_DESC_PAUSE)
            resp = self._session.get(url, headers=_HEADERS, timeout=20)
            if resp.status_code != 200:
                logger.debug("CryptoJobsList: %s -> HTTP %s", url, resp.status_code)
                return ""
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            logger.debug("CryptoJobsList description failed (%s): %s", url, str(e)[:100])
            return ""

        block = soup.find("div", class_=self._DESC_CLASS)
        if block:
            return block.get_text(separator="\n", strip=True)
        main = soup.find("article") or soup.find("main")
        if not main:
            return ""
        text = main.get_text(separator="\n", strip=True)
        for marker in self._FOOTER_MARKERS:
            pos = text.find(marker)
            if pos > 0:
                text = text[:pos]
        return text.strip()

    def parse(self) -> list[Job]:
        try:
            resp = self._session.get(_URL, headers=_HEADERS, timeout=20)
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
        # ⚠️ Адрес вакансии — /jobs/<slug>. Без «/jobs» сайт отдаёт 404: до
        # 17.09.2026 КАЖДАЯ карточка этого источника вела на несуществующую
        # страницу (проверено живьём: 404 против 200).
        url = f"{_URL}/jobs/{slug}" if slug else _URL

        # Описание. Раньше бралось из `jobPostingJSONLD`, но сайт это поле больше
        # не отдаёт (проверено 17.09.2026: 0 из 25), и в описание уходила заглушка
        # «должность at компания + теги» — медиана 120 символов. Гейты по языку,
        # стажу и формату на этом источнике были слепы. Теперь тянем со страницы
        # вакансии (см. _fetch_description), заглушка остаётся запасным вариантом.
        desc = ""
        jsonld_raw = item.get("jobPostingJSONLD", "")
        if jsonld_raw and isinstance(jsonld_raw, str):
            try:
                desc = json.loads(jsonld_raw).get("description", "")
            except json.JSONDecodeError:
                pass
        if not desc:
            desc = self._fetch_description(url)
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
            description=clean_description(desc),
            url=url,
            source="cryptojobslist.com",
            salary_min=sal_min,
            salary_max=sal_max,
            salary_currency=currency,
            is_remote=bool(item.get("remote")),
            published_at=published_at,
            tags=item.get("tags", []),
        )
