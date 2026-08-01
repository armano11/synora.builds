"""Telegram alert — sends the manager a verdict summary via Bot.send_message.

python-telegram-bot v20+ async API. Token/chat from env:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Missing env -> failed ActionResult, and the Bot is never constructed.
The Bot is constructed lazily inside send_manager_alert.

Message format (P6 spec): case ID, order ID, root cause, confidence,
actions (derived honestly from the verdict), portal stamps as supporting
notes, and the new ETA from recalc_eta(case) — but only on the e-way
renewal path, and never a "None" ETA: when recalc fails the line reads
"New ETA: pending".

# ---------------------------------------------------------------------------
# FAILURE MODES (each handled explicitly):
# 1. TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing or empty — return failed
#    ActionResult BEFORE constructing the Bot (no env, no client).
# 2. Bot construction raises (malformed token, PTB token validation) —
#    caught, returned as failed ActionResult.
# 3. send_message raises (network, Telegram API error, rate limit) —
#    caught, returned as failed ActionResult with the reason.
# 4. recalc_eta fails — the alert still sends; the ETA line honestly says
#    "pending" instead of embedding a None ref.
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
from contracts import ActionResult, CasePayload, Verdict

load_dotenv()

_log = logging.getLogger("orbit.telegram")
_CB_FAILURES_SEEN: set[str] = set()


def _log_callback_failure(message: str) -> None:
    """Log each callback-poller failure once so the loop stays quiet while healthy."""
    if message not in _CB_FAILURES_SEEN:
        _CB_FAILURES_SEEN.add(message)
        _log.warning(message)


def _actions_line(verdict: Verdict) -> str:
    """Derive the execution status honestly from the verdict's root cause."""
    if eway_bill_culprit(verdict):
        return "renew e-way bill (in progress)"
    return "no execution (rejected)"


def _build_message(verdict: Verdict, case: CasePayload) -> str:
    """The P6-specified alert text: case/order, cause, confidence, actions, ETA.

    The ETA line is only included on the e-way renewal path (for other
    branches the alert would contradict its own "rejected" actions line),
    and a failed recalc renders as "New ETA: pending" — never "None".
    """
    lines = [
        f"Case {case.case_id} — Order #{case.order_id}",
        f"Root cause: {verdict.root_cause}",
        f"Confidence: {verdict.confidence:.0%}",
        "",
        f"Actions: {_actions_line(verdict)}",
    ]
    if eway_bill_culprit(verdict):
        eta = recalc_eta(case)
        if eta.status == "done":
            lines.append(f"New ETA: {eta.ref}")
        else:
            lines.append("New ETA: pending")
    if verdict.portal_verdicts:
        stamps = ", ".join(
            f"{portal} {stamp.verdict}"
            for portal, stamp in sorted(verdict.portal_verdicts.items())
        )
        lines.append(f"Portal stamps: {stamps}")
    return "\n".join(lines)


async def send_manager_alert(verdict: Verdict, case: CasePayload) -> ActionResult:
    """Send the manager alert; status="sent" with the message id as ref.

    Lazy: reads env and constructs the Bot only on first call. Never raises —
    every failure path returns ActionResult(status="failed", error=...).
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return ActionResult(
            type="telegram", status="failed", error="Telegram env not configured"
        )
    try:
        bot = Bot(token)
        sent = await bot.send_message(chat_id=chat_id, text=_build_message(verdict, case))
        return ActionResult(type="telegram", status="sent", ref=str(sent.message_id))
    except Exception as exc:  # noqa: BLE001
        return ActionResult(
            type="telegram", status="failed", error=f"Telegram send failed: {exc}"
        )


# ---------------------------------------------------------------------------
# P6.5 — the DEMO ENTRY POINT alert: send_alert + the INVESTIGATE button flow.
#
# send_alert fires the instant a case lands: a one-button alert whose callback
# payload carries the case_id. poll_callbacks then answers the button press,
# rewrites the message to "investigation started", and hands the case_id to
# on_investigate (the graph runner). Polling only — no webhook (venue NAT).
#
# FAILURE MODES (each handled explicitly):
# 1. TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing — failed ActionResult /
#    failed dict BEFORE constructing any Bot (same guard as send_manager_alert).
# 2. send_message / get_updates / answer / edit raising (network, API, rate
#    limit) — caught, logged once, loop continues (poll_callbacks) or failed
#    ActionResult (send_alert).
# 3. A callback_query with data not starting with "investigate:" — ignored
#    (offset still advances; other button flows stay possible).
# ---------------------------------------------------------------------------


async def send_alert(case: CasePayload) -> ActionResult:
    """Alert the manager that a case arrived; status="sent" + message id ref.

    Text is exactly the P6.5 spec: order id, one-line summary, urgency. The
    message carries a single 🔍 INVESTIGATE button whose callback_data embeds
    the case_id for poll_callbacks. Never raises.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return ActionResult(
            type="telegram", status="failed", error="Telegram env not configured"
        )
    text = (
        f"🚨 CUSTOMER ISSUE — Order #{case.order_id}\n"
        f"{case.summary or ''}\n"
        f"Urgency: {case.urgency or 'unknown'}"
    )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔍 INVESTIGATE", callback_data=f"investigate:{case.case_id}")]]
    )
    try:
        bot = Bot(token)
        sent = await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
        return ActionResult(type="telegram", status="sent", ref=str(sent.message_id))
    except Exception as exc:  # noqa: BLE001
        return ActionResult(
            type="telegram", status="failed", error=f"Telegram alert failed: {exc}"
        )


async def poll_callbacks(on_investigate, interval: int = 2) -> None | dict:
    """Poll get_updates forever; on "investigate:<case_id>" press, run the flow.

    Answers the callback, rewrites the message, then awaits
    on_investigate(case_id). Offset always advances past the batch max.
    Missing TELEGRAM_BOT_TOKEN -> logged once, failed dict (no bot to poll).
    Every other failure is logged once and polling continues.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        _log_callback_failure("Telegram env not configured — callback polling disabled")
        return {"status": "failed", "error": "Telegram env not configured"}
    try:
        bot = Bot(token)
    except Exception as exc:  # noqa: BLE001
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
                if cb and getattr(cb, "data", "") and cb.data.startswith("investigate:"):
                    case_id = cb.data.split(":", 1)[1]
                    try:
                        await bot.answer_callback_query(callback_query_id=cb.id)
                        await bot.edit_message_text(
                            "🔍 Investigation started — watch the console",
                            message_id=cb.message.message_id,
                            chat_id=cb.message.chat.id,
                        )
                        await on_investigate(case_id)
                    except Exception as exc:  # noqa: BLE001
                        _log_callback_failure(f"investigate {case_id} failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            _log_callback_failure(f"get_updates failed: {exc}")
        await asyncio.sleep(interval)
