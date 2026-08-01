"""The Brain — router, synthesizer, routing. Topology wiring lands in P5.

TRD §5. State exactly per spec (plus case_type — needed by the synthesizer
to evaluate the playbook stamp rules for the classified case).
"""

from __future__ import annotations

import json
import operator
import re
import time
from datetime import datetime
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from contracts import (
    ActionResult,
    CasePayload,
    ChallengeResult,
    Evidence,
    ExecutionResult,
    Hypothesis,
    PortalStamp,
    Verdict,
)
from enterprise.seed import SCENARIO_TODAY
from llm import ainvoke_with_retry, get_llm
from playbook import eliminations_for, hypotheses_for, load_playbook, stamp_rules_for


class InvestigationState(TypedDict):
    case: CasePayload
    case_type: str | None
    hypotheses: list[Hypothesis]
    evidence: Annotated[list[Evidence], operator.add]
    verdict: Verdict | None
    challenge: ChallengeResult | None
    approved: bool | None            # set by interrupt() resume
    execution: ExecutionResult | None
    actions: Annotated[list[ActionResult], operator.add]
    trace: Annotated[list[str], operator.add]
    loop_count: int
    started_at: float


# ---------------------------------------------------------------------------
# Router — playbook-driven hypothesis generation
# ---------------------------------------------------------------------------

class _CaseTypeChoice(BaseModel):
    case_type: str


class _HypothesisRationales(BaseModel):
    rationales: list[dict[str, str]]   # [{id, rationale}]


_ROUTER_CLASSIFY_SYSTEM = (
    "You are the router of an operations-detective system. Given a customer "
    "complaint about a business operation, choose the ONE best case type from "
    "this list. Reply with only the case type id.\n"
    "Available case types:\n"
    + json.dumps(
        [
            {"id": ct, "triggers": data.get("triggers", [])}
            for ct, data in load_playbook()["case_types"].items()
        ],
        indent=1,
    )
)


async def router_node(state: InvestigationState) -> dict:
    """Classify the case type (one cheap LLM call), then load playbook
    hypotheses and have the LLM write one rationale per hypothesis."""
    case = state["case"]
    llm = get_llm()
    classify = llm.with_structured_output(_CaseTypeChoice)
    choice = await ainvoke_with_retry(
        classify,
        [
            SystemMessage(_ROUTER_CLASSIFY_SYSTEM),
            HumanMessage(f"Symptom: {case.symptom} — order #{case.order_id}."),
        ],
    )
    case_type = choice.case_type
    if case_type not in load_playbook()["case_types"]:
        case_type = "shipment_delay"   # hard fallback: never crash the demo

    hypotheses = hypotheses_for(case_type)
    rationale_llm = get_llm().with_structured_output(_HypothesisRationales)
    hlist = [
        {"id": h.id, "label": h.label, "investigator": h.investigator}
        for h in hypotheses
    ]
    try:
        rationales = await ainvoke_with_retry(
            rationale_llm,
            [
                SystemMessage(
                    "Given the case, write a one-line rationale for EACH hypothesis "
                    "id. Keep it specific to the case symptom. Reply with "
                    "{rationales: [{id, rationale}]} covering every id."
                ),
                HumanMessage(
                    f"Order #{case.order_id}. Symptom: {case.symptom}.\n"
                    f"Hypotheses: {json.dumps(hlist)}"
                ),
            ],
        )
        by_id = {r["id"]: r["rationale"] for r in rationales.rationales}
    except Exception:
        by_id = {}
    enriched = [
        Hypothesis(
            id=h.id, label=h.label,
            rationale=by_id.get(h.id, f"investigate {h.label}"),
            investigator=h.investigator,
        )
        for h in hypotheses
    ]
    return {
        "case_type": case_type,
        "hypotheses": enriched,
        "trace": [f"> router: case type={case_type}, {len(enriched)} hypotheses"],
    }


# ---------------------------------------------------------------------------
# Stamp-rule evaluator — deterministic mini-language for playbook conditions
# ---------------------------------------------------------------------------

_ATOM_RE = re.compile(r"^\s*([a-z_0-9]+)\s*(=|!=|>|<)\s*([^\s]+)\s*$")


def _truthiness(text: str) -> bool:
    text = text.strip()
    if text in ("True", "true", "1"):
        return True
    if text in ("False", "false", "0"):
        return False
    try:
        return float(text) != 0.0
    except ValueError:
        return False


def evaluate_condition(condition: str, facts: dict) -> bool:
    """Evaluate a playbook `if` condition against evidence facts.

    Supports: and / or / not, and atoms of the forms key=value, key!=value,
    key>N, key<N, or bare key (truthy). Unknown keys → False. Deterministic.
    """
    expr = condition.strip()
    if not expr:
        return True
    while True:
        m = re.search(r"\bnot\s+([a-z_0-9]+)\b", expr)
        if not m:
            break
        expr = expr[: m.start()] + str(not bool(facts.get(m.group(1)))) + expr[m.end():]

    for token in re.split(r"\s+or\s+", expr, flags=re.IGNORECASE):
        parts = re.split(r"\s+and\s+", token, flags=re.IGNORECASE)
        if all(_atom(part, facts) for part in parts):
            return True
    return False


