"""The Brain — router, synthesizer, routing, full P5 topology.

TRD §5. State exactly per spec (plus case_type — needed by the synthesizer
to evaluate the playbook stamp rules for the classified case).

P5 wiring: router --Send fan-out--> parallel investigators --> synthesizer
--> challenger --> interrupt() approval gate --> executor --> action drafter
--> close. `investigate()` maps the graph's custom stream to the frozen SSE
event vocabulary (contracts.SSE_EVENTS).
"""

from __future__ import annotations

import json
import logging
import operator
import re
import sqlite3
import time
from datetime import datetime
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt
from pydantic import BaseModel, Field

from challenger import challenger_node
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
from enterprise import query as eq
from enterprise.seed import DB_DIR, SCENARIO_TODAY
from investigators import delhivery as delhivery_investigator
from investigators import gst as gst_investigator
from investigators import inventory as inventory_investigator
from investigators import transport as transport_investigator
from investigators import warehouse as warehouse_investigator
from llm import ainvoke_with_retry, get_llm
from playbook import eliminations_for, hypotheses_for, load_playbook, stamp_rules_for

_log = logging.getLogger("orbit.graph")


def _emit(payload: dict) -> None:
    """Emit an SSE event; silently skip when called outside a graph runtime.

    Unit tests invoke nodes directly (no stream context), while the real graph
    streams via get_stream_writer — both must work.
    """
    try:
        get_stream_writer()(payload)
    except RuntimeError:
        pass


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
    _emit(
        {"event": "hypotheses_ready", "hypotheses": [h.model_dump() for h in enriched]}
    )
    eliminated = {
        hid for ev in state.get("evidence", []) for hid in ev.eliminates
    }
    wired_ids = {h.id for h in hypotheses if h.investigator in _WIRED} | set(_EXTRA_CHECK)
    skipped = [
        f"> router: SKIPPED {h.id} — cause locked"
        for h in hypotheses
        if h.id in wired_ids and h.id in eliminated
    ]
    return {
        "case_type": case_type,
        "hypotheses": enriched,
        "loop_count": state.get("loop_count", 0) + (1 if state.get("evidence") else 0),
        "trace": [f"> router: case type={case_type}, {len(enriched)} hypotheses"] + skipped,
    }


# ---------------------------------------------------------------------------
# Fan-out — Send() one task per non-eliminated wired hypothesis
# ---------------------------------------------------------------------------

_SYNTH_KEYS = ("case_type", "hypotheses", "evidence", "verdict", "challenge", "started_at")

# Playbook investigator name → its node. h_dispatch_failure is ALSO checked
# against the delhivery shipment record (a dispatch fact is visible in two
# systems), so it gets a second Send.
_WIRED: dict[str, str] = {
    "gst": "investigator_gst",
    "inventory": "investigator_inventory",
    "warehouse": "investigator_tally",
    "transport": "investigator_transport",
}
_EXTRA_CHECK: dict[str, str] = {
    "h_dispatch_failure": "investigator_delhivery",
}


def fan_out(state: InvestigationState) -> list[Send]:
    """Dispatch one investigator task per hypothesis not yet ruled out.

    A Send payload becomes the spawned task's state (it does NOT see the
    parent's channels), so when nothing is left to dispatch the synthesizer
    is spawned with the exact snapshot it reads — the pipeline never strands.
    """
    eliminated = {
        hid for ev in state.get("evidence", []) for hid in ev.eliminates
    }
    sends: list[Send] = []
    for h in state.get("hypotheses", []):
        if h.id in eliminated:
            continue
        node_name = _WIRED.get(h.investigator)
        if node_name:
            sends.append(Send(node_name, {"case": state["case"], "hypothesis": h}))
        extra = _EXTRA_CHECK.get(h.id)
        if extra:
            sends.append(Send(extra, {"case": state["case"], "hypothesis": h}))
    if not sends:
        sends.append(Send("synthesizer", {k: state.get(k) for k in _SYNTH_KEYS}))
    return sends


# ---------------------------------------------------------------------------
# Investigator wrappers — P3/P5 node modules + SSE emissions (modules untouched)
# ---------------------------------------------------------------------------

def _make_investigator_wrapper(investigator: str, trace_label: str, node):
    """Wrap an investigator node: SSE start/evidence events + trace lines.

    Evidence.source is normalized to the canonical tool name — the LLM's
    free-form label varies run to run ("GST Portal" vs "query_gst"), while the
    system actually queried is the deterministic ground truth.
    """

    async def wrapper(state: InvestigationState) -> dict:
        hypothesis = state["hypothesis"]
        _emit(
            {"event": "investigator_start", "investigator": investigator,
             "hypothesis_id": hypothesis.id}
        )
        try:
            result = await node(state)
        except Exception as exc:  # noqa: BLE001
            _log.warning("investigator %s failed (degraded): %s", investigator, exc)
            degraded = Evidence(
                source=investigator,
                found=False,
                detail=f"investigator unavailable: {type(exc).__name__}",
                supports=[],
                eliminates=[],
            )
            _emit(
                {"event": "evidence_found", "investigator": investigator,
                 "evidence": degraded.model_dump(),
                 "trace_line": f"> {trace_label}: DEGRADED — {exc}"}
            )
            return {"evidence": [degraded],
                    "trace": [f"> {trace_label}: DEGRADED — {exc}"]}
        trace = []
        for ev in result["evidence"]:
            ev.source = investigator
            _emit(
                {"event": "evidence_found", "investigator": investigator,
                 "evidence": ev.model_dump(), "trace_line": f"> {trace_label}: {ev.detail}"}
            )
            trace.append(f"> {trace_label}: {ev.detail}")
        return {"evidence": result["evidence"], "trace": trace}

    return wrapper


