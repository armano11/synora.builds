"""Telegram bot — full lifecycle: alert → investigate → verdict → approve → done.

python-telegram-bot v20+ async API. Token/chat from env:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Missing env -> failed ActionResult, and the Bot is never constructed.

# ---------------------------------------------------------------------------
# FAILURE MODES (each handled explicitly):
# 1. TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing or empty — return failed
#    ActionResult BEFORE constructing the Bot (no env, no client).
# 2. Bot construction raises (malformed token, PTB token validation) —
#    caught, returned as failed ActionResult.
# 3. send_message raises (network, Telegram API error, rate limit) —
#    caught, returned as failed ActionResult with the reason.
# ---------------------------------------------------------------------------
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from actions._common import eway_bill_culprit
from actions.eta_recalc import recalc_eta
from contracts import ActionResult, CasePayload, ExecutionResult, Verdict

# .env is the source of truth — override stale inherited values (e.g. a
# session-scoped env var that outlives the registry entry that created it).
load_dotenv(override=True)

_log = logging.getLogger("orbit.telegram")
_CB_FAILURES_SEEN: set[str] = set()


def _log_callback_failure(message: str) -> None:
    """Log each callback-poller failure once so the loop stays quiet while healthy."""
    if message not in _CB_FAILURES_SEEN:
        _CB_FAILURES_SEEN.add(message)
        _log.warning(message)


def _get_bot() -> tuple[Bot | None, str | None, str | None]:
    """Lazy bot + env guard. Returns (bot, token, chat_id) or (None, None, None)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return None, None, None
    try:
        return Bot(token), token, chat_id
    except Exception as exc:
        _log.warning(f"Telegram bot construction failed: {exc}")
        return None, None, None


# ---------------------------------------------------------------------------
# 1. INITIAL ALERT — email arrives, send [INVESTIGATE] button
# ---------------------------------------------------------------------------

async def send_initial_alert(case: CasePayload) -> ActionResult:
    """Send initial Telegram alert when an angry email arrives.

    Includes a [🔍 INVESTIGATE] inline button that triggers the investigation.
    """
    bot, _, chat_id = _get_bot()
    if not bot:
        return ActionResult(type="telegram", status="failed", error="Telegram env not configured")

    urgency_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(
        getattr(case, "urgency", "medium") or "medium", "🟡"
    )
    text = (
        f"{urgency_emoji} NEW CASE — {case.intent or 'angry_customer'}\n"
        f"Order #{case.order_id}\n"
        f"From: {case.sender or 'unknown'}\n"
        f"Symptom: {case.symptom}\n"
        f"Summary: {case.summary or '—'}\n\n"
        f"Tap INVESTIGATE to start the AI detective."
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔍 INVESTIGATE", callback_data=f"investigate:{case.case_id}")
    ]])
    try:
        msg = await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
        return ActionResult(type="telegram", status="sent", ref=str(msg.message_id))
    except Exception as exc:
        return ActionResult(type="telegram", status="failed", error=f"Telegram send failed: {exc}")


# Alias for backward compat
send_alert = send_initial_alert


# ---------------------------------------------------------------------------
# 2. VERDICT REPORT — investigation done, send [APPROVE & FIX] + [REJECT]
# ---------------------------------------------------------------------------

async def send_verdict_alert(verdict: Verdict, case: CasePayload) -> ActionResult:
    """Send investigation report to Telegram with Approve/Reject buttons."""
    bot, _, chat_id = _get_bot()
    if not bot:
        return ActionResult(type="telegram", status="failed", error="Telegram env not configured")

    root_cause = verdict.root_cause.rsplit(".", 1)[-1].replace("_", " ")
    conf_pct = f"{int(verdict.confidence * 100)}%"
    stamps_text = ""
    for portal, stamp in verdict.portal_verdicts.items():
        stamps_text += f"  {portal}: {stamp.verdict} -- {stamp.reason}\n"

    ruled_out_text = ""
    if verdict.ruled_out:
        ruled_out_text = "Ruled out: " + ", ".join(
            h.replace("h_", "").replace("_", " ") for h in verdict.ruled_out
        ) + "\n"

    evidence_summary = ""
    for ev in verdict.evidence_trail[:4]:
        icon = "+" if ev.found and ev.supports else "-" if ev.eliminates else "."
        evidence_summary += f"  [{icon}] {ev.source}: {ev.detail[:80]}\n"

    text = (
        f"📋 INVESTIGATION COMPLETE — Order #{case.order_id}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Root Cause: {root_cause}\n"
        f"Confidence: {conf_pct}\n\n"
        f"Evidence:\n{evidence_summary}\n"
        f"Portal Verdicts:\n{stamps_text}\n"
        f"{ruled_out_text}\n"
        f"Approve to execute the fix, or reject to close."
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve & Fix", callback_data=f"approve:{case.case_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"reject:{case.case_id}"),
    ]])
    try:
        msg = await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
        return ActionResult(type="telegram", status="sent", ref=str(msg.message_id))
    except Exception as exc:
        return ActionResult(type="telegram", status="failed", error=f"Telegram verdict alert failed: {exc}")


