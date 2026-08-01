"""P6 — action layer tests: eta_recalc (exact math), telegram (mocked Bot),
gmail (mocked creds/service). Nothing touches the real Telegram/Gmail APIs.

eta_recalc is unit-tested for the exact date; telegram/gmail are tested
import-clean and on their failure paths with monkeypatched clients.
"""

import base64
from types import SimpleNamespace

from actions.eta_recalc import recalc_eta
from actions.gmail_drafter import create_buyer_draft
from actions.telegram_bot import send_manager_alert
from contracts import CasePayload, PortalStamp, Verdict
from enterprise.seed import SCENARIO_TODAY

CASE_402 = CasePayload(
    case_id="case_001", order_id="402",
    symptom="shipment stuck at Hubli for 6 days, customer cancelling",
    source="email",
    sender="priya@example.com",
)
UNKNOWN_CASE = CasePayload(
    case_id="case_999", order_id="9999", symptom="stuck", source="manual"
)


def _verdict(**overrides) -> Verdict:
    base = Verdict(
        root_cause="shipment_delay.h_eway_bill_expired",
        confidence=0.91,
        portal_verdicts={
            "tally": PortalStamp(verdict="STALE", reason="dispatch claim stale"),
            "gst": PortalStamp(verdict="TRUE", reason="validity lapsed"),
            "delhivery": PortalStamp(verdict="STALE", reason="last scan 6 days old"),
            "transport": PortalStamp(verdict="MISLEADING", reason="breakdown denied"),
        },
        wall_clock_s=3.0,
    )
    return base.model_copy(update=overrides)


# ---------------------------------------------------------------------------
# eta_recalc — exact deterministic math
# ---------------------------------------------------------------------------


def test_eta_recalc_exact_date():
    result = recalc_eta(CASE_402)
    assert result.type == "eta_recalc"
    assert result.status == "done"
    assert result.ref == "2026-07-23"
    assert result.error is None


def test_eta_recalc_unknown_order_still_deterministic():
    """recalc_eta does NOT touch the DB — any case yields the same ETA."""
    assert recalc_eta(UNKNOWN_CASE).ref == "2026-07-23"


def test_eta_recalc_is_scenario_today_plus_3_days():
    from datetime import timedelta

    result = recalc_eta(CASE_402)
    assert result.ref == (SCENARIO_TODAY + timedelta(days=3)).isoformat()
    assert result.ref == "2026-07-23"


# ---------------------------------------------------------------------------
# telegram — mocked Bot, never the real API
# ---------------------------------------------------------------------------


def test_telegram_import_clean():
    assert callable(send_manager_alert)