_investigator_gst = _make_investigator_wrapper("query_gst", "gst", gst_investigator.node)
_investigator_inventory = _make_investigator_wrapper(
    "query_inventory", "inventory", inventory_investigator.node
)
_investigator_tally = _make_investigator_wrapper(
    "query_tally", "tally", warehouse_investigator.node
)
_investigator_transport = _make_investigator_wrapper(
    "query_transport", "transport", transport_investigator.node
)
_investigator_delhivery = _make_investigator_wrapper(
    "query_delhivery", "delhivery", delhivery_investigator.node
)


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
    if state.get("challenge") is None:
        for portal, stamp in portal_verdicts.items():
            _emit(
                {"event": "portal_stamped", "portal": portal,
                 "stamp": {"verdict": stamp.verdict, "reason": stamp.reason}}
            )
        for hid in eliminated:
            source = next(
                (ev.source for ev in evidence if hid in ev.eliminates), None
            )
            _emit(
                {"event": "hypothesis_ruled_out", "hypothesis_id": hid,
                 "by_evidence_source": source}
            )
        _emit({"event": "verdict_draft", "partial_root_cause": root_cause})
    else:
        _emit({"event": "verdict_locked", "verdict": verdict.model_dump()})
    trace = [
        f"> synth: culprit={culprit}, confidence={verdict.confidence:.2f}, "
        f"eliminated={eliminated}"
    ]
    return {"verdict": verdict, "trace": trace}


def route_after_synthesis(state: InvestigationState) -> Literal["challenger", "router", "approve", "end"]:
    """Challenge verdicts: a refuted challenge re-opens the investigation
    exactly once (loop_count guards against cycles — max 1 re-open per TRD
    §5); a survived challenge proceeds to approval; with no challenge the
    confidence gate applies: >= 0.8 → challenger, else one re-investigation
    loop max (loop_count advanced by the router), then end.

    Threshold lowered from TRD's 0.9 by team decision 2026-08-01 (NOTES #3):
    with 4 hypotheses and the dispatched investigators, #402 scores 0.85 — the
    0.9 bar would skip the Challenger beat entirely.
    """
    if state.get("challenge") is not None:
        if state["challenge"].survived:
            return "approve"
        return "router" if state.get("loop_count", 0) < 1 else "end"
    confidence = (state.get("verdict") or Verdict(
        root_cause="", confidence=0.0, portal_verdicts={}, wall_clock_s=0.0
    )).confidence
    if confidence >= 0.8:
        return "challenger"
    if state.get("loop_count", 0) < 1:
        return "router"
    return "end"


# ---------------------------------------------------------------------------
# Approval gate — interrupt() before any execution (H6 GATE)
# ---------------------------------------------------------------------------

def approval_gate_node(state: InvestigationState) -> dict:
    """Read the honest pre-state, propose the fix, pause for human approval."""
    case = state["case"]
    before = eq.query_gst(case.order_id)
    culprit = state["verdict"].root_cause.rsplit(".", 1)[-1]
    if culprit == "h_eway_bill_expired":
        proposed = (
            f"Renew e-way bill for order #{case.order_id} "
            f"(currently {before.get('eway_status', 'unknown')})"
        )
    else:
        proposed = f"Execute fix for {culprit}"
    payload = {
        "type": "approval_required",
        "proposed_action": proposed,
        "before": {"eway_bill": before.get("eway_status", "unknown")},
    }
    _emit(
        {"event": "approval_required", "proposed_action": proposed,
         "before": payload["before"]}
    )
    approved = interrupt(payload)
    decided = (
        bool(approved.get("approved")) if isinstance(approved, dict) else bool(approved)
    )
    return {
        "approved": decided,
        "trace": [
            "> gate: " + ("APPROVED — executing fix" if decided else "REJECTED — no execution")
        ],
    }


def after_approval(state: InvestigationState) -> Literal["executor", "close_case"]:
    """Approved → execute the fix; rejected → close without touching anything."""
    return "executor" if state["approved"] else "close_case"


# ---------------------------------------------------------------------------
# Executor — renews the e-way bill, re-reads to verify (honest, no claims)
# ---------------------------------------------------------------------------

