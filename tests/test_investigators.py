"""P3 — investigators query THEIR OWN mock system via tools, LLM interprets.

Real LLM calls (~2K tokens). Skipped when no API key is configured.
Written FIRST (TDD RED): fails until investigators/ exists.
"""

import os

import pytest

from contracts import CasePayload, Evidence, Hypothesis

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("LLM_API_KEY")
    ),
    reason="no LLM API key configured",
)

from investigators import gst, inventory  # noqa: E402

CASE_402 = CasePayload(
    case_id="case_001",
    order_id="402",
    symptom="shipment stuck at Hubli 6 days",
    source="email",
)

H_EWAY = Hypothesis(
    id="h_eway_bill_expired",
    label="E-way bill expired",
    rationale="",
    investigator="gst",
)
H_INV = Hypothesis(
    id="h_inventory_damage",
    label="Inventory damaged in staging",
    rationale="",
    investigator="inventory",
)


def _state(hypothesis: Hypothesis) -> dict:
    return {"case": CASE_402, "hypothesis": hypothesis}


async def test_gst_investigator_finds_expired_eway_bill():
    result = await gst.node(_state(H_EWAY))
    assert isinstance(result["evidence"], list)
    evidence = result["evidence"][0]
    assert isinstance(evidence, Evidence)
    assert evidence.found is True
    assert "expired" in evidence.detail.lower()
    assert "h_eway_bill_expired" in evidence.supports


async def test_inventory_investigator_clears_stock():
    result = await inventory.node(_state(H_INV))
    evidence = result["evidence"][0]
    assert isinstance(evidence, Evidence)
    assert evidence.found is True
    assert "12" in evidence.detail
    assert "h_inventory_damage" in evidence.eliminates


def test_gst_investigator_sees_only_gst_tool():
    assert [t.name for t in gst.TOOLS] == ["query_gst"]


def test_inventory_investigator_sees_only_inventory_tool():
    assert [t.name for t in inventory.TOOLS] == ["query_inventory"]