# ---------------------------------------------------------------------------
# 3. FIX APPLIED — execution done, send summary + [SEND DRAFT] button
# ---------------------------------------------------------------------------

async def send_fix_applied_alert(
    case: CasePayload,
    execution: ExecutionResult | None = None,
    verdict: Verdict | None = None,
    draft_id: str | None = None,
) -> ActionResult:
    """Send confirmation that fix was executed + offer SEND DRAFT button."""
    bot, _, chat_id = _get_bot()
    if not bot:
        return ActionResult(type="telegram", status="failed", error="Telegram env not configured")

    action_name = execution.action.replace("_", " ") if execution else "fix"
    verified = execution.verified if execution else False
    verify_icon = "✓ verified" if verified else "⚠ unverified"

    before_text = ""
    after_text = ""
    if execution and execution.before:
        before_text = " | ".join(f"{k}={v}" for k, v in execution.before.items())
    if execution and execution.after:
        after_text = " | ".join(f"{k}={v}" for k, v in execution.after.items())

    text = (
        f"✅ FIX EXECUTED — Order #{case.order_id}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Action: {action_name}\n"
        f"Status: {verify_icon}\n"
    )
    if before_text:
        text += f"Before: {before_text}\n"
    if after_text:
        text += f"After:  {after_text}\n"

    buttons = []
    if draft_id:
        text += f"\nGmail draft ready (ID: {draft_id[:12]}...)\n"
        text += "Tap SEND DRAFT to email the customer."
        buttons.append(
            InlineKeyboardButton("📧 SEND DRAFT", callback_data=f"send_draft:{case.case_id}:{draft_id}")
        )

    keyboard = InlineKeyboardMarkup([buttons]) if buttons else None

    try:
        msg = await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
        return ActionResult(type="telegram", status="sent", ref=str(msg.message_id))
    except Exception as exc:
        return ActionResult(type="telegram", status="failed", error=f"Telegram send failed: {exc}")


# ---------------------------------------------------------------------------
# 4. CASE CLOSED — final summary back to Telegram
# ---------------------------------------------------------------------------

async def send_case_closed_alert(
    case: CasePayload,
    verdict: Verdict | None = None,
    execution: ExecutionResult | None = None,
    wall_clock_s: float = 0.0,
    actions_summary: list[str] | None = None,
) -> ActionResult:
    """Send final 'everything done' message to Telegram."""
    bot, _, chat_id = _get_bot()
    if not bot:
        return ActionResult(type="telegram", status="failed", error="Telegram env not configured")

    root_cause = "unknown"
    conf_pct = "—"
    if verdict:
        root_cause = verdict.root_cause.rsplit(".", 1)[-1].replace("_", " ")
        conf_pct = f"{int(verdict.confidence * 100)}%"

    action_name = execution.action.replace("_", " ") if execution else "none"
    verified = execution.verified if execution else False

    actions_text = ""
    if actions_summary:
        for a in actions_summary:
            actions_text += f"  • {a}\n"

    text = (
        f"🏁 CASE CLOSED — Order #{case.order_id}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Root Cause: {root_cause}\n"
        f"Confidence: {conf_pct}\n"
        f"Fix: {action_name} {'✓' if verified else '⚠'}\n"
        f"Time: {wall_clock_s:.1f}s\n"
    )
    if actions_text:
        text += f"\nActions:\n{actions_text}"
    text += "\n✅ All done. Case resolved."

    try:
        msg = await bot.send_message(chat_id=chat_id, text=text)
        return ActionResult(type="telegram", status="sent", ref=str(msg.message_id))
    except Exception as exc:
        return ActionResult(type="telegram", status="failed", error=f"Telegram send failed: {exc}")


# ---------------------------------------------------------------------------
# 5. SEND GMAIL DRAFT — actually sends the draft via Gmail API
# ---------------------------------------------------------------------------

async def send_gmail_draft(draft_id: str) -> ActionResult:
    """Send a previously created Gmail draft (makes it a real email)."""
    try:
        from actions.gmail_drafter import _load_credentials, _token_path
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds = _load_credentials()
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                return ActionResult(type="gmail_draft", status="failed", error="Gmail not authorized")
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        result = service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
        msg_id = result.get("id", "unknown")
        return ActionResult(type="gmail_draft", status="sent", ref=str(msg_id))
    except Exception as exc:
        return ActionResult(type="gmail_draft", status="failed", error=f"Gmail send failed: {exc}")


# ---------------------------------------------------------------------------
# Legacy compat aliases
# ---------------------------------------------------------------------------

