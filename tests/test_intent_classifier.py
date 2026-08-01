"""P6.5 intent classifier tests — 3 real LLM calls (angry, neutral, spam).

Skipped when no LLM key is configured (same guard as tests/test_e2e.py).
"""

import os

import pytest

from ingest.intent_classifier import classify_email

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("LLM_API_KEY")
    ),
    reason="no LLM API key configured",
)


def _assert_valid(fields: dict):
    assert fields["intent"] in {"angry_customer", "inquiry", "spam", "other"}
    assert fields["urgency"] in {"low", "medium", "high"}
    assert isinstance(fields["summary"], str) and fields["summary"].strip()
    assert isinstance(fields["symptom"], str) and fields["symptom"].strip()


async def test_classify_angry_customer_email():
    fields = await classify_email(
        "URGENT: order #402 stuck!!",
        "My order #402 has not moved in 6 days. The buyer is cancelling the "
        "contract and I will lose the deal. Monday market deadline is gone.",
    )
    _assert_valid(fields)
    assert fields["intent"] == "angry_customer"
    assert fields["urgency"] in {"high", "medium"}


async def test_classify_neutral_inquiry_email():
    fields = await classify_email(
        "order status",
        "Hi, could you tell me when order 402 will ship? No rush.",
    )
    _assert_valid(fields)
    assert fields["intent"] == "inquiry"
    assert fields["urgency"] == "low"


async def test_classify_spam_email():
    fields = await classify_email(
        "WINNER WINNER",
        "Congratulations! You have been selected for a free iPhone. Click "
        "this link now to claim your prize before midnight.",
    )
    _assert_valid(fields)
    assert fields["intent"] == "spam"