def executor_node(state: InvestigationState) -> dict:
    case = state["case"]
    culprit = state["verdict"].root_cause.rsplit(".", 1)[-1]
    before = eq.query_gst(case.order_id)
    if culprit == "h_eway_bill_expired" and before.get("eway_status") == "expired":
        conn = sqlite3.connect(DB_DIR / "gst_portal.db")
        try:
            conn.execute(
                "UPDATE eway_bills SET eway_status = 'renewal_requested' WHERE order_id = ?",
                (case.order_id,),
            )
            conn.commit()
        finally:
            conn.close()
        after = eq.query_gst(case.order_id)
        execution = ExecutionResult(
            action="renew_eway_bill",
            before={"eway_bill": before.get("eway_status")},
            after={"eway_bill": after.get("eway_status")},
            verified=after.get("eway_status") == "renewal_requested",
        )
    else:
        execution = ExecutionResult(action="none", verified=False)
    _emit({"event": "execution_done", "execution": execution.model_dump()})
    return {
        "execution": execution,
        "trace": [f"> executor: {execution.action}, verified={execution.verified}"],
    }


# ---------------------------------------------------------------------------
# Action drafter — drafts only; senders wire in P8
# ---------------------------------------------------------------------------

def action_drafter_node(state: InvestigationState) -> dict:
    case_id = state["case"].case_id
    actions = [
        ActionResult(type="telegram", status="drafted", ref=f"telegram-alert-{case_id}"),
        ActionResult(type="gmail_draft", status="drafted", ref=f"gmail-draft-{case_id}"),
        ActionResult(type="eta_recalc", status="done", ref=None),
    ]
    for action in actions:
        _emit({"event": "action_done", "action": action.model_dump()})
    return {"actions": actions, "trace": ["> drafter: 3 actions drafted (senders in P8)"]}


# ---------------------------------------------------------------------------
# Close — honest wall clock, cost not yet metered
# ---------------------------------------------------------------------------

def close_case_node(state: InvestigationState) -> dict:
    _emit(
        {"event": "case_closed",
         "case_id": state["case"].case_id,
         "wall_clock_s": round(time.time() - state["started_at"], 2),
         "llm_cost_usd": 0.0}
    )
    return {}


# ---------------------------------------------------------------------------
# Topology + investigate() — the P5 entry point
# ---------------------------------------------------------------------------

_checkpointer = MemorySaver()

_ROUTE_AFTER_SYNTHESIS_MAP = {
    "challenger": "challenger",
    "router": "router",
    "approve": "approval_gate",
    "end": "close_case",
}


def build_graph():
    """Compile the full investigation graph with a checkpointer (needed by interrupt())."""
    graph = StateGraph(InvestigationState)
    graph.add_node("router", router_node)
    graph.add_node("investigator_gst", _investigator_gst)
    graph.add_node("investigator_inventory", _investigator_inventory)
    graph.add_node("investigator_tally", _investigator_tally)
    graph.add_node("investigator_transport", _investigator_transport)
    graph.add_node("investigator_delhivery", _investigator_delhivery)
    graph.add_node("synthesizer", synthesizer_node)
    graph.add_node("challenger", challenger_node)
    graph.add_node("approval_gate", approval_gate_node)
    graph.add_node("executor", executor_node)
    graph.add_node("action_drafter", action_drafter_node)
    graph.add_node("close_case", close_case_node)
    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router", fan_out,
        ["investigator_gst", "investigator_inventory", "investigator_tally",
         "investigator_transport", "investigator_delhivery", "synthesizer"],
    )
    for investigator in (
        "investigator_gst", "investigator_inventory", "investigator_tally",
        "investigator_transport", "investigator_delhivery",
    ):
        graph.add_edge(investigator, "synthesizer")
    graph.add_conditional_edges(
        "synthesizer", route_after_synthesis, _ROUTE_AFTER_SYNTHESIS_MAP
    )
    graph.add_edge("challenger", "synthesizer")
    graph.add_conditional_edges(
        "approval_gate", after_approval, {"executor": "executor", "close_case": "close_case"}
    )
    graph.add_edge("executor", "action_drafter")
    graph.add_edge("action_drafter", "close_case")
    graph.add_edge("close_case", END)
    return graph.compile(checkpointer=_checkpointer)


_GRAPH = build_graph()


async def investigate(case: CasePayload, resume: dict | None = None):
    """Run (or resume) an investigation, yielding SSE events as they happen.

    Fresh runs start at the router; `resume` replays the interrupt() answer
    on the same thread. Events follow contracts.SSE_EVENTS payloads exactly.
    """
    thread_id = f"orbit-{case.case_id}"          # stable: resume must find the same thread
    config = {"configurable": {"thread_id": thread_id}}
    if resume is None:
        _checkpointer.delete_thread(thread_id)   # fresh run (safe on missing threads)
        yield {"event": "case_ingested", "case_id": case.case_id, "order_id": case.order_id,
               "symptom": case.symptom, "source": case.source}
        input_state = {"case": case, "evidence": [], "trace": [], "actions": [],
                       "loop_count": 0, "started_at": time.time(), "hypotheses": []}
        stream = _GRAPH.astream(input_state, config, stream_mode="custom")
    else:
        stream = _GRAPH.astream(Command(resume=resume), config, stream_mode="custom")
    try:
        async for ev in stream:
            yield ev
    except Exception as exc:
        yield {"event": "error", "where": "graph.investigate", "message": str(exc), "degraded": True}
