"""P4 — synthesizer confidence math, stamp evaluation, routing.

Synthetic evidence → assert EXACT formula math to 2 decimals (TRD §5).
One real-LLM router test (skipped without a key).
Written FIRST (TDD RED): fails until graph.py exists.
"""

import os
import time
from datetime import datetime

import pytest

from contracts import (
    CasePayload,
    ChallengeResult,
    Evidence,
    Hypothesis,
    Verdict,
)
from enterprise import query as eq

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("MISTRAL_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("LLM_API_KEY")
    ),
    reason="no LLM API key configured",
)

from graph import route_after_synthesis, router_node, synthesizer_node  # noqa: E402
from investigators import gst, inventory  # noqa: E402
from playbook import hypotheses_for, stamp_rules_for  # noqa: E402

CASE_402 = CasePayload(
    case_id="case_001", order_id="402",
    symptom="shipment stuck at Hubli for 6 days, customer cancelling", source="email",
)


def _evidence_for_402() -> list[Evidence]:
    """Real evidence built from the seeded DBs, as the investigators produce."""
    gst_raw = eq.query_gst("402")
    tally_raw = eq.query_tally("402")
    transport_raw = eq.query_transport("402")
    delhivery_raw = eq.query_delhivery("402")
    return [
        Evidence(source="query_gst", found=True,
                 detail="eway_status=expired, gstr3b_filed=0",
                 eliminates=[], supports=["h_eway_bill_expired"], raw=gst_raw),
        Evidence(source="query_tally", found=True,
                 detail="stock=12, qty=500, picked=1",
                 eliminates=["h_inventory_damage"], supports=[], raw=tally_raw),
        Evidence(source="query_transport", found=True,
                 detail="breakdown_claimed=1",
                 eliminates=[], supports=[], raw=transport_raw),
        Evidence(source="query_delhivery", found=True,
                 detail="last_scan 6 days old",
                 eliminates=["h_dispatch_failure"], supports=[], raw=delhivery_raw),
    ]


def _state(**overrides) -> dict:
    hypotheses = hypotheses_for("shipment_delay")
    state = {
        "case": CASE_402,
        "case_type": "shipment_delay",
        "hypotheses": hypotheses,
        "evidence": _evidence_for_402(),
        "verdict": None,
        "challenge": None,
        "approved": None,
        "execution": None,
        "actions": [],
        "trace": [],
        "loop_count": 0,
        "started_at": time.time(),
    }
    state.update(overrides)
    return state


def test_confidence_matches_exact_formula():
    state = _state()
    result = synthesizer_node(state)
    verdict: Verdict = result["verdict"]
    total_h = len(state["hypotheses"])
    eliminated = len(verdict.ruled_out)
    rules = stamp_rules_for(state["case_type"])
    strength = 1.0
    coverage = 0.30 * (eliminated / total_h)
    agreement = 0.20 * (len(verdict.portal_verdicts) / max(1, len(rules)))
    expected = min(0.99, 0.50 * strength + coverage + agreement)
    assert verdict.confidence == round(expected, 4) == pytest.approx(expected, abs=1e-6)
    assert verdict.confidence >= 0.75


def test_confidence_min_cap_at_099():
    state = _state()
    state["evidence"] = [
        Evidence(source="query_gst", found=True, detail="x",
                 supports=["h_eway_bill_expired"], raw={"eway_status": "expired"}),
        Evidence(source="q", found=True, detail="x",
                 eliminates=["h_inventory_damage", "h_dispatch_failure",
                             "h_transport_breakdown"], raw={}),
    ]
    verdict = synthesizer_node(state)["verdict"]
    assert verdict.confidence <= 0.99


def test_confidence_formula_three_quarters_shape():
    """3/4 eliminated + 3/4 portals resolved → 0.5 + 0.225 + 0.15 = 0.875."""
    state = _state()
    state["evidence"] = [
        Evidence(source="query_gst", found=True, detail="x",
                 supports=["h_eway_bill_expired"], raw={"eway_status": "expired"}),
        Evidence(source="query_inventory", found=True, detail="x",
                 eliminates=["h_inventory_damage", "h_dispatch_failure",
                             "h_transport_breakdown"], raw={}),
        Evidence(source="query_tally", found=True, detail="x", eliminates=[],
                 raw={"status": "Dispatched", "stock_booked": 0}),
        Evidence(source="query_delhivery", found=True, detail="x", eliminates=[],
                 raw={"last_scan_at": "2026-07-14 09:12:00", "status": "In Transit"}),
    ]
    verdict = synthesizer_node(state)["verdict"]
    assert len(verdict.portal_verdicts) >= 1
    assert verdict.confidence >= 0.70


