"""
Telegram channel parser via public web preview (t.me/s/CHANNEL).
No API keys required — works for any public channel.
"""
import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
import yaml
from bs4 import BeautifulSoup

from ..models import MAX_DESCRIPTION_CHARS, Job
from .base import BaseParser

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


# Зарплата в посте — только число, рядом с которым деньги: знак валюты вплотную
# или слово «зарплата / оклад / доход» в той же строке. Раньше бралось ЛЮБОЕ первое
# число текста. @workingincrypto начинает каждый пост с номера вакансии («2655
# #Вакансия …»), и 29.10.2026 все 17 свежих вакансий канала шли в карточку и в AI
# как «от 2655 USD», а настоящая зарплата терялась. Заодно ловились «100k+ member
# community», «1,500+ have visited» и «$4,000 – $6,000» превращалось в «от 4000».
_CUR = r"(?:\$|€|₽|usdt|usdc|usd|eur|руб\w*|rub\b)"
_CUR_BEFORE = re.compile(_CUR + r"\s?$", re.IGNORECASE)
_CUR_AFTER = re.compile(r"^\s?\+?\s?(" + _CUR + r")", re.IGNORECASE)
# Число: «180 000», «1,000», «4.5», «2655» + множитель («k», «тыс», «т.р.»).
# «M / млн / B» — оценки и инвестиции («привлекла $350M+»), не зарплата.
_SAL_NUM = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[ \u00a0\u202f,.]\d{3})+|\d+(?:[.,]\d{1,2})?)"
    r"(?:\s?(k|к|тыс\w*\.?|т\.\s?р\.?|млн|млрд|mln|bn|m|b)(?![a-zа-яё]))?(?!\d)",
    re.IGNORECASE)
_RANGE_GAP = re.compile(
    r"\s*(?:" + _CUR + r"\.?)?\s*\+?\s*(?:[-–—~]|до|to)\s*(?:" + _CUR + r"|~)?\s*",
    re.IGNORECASE)
_SALARY_WORD = re.compile(
    r"зарплат|з/п|\bзп\b|оклад|доход(?!н)|оплат[аы]\b|вознагражден|salary|earnings",
    re.IGNORECASE)
# Строка со знаком денег, но без слова «зарплата»: берём, только если она похожа
# на строку зарплаты («$4,000 – $6,000 / month | Remote») и не про чужие деньги.
_NOT_SALARY = re.compile(
    r"бюджет|оборот|привлек|инвест|raised|funding|\btvl\b|\bacv\b|сделк|\bdeals?\b"
    r"|актив|\bassets\b|капитал|депозит|deposit|volume|объ[её]м|выручк|revenue|\bgmv\b"
    r"|фонд|\bfund|prize|приз|грант|grant|\bчек\b|\bmrr\b|\barr\b|valuation",
    re.IGNORECASE)
_PAY_PERIOD = re.compile(
    r"/\s?(?:мес|month|mo\b|год|year|yr|час|hour)|в месяц|в год|per (?:month|year|annum)"
    r"|monthly|\bnet\b|\bgross\b|гросс|на руки",
    re.IGNORECASE)
_SHORT_LINE = 80


def _currency_code(marker: str) -> str:
    m = marker.lower()
    if m in ("€", "eur"):
        return "EUR"
    if m == "₽" or m.startswith("руб") or m == "rub":
        return "RUB"
    return "USD"


def _salary_tokens(line: str) -> list[dict]:
    """Числа строки: значение, валюта рядом (или None), был ли множитель k/тыс."""
    tokens = []
    for m in _SAL_NUM.finditer(line):
        raw, suffix = m.group(1), (m.group(2) or "").lower()
        if suffix and suffix[0] in "mbм":          # m, b, bn, mln, млн, млрд
            continue
        digits = re.sub(r"[ \u00a0\u202f]", "", raw)
        if re.fullmatch(r"\d{1,3}(?:[,.]\d{3})+", digits):
            digits = re.sub(r"[,.]", "", digits)
        value = float(digits.replace(",", "."))
        if suffix:
            value *= 1000
        cur = None
        before = _CUR_BEFORE.search(line[:m.start()])
        after = _CUR_AFTER.search(line[m.end():])
        if before:
            cur = _currency_code(before.group(0).strip())
        elif after:
            cur = _currency_code(after.group(1))
        tokens.append({"start": m.start(), "end": m.end(), "value": value,
                       "cur": cur, "mult": bool(suffix), "plain": raw.isdigit()})
    return tokens


