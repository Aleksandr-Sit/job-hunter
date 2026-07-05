"""Обрабатывает нажатия inline-кнопок Telegram (сейчас — только "Пропустить")."""
import logging
import os

import telegram
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

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


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Callback listener error: %s", context.error)


def run_listener() -> None:
    """Блокирующий polling-цикл. Вызывать в отдельном потоке (не в главном)."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(CallbackQueryHandler(_on_skip, pattern=r"^skip_"))
    app.add_error_handler(_on_error)
    logger.info("Telegram callback listener started")
    app.run_polling(stop_signals=None, close_loop=False)
