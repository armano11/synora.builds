"""ORBIT frozen contracts — TRD §3. Source of truth for all modules.

No business logic lives here. Every module imports from this file.
Frozen at H1; changes require team consensus.

One-line examples:
    CasePayload(case_id="c1", order_id="402", symptom="stuck", source="email")
    Hypothesis(id="h_eway_bill_expired", label="E-way bill expired", rationale="...", investigator="gst")
    Evidence(source="gst", found=True, detail="...", eliminates=[], supports=["h_eway_bill_expired"], raw={})
    PortalStamp(verdict="TRUE", reason="validity lapsed")
    Verdict(root_cause="eway_bill.expired", confidence=0.94, ...)
    ChallengeResult(attack="...", evidence_checked=["gst_portal"], survived=True, confidence_delta=0.06, reasoning="...")
    ExecutionResult(action="renew_eway_bill", before={}, after={}, verified=True)
    ActionResult(type="telegram", status="sent", ref="msg_id")
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CasePayload(BaseModel):
    """The case as ingested from any source (email / manual / cli)."""

    case_id: str
    order_id: str
    symptom: str
    source: Literal["email", "manual", "cli"]
    sender: str | None = None
    thread_id: str | None = None
    intent: str | None = None          # e.g. "angry_customer" — from intent classifier
    urgency: Literal["low", "medium", "high"] | None = None
    summary: str | None = None         # one-line summary for Telegram alert


class Hypothesis(BaseModel):
    """A suspect explanation for the case, loaded from playbook.yaml."""

    id: str
    label: str
    rationale: str
    investigator: str


class Evidence(BaseModel):
    """What one investigator found in its own system."""

    source: str
    found: bool
    detail: str
    eliminates: list[str] = Field(default_factory=list)
    supports: list[str] = Field(default_factory=list)
    raw: dict = Field(default_factory=dict)


class PortalStamp(BaseModel):
    """Rubber-stamp reconciliation of one enterprise portal."""

    verdict: Literal["TRUE", "STALE", "MISLEADING"]
    reason: str


class Verdict(BaseModel):
    """The synthesizer's conclusion — deterministic math only."""

    root_cause: str
    confidence: float
    evidence_trail: list[Evidence] = Field(default_factory=list)
    ruled_out: list[str] = Field(default_factory=list)
    portal_verdicts: dict[str, PortalStamp] = Field(default_factory=dict)
    wall_clock_s: float


class ChallengeResult(BaseModel):
    """The adversary's attack on the verdict, and its outcome."""

    attack: str
    evidence_checked: list[str] = Field(default_factory=list)   # DBs actually re-queried
    survived: bool
    confidence_delta: float
    reasoning: str


class ExecutionResult(BaseModel):
    """What the executor did and whether re-read verification passed."""

    action: str
    before: dict = Field(default_factory=dict)
    after: dict = Field(default_factory=dict)
    verified: bool


class ActionResult(BaseModel):
    """Outcome of one external action (Telegram / Gmail draft / ETA)."""

    type: Literal["telegram", "gmail_draft", "eta_recalc"]
    status: Literal["sent", "drafted", "done", "failed"]
    ref: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# SSE event vocabulary — frozen payload field lists (TRD §3).
# Every server emission must match exactly; the console codes against these.
# ---------------------------------------------------------------------------

SSE_EVENTS: dict[str, list[str]] = {
    "case_ingested": ["case_id", "order_id", "symptom", "source"],
    "hypotheses_ready": ["hypotheses"],
    "investigator_start": ["investigator", "hypothesis_id"],
    "evidence_found": ["investigator", "evidence", "trace_line"],
    "hypothesis_ruled_out": ["hypothesis_id", "by_evidence_source"],
    "portal_stamped": ["portal", "stamp"],
    "verdict_draft": ["partial_root_cause"],
    "challenge_start": ["attack_preview"],
    "challenge_result": ["attack", "evidence_checked", "survived", "confidence_delta"],
    "approval_required": ["proposed_action", "before"],
    "verdict_locked": ["verdict"],
    "execution_done": ["execution"],
    "action_done": ["action"],
    "case_closed": ["case_id", "wall_clock_s", "llm_cost_usd"],
    "error": ["where", "message", "degraded"],
}
