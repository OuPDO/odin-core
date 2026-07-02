import asyncio
import logging
import os
import tempfile

from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from agents.master import process_message
from telegram_bot.transcribe import transcribe

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


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ask command — direct knowledge search without intent routing."""
    if update.effective_user.id not in settings.allowed_user_ids:
        return
    query = update.message.text.partition(" ")[2].strip()
    if not query:
        await update.message.reply_text("Nutzung: /ask <deine Frage>")
        return
    await update.message.chat.send_action("typing")
    from knowledge.search import knowledge_search
    try:
        answer = await asyncio.to_thread(knowledge_search, query)
        await update.message.reply_text(answer)
    except Exception:
        logger.exception("Fehler bei Nachrichtenverarbeitung")
        await update.message.reply_text(
            "Da ist etwas schiefgelaufen. Ich schaue mir das an."
        )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voice messages: download, transcribe, route through ODIN Master."""
    if update.effective_user.id not in settings.allowed_user_ids:
        return
    path = None
    try:
        tg_file = await update.message.voice.get_file()
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            path = tmp.name
        await tg_file.download_to_drive(path)
        text = transcribe(path)
        answer = await process_message(text, update.effective_user.id, update.effective_chat.id)
        await update.message.reply_text(answer)
    except Exception:
        logger.exception("Fehler bei Sprachnotiz-Verarbeitung")
        await update.message.reply_text(
            "Konnte die Sprachnotiz nicht verstehen, bitte nochmal."
        )
    finally:
        if path and os.path.exists(path):
            os.remove(path)


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
