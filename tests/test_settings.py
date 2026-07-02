from config.settings import settings


def test_azure_settings_present():
    assert settings.azure_embedding_dim == 1536
    assert settings.azure_chat_deployment == "gpt-5-mini"
    assert settings.azure_transcribe_deployment == "whisper"
