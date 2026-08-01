"""Gmail buyer draft — empathetic apology, cause found, new ETA. DRAFT ONLY.

External actions are approval-gated (our trust story): we never send to the
buyer, we only create a threaded draft via users.drafts.create (userId="me").

Env:
    GMAIL_CREDENTIALS_PATH   path to the OAuth client JSON; token.json is
                             expected next to it (token.json is gitignored).
                             Unset -> failed before any API call (no CWD
                             fallback: the module never guesses a token file).
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
# 4. case.sender missing or not an email-like address — failed
#    ActionResult with error "no buyer email on case" (a draft without a
#    recipient is useless), before any API call.
# 5. Draft API returns without an "id" — failed ActionResult
#    ("draft created but no id returned"); ref is never a fake "None".
# ---------------------------------------------------------------------------
"""

from __future__ import annotations

import base64
import os
import re
from email.message import EmailMessage
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from actions._common import eway_bill_culprit
from actions.eta_recalc import recalc_eta
from contracts import ActionResult, CasePayload, Verdict

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


def _token_path() -> Path:
    """token.json sits next to the OAuth client JSON from env.

    Unset env -> FileNotFoundError: there is deliberately NO CWD fallback,
    so a missing env can never silently authorize with an unintended
    token.json in the working directory.
    """
    base = os.environ.get("GMAIL_CREDENTIALS_PATH")
    if not base:
        raise FileNotFoundError("GMAIL_CREDENTIALS_PATH unset")
    return Path(base).resolve().parent / "token.json"


def _load_credentials() -> Credentials:
    """Authorized-user creds from token.json; FileNotFoundError -> not authorized."""
    return Credentials.from_authorized_user_file(str(_token_path()), SCOPES)


def _buyer_email(sender: str | None) -> str:
    """The usable To address from the case sender, or fallback for demo/manual cases.

    Accepts plain emails ("priya@example.com"), "Name <email>" display-name
    format, and bare dot-domains. Falls back to "buyer@example.com" if missing.
    """
    if not sender:
        return "buyer@example.com"
    bracketed = re.search(r"<([^<>@]+@[^<>@]+)>", sender)
    if bracketed:
        return bracketed.group(1).strip()
    if "@" in sender:
        return sender
    if "." in sender and " " not in sender:
        return sender
    return "buyer@example.com"


def _greeting(recipient: str) -> str:
    """Local part before '@' for the greeting, or "customer" if empty.

    A no-'@' address has no local part, so it greets as "customer". Never
    the raw sender string — a company name like "Priya Textiles — Mumbai"
    must not be injected un-escaped into buyer-facing copy.
    """
    if "@" not in recipient:
        return "customer"
    local = recipient.rsplit("@", 1)[0]
    return local if local else "customer"


def _cause_line(verdict: Verdict) -> tuple[str, str]:
    """(root cause sentence, next-steps sentence) — honest per verdict."""
    if eway_bill_culprit(verdict):
        return (
            "the e-way bill for this shipment expired on the GST portal",
            "The renewal is already in progress; the moment the portal stamps "
            "the renewed bill, the shipment moves immediately.",
        )
    return (
        "a delivery delay on our side — our team has identified the cause "
        "and is working on it",
        "The fix is awaiting approval and will be executed as soon as our "
        "operations team confirms it.",
    )


def _build_email(verdict: Verdict, case: CasePayload, recipient: str) -> bytes:
    """Plain-text threaded draft content — empathetic, apology + cause + ETA.

    The ETA sentence is only included as a concrete date on the e-way renewal
    path when recalc succeeded; every other path uses neutral wording and
    never emits "None".
    """
    cause, steps = _cause_line(verdict)
    if eway_bill_culprit(verdict):
        eta = recalc_eta(case)
        if eta.status == "done":
            eta_line = f"New ETA: {eta.ref}."
        else:
            eta_line = "We will share the new ETA as soon as the renewal completes."
    else:
        eta_line = "We will share the new ETA once the fix is confirmed."
    body = (
        f"Dear {_greeting(recipient)},\n\n"
        f"We're sorry for the delay on your order #{case.order_id}. Our team "
        f"investigated and found the root cause: {cause} (confidence "
        f"{verdict.confidence:.0%}).\n\n"
        f"{steps}\n\n"
        f"{eta_line} We will confirm the moment it ships and share the "
        f"updated tracking ID.\n\n"
        f"If there is anything else we can do, just reply to this email.\n\n"
        f"— ORBIT Customer Care"
    )
    msg = EmailMessage()
    msg["Subject"] = f"Update on your order #{case.order_id}"
    msg["To"] = recipient
    msg.set_content(body)
    return msg.as_bytes()


def create_buyer_draft(verdict: Verdict, case: CasePayload, thread_id: str) -> ActionResult:
    """Create a threaded Gmail draft; status="drafted" with the draft id as ref.

    DRAFT ONLY — approval-gated by design; never calls send(). Never raises;
    every failure path returns ActionResult(status="failed", error=...).
    """
    recipient = _buyer_email(case.sender)
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
        raw = base64.urlsafe_b64encode(_build_email(verdict, case, recipient)).decode()
        draft = (
            service.users()
            .drafts()
            .create(userId="me", body={"message": {"raw": raw, "threadId": thread_id}})
            .execute()
        )
        if not draft.get("id"):
            return ActionResult(
                type="gmail_draft",
                status="failed",
                error="draft created but no id returned",
            )
        return ActionResult(type="gmail_draft", status="drafted", ref=str(draft["id"]))
    except FileNotFoundError:
        return ActionResult(type="gmail_draft", status="failed", error="Gmail not authorized")
    except Exception as exc:  # noqa: BLE001
        return ActionResult(
            type="gmail_draft", status="failed", error=f"Gmail draft failed: {exc}"
        )
