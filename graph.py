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
    """Emit an SSE event; silently skip when called outside a graph runtime."""
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
    approved: bool | None
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
    rationales: list[dict[str, str]]

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
    """Classify the case type, then load playbook hypotheses + rationales."""
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
        case_type = "payment_hold"

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
        "challenge": None,  # clear stale challenge on re-entry (fixes refutation retry)
        "trace": [f"> router: case type={case_type}, {len(enriched)} hypotheses"] + skipped,
    }

# ---------------------------------------------------------------------------
# Fan-out — Send() one task per non-eliminated wired hypothesis
# ---------------------------------------------------------------------------

_SYNTH_KEYS = ("case_type", "hypotheses", "evidence", "verdict", "challenge", "started_at")

# All investigators are wired — the graph dispatches dynamically based on
# the hypothesis's investigator field from playbook.yaml.
# Each investigator module has a make_node(hypothesis_id, eligible_ids) factory.
_INVESTIGATOR_FACTORIES = {
    "gst": gst_investigator.make_node,
    "inventory": inventory_investigator.make_node,
    "warehouse": warehouse_investigator.make_node,
    "transport": transport_investigator.make_node,
}

# Extra cross-check investigators (like delhivery for dispatch_failure)
_EXTRA_CHECK: dict[str, str] = {
    "h_dispatch_failure": "investigator_delhivery",
}

# Pre-built nodes for the graph (one per investigator type)
_WIRED: dict[str, str] = {
    "gst": "investigator_gst",
    "inventory": "investigator_inventory",
    "warehouse": "investigator_tally",
    "transport": "investigator_transport",
}

# Cache of dynamically created investigator nodes
_DYNAMIC_NODES: dict[str, callable] = {}


def _get_investigator_node(hypothesis_id: str, investigator: str, eligible_ids: list[str]):
    """Get or create a cached investigator node for this hypothesis."""
    cache_key = f"{investigator}:{hypothesis_id}"
    if cache_key not in _DYNAMIC_NODES:
        factory = _INVESTIGATOR_FACTORIES.get(investigator)
        if factory:
            _DYNAMIC_NODES[cache_key] = factory(hypothesis_id, eligible_ids)
        else:
            _log.warning("No investigator factory for: %s", investigator)
            return None
    return _DYNAMIC_NODES[cache_key]


def fan_out(state: InvestigationState) -> list[Send]:
    """Dispatch one investigator task per hypothesis not yet ruled out.
    
    Each hypothesis gets dispatched to the appropriate investigator node
    based on its `investigator` field from playbook.yaml. The investigator
    node is selected dynamically based on the hypothesis ID, allowing
    different case types to reuse the same investigator modules with
    different prompts.
    """
    eliminated = {
        hid for ev in state.get("evidence", []) for hid in ev.eliminates
    }
    sends: list[Send] = []
    hypothesis_ids = [h.id for h in state.get("hypotheses", [])]
    
    for h in state.get("hypotheses", []):
        if h.id in eliminated:
            continue
        # Map investigator to graph node name
        node_name = _WIRED.get(h.investigator)
        if node_name:
            sends.append(Send(node_name, {
                "case": state["case"],
                "hypothesis": h,
                "case_type": state.get("case_type", "shipment_delay"),
            }))
        # Extra cross-checks
        extra = _EXTRA_CHECK.get(h.id)
        if extra:
            sends.append(Send(extra, {
                "case": state["case"],
                "hypothesis": h,
                "case_type": state.get("case_type", "shipment_delay"),
            }))
    if not sends:
        sends.append(Send("synthesizer", {k: state.get(k) for k in _SYNTH_KEYS}))
    return sends

# ---------------------------------------------------------------------------
# Investigator wrappers
# ---------------------------------------------------------------------------

def _make_investigator_wrapper(investigator: str, trace_label: str, node_factory=None, default_node=None):
    """Create a wrapper that dispatches to the right investigator node dynamically.
    
    For multi-hypothesis investigators (gst, inventory, warehouse, transport),
    the wrapper looks up the hypothesis ID and creates/uses the appropriate
    node with the correct system prompt for that hypothesis.
    """
    async def wrapper(state: InvestigationState) -> dict:
        hypothesis = state["hypothesis"]
        _emit(
            {"event": "investigator_start", "investigator": investigator,
             "hypothesis_id": hypothesis.id}
        )
        
        # Get the right node for this hypothesis
        if node_factory:
            # Get all hypothesis IDs for eligible_ids
            case_type = state.get("case_type") or "payment_hold"
            all_hyps = hypotheses_for(case_type)
            eligible_ids = [h.id for h in all_hyps]
            node = node_factory(hypothesis.id, eligible_ids)
        else:
            node = default_node
        
        try:
            result = await node(state)
        except Exception as exc:
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

