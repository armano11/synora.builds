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


def _query_tally_order(order_id: str) -> dict:
    """Tally ERP order record (payment, stock, invoice fields)."""
    return eq.query_tally(order_id)


def _query_transport(order_id: str) -> dict:
    """Transport booking record."""
    return eq.query_transport(order_id)


query_gst_tool = StructuredTool.from_function(
    func=_query_gst,
    name="query_gst",
    description=(
        "Query the GST portal for an order's e-way bill. Returns: eway_number, "
        "validity_from, validity_to, eway_status (active | expired | renewal_requested), "
        "gstr3b_filed (0 or 1), docs_incomplete (0 or 1), tax_rate_wrong (0 or 1). "
        "Use for hypotheses: e-way bill expired, customs docs incomplete, invoice tax error."
    ),
)

query_inventory_tool = StructuredTool.from_function(
    func=_query_inventory,
    name="query_inventory",
    description=(
        "Query Tally ERP inventory for an order's line items. Returns items with "
        "sku, qty, unit_price, stock, name, and picked (1 = goods picked and loaded "
        "for dispatch, so low stock is EXPECTED, not damage). Use for hypothesis: "
        "inventory damaged in staging, inventory count error."
    ),
)

query_tally_order_tool = StructuredTool.from_function(
    func=_query_tally_order,
    name="query_tally_order",
    description=(
        "Query Tally ERP for an order's full record. Returns: order_id, customer, "
        "order_date, status (Pending | Dispatched | In Transit | Delivered), "
        "dispatch_date, transport_booking, amount, payment_received (0 or 1), "
        "stock_booked (0 or 1), invoice_amount, po_amount, delivered (0 or 1), "
        "and items with sku, qty, stock, picked. Use for hypotheses: payment hold, "
        "dispatch failure, invoice amount mismatch, inventory mismatch."
    ),
)

query_transport_tool = StructuredTool.from_function(
    func=_query_transport,
    name="query_transport",
    description=(
        "Query the transport system for an order's vehicle booking. Returns: "
        "order_id, vehicle_no, driver, status, breakdown_claimed (0 or 1), "
        "breakdown_reason, license_expired (0 or 1), delivered (0 or 1). "
        "Use for hypotheses: transport breakdown, compliance license expired, "
        "payment hold (buyer default), customs inspection."
    ),
)
