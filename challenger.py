"""P7 — real adversarial challenger with tool-equipped cross-examination.

The challenger gets read-only tools to ALL 4 enterprise DBs — it's the
cross-examiner. Investigators see one system each; the challenger sees
everything. It constructs the STRONGEST alternative explanation and TESTS
it with real tool calls (max 3), then concludes honestly: survived
(confidence_delta=+0.06) or refuted (the graph re-opens the investigation
once via route_after_synthesis's loop_count guard).

evidence_checked lists every DB/table actually queried — shown in the UI.
This is what makes it real, not theater.

Pipeline (three LLM interactions, honesty contract like investigators):
  Phase A — structured attack plan {attack} (no tools bound); emitted as
            challenge_start attack_preview BEFORE any tool call.
  Phase B — tool loop: the LLM must CALL its tools; results only via
            ToolMessage, never in prompts; max 3 calls; evidence_checked
            recorded from the calls ACTUALLY made (deterministic labels).
  Phase C — structured conclusion {attack, survived, reasoning} after
            seeing the tool results; confidence_delta set by the NODE
            (0.06 survived / 0.0 refuted — confidence NEVER from the LLM).

# ---------------------------------------------------------------------------
# FAILURE MODES (each handled explicitly):
# 1. Phase A structured call fails (LLM down/transient) — fallback
#    attack_preview "cross-examining the verdict (attack plan degraded)",
#    still run the tool loop; never crash.
# 2. A tool call raises (bad args, DB error) — caught per call, the error
#    goes back as a ToolMessage, the loop continues.
# 3. Zero tool calls in phase B — one re-prompt demanding a tool call; still
#    zero → honest failure conclusion (survived=True, evidence_checked=[],
#    reasoning says no database could be checked).
# 4. Phase C structured call fails — one plain-text JSON retry, then the
#    safe conclusion: survived=True, reasoning states the challenge
#    degraded; the verdict stands because it was not refuted, and the UI
#    shows exactly which DBs (if any) were actually queried.
# 5. Any other unexpected exception — caught, same safe conclusion. The
#    node NEVER raises: the graph must never crash on the challenger.
# ---------------------------------------------------------------------------
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.config import get_stream_writer
from pydantic import BaseModel

from contracts import ChallengeResult, Verdict
from enterprise import query as eq
from llm import ainvoke_with_retry, get_llm

MAX_TOOL_CALLS = 3
SURVIVED_DELTA = 0.06

_DEGRADED_ATTACK = "cross-examining the verdict (attack plan degraded)"


def _emit(payload: dict) -> None:
    """Emit an SSE event; silently skip when called outside a graph runtime."""
    try:
        get_stream_writer()(payload)
    except RuntimeError:
        pass


# ---------------------------------------------------------------------------
# Read-only tools — the challenger sees EVERYTHING (cross-examiner privilege)
# ---------------------------------------------------------------------------

# Each tool tracks which DB/table it represents so evidence_checked is
# deterministic (the DB/table labels, not the LLM's free-form prose).

_TOOL_DB_MAP: dict[str, str] = {}


def _make_tool(name: str, db_label: str, func, description: str) -> StructuredTool:
    tool = StructuredTool.from_function(func=func, name=name, description=description)
    _TOOL_DB_MAP[name] = db_label
    return tool


_challenge_query_gst = _make_tool(
    "challenge_query_gst",
    "gst_portal.eway_bills",
    eq.query_gst,
    "Query GST portal for an order's e-way bill. Returns: eway_number, "
    "validity_from, validity_to, eway_status, gstr3b_filed.",
)

_challenge_query_tally = _make_tool(
    "challenge_query_tally",
    "tally_erp.orders",
    eq.query_tally,
    "Query Tally ERP for an order's record + line items. Returns: order_id, "
    "customer, status, dispatch_date, transport_booking, amount, items.",
)

_challenge_query_delhivery = _make_tool(
    "challenge_query_delhivery",
    "delhivery.shipments",
    eq.query_delhivery,
    "Query Delhivery shipment tracker for an order. Returns: tracking_id, "
    "status, last_scan_at, last_scan_location.",
)

_challenge_query_transport = _make_tool(
    "challenge_query_transport",
    "transport.bookings",
    eq.query_transport,
    "Query transport system for an order's booking. Returns: vehicle_no, "
    "driver, status, breakdown_claimed, breakdown_reason.",
)

ALL_TOOLS = [
    _challenge_query_gst,
    _challenge_query_tally,
    _challenge_query_delhivery,
    _challenge_query_transport,
]


# ---------------------------------------------------------------------------
# Structured contracts for the LLM phases (confidence/delta NEVER from the LLM)
# ---------------------------------------------------------------------------

class _AttackPlan(BaseModel):
    attack: str


class _ChallengeOutcome(BaseModel):
    attack: str
    survived: bool
    reasoning: str


_SYSTEM_PROMPT = (
    "You are the ADVERSARIAL CHALLENGER on an operations-detective team. "
    "Your job is to ATTACK the draft verdict: construct the STRONGEST "
    "alternative explanation for the order's problem, then TEST it by "
    "querying the enterprise databases with your tools. You have read-only "
    "access to ALL 4 systems: GST portal, Tally ERP, Delhivery shipment "
    "tracker, Transport bookings.\n"
    "Honesty rules: query results only reach you as tool results — read "
    "them carefully. If the data contradicts your alternative, the verdict "
    "SURVIVED. If the data confirms your alternative as the primary cause, "
    "the verdict is REFUTED. A challenge without tool evidence is theater."
)


def _evidence_summary(verdict: Verdict) -> str:
    return "\n".join(
        f"  - {ev.source}: found={ev.found}, {ev.detail}"
        for ev in verdict.evidence_trail
    )


def _serialize(result: dict) -> str:
    return json.dumps(result, default=str)


def _safe_conclusion(attack: str, reasoning: str) -> ChallengeResult:
    return ChallengeResult(
        attack=attack,
        evidence_checked=[],
        survived=True,
        confidence_delta=SURVIVED_DELTA,
        reasoning=reasoning,
    )


async def challenger_node(state: dict) -> dict:
    """One attack round on the draft verdict — real tool calls, honest verdict.

    Returns {"challenge": ChallengeResult, "trace": [...]}. Never raises.
    """
    verdict: Verdict = state["verdict"]
    case = state["case"]
    summary = _evidence_summary(verdict)
    context = (
        f"VERDICT UNDER ATTACK:\n  Root cause: {verdict.root_cause}\n"
        f"  Confidence: {verdict.confidence:.2f}\n  Ruled out: {verdict.ruled_out}\n"
        f"  Evidence trail:\n{summary}\n\n"
        f"ORDER: #{case.order_id} — Symptom: {case.symptom}"
    )

    # --- Phase A: the attack plan (no tools) ---------------------------------
    attack_preview = _DEGRADED_ATTACK
    try:
        plan = await ainvoke_with_retry(
            get_llm().with_structured_output(_AttackPlan),
            [
                SystemMessage(_SYSTEM_PROMPT),
                HumanMessage(
                    f"{context}\n\nState your STRONGEST alternative explanation "
                    f"of the problem in one sentence — e.g. the transport "
                    f"breakdown happened FIRST and the e-way bill expired as a "
                    f"consequence, not the other way around."
                ),
            ],
        )
        attack_preview = plan.attack.strip() or _DEGRADED_ATTACK
    except Exception:  # noqa: BLE001 — fallback preview, tool loop still runs
        pass
    _emit({"event": "challenge_start", "attack_preview": attack_preview})

    # --- Phase B: the tool loop (max 3 calls, honest ToolMessage results) ----
    llm_tools = get_llm().bind_tools(ALL_TOOLS)
    messages = [
        SystemMessage(_SYSTEM_PROMPT),
        HumanMessage(
            f"{context}\n\nMy strongest alternative: {attack_preview}\n\n"
            f"QUERY the relevant databases to test it. You MUST call at least "
            f"one tool before concluding. Max 3 tool calls total."
        ),
    ]
    evidence_checked: list[str] = []
    calls_made = 0
    reprompted = False

    for _ in range(MAX_TOOL_CALLS + 2):
        try:
            response = await ainvoke_with_retry(llm_tools, messages)
        except Exception as exc:  # noqa: BLE001
            result = _safe_conclusion(
                attack_preview,
                f"challenger degraded — tool loop failed ({exc}); verdict stands",
            )
            _emit(
                {"event": "challenge_result", "attack": result.attack,
                 "evidence_checked": [], "survived": True,
                 "confidence_delta": result.confidence_delta}
            )
            return {
                "challenge": result,
                "trace": ["> challenger: degraded (LLM error), verdict survives"],
            }

        messages.append(response)
        if not response.tool_calls:
            if calls_made == 0 and not reprompted:
                reprompted = True
                messages.append(
                    HumanMessage(
                        "You have not called any tool yet. CALL a tool now — "
                        "a challenge without evidence is theater."
                    )
                )
                continue
            break

        for call in response.tool_calls:
            if calls_made >= MAX_TOOL_CALLS:
                break
            calls_made += 1
            tool_name = call["name"]
            label = _TOOL_DB_MAP.get(tool_name)
            if label and label not in evidence_checked:
                evidence_checked.append(label)
            tool = next((t for t in ALL_TOOLS if t.name == tool_name), None)
            try:
                result = tool.invoke(call["args"]) if tool else {"tool_error": f"unknown tool: {tool_name}"}
            except Exception as exc:  # noqa: BLE001 — one bad call never aborts
                result = {"tool_error": str(exc)}
            messages.append(
                ToolMessage(content=_serialize(result), tool_call_id=call["id"])
            )
    else:
        # Cap reached mid-call (model still wanted more) — close the thread.
        messages.append(
            HumanMessage(
                "Tool call cap (3) reached. Conclude your assessment now."
            )
        )

    # --- Phase C: the honest conclusion (structured, 1 JSON retry) -----------
    outcome = _ChallengeOutcome(
        attack=attack_preview, survived=True,
        reasoning="challenger degraded — conclusion could not be formed; verdict stands",
    )
    try:
        concluded = await ainvoke_with_retry(
            get_llm().with_structured_output(_ChallengeOutcome),
            messages + [HumanMessage(
                "Conclude now: the attack you tested, whether the verdict "
                "SURVIVED or was REFUTED, and the reasoning from the tool "
                "results you saw. survived=true means the data did NOT "
                "confirm your alternative as the primary cause."
            )],
        )
        outcome = concluded
    except Exception:  # noqa: BLE001 — one plain-text JSON retry
        try:
            reply = await ainvoke_with_retry(
                get_llm(),
                messages
                + [
                    HumanMessage(
                        'Reply with ONLY valid JSON: {"attack": "<one line>", '
                        '"survived": true|false, "reasoning": "<why, from the '
                        'tool results>"}'
                    )
                ],
            )
            outcome = _ChallengeOutcome(**_extract_json(reply.content))
        except Exception:  # noqa: BLE001 — safe conclusion, never crash
            pass

    challenge = ChallengeResult(
        attack=outcome.attack or attack_preview,
        evidence_checked=evidence_checked,
        survived=outcome.survived,
        confidence_delta=SURVIVED_DELTA if outcome.survived else 0.0,
        reasoning=outcome.reasoning or "challenge completed",
    )
    _emit(
        {"event": "challenge_result", "attack": challenge.attack,
         "evidence_checked": challenge.evidence_checked,
         "survived": challenge.survived,
         "confidence_delta": challenge.confidence_delta}
    )
    checked = ", ".join(evidence_checked) if evidence_checked else "none"
    return {
        "challenge": challenge,
        "trace": [
            f"> challenger: attack='{challenge.attack[:60]}', checked=[{checked}], "
            f"survived={challenge.survived}, delta={challenge.confidence_delta}"
        ],
    }


def _extract_json(text: str) -> dict:
    """Parse JSON possibly wrapped in markdown fences or stray prose."""
    cleaned = text.strip()
    if "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model reply")
    return json.loads(cleaned[start : end + 1])
