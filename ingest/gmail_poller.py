"""Gmail poller — the demo's email trigger.

poll_inbox(callback, interval=10) runs forever: list unread INBOX messages,
parse each through ingest.parser, call `await callback(case)` per real case
(e.g. create_pending_case + send_alert), and mark the message read.

Credentials follow the established gmail_drafter pattern (env
GMAIL_CREDENTIALS_PATH + token.json next to it, lazy, FileNotFoundError ->
not authorized) but with SCOPES = gmail.modify because the poller MARKS
READ. NOTE: gmail_drafter keeps a read-only-ish compose token file; both
modules share the same token.json and each refreshes it with its own scope.

# ---------------------------------------------------------------------------
# FAILURE MODES (each handled explicitly):
# 1. GMAIL_CREDENTIALS_PATH unset / token.json missing / token revoked —
#    caught, logged once, poll returns a failed dict; the loop keeps polling.
# 2. users().messages().list/get/modify raising (HttpError, quota, network) —
#    caught, logged once, failed dict; loop keeps polling.
# 3. A malformed message (no subject/body/thread) — per-message catch, log
#    once, continue with the next message; never aborts the batch.
# ---------------------------------------------------------------------------
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time

from actions.gmail_drafter import _token_path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ingest.parser import parse_trigger_email

log = logging.getLogger("orbit.poller")
_LOG_SEEN: set[str] = set()

# gmail.modify — the poller must MARK READ after handing a case to the callback
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

_last_poll: float | None = None


def _log_once(message: str) -> None:
    """Log a failure once per episode so the loop stays quiet while healthy."""
    if message not in _LOG_SEEN:
        _LOG_SEEN.add(message)
        log.warning(message)


def _load_credentials() -> Credentials:
    """Authorized-user creds from token.json; FileNotFoundError -> not authorized."""
    return Credentials.from_authorized_user_file(str(_token_path()), SCOPES)


def _header(payload: dict, name: str) -> str | None:
    for header in payload.get("headers", []):
        if header.get("name", "").lower() == name.lower():
            return header.get("value")
    return None


def _extract_body(payload: dict) -> str:
    """First text/plain body from payload.body or a text/plain part."""
    if payload.get("body", {}).get("data"):
        return _decode(payload["body"]["data"])
    for part in payload.get("parts", []):
        if part.get("mimeType", "").startswith("text/plain") and part.get("body", {}).get("data"):
            return _decode(part["body"]["data"])
    return ""


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data.encode()).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


async def _poll_once(callback) -> dict:
    """One poll cycle; returns a status dict, NEVER raises."""
    try:
        creds = _load_credentials()
        if not creds.valid:
            if not (creds.expired and creds.refresh_token):
                _log_once("Gmail not authorized")
                return {"status": "failed", "error": "Gmail not authorized"}
            from google.auth.transport.requests import Request

            creds.refresh(Request())
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    except FileNotFoundError:
        _log_once("Gmail not authorized (token.json missing)")
        return {"status": "failed", "error": "Gmail not authorized"}
    except Exception as exc:  # noqa: BLE001
        _log_once(f"Gmail credential failure: {exc}")
        return {"status": "failed", "error": f"Gmail credential failure: {exc}"}

    try:
        global _last_poll
        query = "in:inbox is:unread"
        listing = (
            service.users().messages().list(
                userId="me", q=query, maxResults=10
            ).execute()
        )
        for entry in listing.get("messages", []):
            msg_id = entry.get("id")
            try:
                full = (
                    service.users().messages().get(
                        userId="me", id=msg_id, format="full"
                    ).execute()
                )
                payload = full.get("payload", {})
                subject = _header(payload, "Subject") or ""
                sender = _header(payload, "From")
                case = await parse_trigger_email(
                    subject, _extract_body(payload), sender, full.get("threadId")
                )
                if case is not None:
                    await callback(case)
                    service.users().messages().modify(
                        userId="me",
                        id=msg_id,
                        body={"removeLabelIds": ["UNREAD"]},
                    ).execute()
            except Exception as exc:  # noqa: BLE001 — one bad message never aborts
                _log_once(f"message {msg_id} failed: {exc}")
                continue
        _last_poll = time.time()
        return {"status": "ok", "processed": len(listing.get("messages", []))}
    except Exception as exc:  # noqa: BLE001
        _log_once(f"Gmail poll failed: {exc}")
        return {"status": "failed", "error": f"Gmail poll failed: {exc}"}


async def poll_inbox(callback, interval: int = 10) -> None:
    """Poll forever: one cycle per interval; cancel the task to stop."""
    while True:
        try:
            await _poll_once(callback)
        except Exception as exc:  # noqa: BLE001 — belt and braces: never die
            _log_once(f"poll loop error: {exc}")
        await asyncio.sleep(interval)
