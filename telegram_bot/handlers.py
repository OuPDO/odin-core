import logging

from telegram import Update
from telegram.ext import ContextTypes

from agents.master import process_message

logger = logging.getLogger("odin.telegram.handlers")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    await update.message.reply_text(
        "Moin! Ich bin ODIN, dein AI Operations System.\n\n"
        "Schreib mir einfach was du brauchst — ich route automatisch "
        "zur richtigen Organisation (OM, ADO oder DO).\n\n"
        "Commands:\n"
        "/status — System-Status anzeigen\n"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command."""
    await update.message.reply_text(
        "ODIN Status:\n"
        "- Core: Running\n"
        "- Master Router: Active\n"
        "- OM-Ops: Standby\n"
        "- ADO-Ops: Standby\n"
        "- DO-Personal: Standby\n"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route incoming messages through ODIN Master."""
    user_message = update.message.text
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    logger.info(
        "Nachricht von User %s in Chat %s: %s",
        user_id,
        chat_id,
        user_message[:100],
    )

    await update.message.chat.send_action("typing")

    try:
        response = await process_message(
            message=user_message,
            user_id=str(user_id),
            chat_id=str(chat_id),
        )
        await update.message.reply_text(response)
    except Exception:
        logger.exception("Fehler bei Nachrichtenverarbeitung")
        await update.message.reply_text(
            "Da ist etwas schiefgelaufen. Ich schaue mir das an."
        )