def _atom(part: str, facts: dict) -> bool:
    part = part.strip().strip("()")
    m = _ATOM_RE.match(part)
    if m:
        key, op, value = m.groups()
        actual = facts.get(key)
        if actual is None:
            return False
        if op in ("=", "!="):
            equal = str(actual).lower() == value.strip("\"'").lower()
            return equal if op == "=" else not equal
        try:
            a, b = float(actual), float(value)
        except (TypeError, ValueError):
            return False
        return {"<": a < b, ">": a > b}[op]
    bare = part.strip().strip("()")
    if re.match(r"^[a-z_0-9]+$", bare):
        return _truthiness(str(facts.get(bare)))
    return False


def facts_from_evidence(evidence: list[Evidence]) -> dict:
    """Flatten evidence raw records into condition facts (deterministic)."""
    facts: dict = {}
    for ev in evidence:
        raw = ev.raw or {}
        if "eway_status" in raw:
            facts["eway_bill"] = raw["eway_status"]
            facts["gstr3b_filed"] = raw.get("gstr3b_filed")
        if "transport_booking" in raw:
            facts["order_status"] = raw.get("status")
            facts["transport_booking"] = raw.get("transport_booking")
            facts["payment_received"] = raw.get("payment_received", 0)
        if "breakdown_claimed" in raw:
            facts["breakdown_claimed"] = raw.get("breakdown_claimed")
            facts["vehicle_no"] = raw.get("vehicle_no")
            facts["license_expired"] = raw.get("license_expired", 0)
        if "last_scan_at" in raw:
            try:
                scan = datetime.strptime(raw["last_scan_at"], "%Y-%m-%d %H:%M:%S").date()
                facts["last_scan_age_days"] = (SCENARIO_TODAY - scan).days
            except ValueError:
                pass
    return facts


# ---------------------------------------------------------------------------
# Synthesizer — deterministic rules ONLY, confidence NEVER from the LLM
# ---------------------------------------------------------------------------

def _culprit_id(evidence: list[Evidence], hypothesis_ids: list[str]) -> str | None:
    for hid in hypothesis_ids:          # playbook order = priority
        for ev in evidence:
            if ev.found and hid in ev.supports:
                return hid
    return None


def synthesizer_node(state: InvestigationState) -> dict:
    """Deterministic: culprit, confidence (exact TRD §5 formula), stamps."""
    case_type = state.get("case_type") or "shipment_delay"
    hypotheses = state["hypotheses"] or hypotheses_for(case_type)
    evidence = state["evidence"]
    hypothesis_ids = [h.id for h in hypotheses]

    eliminated = sorted({
        hid
        for ev in evidence
        for hid in ev.eliminates
        if hid in hypothesis_ids
    })
    culprit = _culprit_id(evidence, hypothesis_ids)

    rules = stamp_rules_for(case_type)
    facts = facts_from_evidence(evidence)
    portal_verdicts: dict[str, PortalStamp] = {}
    for portal, rule in rules.items():
        if evaluate_condition(rule["if"], facts):
            portal_verdicts[portal] = PortalStamp(
                verdict=rule["stamp"], reason=rule["reason"]
            )

    total_h = max(1, len(hypothesis_ids))
    strength = 1.0 if culprit else 0.0
    coverage = 0.30 * (len(eliminated) / total_h)
    total_p = max(1, len(rules))
    agreement = 0.20 * (len(portal_verdicts) / total_p)
    challenge_bonus = 0.06 if state.get("challenge") and state["challenge"].survived else 0.0
    confidence = min(0.99, 0.50 * strength + coverage + agreement + challenge_bonus)

    root_cause = f"{case_type}.{culprit}" if culprit else f"{case_type}.unknown"
    verdict = Verdict(
        root_cause=root_cause,
        confidence=round(confidence, 4),
        evidence_trail=evidence,
        ruled_out=eliminated,
        portal_verdicts=portal_verdicts,
        wall_clock_s=round(time.time() - state["started_at"], 2),   # HONEST clock
    )
    trace = [
        f"> synth: culprit={culprit}, confidence={verdict.confidence:.2f}, "
        f"eliminated={eliminated}"
    ]
    return {"verdict": verdict, "trace": trace}


def route_after_synthesis(state: InvestigationState) -> Literal["challenger", "router", "end"]:
    """confidence >= 0.8 → challenger; else one re-investigation loop max.

    Threshold lowered from TRD's 0.9 by team decision 2026-08-01 (NOTES #3):
    with 4 hypotheses and 2 dispatched investigators, #402 scores 0.85 — the
    0.9 bar would skip the Challenger beat entirely.
    """
    confidence = (state.get("verdict") or Verdict(
        root_cause="", confidence=0.0, portal_verdicts={}, wall_clock_s=0.0
    )).confidence
    if confidence >= 0.8:
        return "challenger"
    if state.get("loop_count", 0) < 1:
        return "router"
    return "end"
