"""
Telegram-бот для отправки вакансий пользователю.
Использует python-telegram-bot (v20+, async).
"""
import asyncio
import logging
import os

import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..models import Job, MatchResult
from .formatter import format_job_message, format_daily_summary

logger = logging.getLogger(__name__)


def _get_bot() -> telegram.Bot:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    return telegram.Bot(token=token)


def _get_chat_id() -> str:
    return os.environ["TELEGRAM_CHAT_ID"]


def _make_keyboard(job: Job) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔗 Открыть", url=job.url),
            InlineKeyboardButton("💾 Сохранить", callback_data=f"save_{job.id}"),
            InlineKeyboardButton("❌ Пропустить", callback_data=f"skip_{job.id}"),
        ]
    ])


async def _send_message_async(bot, chat_id: str, text: str,
                              keyboard=None) -> bool:
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        return True
    except telegram.error.TelegramError as e:
        logger.error("Telegram send error: %s", e)
        return False


async def _send_jobs_batch_async(pairs: list[tuple[Job, MatchResult]]) -> int:
    bot = _get_bot()
    chat_id = _get_chat_id()
    sent = 0
    for job, match in pairs:
        text = format_job_message(job, match)
        keyboard = _make_keyboard(job)
        ok = await _send_message_async(bot, chat_id, text, keyboard)
        if ok:
            sent += 1
        await asyncio.sleep(0.5)  # не флудим Telegram API
    return sent


async def _send_text_async(text: str) -> bool:
    bot = _get_bot()
    chat_id = _get_chat_id()
    return await _send_message_async(bot, chat_id, text)


def send_jobs_batch(pairs: list[tuple[Job, MatchResult]]) -> int:
    """Отправляет все вакансии в одном event loop. Возвращает количество отправленных."""
    return asyncio.run(_send_jobs_batch_async(pairs))


def send_daily_summary(count_parsed: int, count_sent: int, sources: list[str]) -> None:
    text = format_daily_summary(count_parsed, count_sent, sources)
    asyncio.run(_send_text_async(text))


def send_text(text: str) -> None:
    asyncio.run(_send_text_async(text))


# ── тест ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    from datetime import datetime, timezone
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / ".env")

    logging.basicConfig(level=logging.INFO)

    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()

    if args.test:
        test_job = Job(
            id="test-notify-1",
            title="Senior Python Developer",
            company="Web3 Startup",
            description="We are looking for a Senior Python developer to build our DeFi backend. "
                        "FastAPI, PostgreSQL, Docker required. Fully remote. $5000–8000/mo.",
            url="https://example.com/job/1",
            source="test",
            salary_min=5000,
            salary_max=8000,
            salary_currency="USD",
            is_remote=True,
            published_at=datetime.now(timezone.utc),
        )
        test_match = MatchResult(
            job_id="test-notify-1",
            score=87,
            why_fits=[
                "Python is your primary language",
                "FastAPI matches your expertise",
                "Remote work and $5–8k matches preferences",
                "Web3/DeFi aligns with your domain interest",
            ],
            watch_out=["Requires Kubernetes experience (you have basic)"],
            recommendation="Strong match — apply immediately, the stack is ideal.",
        )
        sent = send_jobs_batch([(test_job, test_match)])
        print(f"Sent: {sent}")
