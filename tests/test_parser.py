"""P6.5 parser tests — 6 sample emails, classifier monkeypatched (async API).

parse_trigger_email(subject, body, sender, thread_id) -> CasePayload | None;
it calls ingest.intent_classifier.classify_email (async) — faked here so the
parser is tested without the LLM.
"""

import pytest

from ingest import parser


class _FakeClassifier:
    def __init__(self, result: dict):
        self._result = result
        self.calls = 0

    async def __call__(self, subject: str, body: str) -> dict:
        self.calls += 1
        return dict(self._result)


TRIGGER_OK = {
    "intent": "angry_customer",
    "urgency": "high",
    "summary": "customer furious about stuck order",
    "symptom": "shipment stuck at Hubli for 6 days",
}
INQUIRY_OK = {
    "intent": "inquiry",
    "urgency": "low",
    "summary": "where is my order",
    "symptom": "order not arrived",
}


@pytest.fixture
def fake_classifier(monkeypatch):
    def _install(result):
        fake = _FakeClassifier(result)
        monkeypatch.setattr(parser, "classify_email", fake)
        return fake

    return _install


async def test_trigger_email_with_hash_order_id_parses_to_case(fake_classifier):
    fake = fake_classifier(TRIGGER_OK)
    case = await parser.parse_trigger_email(
        "URGENT: order #402 stuck!!",
        "My order #402 has not moved since last week. The buyer is cancelling.",
        "priya@textiles.in",
        "thread-1",
    )
    assert case is not None
    assert case.order_id == "402"
    assert case.source == "email"
    assert case.sender == "priya@textiles.in"
    assert case.thread_id == "thread-1"
    assert case.intent == "angry_customer"
    assert case.urgency == "high"
    assert case.symptom == TRIGGER_OK["symptom"]
    assert case.case_id.startswith("email-402-")
    assert fake.calls == 1


async def test_trigger_email_with_spelled_out_order_parses_to_case(fake_classifier):
    fake_classifier(INQUIRY_OK)
    case = await parser.parse_trigger_email(
        "status update",
        "Can you confirm when order 402 will ship?",
        "buyer@corp.com",
        "thread-2",
    )
    assert case is not None
    assert case.order_id == "402"
    assert case.intent == "inquiry"
    assert case.urgency == "low"


async def test_email_without_order_id_returns_none(fake_classifier):
    fake = fake_classifier(INQUIRY_OK)
    case = await parser.parse_trigger_email(
        "hello", "just checking in about pricing", "a@b.com", "t3"
    )
    assert case is None
    assert fake.calls == 0  # cheap filter: no LLM call without an order id


async def test_spam_email_returns_none_even_with_order_number(fake_classifier):
    fake_classifier(
        {
            "intent": "spam",
            "urgency": "low",
            "summary": "win a prize",
            "symptom": "none",
        }
    )
    case = await parser.parse_trigger_email(
        "CONGRATULATIONS order 402 winner!!!",
        "You have won. Click here.",
        "scam@spam.com",
        "t4",
    )
    assert case is None


async def test_newsletter_email_returns_none(fake_classifier):
    fake_classifier(INQUIRY_OK)
    case = await parser.parse_trigger_email(
        "Monthly newsletter", "Great offers this month", "news@textiles.in", "t5"
    )
    assert case is None


async def test_empty_subject_and_body_returns_none(fake_classifier):
    case = await parser.parse_trigger_email("", "", "a@b.com", "t6")
    assert case is None


async def test_other_intent_with_order_id_still_creates_case(fake_classifier):
    fake_classifier(
        {
            "intent": "other",
            "urgency": "medium",
            "summary": "order 402 needs docs",
            "symptom": "documents missing for order 402",
        }
    )
    case = await parser.parse_trigger_email(
        "docs for order 402",
        "Please send the packing list for order 402.",
        "ops@corp.com",
        "t7",
    )
    assert case is not None
    assert case.intent == "other"


async def test_classifier_failure_still_returns_none_not_crash(fake_classifier):
    async def boom(subject, body):
        raise RuntimeError("llm down")

    fake_classifier(boom)
    case = await parser.parse_trigger_email(
        "order #402 urgent", "order 402 is stuck", "a@b.com", "t8"
    )
    assert case is None