def _year_like(token: dict) -> bool:
    """«Доход за 2025 год» — год, а не сумма (без диапазона и знака денег)."""
    return token["plain"] and 1990 <= token["value"] <= 2100


def _range_at(line: str, tokens: list[dict], i: int) -> tuple[float, Optional[float]]:
    """Число i и, если за ним диапазон («– $6,000», «до 5,000»), верх диапазона."""
    lo, hi = tokens[i], None
    if i + 1 < len(tokens):
        nxt = tokens[i + 1]
        if _RANGE_GAP.fullmatch(line[lo["end"]:nxt["start"]]):
            hi = nxt
    lo_v = lo["value"]
    if hi is None:
        return lo_v, None
    hi_v = hi["value"]
    if hi["mult"] and not lo["mult"] and lo_v < 1000 and lo_v * 1000 <= hi_v:
        lo_v *= 1000                                # «2-3k USDT» → 2000–3000
    return lo_v, (hi_v if hi_v > lo_v else None)


def _extract_salary(text: str) -> tuple[Optional[int], Optional[int], str]:
    """Первая строка поста, где есть зарплата, → (от, до, валюта)."""
    default_cur = "RUB" if ("руб" in text.lower() or "₽" in text) else "USD"
    for line in text.split("\n"):
        tokens = _salary_tokens(line)
        if not tokens:
            continue
        money = [i for i, t in enumerate(tokens) if t["cur"] and 50 <= t["value"] <= 50_000_000]
        salary_word = _SALARY_WORD.search(line)
        if money and (salary_word or (
                not _NOT_SALARY.search(line)
                and (len(line.strip()) <= _SHORT_LINE or _PAY_PERIOD.search(line)))):
            i = money[0]
            cur = tokens[i]["cur"]
            # «от 1400 до 2200 USDT», «2-3k USDT»: валюта только у верха диапазона
            if i > 0 and tokens[i - 1]["cur"] is None and _RANGE_GAP.fullmatch(
                    line[tokens[i - 1]["end"]:tokens[i]["start"]]):
                i -= 1
        elif salary_word:
            # «Доход в диапазоне 1300–2000» — валюты нет, число без знака денег
            bare = [i for i, t in enumerate(tokens)
                    if 500 <= t["value"] <= 50_000_000
                    and not (_year_like(t) and _range_at(line, tokens, i)[1] is None)]
            if not bare:
                continue
            i, cur = bare[0], default_cur
        else:
            continue
        lo, hi = _range_at(line, tokens, i)
        return int(round(lo)), (int(round(hi)) if hi else None), cur
    return None, None, default_cur


def _clean_title(line: str) -> str:
    """Убирает ведущие эмодзи/хэштеги/маркеры из строки-заголовка."""
    return _TITLE_STRIP.sub("", line).strip()


# Одинокое слово-заголовок («Вакансия», «Hiring») — не роль; берём следующую строку.
_GENERIC_HEADER = {
    "вакансия", "вакансии", "vacancy", "hiring", "job", "jobs", "job opening",
    "открыта вакансия", "новая вакансия", "we are hiring", "we're hiring",
}


def _generic_header(cleaned: str) -> bool:
    """«Вакансия» и «Вакансия:» — одно и то же.

    Раньше сравнение шло со строкой как есть, поэтому двоеточие делало заголовок
    «содержательным»: в проде 15.08.2026 в AI уходила карточка с названием
    «Вакансия:» и компанией «@jobers».
    """
    return cleaned.lower().strip(" :：-–—.!") in _GENERIC_HEADER


