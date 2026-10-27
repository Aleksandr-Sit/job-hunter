"""Форматирует вакансию + результат матчинга в Telegram HTML-сообщение."""
import re

from ..matcher.pre_filter import ai_tools_phrase
from ..models import Job, MatchResult, salary_range
from ..parsers.hh_enrich import schedule_summary

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
    rng = salary_range(job.salary_min, job.salary_max)
    if rng:
        # gross/net знает только страница hh (hh_enrich._compensation)
        tax = {True: " до вычета", False: " на руки"}.get(job.raw.get("salary_gross"), "")
        parts.append(f"💰 {rng} {job.salary_currency}{tax}")
    return "  ·  ".join(parts)


# Фраза-доказательство длиннее — режем: карточка не место для абзаца
_AI_PHRASE_MAX = 160


def _work_mode_lines(job: Job) -> str:
    """График по полям hh и пометка про ИИ-инструменты — под строкой компании.

    График (26.10.2026): владелец откликнулся в OsteoFace, не зная, что там 6/1, —
    поле hh было, а в тексте и в карточке графика не было. ИИ-инструменты — цель
    владельца расти в ИИ; фраза показывает, на чём основана пометка."""
    lines = []
    sched = schedule_summary(job.description)
    if sched:
        lines.append(f"🗓 График (hh): {_esc(sched)}")
    phrase = ai_tools_phrase(job.description)
    if phrase:
        if len(phrase) > _AI_PHRASE_MAX:
            phrase = phrase[:_AI_PHRASE_MAX].rsplit(" ", 1)[0] + "…"
        lines.append(f"🤖 Ценят ИИ-инструменты: «{_esc(phrase)}»")
    return "\n".join(lines)


# Подписи ролей для карточки. Ролей в criteria.yaml семь; подписи были только у
# трёх, и остальные показывались сырым ключом («aml_compliance») — ревизия 17.09.2026.
_ROLE_LABELS = {
    "crypto_ops": "Crypto Ops",
    "web3_support": "Web3 Support",
    "ai_automation": "AI Automation",
    "qa_web3": "Web3 QA",
    "aml_compliance": "AML / Комплаенс",
    "support_fintech": "Поддержка (финтех/IT)",
    "sales_remote": "Продажи удалённо",
}


def _prefilter_line(job: Job) -> str:
    if not job.match_role:
        return ""
    label = _ROLE_LABELS.get(job.match_role, job.match_role)
    reasons = "; ".join(job.match_reasons[:3])
    return f"🎯 <i>{_esc(label)} · {_esc(reasons)}</i>" if reasons else f"🎯 <i>{_esc(label)}</i>"


def format_job_message(job: Job, match: MatchResult) -> str:
    emoji = _score_emoji(match.score)
    meta = _meta_line(job)
    mode = _work_mode_lines(job)
    if mode:
        meta = f"{meta}\n{mode}"

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
