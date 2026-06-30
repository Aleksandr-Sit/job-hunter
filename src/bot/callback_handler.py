"""Обрабатывает нажатия inline-кнопок Telegram (сейчас — только "Пропустить")."""
import logging
import os

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

logger = logging.getLogger(__name__)


async def _on_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer(text="Пропущено")
    await query.delete_message()


def run_listener() -> None:
    """Блокирующий polling-цикл. Вызывать в отдельном потоке (не в главном)."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(CallbackQueryHandler(_on_skip, pattern=r"^skip_"))
    logger.info("Telegram callback listener started")
    app.run_polling(stop_signals=None, close_loop=False)