# Строка ЦЕЛИКОМ из хэштегов — это рубрикатор канала, а не название вакансии.
# Типовой пост: «#Вакансия / #SalesManager / #Sales / #Payments / #Remote», и только
# потом «Требуется Sales Manager в PayPort». Раньше заголовком становилось
# «SalesManager»: `_clean_title` срезал решётку, и оставалось 11 букв — порога в
# 8 букв хватало. Замер 17.08.2026: из-за этого 42 поста из 232 (18%) схлопывались
# дедупом в один — ключ дедупа строится из company+title, а канал и тег у разных
# вакансий совпадают.
_TAGS_ONLY_LINE = re.compile(r'^(?:[#＃][\w\-]+[\s,;|·•]*)+$', re.UNICODE)


def _pick_title(lines: list[str]) -> str:
    """Первая содержательная строка (≥8 букв, не общий заголовок), а не эмодзи/хэштег."""
    for line in lines:
        if _TAGS_ONLY_LINE.match(line.strip()):
            continue
        cleaned = _clean_title(line)
        if sum(ch.isalpha() for ch in cleaned) >= 8 and not _generic_header(cleaned):
            return cleaned[:120]
    # Фолбэк: первая строка с хоть какими-то буквами. Пост целиком из эмодзи/
    # пунктуации давал ПУСТОЙ заголовок и карточку без названия (найдено тестом).
    for line in lines:
        cleaned = _clean_title(line)
        if any(ch.isalpha() for ch in cleaned):
            return cleaned[:120]
    return "Job opening"


# Рекламные вставки и служебные объявления канала. Проверяются по всему тексту:
# «sponsored» обычно стоит в шапке, но «реклама» и «erid» — в подвале поста.
_PROMO_MARKERS = (
    "sponsored", "sponsored job post", "promoted post", "#реклама", "реклама:",
    "на правах рекламы", "erid:", "партнёрский пост", "партнерский пост",
)
# Служебные объявления канала: приветствия, анонсы, «мы вернулись».
_CHANNEL_CHATTER = (
    "добрый день, друзья", "всем привет", "дорогие подписчики", "уважаемые подписчики",
    "отпуск окончен", "мы вернулись", "с возвращением", "напоминаем, что",
)


def _is_promo_post(text_lower: str) -> bool:
    """True для рекламы и служебных постов канала — это не вакансии.

    Реклама режется всегда. Служебное объявление режется только если в посте нет
    ни одного маркера вакансии: пост может начинаться с приветствия и дальше
    содержать настоящую вакансию, и такой терять нельзя.
    """
    if any(m in text_lower for m in _PROMO_MARKERS):
        return True
    head = text_lower[:120]
    return any(m in head for m in _CHANNEL_CHATTER) and not any(
        v in text_lower for v in _VACANCY_MARKERS
    )


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
        # ...и рекламу/объявления канала. В свежей пачке 15.08.2026 до AI доходили
        # «SPONSORED JOB POST» (66 баллов) и «Добрый день, друзья. Отпуск окончен,
        # пора возвращаться к работе» (40 баллов) — оба тратили бюджет Cerebras.
        if _is_promo_post(text_lower):
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
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        title = _pick_title(lines)

        sal_min, sal_max, currency = _extract_salary(text)
        # ОСОЗНАННОЕ отличие от ATS-парсеров (src/parsers/normalize.py):
        # здесь описание — это ВЕСЬ пост, короткий и написанный человеком.
        # Корпоративного boilerplate вроде «remote troubleshooting» или
        # «flexibility of remote work» в нём не бывает, поэтому упоминание
        # «удалённо» — достоверный сигнал формата, а не ложное срабатывание.
        is_remote = any(
            w in text_lower
            for w in ["remote", "удалённо", "удаленно", "дистанционно", "🌍", "🌎"]
        )

        jobs.append(Job(
            id=f"tg_{uid}",
            title=title,
            company=f"@{channel}",
            description=text[:MAX_DESCRIPTION_CHARS],
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
