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


async def log_all_updates(update: Update, context) -> None:
    """Log every incoming update for debugging."""
    user = update.effective_user
    if user:
        logger.info(
            "UPDATE empfangen: user_id=%s username=%s text='%s'",
            user.id,
            user.username,
            (update.message.text[:80] if update.message and update.message.text else "---"),
        )


def auth_filter() -> filters.BaseFilter:
    allowed = settings.allowed_user_ids
    if not allowed:
        logger.warning("ODIN_ALLOWED_USERS nicht gesetzt — alle User erlaubt!")
        return filters.ALL
    return filters.User(user_id=allowed)


def create_bot() -> Application:
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )

    user_filter = auth_filter()

    # Debug: log ALL incoming messages (group -1 = runs before other handlers)
    app.add_handler(MessageHandler(filters.ALL, log_all_updates), group=-1)

    app.add_handler(CommandHandler("start", cmd_start, filters=user_filter))
    app.add_handler(CommandHandler("status", cmd_status, filters=user_filter))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & user_filter, handle_message)
    )

    logger.info("Telegram Bot konfiguriert. Erlaubte User: %s", settings.allowed_user_ids)
    return app
