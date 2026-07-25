"""
Telegram channel parser via public web preview (t.me/s/CHANNEL).
No API keys required — works for any public channel.
"""
import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests
import yaml
from bs4 import BeautifulSoup
from pathlib import Path

from .base import BaseParser
from ..models import Job

logger = logging.getLogger(__name__)
_CONFIG = Path(__file__).parent.parent.parent / "config"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_JOB_KEYWORDS = [
    "ищем", "вакансия", "hiring", "vacancy", "job", "developer",
    "engineer", "разработчик", "backend", "frontend", "зарплата",
    "salary", "удалённо", "remote", "full-time", "part-time",
    "web3", "crypto", "blockchain", "defi", "operations", "qa",
    "specialist", "менеджер", "manager", "analyst", "аналитик",
]

# Посты-резюме / «ищу работу» проходят по _JOB_KEYWORDS (там есть crypto/web3),
# но это НЕ вакансии — их не нужно слать кандидату и тратить на них AI (F1).
# Отсекаем, только если пост НАЧИНАЕТСЯ как резюме и в начале нет признака вакансии.
_RESUME_MARKERS = (
    "резюме", "ищу работу", "ищу проект", "ищу вакансию", "в поиске работы",
    "готов взяться", "open to work", "looking for work", "#резюме", "#ищуработу",
    "#cv", "cv:", "ищу удаленную работу",
)
_VACANCY_MARKERS = ("ваканси", "hiring", "ищем", "требу", "we are looking", "we're looking")

# Ведущие эмодзи/хэштеги/пунктуация/маркеры — не должны становиться заголовком.
# \W ловит эмодзи (включая variation selectors), #, @, •, тире, пробелы; буквы
# (лат./кир.) и цифры сохраняются.
_TITLE_STRIP = re.compile(r'^[\W_]+', re.UNICODE)


def _extract_salary(text: str) -> tuple[Optional[int], Optional[int], str]:
    currency = "RUB" if ("руб" in text.lower() or "₽" in text) else "USD"
    nums = re.findall(r'\d[\d\s,.]*(?:k|тыс)?', text, re.IGNORECASE)
    values = []
    for n in nums[:2]:
        n = n.strip().replace(" ", "").replace(",", "")
        if n.lower().endswith("k"):
            try:
                values.append(int(float(n[:-1]) * 1000))
            except ValueError:
                pass
        else:
            try:
                v = int(n)
                if 500 <= v <= 50_000_000:   # отсеиваем мусор
                    values.append(v)
            except ValueError:
                pass
    if len(values) >= 2:
        return min(values), max(values), currency
    if len(values) == 1:
        return values[0], None, currency
    return None, None, currency


def _clean_title(line: str) -> str:
    """Убирает ведущие эмодзи/хэштеги/маркеры из строки-заголовка."""
    return _TITLE_STRIP.sub("", line).strip()


# Одинокое слово-заголовок («Вакансия», «Hiring») — не роль; берём следующую строку.
_GENERIC_HEADER = {
    "вакансия", "вакансии", "vacancy", "hiring", "job", "jobs", "job opening",
    "открыта вакансия", "новая вакансия", "we are hiring", "we're hiring",
}


def _pick_title(lines: list[str]) -> str:
    """Первая содержательная строка (≥8 букв, не общий заголовок), а не эмодзи/хэштег."""
    for line in lines:
        cleaned = _clean_title(line)
        if sum(ch.isalpha() for ch in cleaned) >= 8 and cleaned.lower() not in _GENERIC_HEADER:
            return cleaned[:120]
    return (_clean_title(lines[0])[:120] if lines else "Job opening")


def _is_resume_post(text_lower: str) -> bool:
    """True, если пост — резюме/«ищу работу», а не вакансия (по началу поста)."""
    head = text_lower[:80]
    return any(m in head for m in _RESUME_MARKERS) and not any(
        v in head for v in _VACANCY_MARKERS
    )


def _parse_datetime(dt_str: str) -> Optional[datetime]:
    """Парсит ISO datetime из атрибута <time datetime="...">."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fetch_channel(channel: str, timeout: int = 20) -> list[Job]:
    url = f"https://t.me/s/{channel}"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
    except Exception as e:
        logger.warning("Telegram web fetch failed for @%s: %s", channel, e)
        return []

    if resp.status_code != 200:
        logger.warning("@%s returned HTTP %d", channel, resp.status_code)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    messages = soup.find_all("div", class_="tgme_widget_message")

    jobs = []
    for msg in messages:
        text_el = msg.find("div", class_="tgme_widget_message_text")
        if not text_el:
            continue
        text = text_el.get_text(separator="\n").strip()

        # Фильтр по ключевым словам
        text_lower = text.lower()
        if not any(kw in text_lower for kw in _JOB_KEYWORDS):
            continue
        # Пропускаем посты-резюме / «ищу работу» — это не вакансии (F1)
        if _is_resume_post(text_lower):
            continue

        # Дата публикации
        time_el = msg.find("time", class_="time")
        published_at = (
            _parse_datetime(time_el.get("datetime"))
            if time_el else datetime.now(timezone.utc)
        )

        # Ссылка на пост
        link_el = msg.find("a", class_="tgme_widget_message_date")
        post_url = link_el["href"] if link_el else f"https://t.me/{channel}"

        # ID из ссылки (https://t.me/channel/12345)
        post_id = post_url.rstrip("/").split("/")[-1]
        uid = hashlib.md5(f"{channel}_{post_id}".encode()).hexdigest()[:12]

        # Заголовок — первая содержательная строка (не эмодзи/хэштег) (F1)
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        title = _pick_title(lines)

        sal_min, sal_max, currency = _extract_salary(text)
        is_remote = any(
            w in text_lower
            for w in ["remote", "удалённо", "удаленно", "дистанционно", "🌍", "🌎"]
        )

        jobs.append(Job(
            id=f"tg_{uid}",
            title=title,
            company=f"@{channel}",
            description=text[:2000],
            url=post_url,
            source=f"telegram:{channel}",
            salary_min=sal_min,
            salary_max=sal_max,
            salary_currency=currency,
            is_remote=is_remote,
            published_at=published_at,
        ))

    return jobs


class TelegramParser(BaseParser):
    name = "telegram"

    def __init__(self) -> None:
        sources = yaml.safe_load(
            (_CONFIG / "sources.yaml").read_text(encoding="utf-8")
        )
        self.channels: list[str] = sources.get("telegram_channels", [])

    def parse(self) -> list[Job]:
        if not self.channels:
            logger.info("No Telegram channels configured in sources.yaml")
            return []

        all_jobs: list[Job] = []
        for i, channel in enumerate(self.channels):
            jobs = _fetch_channel(channel)
            if jobs:
                logger.info("@%s: found %d posts", channel, len(jobs))
            all_jobs.extend(jobs)
            if i < len(self.channels) - 1:
                time.sleep(1)   # не флудим t.me

        return all_jobs