async def send_manager_alert(verdict: Verdict, case: CasePayload) -> ActionResult:
    """Legacy: send verdict summary (no buttons)."""
    bot, _, chat_id = _get_bot()
    if not bot:
        return ActionResult(type="telegram", status="failed", error="Telegram env not configured")
    root_cause = verdict.root_cause.rsplit(".", 1)[-1].replace("_", " ")
    text = (
        f"Case {case.case_id} — Order #{case.order_id}\n"
        f"Root cause: {root_cause}\n"
        f"Confidence: {verdict.confidence:.0%}"
    )
    try:
        sent = await bot.send_message(chat_id=chat_id, text=text)
        return ActionResult(type="telegram", status="sent", ref=str(sent.message_id))
    except Exception as exc:
        return ActionResult(type="telegram", status="failed", error=f"Telegram send failed: {exc}")


# ---------------------------------------------------------------------------
# CALLBACK POLLER — handles all inline button presses
# ---------------------------------------------------------------------------

async def poll_callbacks(on_investigate, interval: int = 2) -> None | dict:
    """Poll get_updates forever; handle all callback buttons.

    Buttons handled:
    - investigate:<case_id> → edit msg to "Investigating...", call on_investigate
    - approve:<case_id> → edit msg to "Approved", call on_investigate(is_approval=True, approved=True)
    - reject:<case_id> → edit msg to "Rejected", call on_investigate(is_approval=True, approved=False)
    - send_draft:<case_id>:<draft_id> → send the Gmail draft, confirm in chat
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        _log_callback_failure("Telegram env not configured — callback polling disabled")
        return {"status": "failed", "error": "Telegram env not configured"}
    try:
        bot = Bot(token)
    except Exception as exc:
        _log_callback_failure(f"Telegram bot failed: {exc}")
        return {"status": "failed", "error": f"Telegram bot failed: {exc}"}

    offset = 0
    while True:
        try:
            updates = await bot.get_updates(offset=offset, timeout=30)
            if updates:
                offset = max(u.update_id for u in updates) + 1
            for update in updates:
                cb = update.callback_query
                if not cb or not getattr(cb, "data", ""):
                    continue

                data = cb.data

                # --- INVESTIGATE button ---
                if data.startswith("investigate:"):
                    case_id = data.split(":", 1)[1]
                    try:
                        await bot.answer_callback_query(callback_query_id=cb.id)
                        await bot.edit_message_text(
                            f"🔍 Investigating case {case_id}...\n"
                            f"Watch live on the dashboard: http://localhost:8000",
                            message_id=cb.message.message_id,
                            chat_id=cb.message.chat.id,
                        )
                        await on_investigate(case_id)
                    except Exception as exc:
                        _log_callback_failure(f"investigate {case_id} failed: {exc}")

                # --- APPROVE button ---
                elif data.startswith("approve:"):
                    case_id = data.split(":", 1)[1]
                    try:
                        await bot.answer_callback_query(callback_query_id=cb.id)
                        await bot.edit_message_text(
                            f"✅ Approved — executing fix for {case_id}...\n"
                            f"Watch live on dashboard.",
                            message_id=cb.message.message_id,
                            chat_id=cb.message.chat.id,
                        )
                        await on_investigate(case_id, is_approval=True, approved=True)
                    except Exception as exc:
                        _log_callback_failure(f"approve {case_id} failed: {exc}")

                # --- REJECT button ---
                elif data.startswith("reject:"):
                    case_id = data.split(":", 1)[1]
                    try:
                        await bot.answer_callback_query(callback_query_id=cb.id)
                        await bot.edit_message_text(
                            f"❌ Rejected — closing case {case_id} without execution.",
                            message_id=cb.message.message_id,
                            chat_id=cb.message.chat.id,
                        )
                        await on_investigate(case_id, is_approval=True, approved=False)
                    except Exception as exc:
                        _log_callback_failure(f"reject {case_id} failed: {exc}")

                # --- SEND DRAFT button ---
                elif data.startswith("send_draft:"):
                    parts = data.split(":", 2)
                    case_id = parts[1] if len(parts) > 1 else "?"
                    draft_id = parts[2] if len(parts) > 2 else ""
                    try:
                        await bot.answer_callback_query(callback_query_id=cb.id)
                        await bot.edit_message_text(
                            f"📧 Sending draft email for case {case_id}...",
                            message_id=cb.message.message_id,
                            chat_id=cb.message.chat.id,
                        )
                        result = await send_gmail_draft(draft_id)
                        if result.status == "sent":
                            chat_id = cb.message.chat.id
                            await bot.send_message(
                                chat_id=chat_id,
                                text=f"📧 Email SENT to customer for Order #{case_id.split('-')[1] if '-' in case_id else case_id}\n"
                                     f"Message ID: {result.ref}"
                            )
                        else:
                            chat_id = cb.message.chat.id
                            await bot.send_message(
                                chat_id=chat_id,
                                text=f"⚠ Email send failed: {result.error}"
                            )
                    except Exception as exc:
                        _log_callback_failure(f"send_draft {case_id} failed: {exc}")

        except Exception as exc:
            _log_callback_failure(f"get_updates failed: {exc}")
        await asyncio.sleep(interval)
