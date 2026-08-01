"""P5 — the H6 GATE: full investigation loop, end to end, real LLM + real DBs.

#402: email-style case → router → parallel investigators → synthesizer →
challenger → interrupt(approval) → executor (e-way bill renewed + verified).

Written FIRST (TDD RED): fails until graph.investigate exists.
"""

import os

import pytest

import enterprise.seed as seed
from contracts import CasePayload
from enterprise import query as eq
from graph import investigate

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("MISTRAL_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("LLM_API_KEY")
    ),
    reason="no LLM API key configured",
)

CASE_402 = CasePayload(
    case_id="e2e-402",
    order_id="402",
    symptom="shipment stuck at Hubli for 6 days, buyer cancelling, Monday market deadline",
    source="manual",
)


async def _run(case: CasePayload, resume: dict | None = None) -> list[dict]:
    return [ev async for ev in investigate(case, resume=resume)]


async def test_e2e_402_approved_full_loop():
    try:
        phase1 = await _run(CASE_402)
        names = [ev["event"] for ev in phase1]

        # The narrative in order: hypotheses → evidence → stamps → challenge → verdict
        assert "case_ingested" in names
        assert names.index("hypotheses_ready") < names.index("evidence_found")
        assert "portal_stamped" in names
        assert "challenge_result" in names
        assert "verdict_locked" in names

        verdict = next(ev["verdict"] for ev in phase1 if ev["event"] == "verdict_locked")
        assert "h_eway_bill_expired" in verdict["root_cause"]
        assert verdict["confidence"] >= 0.9          # 0.85 + 0.06 challenge bonus = 0.91
        assert phase1[-1]["event"] == "approval_required"

        phase2 = await _run(CASE_402, resume={"approved": True})
        names2 = [ev["event"] for ev in phase2]
        assert "execution_done" in names2
        execution = next(ev["execution"] for ev in phase2 if ev["event"] == "execution_done")
        assert execution["verified"] is True
        assert execution["after"]["eway_bill"] == "renewal_requested"
        assert "case_closed" in names2
        assert "action_done" in names2

        # Ground truth really changed (verified by re-read, not just our word)
        assert eq.query_gst("402")["eway_status"] == "renewal_requested"
    finally:
        seed.rebuild()


async def test_e2e_402_rejected_skips_executor():
    try:
        phase1 = await _run(CASE_402)
        assert phase1[-1]["event"] == "approval_required"
        phase2 = await _run(CASE_402, resume={"approved": False})
        names2 = [ev["event"] for ev in phase2]
        assert "execution_done" not in names2
        assert "case_closed" in names2
        # DB untouched
        assert eq.query_gst("402")["eway_status"] == "expired"
    finally:
        seed.rebuild()


async def test_e2e_parallel_investigators_both_contribute():
    """Send() fan-out: at least the culprit source + others land on the board.

    query_gst MUST appear (it's the culprit for #402). query_inventory is
    best-effort — under rate-limit pressure the investigator may degrade
    gracefully and return found=False evidence instead of crashing.
    """
    try:
        phase1 = await _run(CASE_402)
        names = [ev["event"] for ev in phase1]
        assert "error" not in names, f"graph errored: {phase1}"

        sources = {
            ev["evidence"]["source"]
            for ev in phase1 if ev["event"] == "evidence_found"
        }
        # The culprit investigator MUST always produce evidence
        assert "query_gst" in sources, f"culprit source missing, got: {sources}"
        # At least 3 of the 5 investigators should report (graceful degradation)
        assert len(sources) >= 3, f"too few sources: {sources}"
    finally:
        seed.rebuild()
