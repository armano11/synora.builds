"""ETA recalculation — promised + 3 days, deterministic, no wall clock.

The demo scenario's promise is the "Monday market deadline" =
enterprise.seed.SCENARIO_TODAY (2026-07-20). No promised-delivery column
exists in any DB, so the promise is fixed to the scenario date; recalc_eta
never touches a database and never reads the wall clock.

# ---------------------------------------------------------------------------
# FAILURE MODES (each handled explicitly):
# 1. seed contract drift (SCENARIO_TODAY moved/removed) — the exact date is
#    unit-tested, so drift fails the suite loudly instead of misdelivering.
# 2. Date arithmetic breaks (timedelta misuse, non-date SCENARIO_TODAY) —
#    caught and returned as a failed ActionResult.
# 3. ActionResult contract drift (frozen schema changes) — a broken schema
#    is a deployment-time error surfaced by the test suite, not something a
#    runtime handler can faithfully report.
# ---------------------------------------------------------------------------
"""

from __future__ import annotations

from datetime import timedelta

from contracts import ActionResult, CasePayload
from enterprise.seed import SCENARIO_TODAY


def recalc_eta(case: CasePayload) -> ActionResult:
    """Return the new ETA = SCENARIO_TODAY + 3 days in ActionResult(ref).

    `case` is accepted for call-site symmetry with the other action modules;
    the fixture world has a single global promise, so the arithmetic does
    not depend on case fields. Deterministic: always 2026-07-23 for the
    fixture world. Never raises.
    """
    try:
        eta = SCENARIO_TODAY + timedelta(days=3)
        return ActionResult(type="eta_recalc", status="done", ref=eta.isoformat())
    except Exception as exc:  # noqa: BLE001
        return ActionResult(
            type="eta_recalc",
            status="failed",
            error=f"ETA recalc failed: {exc}",
        )