async def test_telegram_missing_env_fails_without_raise(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    result = await send_manager_alert(_verdict(), CASE_402)
    assert result.type == "telegram"
    assert result.status == "failed"
    assert result.error


async def test_telegram_missing_chat_id_fails(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fake-token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    result = await send_manager_alert(_verdict(), CASE_402)
    assert result.status == "failed"


async def test_telegram_success_format_and_ref(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "98765")
    seen = {}

    class FakeBot:
        def __init__(self, token):
            seen["token"] = token

        async def send_message(self, chat_id, text):
            seen["chat_id"] = chat_id
            seen["text"] = text
            return SimpleNamespace(message_id=4242)

    monkeypatch.setattr("actions.telegram_bot.Bot", FakeBot)
    result = await send_manager_alert(_verdict(), CASE_402)
    assert result.status == "sent"
    assert result.ref == "4242"
    assert seen["token"] == "123456:fake-token"
    assert seen["chat_id"] == "98765"
    text = seen["text"]
    assert "Case case_001" in text
    assert "Order #402" in text
    assert "Root cause: shipment_delay.h_eway_bill_expired" in text
    assert "Confidence: 91%" in text
    assert "Actions: renew e-way bill (in progress)" in text
    assert "New ETA: 2026-07-23" in text
    assert "Portal stamps: delhivery STALE, gst TRUE, tally STALE, transport MISLEADING" in text


async def test_telegram_non_eway_cause_shows_rejected_action(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "98765")
    seen = {}

    class FakeBot:
        def __init__(self, token):
            pass

        async def send_message(self, chat_id, text):
            seen["text"] = text
            return SimpleNamespace(message_id=1)

    monkeypatch.setattr("actions.telegram_bot.Bot", FakeBot)
    verdict = _verdict(root_cause="payment_hold.h_release_blocked", confidence=0.5)
    result = await send_manager_alert(verdict, CASE_402)
    assert result.status == "sent"
    assert "Actions: no execution (rejected)" in seen["text"]
    assert "New ETA" not in seen["text"]


async def test_telegram_recalc_failure_sends_pending_eta(monkeypatch):
    """A failed recalc must NOT fail the alert, and must never print 'None'."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "98765")
    seen = {}

    class FakeBot:
        def __init__(self, token):
            pass

        async def send_message(self, chat_id, text):
            seen["text"] = text
            return SimpleNamespace(message_id=1)

    monkeypatch.setattr("actions.telegram_bot.Bot", FakeBot)
    monkeypatch.setattr(
        "actions.telegram_bot.recalc_eta",
        lambda case: SimpleNamespace(status="failed", ref=None, error="boom"),
    )
    result = await send_manager_alert(_verdict(), CASE_402)
    assert result.status == "sent"
    assert "New ETA: pending" in seen["text"]
    assert "None" not in seen["text"]


async def test_telegram_bot_construction_failure_returns_failed(monkeypatch):
    """Bot() raising (malformed token, PTB validation) -> failed ActionResult."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "98765")

    def broken_ctor(token):
        raise ValueError("Invalid token")

    monkeypatch.setattr("actions.telegram_bot.Bot", broken_ctor)
    result = await send_manager_alert(_verdict(), CASE_402)
    assert result.status == "failed"
    assert "Invalid token" in result.error


async def test_telegram_send_error_returns_failed(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "98765")

    class BrokenBot:
        def __init__(self, token):
            pass

        async def send_message(self, chat_id, text):
            raise RuntimeError("telegram network timeout")

    monkeypatch.setattr("actions.telegram_bot.Bot", BrokenBot)
    result = await send_manager_alert(_verdict(), CASE_402)
    assert result.status == "failed"
    assert "telegram network timeout" in result.error


# ---------------------------------------------------------------------------
# gmail — mocked creds/service, never the real API
# ---------------------------------------------------------------------------


def test_gmail_import_clean():
    assert callable(create_buyer_draft)


def _monkeypatch_creds(monkeypatch, creds_class):
    """Replace gmail_drafter.Credentials with a stub whose
    from_authorized_user_file(path, scopes) returns a fresh creds instance.

    GMAIL_CREDENTIALS_PATH must be set (the module now refuses to run
    without it); the stubbed Credentials never touches the real file."""
    monkeypatch.setenv("GMAIL_CREDENTIALS_PATH", "C:\\dummy\\client.json")
    monkeypatch.setattr(
        "actions.gmail_drafter.Credentials",
        SimpleNamespace(
            from_authorized_user_file=lambda path, scopes: creds_class()
        ),
    )


def _fake_service(monkeypatch, draft_execute):
    """Replace gmail_drafter.build with a stub service; capture the call body."""
    captured = {}

    class FakeDrafts:
        def create(self, userId, body):
            captured["userId"] = userId
            captured["body"] = body
            return SimpleNamespace(execute=draft_execute)

    class FakeUsers:
        def drafts(self):
            return FakeDrafts()

    class FakeService:
        def users(self):
            return FakeUsers()

    monkeypatch.setattr("actions.gmail_drafter.build", lambda *a, **k: FakeService())
    return captured


def test_gmail_no_token_file_returns_failed(monkeypatch):
    """Env unset -> failed BEFORE any Credentials are involved (real path).

    No Credentials stub: the module's own env logic must produce the
    failure by itself.
    """
    monkeypatch.delenv("GMAIL_CREDENTIALS_PATH", raising=False)
    result = create_buyer_draft(_verdict(), CASE_402, "thread-123")
    assert result.type == "gmail_draft"
    assert result.status == "failed"
    assert result.error == "Gmail not authorized"


def test_gmail_missing_credentials_file_fails_real_path(monkeypatch, tmp_path):
    """Env pointing at a nonexistent client JSON -> real library raises
    FileNotFoundError -> failed, no mocked Credentials involved."""
    monkeypatch.setenv("GMAIL_CREDENTIALS_PATH", str(tmp_path / "no-such" / "client.json"))
    result = create_buyer_draft(_verdict(), CASE_402, "thread-123")
    assert result.status == "failed"
    assert result.error == "Gmail not authorized"


def test_gmail_expired_refresh_failure_returns_failed(monkeypatch):
    class ExpiredCreds:
        valid = False
        expired = True
        refresh_token = "rt"

        def refresh(self, request):
            raise RuntimeError("invalid_grant: token revoked")

    _monkeypatch_creds(monkeypatch, ExpiredCreds)
    result = create_buyer_draft(_verdict(), CASE_402, "thread-123")
    assert result.status == "failed"
    assert "invalid_grant" in result.error


def test_gmail_api_raise_returns_failed(monkeypatch):
    class ValidCreds:
        valid = True

    _monkeypatch_creds(monkeypatch, ValidCreds)

    def boom():
        raise RuntimeError("quota exceeded")

    _fake_service(monkeypatch, boom)
    result = create_buyer_draft(_verdict(), CASE_402, "thread-123")
    assert result.status == "failed"
    assert "quota exceeded" in result.error


def test_gmail_success_draft_threaded_with_content(monkeypatch):
    class ValidCreds:
        valid = True

    _monkeypatch_creds(monkeypatch, ValidCreds)
    captured = _fake_service(monkeypatch, lambda: {"id": "draft-77"})
    result = create_buyer_draft(_verdict(), CASE_402, "thread-123")
    assert result.status == "drafted"
    assert result.ref == "draft-77"
    assert captured["userId"] == "me"
    message = captured["body"]["message"]
    assert message["threadId"] == "thread-123"
    raw = base64.urlsafe_b64decode(message["raw"].encode()).decode()
    assert "To: priya@example.com" in raw
    assert "Subject: Update on your order #402" in raw
    assert "Dear priya," in raw
    assert "We're sorry for the delay" in raw
    assert "e-way bill for this shipment expired" in raw
    assert "New ETA: 2026-07-23" in raw


def test_gmail_expired_creds_refresh_then_draft(monkeypatch):
    class RefreshableCreds:
        valid = False
        expired = True
        refresh_token = "rt"

        def refresh(self, request):
            self.valid = True

    _monkeypatch_creds(monkeypatch, RefreshableCreds)
    captured = _fake_service(monkeypatch, lambda: {"id": "draft-88"})
    result = create_buyer_draft(_verdict(), CASE_402, "thread-123")
    assert result.status == "drafted"
    assert result.ref == "draft-88"


def test_gmail_no_sender_fails(monkeypatch):
    """No sender -> failed before any API call; a draft without recipient is useless."""
    case = CASE_402.model_copy(update={"sender": None})
    result = create_buyer_draft(_verdict(), case, "thread-123")
    assert result.status == "failed"
    assert result.error == "no buyer email on case"


def test_gmail_company_name_sender_fails(monkeypatch):
    """A raw company name is not an address: fail, never inject it into copy."""
    case = CASE_402.model_copy(update={"sender": "Priya Textiles — Mumbai"})
    result = create_buyer_draft(_verdict(), case, "thread-123")
    assert result.status == "failed"
    assert result.error == "no buyer email on case"


def test_gmail_bare_dot_domain_sender_addresses_dear_customer(monkeypatch):
    """A no-'@' dot-domain sender is addressable, greeted as 'customer'."""
    class ValidCreds:
        valid = True

    _monkeypatch_creds(monkeypatch, ValidCreds)
    captured = _fake_service(monkeypatch, lambda: {"id": "draft-89"})
    case = CASE_402.model_copy(update={"sender": "buyer.example.com"})
    result = create_buyer_draft(_verdict(), case, "thread-123")
    assert result.status == "drafted"
    message = captured["body"]["message"]
    raw = base64.urlsafe_b64decode(message["raw"].encode()).decode()
    assert "To: buyer.example.com" in raw
    assert "Dear customer," in raw


def test_gmail_display_name_format_uses_bracketed_email(monkeypatch):
    class ValidCreds:
        valid = True

    _monkeypatch_creds(monkeypatch, ValidCreds)
    captured = _fake_service(monkeypatch, lambda: {"id": "draft-90"})
    case = CASE_402.model_copy(
        update={"sender": "Priya Textiles <priya@example.com>"}
    )
    result = create_buyer_draft(_verdict(), case, "thread-123")
    assert result.status == "drafted"
    message = captured["body"]["message"]
    raw = base64.urlsafe_b64decode(message["raw"].encode()).decode()
    assert "To: priya@example.com" in raw
    assert "Dear priya," in raw
    assert "Priya Textiles" not in raw


def test_gmail_draft_missing_id_fails(monkeypatch):
    """API response without an id -> failed, never a fake ref='None'."""
    class ValidCreds:
        valid = True

    _monkeypatch_creds(monkeypatch, ValidCreds)
    _fake_service(monkeypatch, lambda: {})
    result = create_buyer_draft(_verdict(), CASE_402, "thread-123")
    assert result.status == "failed"
    assert result.error == "draft created but no id returned"


def test_gmail_non_eway_neutral_eta_and_human_wording(monkeypatch):
    """Non-eway verdict: no concrete ETA (no contradiction), no internal
    root_cause id in buyer-facing copy."""
    class ValidCreds:
        valid = True

    _monkeypatch_creds(monkeypatch, ValidCreds)
    captured = _fake_service(monkeypatch, lambda: {"id": "draft-91"})
    verdict = _verdict(root_cause="payment_hold.h_release_blocked", confidence=0.5)
    result = create_buyer_draft(verdict, CASE_402, "thread-123")
    assert result.status == "drafted"
    message = captured["body"]["message"]
    raw = base64.urlsafe_b64decode(message["raw"].encode()).decode()
    assert "We will share the new ETA once the fix is confirmed." in raw
    assert "New ETA: 2026-07-23" not in raw
    assert "payment_hold.h_release_blocked" not in raw


def test_gmail_recalc_failure_neutral_eta(monkeypatch):
    """Failed recalc -> draft still created, neutral ETA wording, no 'None'."""
    class ValidCreds:
        valid = True

    _monkeypatch_creds(monkeypatch, ValidCreds)
    captured = _fake_service(monkeypatch, lambda: {"id": "draft-92"})
    monkeypatch.setattr(
        "actions.gmail_drafter.recalc_eta",
        lambda case: SimpleNamespace(status="failed", ref=None, error="boom"),
    )
    result = create_buyer_draft(_verdict(), CASE_402, "thread-123")
    assert result.status == "drafted"
    message = captured["body"]["message"]
    raw = base64.urlsafe_b64decode(message["raw"].encode()).decode()
    assert "We will share the new ETA as soon as the renewal completes." in raw
    assert "None" not in raw


# ---------------------------------------------------------------------------
# eta_recalc — failure path (MINOR-1)
# ---------------------------------------------------------------------------


def test_eta_recalc_arithmetic_failure_returns_failed(monkeypatch):
    """Broken SCENARIO_TODAY (non-date) -> caught, failed ActionResult."""
    monkeypatch.setattr("actions.eta_recalc.SCENARIO_TODAY", "not-a-date")
    result = recalc_eta(CASE_402)
    assert result.status == "failed"
    assert result.ref is None
    assert result.error
