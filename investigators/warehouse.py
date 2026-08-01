"""Warehouse investigator — checks the dispatch record in tally_erp.db (TRD §6).

Hypothesis h_dispatch_failure: dispatch never happened. The order record is
the source of truth: status=Dispatched + a dispatch_date means the goods LEFT
the DC, so the hypothesis is FALSE and MUST be eliminated.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from enterprise import query as eq
from investigators.base import build_node

HYPOTHESIS_ID = "h_dispatch_failure"

_query_tally = StructuredTool.from_function(
    func=eq.query_tally,
    name="query_tally",
    description=(
        "Query Tally ERP for an order's record and line items. Returns: order_id, "
        "customer, order_date, status (Pending | Dispatched | In Transit | Delivered), "
        "dispatch_date, transport_booking, amount, and items with sku, qty, stock, "
        "picked. Use for hypothesis: dispatch never happened."
    ),
)

TOOLS = [_query_tally]

_SYSTEM_PROMPT = (
    "You are the warehouse investigator on an operations-detective team. "
    "Your ONLY tool is query_tally — it reads the dispatch order record for an "
    "order. "
    f"Investigate hypothesis {HYPOTHESIS_ID}: dispatch never happened. "
    "DECISIVE RULE: status=Dispatched with a dispatch_date means the goods LEFT "
    "the DC — dispatch DID happen, so you MUST set eliminates=[h_dispatch_failure] "
    "and NOT support the hypothesis, even if the shipment is late. "
    "Quote exact facts in detail (e.g. status=Dispatched, transport_booking=none) "
    "and include the COMPLETE tool result as raw."
)

node = build_node(
    system_prompt=_SYSTEM_PROMPT,
    tool=_query_tally,
    hypothesis_id=HYPOTHESIS_ID,
    eligible_ids=[HYPOTHESIS_ID],
)
