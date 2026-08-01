"""Warehouse investigator — checks the dispatch record in tally_erp.db (TRD §6).

Handles: dispatch failure, inventory damage/loss in DC.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from enterprise import query as eq
from investigators.base import build_node

_query_tally = StructuredTool.from_function(
    func=eq.query_tally,
    name="query_tally",
    description=(
        "Query Tally ERP for an order's record and line items. Returns: order_id, "
        "customer, order_date, status (Pending | Dispatched | In Transit | Delivered), "
        "dispatch_date, transport_booking, amount, payment_received, stock_booked, "
        "invoice_amount, po_amount, delivered, and items with sku, qty, stock, picked. "
        "Use for hypothesis: dispatch never happened, stock damaged or lost in DC."
    ),
)

TOOLS = [_query_tally]

_SYSTEM_PROMPT_DISPATCH = (
    "You are the warehouse investigator on an operations-detective team. "
    "Your ONLY tool is query_tally — it reads the dispatch order record for an "
    "order. "
    "DECISIVE RULE: status=Dispatched with a dispatch_date means the goods LEFT "
    "the DC — dispatch DID happen, so you MUST set eliminates=[h_dispatch_failure] "
    "and NOT support the hypothesis, even if the shipment is late. "
    "Quote exact facts in detail (e.g. status=Dispatched, transport_booking=none) "
    "and include the COMPLETE tool result as raw."
)

_SYSTEM_PROMPT_DAMAGE_LOSS = (
    "You are the warehouse investigator checking for stock damage or loss in the DC. "
    "Your tool is query_tally — it reads the order record and line items. "
    "Check stock_booked — if 0, stock was NOT booked for this order, suggesting a mismatch. "
    "Check if picked=0 and stock < qty — goods may be damaged or lost. "
    "If picked=1, goods left the DC — eliminate the damage/loss hypothesis. "
    "Quote exact facts (e.g. stock_booked=0, picked=1, stock=490, qty=500)."
)


def make_node(hypothesis_id: str, eligible_ids: list[str] | None = None):
    """Create an investigator node for a specific warehouse-related hypothesis."""
    prompts = {
        "h_dispatch_failure": _SYSTEM_PROMPT_DISPATCH,
        "h_inventory_damage_loss": _SYSTEM_PROMPT_DAMAGE_LOSS,
    }
    prompt = prompts.get(hypothesis_id, _SYSTEM_PROMPT_DISPATCH)
    return build_node(
        system_prompt=prompt,
        tool=_query_tally,
        hypothesis_id=hypothesis_id,
        eligible_ids=eligible_ids or [hypothesis_id],
    )


# Default node for backward compatibility
node = make_node("h_dispatch_failure")
