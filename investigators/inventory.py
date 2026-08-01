"""Inventory investigator — queries tally_erp.db inventory ONLY (TRD §6).

Handles: inventory damage, inventory count error, payment hold, invoice amount mismatch.
The hypothesis_id is passed dynamically from the graph.
"""

from __future__ import annotations

from investigators.base import build_node
from investigators.tools import query_inventory_tool, query_tally_order_tool

TOOLS = [query_inventory_tool]

_SYSTEM_PROMPT_DAMAGE = (
    "You are the inventory investigator on an operations-detective team. "
    "Your ONLY tool is query_inventory — it reads Tally ERP inventory for an "
    "order's line items (sku, qty, stock, picked). "
    "DECISIVE RULE: picked=1 means the goods were picked and LOADED onto the "
    "truck for this order — they left the DC. A loaded order cannot be damaged "
    "in staging, so when picked=1 you MUST set eliminates=[h_inventory_damage] "
    "and NOT support the hypothesis, even if the shipment is late. "
    "Quote exact facts in detail (e.g. stock=12, qty=500, picked=1)."
)

_SYSTEM_PROMPT_COUNT = (
    "You are the inventory investigator on an operations-detective team. "
    "Your ONLY tool is query_inventory — it reads Tally ERP inventory for an "
    "order's line items (sku, qty, stock, picked). "
    "Check if the system stock count matches the ordered quantity. "
    "If stock < qty, there is a count mismatch — support the hypothesis. "
    "If stock >= qty, the count is correct — eliminate the hypothesis. "
    "Quote exact facts in detail (e.g. stock=490, qty=500, mismatch=10)."
)

_SYSTEM_PROMPT_PAYMENT = (
    "You are the Tally ERP investigator checking payment status. "
    "Your tool is query_tally_order — it reads the full order record including "
    "payment_received, stock_booked, invoice_amount, po_amount. "
    "Check payment_received — if 0, payment has NOT been received. "
    "Check if the order is delivered but unpaid — that signals a bank reconciliation hold. "
    "Quote exact facts (e.g. payment_received=0, delivered=1, status=Dispatched)."
)

_SYSTEM_PROMPT_INVOICE = (
    "You are the Tally ERP investigator checking invoice accuracy. "
    "Your tool is query_tally_order — it reads the full order record including "
    "invoice_amount and po_amount. "
    "If invoice_amount != po_amount, the invoice is wrong — support h_invoice_amount_mismatch "
    "and ELIMINATE h_invoice_tax_error (the issue is the amount, not the tax rate). "
    "If they match, the invoice amount is correct — eliminate h_invoice_amount_mismatch. "
    "Quote exact facts (e.g. invoice_amount=96000, po_amount=92000, difference=4000)."
)


def make_node(hypothesis_id: str, eligible_ids: list[str] | None = None):
    """Create an investigator node for a specific inventory-related hypothesis."""
    prompts = {
        "h_inventory_damage": _SYSTEM_PROMPT_DAMAGE,
        "h_inventory_count_error": _SYSTEM_PROMPT_COUNT,
        "h_payment_hold_bank_recon": _SYSTEM_PROMPT_PAYMENT,
        "h_invoice_amount_mismatch": _SYSTEM_PROMPT_INVOICE,
    }
    prompt = prompts.get(hypothesis_id, _SYSTEM_PROMPT_DAMAGE)
    tool = query_tally_order_tool if hypothesis_id in ("h_payment_hold_bank_recon", "h_invoice_amount_mismatch") else query_inventory_tool
    return build_node(
        system_prompt=prompt,
        tool=tool,
        hypothesis_id=hypothesis_id,
        eligible_ids=eligible_ids or [hypothesis_id],
    )


# Default node for backward compatibility (shipment_delay case)
node = make_node("h_inventory_damage")
