"""Обрабатывает нажатия inline-кнопок Telegram и команды трекера откликов."""
import asyncio
import html
import logging
import os
from datetime import datetime, timezone

import telegram
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from .. import storage

logger = logging.getLogger(__name__)


async def _on_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer(text="Пропущено")
    # Сначала пытаемся удалить сообщение целиком (чистит ленту). Telegram запрещает
    # ботам удалять сообщения старше 48ч → на BadRequest откатываемся на снятие
    # клавиатуры, чтобы старые карточки хотя бы теряли кнопки без ошибки.
    try:
        await query.delete_message()
    except telegram.error.BadRequest:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except telegram.error.BadRequest as e:
            logger.debug("Skip: ни удалить, ни снять клавиатуру (%s)", e)


def _parse_card(text: str) -> tuple[str, str, int | None]:
    """Достаёт заголовок/компанию/балл из текста карточки (для записи в трекер).
    Формат карточки задан в formatter.py; при изменении — деградирует мягко."""
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    title = lines[0].lstrip("🎯 ").strip() if lines else ""
    company = ""
    if len(lines) > 1:
        company = lines[1].split("·")[0].strip()
    score = None
    for ln in reversed(lines):
        if "/100" in ln:
            digits = ln.split("/100")[0].strip().split()[-1]
            if digits.isdigit():
                score = int(digits)
            break
    return title[:200], company[:120], score


async def _on_applied(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отмечает отклик: пишет в трекер и помечает карточку, кнопки убирает."""
    query = update.callback_query
    job_id = query.data[len("applied_"):]
    msg = query.message
    title, company, score = _parse_card(msg.text if msg else "")
    url = ""
    if msg and msg.reply_markup:
        for row in msg.reply_markup.inline_keyboard:
            for btn in row:
                if btn.url:
                    url = btn.url
                    break

    try:
        is_new = storage.save_application(job_id, title, company, url, score)
    except Exception as e:  # БД не должна ронять обработчик
        logger.error("Не удалось записать отклик %s: %s", job_id, e)
        await query.answer(text="Ошибка записи, попробуй ещё раз")
        return

    await query.answer(text="Записал отклик ✅" if is_new else "Уже отмечено ранее")
    stamp = datetime.now(timezone.utc).strftime("%d.%m")
    try:
        await query.edit_message_text(
            text=f"{msg.text_html}\n\n✅ <b>Откликнулся · {stamp}</b>",
            parse_mode="HTML",
            reply_markup=None,
            disable_web_page_preview=True,
        )
    except telegram.error.BadRequest as e:
        logger.debug("Applied: не удалось обновить карточку (%s)", e)


async def _cmd_applications(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/applications — список откликов и сводка."""
    try:
        rows = storage.list_applications(limit=15)
        stats = storage.application_stats()
    except Exception as e:
        logger.error("Ошибка чтения откликов: %s", e)
        await update.message.reply_text("Не удалось прочитать трекер откликов.")
        return

    if not rows:
        await update.message.reply_text(
            "Откликов пока нет.\nНажимай «✅ Откликнулся» в карточке вакансии — "
            "и они появятся здесь."
        )
        return

    lines = [
        f"📋 <b>Отклики</b> — всего {stats['total']}, "
        f"за 7 дней {stats['last7']}",
    ]
    if stats["stale"]:
        lines.append(
            f"⏳ Без ответа дольше {stats['stale_days']} дней: <b>{stats['stale']}</b>"
        )
    lines.append("──────────────────────")
    for r in rows:
        date = (r["applied_at"] or "")[:10]
        title = html.escape(r["title"] or "без названия")
        company = html.escape(r["company"] or "")
        score = f" · {r['score']}/100" if r["score"] is not None else ""
        head = f'<a href="{r["url"]}">{title}</a>' if r["url"] else title
        lines.append(f"· {date} — {head}{(' — ' + company) if company else ''}{score}")

    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML", disable_web_page_preview=True
    )


async def _on_letter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Генерирует сопроводительное под вакансию и присылает ответом на карточку.

    Смысл: написание письма — главный источник трения при отклике (OFFER_PLAN).
    Готовый черновик превращает отклик из получаса в пять минут.
    """
    query = update.callback_query
    job_id = query.data[len("letter_"):]
    msg = query.message
    title, company, _ = _parse_card(msg.text if msg else "")
    await query.answer(text="Пишу письмо…")

    cached = None
    try:
        cached = storage.get_cached_match(job_id)
    except Exception as e:
        logger.warning("Письмо: не удалось прочитать кэш матча %s: %s", job_id, e)

    why = cached.why_fits if cached else []
    rec = cached.recommendation if cached else ""

    # LLM-вызов синхронный — уводим в поток, чтобы не блокировать polling.
    from ..matcher.cerebras_matcher import generate_cover_letter
    letter = await asyncio.to_thread(generate_cover_letter, title, company, why, rec)

    if not letter:
        await msg.reply_text(
            "Не удалось сгенерировать письмо (лимит или сбой API). "
            "Попробуй ещё раз через минуту.",
            reply_to_message_id=msg.message_id,
        )
        return

    await msg.reply_text(
        f"✉️ <b>Черновик письма</b> — {html.escape(title[:60])}\n"
        f"<i>Проверь факты и отправь. Правь смело — это черновик, не финал.</i>\n\n"
        f"<blockquote>{html.escape(letter)}</blockquote>",
        parse_mode="HTML",
        reply_to_message_id=msg.message_id,
        disable_web_page_preview=True,
    )


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Callback listener error: %s", context.error)


def run_listener() -> None:
    """Блокирующий polling-цикл. Вызывать в отдельном потоке (не в главном)."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(CallbackQueryHandler(_on_skip, pattern=r"^skip_"))
    app.add_handler(CallbackQueryHandler(_on_applied, pattern=r"^applied_"))
    app.add_handler(CallbackQueryHandler(_on_letter, pattern=r"^letter_"))
    app.add_handler(CommandHandler("applications", _cmd_applications))
    app.add_error_handler(_on_error)
    logger.info("Telegram callback listener started")
    app.run_polling(stop_signals=None, close_loop=False)
