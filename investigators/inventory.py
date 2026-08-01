"""Inventory investigator — queries tally_erp.db inventory ONLY (TRD §6)."""

from __future__ import annotations

from investigators.base import build_node
from investigators.tools import query_inventory_tool

HYPOTHESIS_ID = "h_inventory_damage"

TOOLS = [query_inventory_tool]

_SYSTEM_PROMPT = (
    "You are the inventory investigator on an operations-detective team. "
    "Your ONLY tool is query_inventory — it reads Tally ERP inventory for an "
    "order's line items (sku, qty, stock). "
    f"Investigate hypothesis {HYPOTHESIS_ID}: inventory damaged in staging. "
    "If stock covers the ordered qty, the hypothesis is CLEAN — eliminate it. "
    "Quote exact facts in detail (e.g. stock=12, qty=500)."
)

node = build_node(
    system_prompt=_SYSTEM_PROMPT,
    tool=query_inventory_tool,
    hypothesis_id=HYPOTHESIS_ID,
    eligible_ids=[HYPOTHESIS_ID],
)
