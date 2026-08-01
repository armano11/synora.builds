"""Read-only query tools for the mock enterprise systems (TRD §6).

Each system exposes one function returning a plain dict ({} when unknown).
No writes here — investigators and the executor write via their own paths.
"""

from __future__ import annotations

import sqlite3

from enterprise.seed import DB_DIR


def _fetch_one(db_name: str, sql: str, order_id: str) -> dict:
    try:
        conn = sqlite3.connect(DB_DIR / db_name)
        conn.row_factory = sqlite3.Row
        row = conn.execute(sql, (order_id,)).fetchone()
        conn.close()
        return dict(row) if row else {}
    except sqlite3.Error:
        return {}


def query_tally(order_id: str) -> dict:
    """Order-level record + line items joined to inventory (tally_erp.db)."""
    result = _fetch_one(
        "tally_erp.db",
        "SELECT order_id, customer, order_date, status, dispatch_date,"
        " transport_booking, amount FROM orders WHERE order_id = ?",
        order_id,
    )
    if not result:
        return {}
    try:
        conn = sqlite3.connect(DB_DIR / "tally_erp.db")
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT oi.sku, oi.qty, oi.unit_price, i.stock, i.name"
            " FROM order_items oi LEFT JOIN inventory i ON oi.sku = i.sku"
            " WHERE oi.order_id = ?",
            (order_id,),
        ).fetchall()
        conn.close()
        result["items"] = [dict(row) for row in rows]
    except sqlite3.Error:
        result["items"] = []
    return result


def query_gst(order_id: str) -> dict:
    """E-way bill record — the writable table (executor renews here)."""
    return _fetch_one(
        "gst_portal.db",
        "SELECT order_id, eway_number, validity_from, validity_to,"
        " eway_status, gstr3b_filed FROM eway_bills WHERE order_id = ?",
        order_id,
    )


def query_delhivery(order_id: str) -> dict:
    """Shipment tracking record (delhivery.db)."""
    return _fetch_one(
        "delhivery.db",
        "SELECT order_id, tracking_id, status, last_scan_at, last_scan_location"
        " FROM shipments WHERE order_id = ?",
        order_id,
    )


def query_transport(order_id: str) -> dict:
    """Transport booking record (transport.db)."""
    return _fetch_one(
        "transport.db",
        "SELECT order_id, vehicle_no, driver, status, breakdown_claimed, breakdown_reason"
        " FROM bookings WHERE order_id = ?",
        order_id,
    )


def list_closed_cases() -> list[dict]:
    """The 5 closed fixture cases (case board / generalization proof)."""
    try:
        conn = sqlite3.connect(DB_DIR / "cases.db")
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT case_id, order_id, case_type, root_cause, confidence, status,"
            " created_at, verdict_summary FROM cases WHERE status = 'closed' ORDER BY case_id"
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []
