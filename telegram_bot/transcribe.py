from openai import AzureOpenAI

from config.settings import settings


def transcribe(audio_path: str) -> str:
    client = AzureOpenAI(
        api_key=settings.azure_openai_api_key,
        azure_endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_transcribe_api_version,
    )
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model=settings.azure_transcribe_deployment,
            file=f,
            language="de",
        )
    return result.text
