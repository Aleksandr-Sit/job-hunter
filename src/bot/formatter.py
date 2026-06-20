"""Форматирует вакансию + результат матчинга в красивое HTML-сообщение для Telegram."""
import re
from ..models import Job, MatchResult


def _strip_html(text: str) -> str:
    """Убирает все HTML-теги — Telegram поддерживает только b, i, a, code, pre."""
    return re.sub(r"<[^>]+>", "", text).strip()


def _esc(text: str) -> str:
    """HTML-экранирование для Telegram HTML-режима."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _score_emoji(score: int) -> str:
    if score >= 90:
        return "🔥"
    if score >= 80:
        return "⭐"
    if score >= 70:
        return "✅"
    return "👀"


def _salary_line(job: Job) -> str:
    if not job.salary_min and not job.salary_max:
        return ""
    lo = f"{job.salary_min:,}" if job.salary_min else ""
    hi = f"{job.salary_max:,}" if job.salary_max else ""
    rng = f"{lo}–{hi}" if lo and hi else (lo or hi)
    return f"💰 <b>{rng} {job.salary_currency}</b>"


def _source_line(job: Job) -> str:
    remote = " • Remote 🌍" if job.is_remote else ""
    return f"📍 {job.source}{remote}"


def format_job_message(job: Job, match: MatchResult) -> str:
    emoji = _score_emoji(match.score)
    salary = _salary_line(job)
    source = _source_line(job)

    fits_lines = "\n".join(f"  • {_esc(r)}" for r in match.why_fits[:4])
    watch_lines = "\n".join(f"  • {_esc(r)}" for r in match.watch_out[:3])

    fits_block = f"\n✅ <b>Почему подходит:</b>\n{fits_lines}" if fits_lines else ""
    watch_block = f"\n⚠️ <b>Обратить внимание:</b>\n{watch_lines}" if watch_lines else ""
    rec_block = f"\n\n💡 {_esc(match.recommendation)}" if match.recommendation else ""

    desc = _esc(_strip_html(job.description)[:400])
    if len(job.description) > 400:
        desc += "…"

    return (
        f"{emoji} <b>{_esc(job.title)}</b> — <b>{match.score}/100</b>\n\n"
        f"🏢 {_esc(job.company)}\n"
        f"{salary}\n"
        f"{source}\n\n"
        f"📋 <i>{desc}</i>"
        f"{fits_block}"
        f"{watch_block}"
        f"{rec_block}"
    ).strip()


def format_daily_summary(count_parsed: int, count_sent: int, sources: list[str]) -> str:
    src_list = ", ".join(sources) if sources else "—"
    return (
        f"📊 <b>Дневной отчёт Job Hunter</b>\n\n"
        f"🔍 Просмотрено вакансий: <b>{count_parsed}</b>\n"
        f"📨 Отправлено подходящих: <b>{count_sent}</b>\n"
        f"📡 Источники: {src_list}"
    )
