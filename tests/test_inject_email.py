"""P6.5 — inject_email CLI test (real subprocess, real DB row).

Runs `python -m ingest.inject_email --order 402` exactly as the demo does,
asserts the pending row landed in cases.db, and cleans up after itself.
"""

import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from enterprise.seed import DB_DIR, SCENARIO_TODAY

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def _run_cli(*args):
    return subprocess.run(
        [PY, "-m", "ingest.inject_email", *args],
        cwd=ROOT, capture_output=True, text=True, timeout=90,
    )


def _extract_case_id(stdout: str) -> str | None:
    for line in stdout.splitlines():
        if line.startswith("Pending case created:"):
            return line.split(":", 1)[1].strip().split()[0]
    return None


def test_inject_email_cli_creates_pending_case_and_cleans_up():
    proc = _run_cli("--order", "402")
    assert proc.returncode == 0, proc.stderr
    case_id = _extract_case_id(proc.stdout)
    assert case_id and case_id.startswith("cli-402-"), f"unexpected stdout: {proc.stdout}"

    conn = sqlite3.connect(DB_DIR / "cases.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM cases WHERE case_id = ?", (case_id,)
    ).fetchone()
    conn.close()
    try:
        assert row is not None, "pending case row must exist in cases.db"
        assert row["status"] == "pending"
        assert row["order_id"] == "402"
        assert row["created_at"] == SCENARIO_TODAY.isoformat()
    finally:
        conn = sqlite3.connect(DB_DIR / "cases.db")
        conn.execute("DELETE FROM cases WHERE case_id = ?", (case_id,))
        conn.commit()
        conn.close()


def test_inject_email_cli_custom_flags():
    proc = _run_cli(
        "--order", "402",
        "--symptom", "custom symptom",
        "--summary", "custom summary line",
    )
    assert proc.returncode == 0, proc.stderr
    case_id = _extract_case_id(proc.stdout)
    assert case_id and case_id.startswith("cli-402-")

    conn = sqlite3.connect(DB_DIR / "cases.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM cases WHERE case_id = ?", (case_id,)
    ).fetchone()
    conn.close()
    try:
        assert row is not None
        assert row["status"] == "pending"
    finally:
        conn = sqlite3.connect(DB_DIR / "cases.db")
        conn.execute("DELETE FROM cases WHERE case_id = ?", (case_id,))
        conn.commit()
        conn.close()


def test_inject_email_cli_bad_order_never_raises():
    proc = _run_cli("--order", "abc")
    assert proc.returncode == 1
    assert "cli-402" not in proc.stdout
    assert proc.stdout or proc.stderr, "must print a failure message"
