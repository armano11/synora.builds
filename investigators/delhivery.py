"""Delhivery investigator — checks the shipment record in delhivery.db (TRD §6).

Second check on h_dispatch_failure: a shipment record with status=In Transit
and a last scan location means the goods were handed to the carrier and are
moving — dispatch DID happen, so the hypothesis is FALSE.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from enterprise import query as eq
from investigators.base import build_node

HYPOTHESIS_ID = "h_dispatch_failure"

_query_delhivery = StructuredTool.from_function(
    func=eq.query_delhivery,
    name="query_delhivery",
    description=(
        "Query the Delhivery shipment tracker for an order. Returns: order_id, "
        "tracking_id, status (Pending | In Transit | Delivered), last_scan_at, "
        "last_scan_location. Use for hypothesis: dispatch never happened."
    ),
)

TOOLS = [_query_delhivery]

_SYSTEM_PROMPT = (
    "You are the shipment-tracker investigator on an operations-detective team. "
    "Your ONLY tool is query_delhivery — it reads the courier shipment record "
    "for an order. "
    f"Investigate hypothesis {HYPOTHESIS_ID}: dispatch never happened. "
    "DECISIVE RULE: a shipment record with status=In Transit and a last scan "
    "location means the goods LEFT the DC and are with the carrier — dispatch "
    "DID happen, so you MUST set eliminates=[h_dispatch_failure] and NOT "
    "support the hypothesis, even if the shipment is stalled at a checkpoint. "
    "Quote exact facts in detail (e.g. status=In Transit, last_scan_location=Hubli "
    "Checkpoint) and include the COMPLETE tool result as raw."
)

node = build_node(
    system_prompt=_SYSTEM_PROMPT,
    tool=_query_delhivery,
    hypothesis_id=HYPOTHESIS_ID,
    eligible_ids=[HYPOTHESIS_ID],
)
