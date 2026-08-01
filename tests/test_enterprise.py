"""P2 — the enterprise fixtures must encode the contradictions of the #402 story.

Scenario day (hardcoded, no now(), no random): 2026-07-20.
#402 shipped 2026-07-14 → stuck 6 days. E-way bill expired 2026-07-18.

Written FIRST (TDD RED): fails until seed.py + query.py exist.
"""

import sqlite3
from pathlib import Path

import pytest

import enterprise.query as q
import enterprise.seed as seed

DB_DIR = Path(__file__).resolve().parents[1] / "enterprise" / "dbs"

GROUND_TRUTH_EXPIRED = "expired"
GROUND_TRUTH_RENEWAL = "renewal_requested"


def test_tally_marks_402_dispatched_but_no_transport_booking():
    row = q.query_tally("402")
    assert row["status"] == "Dispatched"
    assert row["transport_booking"] in ("none", None)


def test_gst_eway_bill_expired_because_gstr3b_unfiled():
    row = q.query_gst("402")
    assert row["eway_status"] == "expired"
    assert row["gstr3b_filed"] == 0


def test_delhivery_in_transit_but_last_scan_six_days_old():
    row = q.query_delhivery("402")
    assert row["status"] == "In Transit"
    assert row["last_scan_at"].startswith("2026-07-14")


def test_transport_claims_breakdown():
    row = q.query_transport("402")
    assert row["breakdown_claimed"] == 1
    assert row["breakdown_reason"]


def test_inventory_has_stock_for_402_sku():
    row = q.query_tally("402")
    assert row["items"], "order 402 must have line items"
    assert any(item["stock"] == 12 for item in row["items"])


def test_gst_eway_bill_is_writable_renewal_path():
    conn = sqlite3.connect(DB_DIR / "gst_portal.db")
    conn.execute(
        "UPDATE eway_bills SET eway_status = ? WHERE order_id = '402'",
        (GROUND_TRUTH_RENEWAL,),
    )
    conn.commit()
    conn.close()
    try:
        assert q.query_gst("402")["eway_status"] == GROUND_TRUTH_RENEWAL
    finally:
        conn = sqlite3.connect(DB_DIR / "gst_portal.db")
        conn.execute(
            "UPDATE eway_bills SET eway_status = ? WHERE order_id = '402'",
            (GROUND_TRUTH_EXPIRED,),
        )
        conn.commit()
        conn.close()


def test_five_closed_fixture_cases_exist():
    cases = q.list_closed_cases()
    assert len(cases) == 5
    for case in cases:
        assert case["status"] == "closed"
        assert case["root_cause"]
        assert 0.0 <= case["confidence"] <= 1.0


def test_query_functions_return_dicts():
    for fn in (q.query_tally, q.query_gst, q.query_delhivery, q.query_transport):
        assert isinstance(fn("402"), dict)


def test_unknown_order_returns_empty_dict():
    assert q.query_gst("999999") == {}
    assert q.query_tally("999999") == {}


def test_seed_is_idempotent():
    seed.rebuild()
    before = q.query_tally("402")
    seed.rebuild()
    assert q.query_tally("402") == before
