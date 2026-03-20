"""LLM Provider Abstraction — easily swap between OpenRouter, Anthropic, Azure, Vertex."""

import logging
from functools import lru_cache

from langchain_core.language_models import BaseChatModel

from config.settings import settings

logger = logging.getLogger("odin.config.llm")


def _create_openrouter_model(model: str, max_tokens: int) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        max_tokens=max_tokens,
        default_headers={
            "HTTP-Referer": "https://odin.dobla.biz",
            "X-Title": "ODIN",
        },
    )


def _create_anthropic_model(model: str, max_tokens: int) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    # Strip provider prefix if present (e.g., "anthropic/claude-sonnet-4-20250514" -> "claude-sonnet-4-20250514")
    if "/" in model:
        model = model.split("/", 1)[1]

    return ChatAnthropic(
        model=model,
        api_key=settings.llm_api_key,
        max_tokens=max_tokens,
    )


def _create_azure_model(model: str, max_tokens: int) -> BaseChatModel:
    from langchain_openai import AzureChatOpenAI

    return AzureChatOpenAI(
        model=model,
        api_key=settings.llm_api_key,
        azure_endpoint=settings.llm_base_url,
        max_tokens=max_tokens,
        api_version="2024-12-01-preview",
    )


_PROVIDERS = {
    "openrouter": _create_openrouter_model,
    "anthropic": _create_anthropic_model,
    "azure": _create_azure_model,
}


def get_llm(role: str = "default", max_tokens: int = 1024) -> BaseChatModel:
    """Get an LLM instance for the given role.

    Args:
        role: "router" (fast/cheap), "default" (standard), "analysis" (best)
        max_tokens: Maximum response tokens

    Returns:
        A LangChain chat model ready to use
    """
    model_map = {
        "router": settings.llm_model_router,
        "default": settings.llm_model_default,
        "analysis": settings.llm_model_analysis,
    }
    model = model_map.get(role, settings.llm_model_default)

    provider = settings.llm_provider
    factory = _PROVIDERS.get(provider)
    if not factory:
        raise ValueError(
            f"Unbekannter LLM Provider: {provider}. "
            f"Verfuegbar: {', '.join(_PROVIDERS.keys())}"
        )

    logger.debug("LLM erstellt: provider=%s, model=%s, role=%s", provider, model, role)
    return factory(model, max_tokens)
