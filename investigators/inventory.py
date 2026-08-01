"""Inventory investigator — queries tally_erp.db inventory ONLY (TRD §6)."""

from __future__ import annotations

from investigators.base import build_node
from investigators.tools import query_inventory_tool

HYPOTHESIS_ID = "h_inventory_damage"

TOOLS = [query_inventory_tool]

_SYSTEM_PROMPT = (
    "You are the inventory investigator on an operations-detective team. "
    "Your ONLY tool is query_inventory — it reads Tally ERP inventory for an "
    "order's line items (sku, qty, stock, picked). "
    f"Investigate hypothesis {HYPOTHESIS_ID}: inventory damaged in staging. "
    "DECISIVE RULE: picked=1 means the goods were picked and LOADED onto the "
    "truck for this order — they left the DC. A loaded order cannot be damaged "
    "in staging, so when picked=1 you MUST set eliminates=[h_inventory_damage] "
    "and NOT support the hypothesis, even if the shipment is late. "
    "Quote exact facts in detail (e.g. stock=12, qty=500, picked=1)."
)

node = build_node(
    system_prompt=_SYSTEM_PROMPT,
    tool=query_inventory_tool,
    hypothesis_id=HYPOTHESIS_ID,
    eligible_ids=[HYPOTHESIS_ID],
)
