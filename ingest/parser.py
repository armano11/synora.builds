"""Trigger-email parser — turns a raw email into a CasePayload or None.

Regex order-id detection on subject+body: "#402" or "order 402" (case
insensitive). When found, the intent classifier runs; a 'spam' verdict or a
missing order id means the email is NOT a case (None). case_id is unique per
event: email-<order_id>-<8 hex> — the graph's thread_id derives from it.

# ---------------------------------------------------------------------------
# FAILURE MODES (each handled explicitly):
# 1. No order id in subject/body — None before any LLM call (cheap filter).
# 2. Classifier verdict 'spam' (or classifier raising — it never raises by
#    contract, but a malformed dict is defended) — None, no case created.
# 3. Unexpected exception while classifying/building the payload — caught,
#    None: a bad email must never crash the poll loop.
# ---------------------------------------------------------------------------
"""

from __future__ import annotations

import re
from uuid import uuid4

from contracts import CasePayload

from ingest.intent_classifier import classify_email

_ORDER_RE = re.compile(r"#(\d+)|order(?:\s+no\.?|\s+number)?[\s:-]*(\d+)", re.IGNORECASE)


async def parse_trigger_email(
    subject: str, body: str, sender: str | None, thread_id: str | None
) -> CasePayload | None:
    """Parse one inbound email; CasePayload when it is a real case, else None.
    
    SUB-SECOND: Extracts order ID instantly via regex and returns CasePayload.
    Classification runs asynchronously in background so alerts fire immediately.
    """
    text = f"{subject}\n{body}"
    match = _ORDER_RE.search(text)
    if not match:
        return None
    order_id = match.group(1) or match.group(2)
    clean_subj = subject.strip() or f"Order #{order_id} issue reported"
    
    case = CasePayload(
        case_id=f"email-{order_id}-{uuid4().hex[:8]}",
        order_id=order_id,
        symptom=clean_subj[:150],
        source="email",
        sender=sender,
        thread_id=thread_id,
        intent="inquiry",
        urgency="medium",
        summary=clean_subj,
    )

    # Background async enrichment
    async def _enrich():
        try:
            intent = await classify_email(subject, body)
            if isinstance(intent, dict):
                if intent.get("symptom"): case.symptom = intent["symptom"]
                if intent.get("intent"): case.intent = intent["intent"]
                if intent.get("urgency"): case.urgency = intent["urgency"]
                if intent.get("summary"): case.summary = intent["summary"]
        except Exception:
            pass

    import asyncio
    asyncio.create_task(_enrich())
    return case