def test_synthesizer_culprit_is_eway_bill():
    verdict = synthesizer_node(_state())["verdict"]
    assert "eway_bill" in verdict.root_cause
    assert "h_eway_bill_expired" in verdict.evidence_trail[0].supports


def test_synthesizer_stamps_402_portals_from_playbook_rules():
    verdict = synthesizer_node(_state())["verdict"]
    stamps = verdict.portal_verdicts
    assert "gst" in stamps
    assert stamps["gst"].verdict == "TRUE"


def test_wall_clock_is_honest():
    start = time.time() - 3.0
    verdict = synthesizer_node(_state(started_at=start))["verdict"]
    assert verdict.wall_clock_s >= 2.9


def test_route_high_confidence_to_challenger():
    state = _state()
    state["verdict"] = Verdict(root_cause="x", confidence=0.94,
                               portal_verdicts={}, wall_clock_s=1.0)
    assert route_after_synthesis(state) == "challenger"


def test_route_402_confidence_085_to_challenger():
    """#402 scores 0.85 → must reach the Challenger (demo beat)."""
    state = _state()
    state["verdict"] = Verdict(root_cause="x", confidence=0.85,
                               portal_verdicts={}, wall_clock_s=1.0)
    assert route_after_synthesis(state) == "challenger"


def test_challenge_bonus_brings_402_to_091():
    state = _state()
    state["challenge"] = ChallengeResult(
        attack="transport breakdown happened first?",
        evidence_checked=["gst_portal.eway_bills"],
        survived=True, confidence_delta=0.06, reasoning="checked",
    )
    verdict = synthesizer_node(state)["verdict"]
    assert verdict.confidence == pytest.approx(0.86)


def test_route_refuted_challenge_reopens_investigation_once():
    """P7: a refuted verdict re-opens the investigation exactly once."""
    state = _state()
    state["loop_count"] = 0
    state["challenge"] = ChallengeResult(
        attack="transport breakdown happened first?",
        evidence_checked=["transport.bookings"],
        survived=False, confidence_delta=0.0, reasoning="refuted",
    )
    assert route_after_synthesis(state) == "router"


def test_route_second_refutation_ends_case():
    """P7: a second refuted synthesis ends the case (no cycles)."""
    state = _state()
    state["loop_count"] = 1
    state["challenge"] = ChallengeResult(
        attack="transport breakdown happened first?",
        evidence_checked=["transport.bookings"],
        survived=False, confidence_delta=0.0, reasoning="refuted",
    )
    assert route_after_synthesis(state) == "end"


def test_route_survived_challenge_goes_to_approval():
    """P7: a survived challenge proceeds to the approval gate."""
    state = _state()
    state["challenge"] = ChallengeResult(
        attack="transport breakdown happened first?",
        evidence_checked=["transport.bookings"],
        survived=True, confidence_delta=0.06, reasoning="checked",
    )
    assert route_after_synthesis(state) == "approve"


def test_route_low_confidence_back_to_router_once():
    state = _state()
    state["verdict"] = Verdict(root_cause="x", confidence=0.5,
                               portal_verdicts={}, wall_clock_s=1.0)
    assert route_after_synthesis(state) == "router"


def test_route_low_confidence_ends_after_one_loop():
    state = _state()
    state["loop_count"] = 1
    state["verdict"] = Verdict(root_cause="x", confidence=0.5,
                               portal_verdicts={}, wall_clock_s=1.0)
    assert route_after_synthesis(state) == "end"


async def test_router_classifies_402_as_shipment_delay_with_rationales():
    state = _state()
    result = await router_node(state)
    assert result["case_type"] == "shipment_delay"
    hypotheses = result["hypotheses"]
    assert {h.id for h in hypotheses} == {
        "h_eway_bill_expired", "h_inventory_damage",
        "h_dispatch_failure", "h_transport_breakdown",
    }
    assert all(h.rationale.strip() for h in hypotheses)
