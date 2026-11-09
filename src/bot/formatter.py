"""Форматирует вакансию + результат матчинга в Telegram HTML-сообщение."""
import re

from ..matcher.pre_filter import ai_tools_phrase, listing_warnings
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
    "support_fintech": "CSM / Внедрение / Поддержка (IT)",
    "sales_remote": "Продажи удалённо",
}


def _prefilter_line(job: Job) -> str:
    if not job.match_role:
        return ""
    label = _ROLE_LABELS.get(job.match_role, job.match_role)
    reasons = "; ".join(job.match_reasons[:3])
    return f"🎯 <i>{_esc(label)} · {_esc(reasons)}</i>" if reasons else f"🎯 <i>{_esc(label)}</i>"


# Только общие фразы об отсутствии зарплаты: конкретные условия и суммы
# нельзя удалять лишь потому, что они тоже относятся к оплате.
_UNKNOWN_PAY_CLAUSE = re.compile(
    r"(?:(?:в (?:объявлении|вакансии|описании) )?(?:не указан[аы]? "
    r"(?:зарплата|заработная плата|доход|оплата)|"
    r"(?:зарплата|заработная плата|доход|оплата|условия оплаты) "
    r"не (?:указан[аы]?|раскрыт[аы]?|подтвержд[её]н[аы]?)"
    r")(?: в (?:объявлении|вакансии|описании))?"
    r"|(?:доход|зарплату|условия оплаты|оплату) (?:необходимо |нужно |стоит )?уточнить"
    r"|(?:необходимо |нужно |стоит )?уточнить (?:доход|зарплату|условия оплаты|оплату)"
    r"|(?:требуется|необходимо|нужно) уточнение (?:дохода|зарплаты|условий оплаты|оплаты))",
    re.IGNORECASE,
)


_VARIABLE_PAY_CLAUSE = re.compile(
    r"(?:(?:комиссионные|премии|бонусы|проценты|kpi)"
    r"(?:\s*[/и+]\s*(?:комиссионные|премии|бонусы|проценты|kpi))*"
    r"\s+не гарантированы"
    r"|(?:необходимо |нужно |стоит )?уточнить (?:реальный размер дохода|"
    r"гарантированный оклад|итоговую оплату))",
    re.IGNORECASE,
)


_VARIABLE_PAY_TOPIC = re.compile(
    r"kpi|преми\w*|бонус\w*|комиссион\w*|процент\w*|переменн\w*\s+част\w*",
    re.IGNORECASE,
)
_PAY_DETAIL = re.compile(
    r"\d|испытательн|ежемесяч|ежекварталь|кварталь|ежегод|еженедель|"
    r"выпла\w*|начисл\w*|удерж\w*|после\s|при\s|за\s|срок\w*",
    re.IGNORECASE,
)


def _without_variable_pay_repetition(note: str) -> str:
    """Убирать общую мысль об оплате, сохраняя суммы, условия и соседние темы."""
    clauses = re.split(r"\s*[;,.!:–—]\s*|\s+и\s+(?=возможност)", note)
    remaining = [c for c in clauses if c and not (
        _VARIABLE_PAY_CLAUSE.fullmatch(c)
        or (_VARIABLE_PAY_TOPIC.search(c) and not _PAY_DETAIL.search(c))
    )]
    if len(remaining) == len([c for c in clauses if c]):
        return note
    return "; ".join(remaining)


_MISSING_INFORMATION = re.compile(
    r"не\s+(?:указан\w*|уточн[её]н\w*|раскрыт\w*|подтвержд[её]н\w*|описан\w*)"
    r"|нет\s+(?:информации|данных|сведений)"
    r"|(?:информация|данные|сведения)\s+отсутству\w*",
    re.IGNORECASE,
)


def _without_missing_information(note: str) -> str:
    """Не показывать заполнители об отсутствующих данных, в том числе из кэша."""
    clauses = re.split(r"\s*[;,.!:–—]\s*", note)
    remaining = [c for c in clauses if c and not (
        _MISSING_INFORMATION.search(c) or _UNKNOWN_PAY_CLAUSE.fullmatch(c)
        or re.fullmatch(r"(?:требуется|необходимо|нужно) уточнение", c, re.I)
    )]
    return "; ".join(remaining) if len(remaining) != len([c for c in clauses if c]) else note


def _watch_items(job: Job, warnings: list[str]) -> list[str]:
    items = listing_warnings(job)
    unknown_pay = not job.salary_min and not job.salary_max
    variable_pay = any("Есть KPI/премии/проценты" in item for item in items)
    seen = {" ".join(item.casefold().split()).rstrip(".") for item in items}
    for note in warnings[:2]:
        note = _without_missing_information(note)
        if variable_pay:
            note = _without_variable_pay_repetition(note)
        # Удаляем только повторяющиеся части; например, офис в Дубае
        # в том же пункте остаётся самостоятельным полезным замечанием.
        if unknown_pay or variable_pay:
            clauses = re.split(r"\s*[;,.!:–—]\s*", note)
            remaining = [c for c in clauses if c and not (
                (unknown_pay and _UNKNOWN_PAY_CLAUSE.fullmatch(c))
                or (variable_pay and _VARIABLE_PAY_CLAUSE.fullmatch(c))
            )]
            if len(remaining) != len([c for c in clauses if c]):
                note = "; ".join(remaining)
        key = " ".join(note.casefold().split()).rstrip(".")
        if key and key not in seen:
            items.append(note)
            seen.add(key)
    return items


def format_job_message(job: Job, match: MatchResult) -> str:
    emoji = _score_emoji(match.score)
    meta = _meta_line(job)
    mode = _work_mode_lines(job)
    if mode:
        meta = f"{meta}\n{mode}"

    variable_pay = any("Есть KPI/премии/проценты" in item
                       for item in listing_warnings(job))
    fit_notes = [_without_missing_information(note) for note in match.why_fits[:4]]
    if variable_pay:
        fit_notes = [_without_variable_pay_repetition(note) for note in fit_notes]
    fits = "\n".join(f"· {_esc(note)}" for note in fit_notes if note)
    fits_block = f"✅ <b>Почему подходит</b>\n{fits}" if fits else ""

    # Обязательные пометки не должны зависеть от дисциплины LLM или теряться
    # за первыми двумя пунктами ответа (решения владельца 03.11.2026).
    watch_items = _watch_items(job, match.watch_out)
    watch = "\n".join(f"· {_esc(r)}" for r in watch_items)
    watch_block = f"⚠️ <b>Учесть</b>\n{watch}" if watch else ""

    sections = [s for s in [fits_block, watch_block] if s]
    middle = f"\n\n{_DIV}\n\n".join(sections)

    recommendation = _without_missing_information(match.recommendation)
    if variable_pay:
        recommendation = _without_variable_pay_repetition(recommendation)
    rec = f"<i>💬 {_esc(recommendation)}</i>" if recommendation else ""
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
