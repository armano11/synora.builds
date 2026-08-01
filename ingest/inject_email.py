"""CLI injection — the demo's dev fallback: `python -m ingest.inject_email --order 402`.

Builds the SAME CasePayload shape as the email path (source="cli",
pre-filled intent fields) and funnels it through THE shared pending-case
path (ingest.pending.create_pending_case) — judges never see this; it exists
so the demo has a zero-setup trigger when Gmail is not wired.

# ---------------------------------------------------------------------------
# FAILURE MODES (each handled explicitly):
# 1. --order missing or not digits — printed error, exit 1 (argparse handles
#    the missing case; we validate digits), never a traceback.
# 2. create_pending_case returns a failure dict (cases table missing, DB
#    locked) — printed as-is, exit 1.
# 3. Any unexpected exception during payload build/insert — printed, exit 1.
# ---------------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
from uuid import uuid4

from contracts import CasePayload
from ingest.pending import create_pending_case

DEFAULTS = {
    "symptom": "shipment stuck",
    "summary": "customer reports stuck order",
    "intent": "angry_customer",
    "urgency": "high",
    "sender": "ops-internal@orbit.local",
}

# Per-case-type demo symptoms for multi-case injection
CASE_SYMPTOMS = {
    "shipment_delay": "shipment stuck at Hubli for 6 days, buyer cancelling, Monday market deadline",
    "payment_hold": "payment held by bank for delivered order, buyer threatening legal action",
    "inventory_mismatch": "stock mismatch between system and physical count, order short by 10 units",
    "customs_block": "customs hold at Mumbai port, documents incomplete, clearance stuck 5 days",
    "invoice_dispute": "invoice amount dispute, buyer refusing payment, billing error of 4000 rupees",
    "compliance_block": "transport license expired, vehicle grounded, shipment not moving",
}

# Map case types to their anchor order IDs
CASE_ORDERS = {
    "shipment_delay": "402",
    "payment_hold": "501",
    "inventory_mismatch": "502",
    "customs_block": "503",
    "invoice_dispute": "504",
    "compliance_block": "505",
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ingest.inject_email",
        description="Inject a pending case directly (demo dev fallback).",
    )
    parser.add_argument("--order", required=False, default=None, help="order id, digits only")
    parser.add_argument("--case-type", default=None,
                        choices=list(CASE_SYMPTOMS.keys()),
                        help="inject a specific case type with its anchor order")
    parser.add_argument("--symptom", default=None)
    parser.add_argument("--summary", default=DEFAULTS["summary"])
    parser.add_argument("--intent", default=DEFAULTS["intent"])
    parser.add_argument("--urgency", default=DEFAULTS["urgency"])
    parser.add_argument("--sender", default=DEFAULTS["sender"])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    
    # Case-type-based injection
    if args.case_type:
        order_id = CASE_ORDERS.get(args.case_type, args.order or "402")
        symptom = args.symptom or CASE_SYMPTOMS[args.case_type]
    else:
        if not args.order:
            print("Error: --order or --case-type is required")
            return 1
        order_id = args.order
        symptom = args.symptom or DEFAULTS["symptom"]
    
    if not order_id.isdigit():
        print(f"Error: order id must be digits, got: {order_id!r}")
        return 1
    case = CasePayload(
        case_id=f"cli-{order_id}-{uuid4().hex[:8]}",
        order_id=order_id,
        symptom=symptom,
        source="cli",
        sender=args.sender,
        intent=args.intent,
        urgency=args.urgency,
        summary=args.summary,
    )
    try:
        result = create_pending_case(case)
    except Exception as exc:  # noqa: BLE001 — never a traceback
        print(f"Error: {exc}")
        return 1
    if isinstance(result, dict):
        print(f"Error: {result.get('error', 'pending case failed')}")
        return 1
    print(f"Pending case created: {result} (order {case.order_id}, source cli)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