_investigator_gst = _make_investigator_wrapper("query_gst", "gst", node_factory=gst_investigator.make_node)
_investigator_inventory = _make_investigator_wrapper(
    "query_inventory", "inventory", node_factory=inventory_investigator.make_node
)
_investigator_tally = _make_investigator_wrapper(
    "query_tally", "tally", node_factory=warehouse_investigator.make_node
)
_investigator_transport = _make_investigator_wrapper(
    "query_transport", "transport", node_factory=transport_investigator.make_node
)
_investigator_delhivery = _make_investigator_wrapper(
    "query_delhivery", "delhivery", default_node=delhivery_investigator.node
)

# ---------------------------------------------------------------------------
# Stamp-rule evaluator
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
    condition = condition.strip()
    if " or " in condition:
        return any(evaluate_condition(p, facts) for p in condition.split(" or "))
    if " and " in condition:
        return all(evaluate_condition(p, facts) for p in condition.split(" and "))
    if condition.startswith("not "):
        return not evaluate_condition(condition[4:], facts)

    def _eval_part(part: str) -> bool:
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

    return _eval_part(condition)

def facts_from_evidence(evidence: list[Evidence]) -> dict:
    facts: dict = {}
    for ev in evidence:
        raw = ev.raw or {}
        if "eway_status" in raw:
            facts["eway_bill"] = raw["eway_status"]
            facts["gstr3b_filed"] = raw.get("gstr3b_filed")
            facts["docs_incomplete"] = raw.get("docs_incomplete", 0)
            facts["tax_rate_wrong"] = raw.get("tax_rate_wrong", 0)
        if "transport_booking" in raw:
            facts["order_status"] = raw.get("status")
            facts["transport_booking"] = raw.get("transport_booking")
            facts["payment_received"] = raw.get("payment_received", 0)
            facts["stock_booked"] = raw.get("stock_booked", 1)
            facts["invoice_amount"] = raw.get("invoice_amount")
            facts["po_amount"] = raw.get("po_amount")
            facts["delivered"] = raw.get("delivered", 0)
        if "breakdown_claimed" in raw:
            facts["breakdown_claimed"] = raw.get("breakdown_claimed")
            facts["vehicle_no"] = raw.get("vehicle_no")
            facts["license_expired"] = raw.get("license_expired", 0)
            facts["transport_delivered"] = raw.get("delivered", 0)
            facts["breakdown_claimed"] = raw.get("breakdown_claimed")
            facts["vehicle_no"] = raw.get("vehicle_no")
            facts["license_expired"] = raw.get("license_expired", 0)
            facts["transport_delivered"] = raw.get("delivered", 0)
        if "last_scan_at" in raw:
            try:
                scan = datetime.strptime(raw["last_scan_at"], "%Y-%m-%d %H:%M:%S").date()
                facts["last_scan_age_days"] = (SCENARIO_TODAY - scan).days
            except ValueError:
                pass
        # Also extract from items (inventory mismatch)
        if "items" in raw and raw["items"]:
            for item in raw["items"]:
                if "stock" in item and "qty" in item:
                    facts["stock"] = item.get("stock")
                    facts["qty"] = item.get("qty")
                    facts["picked"] = item.get("picked", 0)
    return facts

# ---------------------------------------------------------------------------
# Synthesizer
# ---------------------------------------------------------------------------

def _culprit_id(evidence: list[Evidence], hypothesis_ids: list[str]) -> str | None:
    for hid in hypothesis_ids:
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
        wall_clock_s=round(time.time() - state["started_at"], 2),
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
    """Threshold: 0.8 per team decision (NOTES #3)."""
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
# Approval gate — interrupt() before any execution
# ---------------------------------------------------------------------------

