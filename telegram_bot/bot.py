import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import settings
from telegram_bot.handlers import handle_message, cmd_start, cmd_status

logger = logging.getLogger("odin.telegram")


def auth_filter() -> filters.BaseFilter:
    """Only allow messages from David's Telegram user IDs."""
    allowed = settings.allowed_user_ids
    if not allowed:
        logger.warning("ODIN_ALLOWED_USERS nicht gesetzt — alle User erlaubt!")
        return filters.ALL
    return filters.User(user_id=allowed)


def create_bot() -> Application:
    """Create and configure the Telegram bot application."""
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )

    user_filter = auth_filter()

    app.add_handler(CommandHandler("start", cmd_start, filters=user_filter))
    app.add_handler(CommandHandler("status", cmd_status, filters=user_filter))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & user_filter, handle_message)
    )

    logger.info("Telegram Bot konfiguriert. Erlaubte User: %s", settings.allowed_user_ids)
    return app
