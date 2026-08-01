"""LLM factory — the single place models are created.

Reads env (optionally from .env):
    MODEL_PROVIDER     provider name (informational; currently "deepseek" | "openai")
    MODEL_NAME         model id (default "deepseek-v4-flash")
    MODEL_BASE_URL     OpenAI-compatible base URL (default https://api.deepseek.com)
    DEEPSEEK_API_KEY   primary key (fallbacks: OPENAI_API_KEY, LLM_API_KEY)

LangSmith tracing is enabled automatically when LANGSMITH_API_KEY is set.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

_DEFAULT_BASE_URL = "https://api.deepseek.com"
_DEFAULT_MODEL = "deepseek-v4-flash"


def _api_key() -> str | None:
    return (
        os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("LLM_API_KEY")
    )


def get_llm(temperature: float = 0.0) -> ChatOpenAI:
    """Return a ChatOpenAI pointed at the configured provider.

    Deterministic (temperature 0) by default — investigations must be
    reproducible. Never raises: a missing key surfaces as a normal
    LangChain/LangSmith authentication error at call time.
    """
    if os.environ.get("LANGSMITH_API_KEY"):
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")

    kwargs: dict = {
        "model": os.environ.get("MODEL_NAME", _DEFAULT_MODEL),
        "base_url": os.environ.get("MODEL_BASE_URL", _DEFAULT_BASE_URL),
        "temperature": temperature,
        "timeout": 60,
        "max_retries": 1,
    }
    # Placeholder keeps construction side-effect-free: with no key configured
    # the server still boots and the auth failure surfaces at call time,
    # where the resilience layer turns it into a failure dict (P12 drill #2).
    kwargs["api_key"] = _api_key() or "sk-not-configured"
    return ChatOpenAI(**kwargs)
