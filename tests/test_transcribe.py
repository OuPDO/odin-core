from unittest.mock import MagicMock, patch
import telegram_bot.transcribe as tr


def test_transcribe_returns_text(tmp_path):
    audio = tmp_path / "v.ogg"
    audio.write_bytes(b"x")
    client = MagicMock()
    client.audio.transcriptions.create.return_value = MagicMock(
        text="welche projekte habe ich"
    )
    with patch.object(tr, "AzureOpenAI", return_value=client):
        out = tr.transcribe(str(audio))
    assert out == "welche projekte habe ich"
    _, kwargs = client.audio.transcriptions.create.call_args
    assert kwargs["language"] == "de"
