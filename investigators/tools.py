"""Read-only query tools for investigators (TRD §6).

Each investigator's tool sees ONLY its own system — the contradictions
live in the data, correlation happens in the brain.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from enterprise import query as eq


def _query_gst(order_id: str) -> dict:
    """GST portal e-way bill record."""
    return eq.query_gst(order_id)


def _query_inventory(order_id: str) -> dict:
    """Tally ERP inventory for the order's line items (sku, qty, stock, picked)."""
    row = eq.query_tally(order_id)
    return {"order_id": order_id, "items": row.get("items", [])}


query_gst_tool = StructuredTool.from_function(
    func=_query_gst,
    name="query_gst",
    description=(
        "Query the GST portal for an order's e-way bill. Returns: eway_number, "
        "validity_from, validity_to, eway_status (active | expired | renewal_requested), "
        "gstr3b_filed (0 or 1). Use for hypothesis: e-way bill expired."
    ),
)

query_inventory_tool = StructuredTool.from_function(
    func=_query_inventory,
    name="query_inventory",
    description=(
        "Query Tally ERP inventory for an order's line items. Returns items with "
        "sku, qty, unit_price, stock, name, and picked (1 = goods picked and loaded "
        "for dispatch, so low stock is EXPECTED, not damage). Use for hypothesis: "
        "inventory damaged in staging."
    ),
)
