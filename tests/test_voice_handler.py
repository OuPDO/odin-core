import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import telegram_bot.handlers as h


def test_handle_voice_transcribes_and_answers():
    update = MagicMock()
    update.effective_user.id = 8428283452
    update.effective_chat.id = 1
    tg_file = AsyncMock()
    update.message.voice.get_file = AsyncMock(return_value=tg_file)
    update.message.reply_text = AsyncMock()
    # Patch the class-level property (required for pydantic models — instance
    # attribute deletion is not supported for @property descriptors in pydantic V2).
    with patch.object(type(h.settings), "allowed_user_ids", new_callable=PropertyMock, return_value={8428283452}), \
         patch.object(h, "transcribe", return_value="welche OM-Projekte?"), \
         patch.object(h, "process_message", new=AsyncMock(return_value="Antwort")):
        asyncio.run(h.handle_voice(update, MagicMock()))
    update.message.voice.get_file.assert_awaited()
    update.message.reply_text.assert_awaited_with("Antwort")


def test_handle_voice_rejects_unauthorized_user():
    update = MagicMock()
    update.effective_user.id = 9999  # Non-allowed id
    update.message.reply_text = AsyncMock()
    with patch.object(type(h.settings), "allowed_user_ids", new_callable=PropertyMock, return_value={8428283452}), \
         patch.object(h, "transcribe") as mock_transcribe, \
         patch.object(h, "process_message") as mock_process:
        asyncio.run(h.handle_voice(update, MagicMock()))
    update.message.reply_text.assert_not_awaited()
    mock_transcribe.assert_not_called()
    mock_process.assert_not_called()
