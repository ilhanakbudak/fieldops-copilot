"""Selecting a generation provider."""

from __future__ import annotations

from app.config import Settings, get_settings
from app.llm.base import Completion, LlmProvider, Message, StreamEvent, Usage
from app.llm.mock import MockLlmProvider
from app.llm.openai import OpenAiProvider
from app.llm.pricing import cost_usd

__all__ = [
    "Completion",
    "LlmProvider",
    "Message",
    "MockLlmProvider",
    "OpenAiProvider",
    "StreamEvent",
    "Usage",
    "build_llm_provider",
    "cost_usd",
    "get_llm_provider",
    "reset_llm_provider",
]

_provider: LlmProvider | None = None


def build_llm_provider(settings: Settings) -> LlmProvider:
    if settings.llm_provider == "openai":
        return OpenAiProvider(settings)
    return MockLlmProvider()


def get_llm_provider() -> LlmProvider:
    global _provider
    if _provider is None:
        _provider = build_llm_provider(get_settings())
    return _provider


def reset_llm_provider() -> None:
    global _provider
    _provider = None
