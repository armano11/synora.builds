"""LLM factory — the single place models are created.

Reads env (optionally from .env):
    MODEL_PROVIDER          provider name (informational)
    MODEL_NAME              model id (default "deepseek-ai/deepseek-v4-flash")
    MODEL_BASE_URL          OpenAI-compatible base URL (default NVIDIA NIM)
    MODEL_FALLBACK_NAME     automatic fallback when primary is throttled/529
    DEEPSEEK_API_KEY        primary key (fallbacks: OPENAI_API_KEY, LLM_API_KEY)

LangSmith tracing is enabled automatically when LANGSMITH_API_KEY is set.

Resilience (P12 drill #2): when the primary model returns a transient
error (529 overloaded, 5xx), the call transparently retries on the
fallback model — the demo never dies on one provider's hiccup.
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
_DEFAULT_MODEL = "deepseek-ai/deepseek-v4-flash"
_DEFAULT_FALLBACK_MODEL = "mistralai/mistral-medium-3.5-128b"


def _api_key() -> str | None:
    return (
        os.environ.get("MISTRAL_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("LLM_API_KEY")
    )


def _make_chat(model: str, base_url: str, key: str | None, temperature: float) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        base_url=base_url,
        # Placeholder keeps construction side-effect-free: with no key configured
        # the server still boots and the auth failure surfaces at call time,
        # where the resilience layer turns it into a failure dict (P12 drill #2).
        api_key=key or "sk-not-configured",
        temperature=temperature,
        timeout=60,
        max_retries=1,
    )


def get_llm(temperature: float = 0.0) -> ChatOpenAI:
    """Return a ChatOpenAI pointed at the configured provider.

    Deterministic (temperature 0) by default — investigations must be
    reproducible. Returns the primary model wrapped with an automatic
    fallback model for transient overload. Never raises at construction.
    """
    if os.environ.get("LANGSMITH_API_KEY"):
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")

    key = _api_key()
    base_url = os.environ.get("MODEL_BASE_URL", _DEFAULT_BASE_URL)
    primary = _make_chat(
        os.environ.get("MODEL_NAME", _DEFAULT_MODEL), base_url, key, temperature
    )
    fallback = _make_chat(
        os.environ.get("MODEL_FALLBACK_NAME", _DEFAULT_FALLBACK_MODEL),
        base_url,
        key,
        temperature,
    )
    return primary.with_fallbacks([fallback])


def _is_transient(exc: Exception) -> bool:
    """Hangs, 429/529 overloaded, 5xx, connection errors are retryable."""
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return (
        "timeout" in name
        or "429" in message
        or "529" in message
        or "internal" in name
        or "overloaded" in message
    )


# The free tier throttles concurrent calls (429 storms under Send() fan-out);
# a global cap keeps the demo alive without changing the graph topology.
_LLM_SEMAPHORE = asyncio.Semaphore(2)

# Hard per-call ceiling. The OpenAI client's own timeout is NOT reliable on a
# congested provider — a hung call would otherwise stall the graph forever
# (NIM free tier: 40-60s common, multi-minute hangs under load). An asyncio
# backstop converts hangs into retries with visible progress.
_CALL_TIMEOUT_S = 120.0


async def ainvoke_with_retry(runnable, messages: list, attempts: int = 5, backoff_s: float = 1.5):
    """Async invoke with exponential backoff on transient failures.

    Transient = hangs, 429/529 overloaded, 5xx, connection errors.
    Deterministic failures (auth, bad schema) surface immediately. Never
    used to mask real errors — the final attempt's exception still
    propagates (as asyncio.TimeoutError when the provider stalls).
    """
    async with _LLM_SEMAPHORE:
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                return await asyncio.wait_for(
                    runnable.ainvoke(messages), timeout=_CALL_TIMEOUT_S
                )
            except Exception as exc:  # noqa: BLE001
                last = exc
                if not _is_transient(exc) or attempt == attempts - 1:
                    raise
                await asyncio.sleep(backoff_s * (2**attempt))
        raise last  # type: ignore[misc]
