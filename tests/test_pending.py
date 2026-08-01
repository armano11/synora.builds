"""P6.5 — pending-case path tests.

create_pending_case is THE shared pending-case creation path: inject_email
and the poller's callback both call it. It inserts into cases.db with
status='pending' and never raises (missing table -> failure dict).
"""

import sqlite3

import pytest

from contracts import CasePayload
from enterprise.seed import DB_DIR, SCENARIO_TODAY

CASES_DB = DB_DIR / "cases.db"

from ingest.pending import create_pending_case  # noqa: E402


def _email_case(case_id="email-402-t3st1d01") -> CasePayload:
    return CasePayload(
        case_id=case_id,
        order_id="402",
        symptom="shipment stuck",
        source="email",
        sender="priya@example.com",
        thread_id="thread-9",
        intent="angry_customer",
        urgency="high",
        summary="customer reports stuck order",
    )


def test_create_pending_case_inserts_pending_row():
    result = create_pending_case(_email_case())
    assert result == "email-402-t3st1d01"

    conn = sqlite3.connect(CASES_DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM cases WHERE case_id = ?", ("email-402-t3st1d01",)
    ).fetchone()
    conn.close()
    try:
        assert row is not None
        assert row["status"] == "pending"
        assert row["order_id"] == "402"
        assert row["case_type"] is None
        assert row["created_at"] == SCENARIO_TODAY.isoformat()
    finally:
        conn = sqlite3.connect(CASES_DB)
        conn.execute("DELETE FROM cases WHERE case_id = ?", ("email-402-t3st1d01",))
        conn.commit()
        conn.close()


def test_create_pending_case_cli_id_roundtrip():
    result = create_pending_case(_email_case(case_id="cli-402-c11xxxxx"))
    assert result == "cli-402-c11xxxxx"

    conn = sqlite3.connect(CASES_DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM cases WHERE case_id = ?", ("cli-402-c11xxxxx",)
    ).fetchone()
    conn.close()
    try:
        assert row is not None
        assert row["status"] == "pending"
    finally:
        conn = sqlite3.connect(CASES_DB)
        conn.execute("DELETE FROM cases WHERE case_id = ?", ("cli-402-c11xxxxx",))
        conn.commit()
        conn.close()


def test_create_pending_case_missing_table_returns_failure_dict(monkeypatch, tmp_path):
    """Missing cases table -> failure dict, never a raise."""
    bad_db = tmp_path / "cases.db"
    bad_db.write_bytes(b"")
    monkeypatch.setattr("ingest.pending.CASES_DB", bad_db)

    result = create_pending_case(_email_case())
    assert isinstance(result, dict)
    assert result["status"] == "failed"
    assert "cases" in result["error"]
