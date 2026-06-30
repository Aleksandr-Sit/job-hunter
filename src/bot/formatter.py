"""Форматирует вакансию + результат матчинга в Telegram HTML-сообщение."""
import re
from ..models import Job, MatchResult

_DIV = "─" * 22


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _score_emoji(score: int) -> str:
    if score >= 90:
        return "🔥"
    if score >= 80:
        return "⭐"
    if score >= 70:
        return "✅"
    return "👀"


def _meta_line(job: Job) -> str:
    parts = [f"<b>{_esc(job.company)}</b>"]
    if job.is_remote:
        parts.append("Remote")
    if job.location and job.location.lower() not in ("remote", "anywhere", ""):
        parts.append(_esc(job.location))
    if job.salary_min or job.salary_max:
        lo = f"{job.salary_min:,}" if job.salary_min else ""
        hi = f"{job.salary_max:,}" if job.salary_max else ""
        rng = f"{lo}–{hi}" if lo and hi else lo or hi
        parts.append(f"💰 {rng} {job.salary_currency}")
    return "  ·  ".join(parts)


_ROLE_LABELS = {"crypto_ops": "Crypto Ops", "web3_support": "Web3 Support", "ai_automation": "AI Automation"}


def _prefilter_line(job: Job) -> str:
    if not job.match_role:
        return ""
    label = _ROLE_LABELS.get(job.match_role, job.match_role)
    reasons = "; ".join(job.match_reasons[:3])
    return f"🎯 <i>{_esc(label)} · {_esc(reasons)}</i>" if reasons else f"🎯 <i>{_esc(label)}</i>"


def format_job_message(job: Job, match: MatchResult) -> str:
    emoji = _score_emoji(match.score)
    meta = _meta_line(job)

    fits = "\n".join(f"· {_esc(r)}" for r in match.why_fits[:4])
    fits_block = f"✅ <b>Почему подходит</b>\n{fits}" if fits else ""

    watch = "\n".join(f"· {_esc(r)}" for r in match.watch_out[:2])
    watch_block = f"⚠️ <b>Учесть</b>\n{watch}" if watch else ""

    sections = [s for s in [fits_block, watch_block] if s]
    middle = f"\n\n{_DIV}\n\n".join(sections)

    rec = f"<i>💬 {_esc(match.recommendation)}</i>" if match.recommendation else ""
    prefilter = _prefilter_line(job)
    footer = f"<code>{match.score}/100</code>  ·  {_esc(job.source)}"

    blocks = [
        f"{emoji} <b>{_esc(job.title)}</b>\n{meta}",
        middle,
        rec,
        prefilter,
        footer,
    ]

    return f"\n{_DIV}\n\n".join(b for b in blocks if b).strip()


def format_daily_summary(count_parsed: int, count_sent: int, sources: list[str]) -> str:
    src_list = ", ".join(sources) if sources else "—"
    return (
        f"📊 <b>Job Hunter — итоги</b>\n\n"
        f"🔍 Просмотрено: <b>{count_parsed}</b>\n"
        f"📨 Отправлено: <b>{count_sent}</b>\n"
        f"📡 Источники: {src_list}"
    )
