"""Email intent classifier — one cheap LLM call, never blocks ingestion.

classify_email(subject, body) -> {intent, urgency, summary, symptom}:
    intent  ∈ angry_customer | inquiry | spam | other
    urgency ∈ low | medium | high
    summary   one-line customer-facing summary
    symptom   the operational symptom string the graph investigates

Pipeline: with_structured_output(_IntentFields) via llm.get_llm() (temperature
0) + llm.ainvoke_with_retry (transient retry + model fallback). If the
structured call fails, ONE plain-text retry asks for the 4 fields as JSON;
if that fails too, a SAFE DEFAULT (other/low, summary from subject) is
returned — a classifier failure must never block email ingestion.

# ---------------------------------------------------------------------------
# FAILURE MODES (each handled explicitly):
# 1. LLM transient overload / auth failure on the structured call — retried
#    once via a plain-text JSON prompt (the "1 retry on validation failure"),
#    then the safe default; never raised.
# 2. Model returns non-JSON or a JSON that fails _IntentFields validation on
#    the retry — caught, safe default returned.
# 3. get_llm()/with_structured_output construction problems (missing key etc.)
#    — caught by the same outer try, safe default returned.
# ---------------------------------------------------------------------------
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from typing import Literal

from llm import ainvoke_with_retry, get_llm

_SYSTEM_PROMPT = (
    "You classify customer emails for a B2B logistics operations desk. "
    "Respond with exactly four fields. intent: 'angry_customer' when the "
    "customer is frustrated, cancelling, or escalating; 'inquiry' for neutral "
    "questions; 'spam' for promotional or scam content; 'other' otherwise. "
    "urgency: 'low', 'medium', or 'high'. summary: one sentence (no newlines) "
    "capturing the ask. symptom: one short operational symptom phrase, e.g. "
    "'shipment stuck', 'missing documents'."
)


class _IntentFields(BaseModel):
    intent: Literal["angry_customer", "inquiry", "spam", "other"]
    urgency: Literal["low", "medium", "high"]
    summary: str = Field(description="one line, no newlines")
    symptom: str


def _safe_default(subject: str, body: str) -> dict:
    summary = subject.strip() or "no subject"
    symptom = f"{subject} {body}".strip()[:200]
    return {"intent": "other", "urgency": "low", "summary": summary, "symptom": symptom}


def _extract_json(text: str) -> dict:
    """Parse JSON possibly wrapped in markdown fences or stray prose."""
    cleaned = text.strip()
    if "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model reply")
    return json.loads(cleaned[start : end + 1])


async def classify_email(subject: str, body: str) -> dict:
    """Classify one email; NEVER raises — safe default on total failure."""
    llm = get_llm()
    messages = [
        SystemMessage(_SYSTEM_PROMPT),
        HumanMessage(f"Subject: {subject}\n\nBody: {body}"),
    ]
    try:
        structured = llm.with_structured_output(_IntentFields)
        result = await ainvoke_with_retry(structured, messages)
        return result.model_dump()
    except Exception:  # noqa: BLE001 — structured path failed; one JSON retry
        try:
            retry_messages = messages + [
                HumanMessage(
                    'Reply with ONLY valid JSON: {"intent": "<angry_customer|'
                    'inquiry|spam|other>", "urgency": "<low|medium|high>", '
                    '"summary": "<one line>", "symptom": "<short phrase>"}'
                )
            ]
            reply = await ainvoke_with_retry(llm, retry_messages)
            fields = _IntentFields(**_extract_json(reply.content))
            return fields.model_dump()
        except Exception:  # noqa: BLE001 — never block ingestion
            return _safe_default(subject, body)
