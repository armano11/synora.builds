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

import os

from telegram import Bot

from actions._common import eway_bill_culprit
from actions.eta_recalc import recalc_eta
from contracts import ActionResult, CasePayload, Verdict


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
