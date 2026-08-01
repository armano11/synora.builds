"""Pending-case ledger — THE shared pending-case creation path.

Every entry point (email poller callback, CLI injection) funnels new cases
through create_pending_case so the demo always lands a 'pending' row in the
same cases.db the board reads. Case type/root cause stay NULL until the
investigation runs.

# ---------------------------------------------------------------------------
# FAILURE MODES (each handled explicitly):
# 1. cases.db missing or the 'cases' table absent (e.g. seed never ran) —
#    probed via sqlite_master, returned as a failure dict, never raised.
# 2. DB locked / disk error / INSERT constraint clash (duplicate case_id) —
#    caught, returned as a failure dict with the reason.
# 3. SCENARIO_TODAY non-date or any other unexpected exception — caught,
#    returned as a failure dict.
# ---------------------------------------------------------------------------
"""

from __future__ import annotations

import logging
import sqlite3

from contracts import CasePayload
from enterprise.seed import DB_DIR, SCENARIO_TODAY

CASES_DB = DB_DIR / "cases.db"

log = logging.getLogger("orbit.pending")


def create_pending_case(case: CasePayload) -> str | dict:
    """Insert the case with status='pending'; return the case_id on success.

    Never raises: missing table / DB errors come back as a failure dict.
    """
    try:
        conn = sqlite3.connect(CASES_DB)
        try:
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='cases'"
            ).fetchone()
            if table is None:
                return {
                    "status": "failed",
                    "error": "cases table missing — run enterprise.seed.rebuild()",
                }
            # Add symptom column if it doesn't exist yet (safe migration)
            try:
                conn.execute("ALTER TABLE cases ADD COLUMN symptom TEXT")
            except Exception:
                pass  # column already exists
            conn.execute(
                "INSERT INTO cases (case_id, order_id, case_type, root_cause,"
                " confidence, status, created_at, verdict_summary, symptom)"
                " VALUES (?, ?, ?, ?, ?, 'pending', ?, NULL, ?)",
                (case.case_id, case.order_id, None, None, None,
                 SCENARIO_TODAY.isoformat(), case.symptom),
            )
            conn.commit()
            return case.case_id
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"pending case failed: {exc}"}


def get_pending_case(case_id: str) -> dict | None:
    """Return the pending-case row (dict) or None; never raises."""
    try:
        conn = sqlite3.connect(CASES_DB)
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log.warning(f"get_pending_case failed: {exc}")
        return None


def delete_pending_case(case_id: str) -> None:
    """Delete one case row (test/demo cleanup); never raises."""
    try:
        conn = sqlite3.connect(CASES_DB)
        try:
            conn.execute("DELETE FROM cases WHERE case_id = ?", (case_id,))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log.warning(f"delete_pending_case failed: {exc}")
