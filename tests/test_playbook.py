"""P2.5 — the investigation playbook is the moat. Declarative case logic.

Written FIRST (TDD RED): fails until playbook.yaml + playbook.py exist.
"""

import pytest

import playbook as pb
from contracts import Hypothesis


def test_all_five_case_types_load():
    data = pb.load_playbook()
    assert set(data["case_types"]) >= {
        "shipment_delay",
        "payment_hold",
        "inventory_mismatch",
        "customs_block",
        "invoice_dispute",
        "compliance_block",
    }


def test_shipment_delay_yields_four_canonical_hypothesis_ids():
    hypotheses = pb.hypotheses_for("shipment_delay")
    ids = {h.id for h in hypotheses}
    assert ids == {
        "h_eway_bill_expired",
        "h_inventory_damage",
        "h_dispatch_failure",
        "h_transport_breakdown",
    }


def test_hypotheses_parse_to_exact_contract_schema():
    for case_type in ("shipment_delay", "payment_hold", "inventory_mismatch",
                      "customs_block", "invoice_dispute", "compliance_block"):
        for h in pb.hypotheses_for(case_type):
            assert isinstance(h, Hypothesis)
            assert h.investigator


def test_shipment_delay_stamp_rules_cover_all_portals():
    rules = pb.stamp_rules_for("shipment_delay")
    assert set(rules) == {"tally", "gst", "delhivery", "transport"}
    for portal, rule in rules.items():
        assert rule["stamp"] in ("TRUE", "STALE", "MISLEADING")
        assert rule["reason"]


def test_every_case_type_has_hypotheses_and_stamp_rules():
    for case_type in pb.load_playbook()["case_types"]:
        assert pb.hypotheses_for(case_type), f"{case_type} has no hypotheses"
        assert pb.stamp_rules_for(case_type), f"{case_type} has no stamp rules"


def test_elimination_rules_reference_real_hypothesis_ids():
    for case_type in pb.load_playbook()["case_types"]:
        hypotheses = pb.hypotheses_for(case_type)
        known = {h.id for h in hypotheses}
        for source_id, targets in pb.eliminations_for(case_type).items():
            assert source_id in known
            for target in targets:
                assert target in known, (
                    f"{source_id} eliminates unknown hypothesis {target}"
                )
