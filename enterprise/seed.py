"""Build the 4 mock enterprise SQLite DBs + Orbit's closed-case ledger.

Contradictions by design (TRD §6). Scenario day: 2026-07-20.
#402: shipped 2026-07-14 → stuck 6 days. E-way bill expired 2026-07-18.

Deterministic: hardcoded dates, no random, no now(). Idempotent rebuild.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent / "dbs"

# The fixture world's "today" — every relative fact (last_scan_age_days etc.)
# is computed against this, never the wall clock (deterministic demo).
SCENARIO_TODAY: date = date(2026, 7, 20)

# ---------------------------------------------------------------------------
# ground truth — order 402
# ---------------------------------------------------------------------------

TALLY_ORDERS = [
    ("402", "Priya Textiles — Mumbai", "2026-07-10", "Dispatched", "2026-07-14", "none", 184200.00),
]

TALLY_ORDER_ITEMS = [
    ("402", "COT-1000", 500, 368.40),
]

TALLY_INVENTORY = [
    # picked=1 → goods picked & loaded for dispatch (low stock is expected, not damage)
    ("COT-1000", "Cotton sheeting 40s", 12, "Mangaluru DC", 1),
]

GST_EWAY_BILLS = [
    ("402", "EWB-4022026", "2026-07-13", "2026-07-18", "expired", 0),
]

DELHIVERY_SHIPMENTS = [
    ("402", "DLH-88412", "In Transit", "2026-07-14 09:12:00", "Hubli Checkpoint"),
]

TRANSPORT_BOOKINGS = [
    ("402", "KA-19-G-4212", "Ramesh K", "breakdown", 1, "Engine failure — awaiting repair"),
]

# ---------------------------------------------------------------------------
# filler rows — #402 must never look staged (8-12 rows per DB)
# ---------------------------------------------------------------------------

FILLER_ORDERS = [
    ("117", "Karnataka Fabrics", "2026-06-28", "Delivered", "2026-07-01", "KA-11-B-7781", 96400.00),
    ("118", "Shree Textiles Hubli", "2026-06-30", "Delivered", "2026-07-03", "KA-03-C-2214", 51250.00),
    ("120", "Chennai Garments", "2026-07-02", "Delivered", "2026-07-05", "TN-22-D-9930", 141000.00),
    ("121", "Nellore Synthetics", "2026-07-03", "In Transit", "2026-07-15", "AP-16-A-4402", 73800.00),
    ("125", "Kochi Wholesale Mart", "2026-07-05", "Dispatched", "2026-07-16", "KL-07-E-1120", 205500.00),
    ("130", "Jaipur Handloom House", "2026-07-08", "Dispatched", "2026-07-17", "RJ-14-F-5531", 88400.00),
    ("133", "Indore Retail Chain", "2026-07-09", "In Transit", "2026-07-17", "MP-09-G-6677", 129300.00),
    ("138", "Surat Polyesters", "2026-07-10", "Dispatched", "2026-07-18", "GJ-01-H-8810", 62300.00),
    ("141", "Bhopal Apparels", "2026-07-11", "In Transit", "2026-07-18", "MP-04-J-2019", 98700.00),
    ("145", "Visakhapatnam Textiles", "2026-07-12", "Pending", None, None, 45100.00),
    ("148", "Nagpur Cotton Co", "2026-07-13", "Pending", None, None, 112800.00),
    ("150", "Pune Fabric Mart", "2026-07-14", "Pending", None, None, 77500.00),
]

FILLER_ITEMS = [
    ("117", "COT-1000", 400, 241.00),
    ("118", "COT-2000", 250, 205.00),
    ("120", "POL-3000", 300, 470.00),
    ("121", "COT-1000", 220, 335.45),
    ("125", "POL-3000", 420, 489.29),
    ("130", "SIL-4000", 180, 491.11),
    ("133", "COT-2000", 600, 215.50),
    ("138", "POL-3000", 140, 445.00),
    ("141", "SIL-4000", 200, 493.50),
    ("145", "COT-1000", 150, 300.67),
    ("148", "COT-2000", 500, 225.60),
    ("150", "POL-3000", 170, 455.88),
]

FILLER_INVENTORY = [
    ("COT-2000", "Cotton sheeting 60s", 240, "Hubli DC", 0),
    ("POL-3000", "Polyester twill", 90, "Mangaluru DC", 1),
    ("SIL-4000", "Silk blend", 35, "Bengaluru DC", 0),
    ("LIN-5000", "Linen voile", 118, "Mangaluru DC", 0),
    ("DEN-6000", "Denim 12oz", 260, "Hubli DC", 0),
]

FILLER_GST = [
    ("117", "EWB-1172026", "2026-06-29", "2026-07-04", "expired", 1),
    ("118", "EWB-1182026", "2026-07-01", "2026-07-06", "expired", 1),
    ("120", "EWB-1202026", "2026-07-03", "2026-07-08", "expired", 1),
    ("121", "EWB-1212026", "2026-07-14", "2026-07-19", "active", 1),
    ("125", "EWB-1252026", "2026-07-15", "2026-07-20", "active", 1),
    ("130", "EWB-1302026", "2026-07-16", "2026-07-21", "active", 1),
    ("133", "EWB-1332026", "2026-07-16", "2026-07-21", "active", 1),
    ("138", "EWB-1382026", "2026-07-17", "2026-07-22", "active", 1),
    ("141", "EWB-1412026", "2026-07-17", "2026-07-22", "active", 1),
]

FILLER_DELHIVERY = [
    ("117", "DLH-77001", "Delivered", "2026-07-03 18:40:00", "Bengaluru Hub"),
    ("118", "DLH-77002", "Delivered", "2026-07-05 11:05:00", "Hubli Checkpoint"),
    ("120", "DLH-77003", "Delivered", "2026-07-07 09:30:00", "Chennai Hub"),
    ("121", "DLH-77004", "In Transit", "2026-07-19 07:50:00", "Nellore Hub"),
    ("125", "DLH-77005", "In Transit", "2026-07-19 14:22:00", "Kochi Hub"),
    ("130", "DLH-77006", "In Transit", "2026-07-19 10:11:00", "Jaipur Hub"),
    ("133", "DLH-77007", "In Transit", "2026-07-19 12:45:00", "Indore Hub"),
    ("138", "DLH-77008", "In Transit", "2026-07-19 08:03:00", "Surat Hub"),
    ("141", "DLH-77009", "In Transit", "2026-07-19 16:18:00", "Bhopal Hub"),
]

FILLER_TRANSPORT = [
    ("117", "KA-11-B-7781", "Suresh M", "delivered", 0, None),
    ("118", "KA-03-C-2214", "Anil P", "delivered", 0, None),
    ("120", "TN-22-D-9930", "Murugan S", "delivered", 0, None),
    ("121", "AP-16-A-4402", "Venkatesh R", "in_transit", 0, None),
    ("125", "KL-07-E-1120", "Jacob T", "in_transit", 0, None),
    ("130", "RJ-14-F-5531", "Mahesh D", "in_transit", 0, None),
    ("133", "MP-09-G-6677", "Ravi K", "in_transit", 0, None),
    ("138", "GJ-01-H-8810", "Imran S", "in_transit", 0, None),
    ("141", "MP-04-J-2019", "Deepak N", "in_transit", 0, None),
    ("145", "MH-12-K-3310", "Sagar V", "assigned", 0, None),
]

# ---------------------------------------------------------------------------
# 5 closed fixture cases (case board / generalization proof)
# ---------------------------------------------------------------------------

CLOSED_CASES = [
    ("case_002", "211", "payment_hold", "payment_hold.release_blocked", 0.92, "closed",
     "2026-07-05 14:22:00", "Buyer payment held by bank reconciliation; released after proof of delivery"),
    ("case_003", "156", "inventory_mismatch", "inventory_mismatch.stock_count_mismatch", 0.91, "closed",
     "2026-07-08 09:40:00", "System stock 500 vs counted 490; cycle-count error corrected"),
    ("case_004", "198", "customs_block", "customs_block.documents_incomplete", 0.89, "closed",
     "2026-07-11 17:05:00", "Missing commercial invoice on e-way portal; document re-uploaded"),
    ("case_005", "173", "invoice_dispute", "invoice_dispute.amount_mismatch", 0.90, "closed",
     "2026-07-13 11:30:00", "Invoice total vs PO mismatch of ₹4,200; credit note issued"),
    ("case_006", "187", "compliance_block", "compliance_block.license_expired", 0.93, "closed",
     "2026-07-15 16:48:00", "Transport operator's NPST license lapsed; renewed and shipment released"),
]


def _connect(db_name: str) -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_DIR / db_name)
    conn.row_factory = sqlite3.Row
    return conn


def _rebuild(db_name: str, ddl: list[str], tables: dict[str, list[tuple]]) -> None:
    conn = _connect(db_name)
    for statement in ddl:
        conn.execute(statement)
    for table, rows in tables.items():
        if rows:
            placeholders = ",".join("?" * len(rows[0]))
            conn.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
    conn.commit()
    conn.close()


def rebuild() -> None:
    """Idempotent: drop and recreate all DBs with ground-truth + filler data."""
    _rebuild(
        "tally_erp.db",
        [
            "DROP TABLE IF EXISTS order_items",
            "DROP TABLE IF EXISTS inventory",
            "DROP TABLE IF EXISTS orders",
            "CREATE TABLE orders (order_id TEXT PRIMARY KEY, customer TEXT, order_date TEXT,"
            " status TEXT, dispatch_date TEXT, transport_booking TEXT, amount REAL)",
            "CREATE TABLE order_items (order_id TEXT, sku TEXT, qty INTEGER, unit_price REAL,"
            " PRIMARY KEY (order_id, sku))",
            "CREATE TABLE inventory (sku TEXT PRIMARY KEY, name TEXT, stock INTEGER,"
            " warehouse TEXT, picked INTEGER)",
        ],
        {
            "orders": TALLY_ORDERS + FILLER_ORDERS,
            "order_items": TALLY_ORDER_ITEMS + FILLER_ITEMS,
            "inventory": TALLY_INVENTORY + FILLER_INVENTORY,
        },
    )
    _rebuild(
        "gst_portal.db",
        [
            "DROP TABLE IF EXISTS eway_bills",
            "CREATE TABLE eway_bills (order_id TEXT PRIMARY KEY, eway_number TEXT,"
            " validity_from TEXT, validity_to TEXT, eway_status TEXT, gstr3b_filed INTEGER)",
        ],
        {"eway_bills": GST_EWAY_BILLS + FILLER_GST},
    )
    _rebuild(
        "delhivery.db",
        [
            "DROP TABLE IF EXISTS shipments",
            "CREATE TABLE shipments (order_id TEXT PRIMARY KEY, tracking_id TEXT, status TEXT,"
            " last_scan_at TEXT, last_scan_location TEXT)",
        ],
        {"shipments": DELHIVERY_SHIPMENTS + FILLER_DELHIVERY},
    )
    _rebuild(
        "transport.db",
        [
            "DROP TABLE IF EXISTS bookings",
            "CREATE TABLE bookings (order_id TEXT PRIMARY KEY, vehicle_no TEXT, driver TEXT,"
            " status TEXT, breakdown_claimed INTEGER, breakdown_reason TEXT)",
        ],
        {"bookings": TRANSPORT_BOOKINGS + FILLER_TRANSPORT},
    )
    _rebuild(
        "cases.db",
        [
            "DROP TABLE IF EXISTS cases",
            "CREATE TABLE cases (case_id TEXT PRIMARY KEY, order_id TEXT, case_type TEXT,"
            " root_cause TEXT, confidence REAL, status TEXT, created_at TEXT, verdict_summary TEXT)",
        ],
        {"cases": CLOSED_CASES},
    )


if __name__ == "__main__":
    rebuild()
    print("enterprise DBs rebuilt in", DB_DIR)