def approval_gate_node(state: InvestigationState) -> dict:
    """Read the honest pre-state, propose the fix, pause for human approval."""
    case = state["case"]
    culprit = state["verdict"].root_cause.rsplit(".", 1)[-1]
    before_gst = eq.query_gst(case.order_id)
    before_tally = eq.query_tally(case.order_id)
    before_transport = eq.query_transport(case.order_id)
    
    # Build proposed action based on culprit
    _ACTIONS = {
        "h_eway_bill_expired": lambda: (
            f"Renew e-way bill for order #{case.order_id} "
            f"(currently {before_gst.get('eway_status', 'unknown')})",
            {"eway_bill": before_gst.get("eway_status", "unknown")}
        ),
        "h_payment_hold_bank_recon": lambda: (
            f"Release payment hold for order #{case.order_id} "
            f"(payment_received={before_tally.get('payment_received', '?')}, "
            f"delivered={before_tally.get('delivered', '?')})",
            {"payment_received": before_tally.get("payment_received", 0),
             "delivered": before_tally.get("delivered", 0)}
        ),
        "h_inventory_count_error": lambda: (
            f"Correct cycle count for order #{case.order_id} "
            f"(stock_booked={before_tally.get('stock_booked', '?')})",
            {"stock_booked": before_tally.get("stock_booked", 1)}
        ),
        "h_customs_docs_incomplete": lambda: (
            f"Re-upload customs documents for order #{case.order_id} "
            f"(docs_incomplete={before_gst.get('docs_incomplete', '?')})",
            {"docs_incomplete": before_gst.get("docs_incomplete", 0),
             "eway_bill": before_gst.get("eway_status", "unknown")}
        ),
        "h_invoice_amount_mismatch": lambda: (
            f"Issue credit note for order #{case.order_id} "
            f"(invoice_amount={before_tally.get('invoice_amount', '?')}, "
            f"po_amount={before_tally.get('po_amount', '?')})",
            {"invoice_amount": before_tally.get("invoice_amount"),
             "po_amount": before_tally.get("po_amount")}
        ),
        "h_compliance_license_expired": lambda: (
            f"Renew transport license for order #{case.order_id} "
            f"(license_expired={before_transport.get('license_expired', '?')})",
            {"license_expired": before_transport.get("license_expired", 0)}
        ),
    }
    
    action_fn = _ACTIONS.get(culprit)
    if action_fn:
        proposed, before_payload = action_fn()
    else:
        proposed = f"Execute fix for {culprit}"
        before_payload = {"culprit": culprit}
    
    payload = {
        "type": "approval_required",
        "proposed_action": proposed,
        "before": before_payload,
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
    return "executor" if state["approved"] else "close_case"

# ---------------------------------------------------------------------------
# Executor — renews the e-way bill, re-reads to verify
# ---------------------------------------------------------------------------

def executor_node(state: InvestigationState) -> dict:
    case = state["case"]
    culprit = state["verdict"].root_cause.rsplit(".", 1)[-1]
    order_id = case.order_id
    
    # E-way bill renewal (shipment_delay, customs_block)
    if culprit in ("h_eway_bill_expired", "h_customs_docs_incomplete"):
        before = eq.query_gst(order_id)
        conn = sqlite3.connect(DB_DIR / "gst_portal.db")
        try:
            if culprit == "h_eway_bill_expired":
                conn.execute(
                    "UPDATE eway_bills SET eway_status = 'renewal_requested' WHERE order_id = ?",
                    (order_id,),
                )
            else:  # customs_docs_incomplete
                conn.execute(
                    "UPDATE eway_bills SET docs_incomplete = 0, eway_status = 'renewal_requested' WHERE order_id = ?",
                    (order_id,),
                )
            conn.commit()
        finally:
            conn.close()
        after = eq.query_gst(order_id)
        verified = after.get("eway_status") == "renewal_requested"
        if culprit == "h_customs_docs_incomplete":
            verified = verified and after.get("docs_incomplete") == 0
        execution = ExecutionResult(
            action="renew_eway_bill" if culprit == "h_eway_bill_expired" else "upload_customs_docs",
            before={"eway_bill": before.get("eway_status"), "docs_incomplete": before.get("docs_incomplete")},
            after={"eway_bill": after.get("eway_status"), "docs_incomplete": after.get("docs_incomplete")},
            verified=verified,
        )
    
    # Payment hold release
    elif culprit == "h_payment_hold_bank_recon":
        before = eq.query_tally(order_id)
        conn = sqlite3.connect(DB_DIR / "tally_erp.db")
        try:
            conn.execute(
                "UPDATE orders SET payment_received = 1 WHERE order_id = ?",
                (order_id,),
            )
            conn.commit()
        finally:
            conn.close()
        after = eq.query_tally(order_id)
        execution = ExecutionResult(
            action="release_payment_hold",
            before={"payment_received": before.get("payment_received")},
            after={"payment_received": after.get("payment_received")},
            verified=after.get("payment_received") == 1,
        )
    
    # Inventory count correction
    elif culprit == "h_inventory_count_error":
        before = eq.query_tally(order_id)
        conn = sqlite3.connect(DB_DIR / "tally_erp.db")
        try:
            conn.execute(
                "UPDATE orders SET stock_booked = 1 WHERE order_id = ?",
                (order_id,),
            )
            conn.commit()
        finally:
            conn.close()
        after = eq.query_tally(order_id)
        execution = ExecutionResult(
            action="correct_cycle_count",
            before={"stock_booked": before.get("stock_booked")},
            after={"stock_booked": after.get("stock_booked")},
            verified=after.get("stock_booked") == 1,
        )
    
    # Invoice correction (credit note)
    elif culprit == "h_invoice_amount_mismatch":
        before = eq.query_tally(order_id)
        conn = sqlite3.connect(DB_DIR / "tally_erp.db")
        try:
            conn.execute(
                "UPDATE orders SET invoice_amount = po_amount WHERE order_id = ?",
                (order_id,),
            )
            conn.commit()
        finally:
            conn.close()
        after = eq.query_tally(order_id)
        execution = ExecutionResult(
            action="issue_credit_note",
            before={"invoice_amount": before.get("invoice_amount"), "po_amount": before.get("po_amount")},
            after={"invoice_amount": after.get("invoice_amount"), "po_amount": after.get("po_amount")},
            verified=after.get("invoice_amount") == after.get("po_amount"),
        )
    
    # Compliance license renewal
    elif culprit == "h_compliance_license_expired":
        before = eq.query_transport(order_id)
        conn = sqlite3.connect(DB_DIR / "transport.db")
        try:
            conn.execute(
                "UPDATE bookings SET license_expired = 0, status = 'in_transit' WHERE order_id = ?",
                (order_id,),
            )
            conn.commit()
        finally:
            conn.close()
        after = eq.query_transport(order_id)
        execution = ExecutionResult(
            action="renew_transport_license",
            before={"license_expired": before.get("license_expired"), "status": before.get("status")},
            after={"license_expired": after.get("license_expired"), "status": after.get("status")},
            verified=after.get("license_expired") == 0,
        )
    
    else:
        execution = ExecutionResult(action="none", verified=False)
    
    _emit({"event": "execution_done", "execution": execution.model_dump()})
    return {
        "execution": execution,
        "trace": [f"> executor: {execution.action}, verified={execution.verified}"],
    }

# ---------------------------------------------------------------------------
# Action drafter — FIX: actually calls the real action modules
# ---------------------------------------------------------------------------

async def action_drafter_node(state: InvestigationState) -> dict:
    """Wire P8: calls telegram, gmail_drafter, eta_recalc — graceful on any failure."""
    from actions.eta_recalc import recalc_eta as _recalc_eta
    from actions.gmail_drafter import create_buyer_draft
    from actions.telegram_bot import send_manager_alert

    case = state["case"]
    verdict = state.get("verdict")
    actions: list[ActionResult] = []

    # 1. Telegram verdict alert (internal — auto-send)
    try:
        if verdict:
            tg_result = await send_manager_alert(verdict, case)
        else:
            tg_result = ActionResult(type="telegram", status="failed", error="no verdict")
    except Exception as exc:
        tg_result = ActionResult(type="telegram", status="failed", error=f"telegram: {exc}")
    actions.append(tg_result)
    _emit({"event": "action_done", "action": tg_result.model_dump()})

    # 2. Gmail draft reply to buyer (external — approval-gated, draft only)
    try:
        if case.thread_id:
            gm_result = create_buyer_draft(verdict, case, case.thread_id)
        else:
            # No thread_id (manual/CLI trigger) — still create a standalone draft
            gm_result = create_buyer_draft(verdict, case, None)
    except Exception as exc:
        gm_result = ActionResult(type="gmail_draft", status="failed", error=f"gmail: {exc}")
    actions.append(gm_result)
    _emit({"event": "action_done", "action": gm_result.model_dump()})

    # 3. ETA recalculation
    try:
        eta_result = _recalc_eta(case)
    except Exception as exc:
        eta_result = ActionResult(type="eta_recalc", status="failed", error=f"eta: {exc}")
    actions.append(eta_result)
    _emit({"event": "action_done", "action": eta_result.model_dump()})

    return {
        "actions": actions,
        "trace": ["> drafter: telegram + gmail_draft + eta_recalc executed"],
    }

# ---------------------------------------------------------------------------
# Close case
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
# Topology + investigate()
# ---------------------------------------------------------------------------

_checkpointer = MemorySaver()

_ROUTE_AFTER_SYNTHESIS_MAP = {
    "challenger": "challenger",
    "router": "router",
    "approve": "approval_gate",
    "end": "close_case",
}

def build_graph():
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

    FIX: Replaced _checkpointer.delete_thread() (doesn't exist on MemorySaver)
    with a safe storage clear that works across LangGraph versions.
    """
    thread_id = f"orbit-{case.case_id}"
    config = {"configurable": {"thread_id": thread_id}}
    if resume is None:
        # FIX: safely clear stale checkpoint — delete_thread() doesn't exist on MemorySaver
        try:
            if hasattr(_checkpointer, 'storage'):
                _checkpointer.storage.pop(thread_id, None)
            if hasattr(_checkpointer, 'writes'):
                _checkpointer.writes.pop(thread_id, None)
        except Exception:
            pass
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
