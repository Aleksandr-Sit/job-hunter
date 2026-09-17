"""
HH.ru parser via public RSS feed — без OAuth, без регистрации.
RSS endpoint: hh.ru/search/vacancy/rss?text=...&area=113
"""
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

from ..models import MAX_DESCRIPTION_CHARS, Job
from .base import BaseParser
from .normalize import detect_remote

logger = logging.getLogger(__name__)

_CONFIG = Path(__file__).parent.parent.parent / "config" / "settings.yaml"
_RSS_URL = "https://hh.ru/search/vacancy/rss"

# RSS жёстко отдаёт максимум 20 вакансий на запрос. Проверено 15.08.2026:
# `per_page` игнорируется (раньше слался 50 — мёртвый параметр), пагинация тоже:
# `&page=1,2,3` возвращают ту же выдачу с тем же первым ID. Единственный способ
# получить с одного запроса больше 20 — сменить сортировку (см. parse()).
_RSS_PAGE_CAP = 20
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# Паттерны для извлечения зарплаты из текста описания
_SALARY_RE = re.compile(
    r"(?:от\s*)?([\d\s]+)\s*(?:до\s*([\d\s]+))?\s*(руб|rub|\$|usd|usdt|eur)?",
    re.IGNORECASE,
)


def _norm_company(name: str | None) -> str:
    """Название компании для сравнения: регистр, неразрывные пробелы, лишние пробелы.
    В RSS встречается «ООО\\xa0ТРАНОМИКА» — с неразрывным пробелом после формы."""
    return " ".join((name or "").replace("\xa0", " ").lower().split())


class HHParser(BaseParser):
    name = "hh"

    def __init__(self) -> None:
        cfg = yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))
        self.cfg = cfg["parsers"]["hh"]

    def parse(self) -> list[Job]:
        if not self.cfg.get("enabled", True):
            return []

        second_pass = self.cfg.get("second_pass", True)
        seen: dict[str, Job] = {}
        jobs: list[Job] = []
        added_by_second = 0

        for query in self.cfg.get("search_queries", ["developer"]):
            fresh = self._fetch_query(query, by_date=True)
            for job in fresh:
                if job.id not in seen:
                    seen[job.id] = job
                    jobs.append(job)

            # Второй проход имеет смысл ТОЛЬКО для запросов, упёршихся в потолок:
            # если выдача короче 20, обе сортировки возвращают один и тот же полный
            # набор. Замер 15.08.2026 на 45 запросах: у всех 18 ненасыщенных запросов
            # прирост был ровно 0, а 27 насыщенных дали +333 вакансии (+63%).
            if second_pass and len(fresh) >= _RSS_PAGE_CAP:
                for job in self._fetch_query(query, by_date=False):
                    if job.id not in seen:
                        seen[job.id] = job
                        jobs.append(job)
                        added_by_second += 1

        if second_pass:
            logger.info("HH: +%d вакансий вторым проходом (по релевантности)",
                        added_by_second)

        # Слежка за работодателями по employer_id: ВСЕ их вакансии, а не только те,
        # где крипто-слово попало в текст запроса. Скан 15.09.2026 показал, что
        # роли малых крипто-компаний называются «Специалист казначейства», «Казначей»,
        # «Специалист бэк-офиса» — по тексту их не найти ни одним разумным запросом.
        # Метка `employer_watch` ставит вакансию в начало очереди пересмотра отказов
        # (scheduler._rescue_order): слежка идёт в конце выдачи, и без метки её срезал
        # бы потолок пересмотра. Ставим и на вакансию, уже найденную текстовым запросом.
        added_by_employers = 0
        for emp in self.cfg.get("employer_ids") or []:
            for job in self._fetch_employer(emp, second_pass=second_pass):
                if job.id in seen:
                    seen[job.id].raw["employer_watch"] = True
                    continue
                job.raw["employer_watch"] = True
                seen[job.id] = job
                jobs.append(job)
                added_by_employers += 1
        if self.cfg.get("employer_ids"):
            logger.info("HH: +%d вакансий слежкой за работодателями",
                        added_by_employers)
        return jobs

    def _fetch_employer(self, emp: dict, second_pass: bool = True) -> list[Job]:
        """Вакансии одного работодателя с защитой от неверного id.

        ⚠️ Несуществующий employer_id hh.ru НЕ отвергает, а молча игнорирует и отдаёт
        обычную выдачу по всей России. Проверено 15.09.2026: id 99999999999 вернул
        20 вакансий Lamoda и «Алабуги». Поэтому оставляем только вакансии, у которых
        компания совпадает с `name` из конфига, а полностью чужую выдачу — в лог.
        """
        emp_id = emp.get("id")
        if not emp_id:
            return []
        fresh = self._fetch_query(None, by_date=True, employer_id=emp_id)
        if second_pass and len(fresh) >= _RSS_PAGE_CAP:
            known = {j.id for j in fresh}
            fresh += [j for j in self._fetch_query(None, by_date=False, employer_id=emp_id)
                      if j.id not in known]
        name = _norm_company(emp.get("name"))
        if not name:
            return fresh
        own = [j for j in fresh if name in _norm_company(j.company)]
        if fresh and not own:
            logger.warning(
                "HH employer_id=%s (%s): в выдаче ни одной вакансии этой компании — "
                "id неверный или компания переименована; отброшено %d чужих",
                emp_id, emp.get("name"), len(fresh))
        return own

    def _fetch_query(self, query: str | None, by_date: bool = True,
                     employer_id: int | str | None = None) -> list[Job]:
        params = {"area": self.cfg.get("area", 113)}
        if query:
            params["text"] = query
        if employer_id:
            params["employer_id"] = employer_id
        if by_date:
            params["order_by"] = "publication_time"
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

        # Формат работы — только через общий модуль. Своя проверка здесь ловила
        # одиночное «удалённо»/«remote» в любом месте текста, и офисная вакансия
        # («удалённая консультация», «remote access») проходила как удалённая:
        # так 19.08.2026 приехал «Специалист поддержки» из Екатеринбурга.
        is_remote = detect_remote(location=location, description=desc_text)

        return Job(
            id=f"hh_{vacancy_id}",
            title=title,
            company=company,
            description=desc_text[:MAX_DESCRIPTION_CHARS],
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
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / ".env")
    logging.basicConfig(level=logging.INFO)
    parser = HHParser()
    jobs = parser.parse()
    print(f"Found {len(jobs)} jobs from HH.ru")
    for j in jobs[:5]:
        print(f"  [{j.salary_min}–{j.salary_max} {j.salary_currency}] {j.title} @ {j.company} | {j.location}")
