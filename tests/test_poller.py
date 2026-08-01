"""P6.5 — pending-case path + poller tests (APIs mocked, real parser skipped).

poll_inbox(callback, interval) runs forever; tests cancel the task after a
tick and monkeypatch ingest.gmail_poller.build + _load_credentials with
fakes so nothing touches Gmail.
"""

import asyncio
import sqlite3

from ingest import pending
from ingest.gmail_poller import poll_inbox


def test_create_pending_case_inserts_row():
    from contracts import CasePayload

    case = CasePayload(
        case_id="email-402-abc12345",
        order_id="402",
        symptom="stuck",
        source="email",
        intent="angry_customer",
        urgency="high",
        summary="stuck",
    )
    assert pending.create_pending_case(case) == case.case_id
    row = pending.get_pending_case(case.case_id)
    assert row is not None
    assert row["order_id"] == "402"
    assert row["status"] == "pending"
    pending.delete_pending_case(case.case_id)


def test_get_pending_case_unknown_id_returns_none():
    assert pending.get_pending_case("does-not-exist") is None


class _FakeRequest:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _FakeMessages:
    def __init__(self, payloads):
        self._payloads = payloads
        self.modified = []

    def list(self, **kwargs):
        unread = [p for p in self._payloads if p["id"] not in self.modified]
        return _FakeRequest({"messages": [{"id": p["id"]} for p in unread]})

    def get(self, **kwargs):
        pid = kwargs["id"]
        payload = next(p for p in self._payloads if p["id"] == pid)
        return _FakeRequest({
            "id": payload["id"],
            "threadId": payload["threadId"],
            "payload": {"headers": [
                {"name": "Subject", "value": payload["headers"]["subject"]},
                {"name": "From", "value": payload["headers"]["from"]},
            ]},
        })

    def modify(self, **kwargs):
        self.modified.append(kwargs["id"])
        return _FakeRequest({})


class _FakeUsers:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class _FakeService:
    def __init__(self, payloads):
        self._users = _FakeUsers(_FakeMessages(payloads))

    def users(self):
        return self._users


async def test_poll_inbox_calls_callback_and_marks_read(monkeypatch):
    from contracts import CasePayload

    payloads = [
        {
            "id": "msg-1",
            "threadId": "thread-1",
            "headers": {"subject": "URGENT order #402 stuck", "from": "priya@textiles.in"},
        },
        {
            "id": "msg-2",
            "threadId": "thread-9",
            "headers": {"subject": "newsletter", "from": "news@textiles.in"},
        },
    ]

    async def fake_parse(subject, body, sender, thread_id):
        if "402" not in subject:
            return None
        return CasePayload(
            case_id="email-402-abc12345",
            order_id="402",
            symptom="stuck",
            source="email",
            sender=sender,
            thread_id=thread_id,
            intent="angry_customer",
            urgency="high",
            summary="stuck order",
        )

    monkeypatch.setattr("ingest.gmail_poller.parse_trigger_email", fake_parse)

    class _FakeCreds:
        valid = True

    monkeypatch.setattr("ingest.gmail_poller._load_credentials", lambda: _FakeCreds())

    hits = []
    service = _FakeService(payloads)
    monkeypatch.setattr("ingest.gmail_poller.build", lambda *a, **k: service)

    async def on_case(case):
        hits.append(case)

    task = asyncio.create_task(poll_inbox(on_case, interval=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(hits) == 1
    assert hits[0].order_id == "402"
    assert service.users().messages().modified, "trigger message must be marked read"


async def test_poll_inbox_service_error_logs_and_keeps_polling(monkeypatch):
    hits = []

    async def fake_parse(subject, body, sender, thread_id):
        return None

    monkeypatch.setattr("ingest.gmail_poller.parse_trigger_email", fake_parse)

    class _FakeCreds:
        valid = True

    monkeypatch.setattr("ingest.gmail_poller._load_credentials", lambda: _FakeCreds())
    monkeypatch.setattr(
        "ingest.gmail_poller.build", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gmail down"))
    )

    async def on_case(case):
        hits.append(case)

    task = asyncio.create_task(poll_inbox(on_case, interval=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert hits == []
