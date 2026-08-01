"""P7 — the real challenger: adversarial verification with live tool calls.

The #402 verdict must survive the strongest alternative attack AND the node
must really have queried at least one enterprise DB (evidence_checked >= 1 —
the proof it is not theater). Real LLM; skipped without a key.

Written FIRST (TDD RED): the P5 stub returns survived=True but
evidence_checked=[] — the ≥1 assertion fails until the real node lands.
"""

import os
import time

import pytest

from challenger import challenger_node
from contracts import CasePayload, Evidence, Verdict

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("LLM_API_KEY")
    ),
    reason="no LLM API key configured",
)

CASE_402 = CasePayload(
    case_id="case_001", order_id="402",
    symptom="shipment stuck at Hubli for 6 days, customer cancelling", source="email",
)

_KNOWN_LABELS = {
    "gst_portal.eway_bills",
    "tally_erp.orders",
    "delhivery.shipments",
    "transport.bookings",
}


def _verdict_for_402() -> Verdict:
    """Realistic draft verdict for #402, as the synthesizer produces it."""
    return Verdict(
        root_cause="shipment_delay.h_eway_bill_expired",
        confidence=0.85,
        evidence_trail=[
            Evidence(source="query_gst", found=True,
                     detail="eway_status=expired, gstr3b_filed=0",
                     eliminates=[], supports=["h_eway_bill_expired"],
                     raw={"eway_status": "expired", "gstr3b_filed": 0}),
            Evidence(source="query_inventory", found=True,
                     detail="stock=12, qty=500, picked=1",
                     eliminates=["h_inventory_damage"], supports=[],
                     raw={"sku": "COT-1000", "stock": 12, "picked": 1}),
            Evidence(source="query_transport", found=True,
                     detail="breakdown_claimed=1",
                     eliminates=[], supports=[],
                     raw={"breakdown_claimed": 1, "status": "breakdown"}),
            Evidence(source="query_delhivery", found=True,
                     detail="last_scan 6 days old, In Transit at Hubli",
                     eliminates=["h_dispatch_failure"], supports=[],
                     raw={"status": "In Transit",
                          "last_scan_location": "Hubli Checkpoint"}),
            Evidence(source="query_tally", found=True,
                     detail="transport_booking=none, status=Dispatched",
                     eliminates=[], supports=[],
                     raw={"transport_booking": "none", "status": "Dispatched"}),
        ],
        ruled_out=["h_inventory_damage", "h_dispatch_failure"],
        portal_verdicts={},
        wall_clock_s=12.5,
    )


async def test_challenger_402_survives_and_really_queries():
    result = await challenger_node({"case": CASE_402, "verdict": _verdict_for_402()})
    challenge = result["challenge"]
    assert challenge.survived is True
    assert len(challenge.evidence_checked) >= 1
    assert challenge.confidence_delta == 0.06
    assert challenge.attack.strip()
    assert challenge.reasoning.strip()
    assert any(line.startswith("> challenger:") for line in result["trace"])


async def test_challenger_evidence_checked_labels_are_db_tables():
    """evidence_checked uses the deterministic DB/table labels, not LLM prose."""
    result = await challenger_node({"case": CASE_402, "verdict": _verdict_for_402()})
    labels = result["challenge"].evidence_checked
    assert len(labels) >= 1
    assert set(labels) <= _KNOWN_LABELS
