from functools import lru_cache

from langchain_openai import AzureOpenAIEmbeddings

from config.settings import settings


@lru_cache(maxsize=1)
def get_embeddings() -> AzureOpenAIEmbeddings:
    return AzureOpenAIEmbeddings(
        azure_deployment=settings.azure_embedding_deployment,
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_embedding_api_version,
    )
