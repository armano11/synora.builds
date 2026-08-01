"""Gmail buyer draft — empathetic apology, cause found, new ETA. DRAFT ONLY.

External actions are approval-gated (our trust story): we never send to the
buyer, we only create a threaded draft via users.drafts.create (userId="me").

Env:
    GMAIL_CREDENTIALS_PATH   path to the OAuth client JSON; token.json is
                             expected next to it (token.json is gitignored).
Scopes: ["https://www.googleapis.com/auth/gmail.compose"].
Credentials are loaded lazily via
Credentials.from_authorized_user_file(token_path, SCOPES), refreshed when
expired, and the service is built lazily with cache_discovery=False.

# ---------------------------------------------------------------------------
# FAILURE MODES (each handled explicitly):
# 1. GMAIL_CREDENTIALS_PATH unset or token.json missing — failed
#    ActionResult with error "Gmail not authorized", before any API call.
# 2. Token expired and the refresh fails (revoked / invalid_grant) — caught,
#    returned as failed ActionResult.
# 3. users().drafts().create/execute raises (HttpError 403/404, network,
#    quota) — caught, returned as failed ActionResult with the reason.
# ---------------------------------------------------------------------------
"""

from __future__ import annotations

import base64
import os
from email.message import EmailMessage
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from actions.eta_recalc import recalc_eta
from contracts import ActionResult, CasePayload, Verdict

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


def _token_path() -> Path:
    """token.json sits next to the OAuth client JSON from env."""
    base = os.environ.get("GMAIL_CREDENTIALS_PATH")
    return Path(base).resolve().parent / "token.json" if base else Path("token.json")


def _load_credentials() -> Credentials:
    """Authorized-user creds from token.json; FileNotFoundError -> not authorized."""
    return Credentials.from_authorized_user_file(str(_token_path()), SCOPES)


def _cause_line(verdict: Verdict) -> tuple[str, str]:
    """(root cause sentence, next-steps sentence) — honest per verdict."""
    if verdict.root_cause.endswith("h_eway_bill_expired"):
        return (
            "the e-way bill for this shipment expired on the GST portal",
            "The renewal is already in progress; the moment the portal stamps "
            "the renewed bill, the shipment moves immediately.",
        )
    return (
        f"the delay is caused by: {verdict.root_cause}",
        "The fix is awaiting approval and will be executed as soon as our "
        "operations team confirms it.",
    )


def _build_email(verdict: Verdict, case: CasePayload) -> bytes:
    """Plain-text threaded draft content — empathetic, apology + cause + ETA."""
    cause, steps = _cause_line(verdict)
    eta = recalc_eta(case).ref
    name = case.sender if (case.sender and "@" not in case.sender) else "customer"
    body = (
        f"Dear {name},\n\n"
        f"We're sorry for the delay on your order #{case.order_id}. Our team "
        f"investigated and found the root cause: {cause} (confidence "
        f"{verdict.confidence:.0%}).\n\n"
        f"{steps}\n\n"
        f"New ETA: {eta}. We will confirm the moment it ships and share the "
        f"updated tracking ID.\n\n"
        f"If there is anything else we can do, just reply to this email.\n\n"
        f"— ORBIT Customer Care"
    )
    msg = EmailMessage()
    msg["Subject"] = f"Update on your order #{case.order_id}"
    if case.sender:
        msg["To"] = case.sender
    msg.set_content(body)
    return msg.as_bytes()


def create_buyer_draft(verdict: Verdict, case: CasePayload, thread_id: str) -> ActionResult:
    """Create a threaded Gmail draft; status="drafted" with the draft id as ref.

    DRAFT ONLY — approval-gated by design; never calls send(). Never raises;
    every failure path returns ActionResult(status="failed", error=...).
    """
    try:
        creds = _load_credentials()
        if not creds.valid:
            if not (creds.expired and creds.refresh_token):
                return ActionResult(
                    type="gmail_draft",
                    status="failed",
                    error="Gmail not authorized",
                )
            creds.refresh(Request())
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        raw = base64.urlsafe_b64encode(_build_email(verdict, case)).decode()
        draft = (
            service.users()
            .drafts()
            .create(userId="me", body={"message": {"raw": raw, "threadId": thread_id}})
            .execute()
        )
        return ActionResult(
            type="gmail_draft", status="drafted", ref=str(draft.get("id"))
        )
    except FileNotFoundError:
        return ActionResult(type="gmail_draft", status="failed", error="Gmail not authorized")
    except Exception as exc:  # noqa: BLE001
        return ActionResult(
            type="gmail_draft", status="failed", error=f"Gmail draft failed: {exc}"
        )
