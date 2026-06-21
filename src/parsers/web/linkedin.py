"""
LinkedIn Jobs — парсинг через JobSpy (jobs-guest публичный endpoint, без авторизации).
Документация: https://github.com/speedyapply/JobSpy

Ограничения:
- С одного VPS IP блокирует после ~100–200 запросов
- При блокировке (429/ConnectionError) парсер логирует warning и возвращает []
- Отключить: linkedin.enabled: false в settings.yaml
"""
import hashlib
import logging
import time
from pathlib import Path

import yaml

from ..base import BaseParser
from ...models import Job

logger = logging.getLogger(__name__)

_CONFIG = Path(__file__).parent.parent.parent.parent / "config" / "settings.yaml"


class LinkedInParser(BaseParser):
    name = "linkedin"

    def __init__(self) -> None:
        cfg = yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))
        parser_cfg = cfg.get("parsers", {}).get("linkedin", {})
        self.enabled = parser_cfg.get("enabled", True)
        self.queries: list[str] = parser_cfg.get("queries", [])
        self.results_per_query: int = parser_cfg.get("results_per_query", 30)
        self.hours_old: int = parser_cfg.get("hours_old", 72)

    def parse(self) -> list[Job]:
        if not self.enabled:
            return []

        try:
            from jobspy import scrape_jobs
        except ImportError:
            logger.error("LinkedIn: python-jobspy не установлен. Запусти: pip install python-jobspy")
            return []

        jobs: list[Job] = []
        seen_urls: set[str] = set()

        for query in self.queries:
            try:
                df = scrape_jobs(
                    site_name=["linkedin"],
                    search_term=query,
                    location="Remote",
                    results_wanted=self.results_per_query,
                    hours_old=self.hours_old,
                    linkedin_fetch_description=False,
                    verbose=0,
                )
                if df is None or df.empty:
                    logger.debug("LinkedIn [%s]: 0 результатов", query)
                    continue

                count_before = len(jobs)
                for _, row in df.iterrows():
                    url = str(row.get("job_url") or "")
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    try:
                        jobs.append(self._row_to_job(row))
                    except Exception as e:
                        logger.debug("LinkedIn parse row error: %s", e)

                added = len(jobs) - count_before
                logger.debug("LinkedIn [%s]: +%d вакансий", query, added)
                time.sleep(2)  # пауза между запросами чтобы не схватить бан

            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "rate" in err_str or "blocked" in err_str:
                    logger.warning("LinkedIn: заблокирован (rate limit). Пропускаем до следующего цикла.")
                    break
                logger.warning("LinkedIn [%s] ошибка: %s", query, e)

        logger.info("LinkedIn: %d вакансий итого", len(jobs))
        return jobs

    def _row_to_job(self, row) -> Job:
        def _str(val) -> str:
            return "" if (val is None or str(val) == "nan") else str(val).strip()

        title = _str(row.get("title"))
        company = _str(row.get("company"))
        url = _str(row.get("job_url"))
        location = _str(row.get("location"))
        description = _str(row.get("description"))[:3000]

        # Зарплата
        sal_min = sal_max = None
        sal_currency = "USD"
        try:
            sal_min_raw = row.get("min_amount")
            sal_max_raw = row.get("max_amount")
            currency_raw = row.get("currency")
            if sal_min_raw and str(sal_min_raw) != "nan":
                sal_min = int(float(sal_min_raw))
            if sal_max_raw and str(sal_max_raw) != "nan":
                sal_max = int(float(sal_max_raw))
            if currency_raw and str(currency_raw) != "nan":
                sal_currency = str(currency_raw)
        except (ValueError, TypeError):
            pass

        is_remote = any(
            w in (location + description).lower()
            for w in ["remote", "anywhere", "удалённо", "удаленно"]
        )

        # Стабильный ID из URL
        job_id = "li_" + hashlib.md5(url.encode()).hexdigest()[:12]

        return Job(
            id=job_id,
            title=title,
            company=company,
            description=description or f"{title} at {company}",
            url=url,
            source="linkedin.com",
            location=location,
            is_remote=is_remote,
            salary_min=sal_min,
            salary_max=sal_max,
            salary_currency=sal_currency,
        )


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")
    logging.basicConfig(level=logging.INFO)
    parser = LinkedInParser()
    jobs = parser.parse()
    print(f"\nНайдено: {len(jobs)} вакансий")
    for j in jobs[:10]:
        print(f"  {j.title} @ {j.company} | {j.location} | {j.url[:60]}")
