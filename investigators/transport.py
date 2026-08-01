"""Transport investigator — checks the breakdown/license claim in transport.db (TRD §6).

Handles: transport breakdown, compliance license expired, buyer default (payment),
customs inspection.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from enterprise import query as eq
from investigators.base import build_node

_query_transport = StructuredTool.from_function(
    func=eq.query_transport,
    name="query_transport",
    description=(
        "Query the transport system for an order's vehicle booking. Returns: "
        "order_id, vehicle_no, driver, status, breakdown_claimed (0 or 1), "
        "breakdown_reason, license_expired (0 or 1), delivered (0 or 1). "
        "Use for hypotheses: transport breakdown, compliance license expired, "
        "payment hold (buyer default), customs inspection."
    ),
)

TOOLS = [_query_transport]

_SYSTEM_PROMPT_BREAKDOWN = (
    "You are the transport investigator on an operations-detective team. "
    "Your ONLY tool is query_transport — it reads the vehicle booking record "
    "for an order. "
    "DECISIVE RULE: breakdown_claimed=1 confirms the breakdown claim is TRUE — "
    "the hypothesis is not eliminated. But a breakdown does not get decided "
    "here: the truck is held at a checkpoint because the e-way bill expired, "
    "so do NOT support the hypothesis either. Report the facts and let the "
    "synthesizer weigh the portals. "
    "Quote exact facts in detail (e.g. breakdown_claimed=1, status=breakdown) "
    "and include the COMPLETE tool result as raw."
)

_SYSTEM_PROMPT_LICENSE = (
    "You are the transport investigator checking compliance status. "
    "Your tool is query_transport — it reads the vehicle booking record. "
    "Check license_expired — if 1, the operator's license has expired and the "
    "vehicle is legally grounded. Support the hypothesis. "
    "If license_expired=0, the license is valid — eliminate the hypothesis. "
    "Quote exact facts (e.g. license_expired=1, status=grounded, breakdown_claimed=0)."
)

_SYSTEM_PROMPT_BUYER_DEFAULT = (
    "You are the transport investigator checking delivery and payment status. "
    "Your tool is query_transport — it reads the vehicle booking record. "
    "Check delivered — if 1, the goods WERE delivered. If payment_received=0 "
    "and delivered=1, the buyer received goods but hasn't paid — this is NOT "
    "a buyer default, it's a bank reconciliation issue. Eliminate the buyer "
    "default hypothesis. If delivered=0 and payment_received=0, the buyer may "
    "be withholding payment. "
    "Quote exact facts (e.g. delivered=1, breakdown_claimed=0)."
)

_SYSTEM_PROMPT_CUSTOMS = (
    "You are the transport investigator checking customs status. "
    "Your tool is query_transport — it reads the vehicle booking record. "
    "Check status — if 'customs_hold', the vehicle is held at customs. "
    "Check breakdown_claimed — if 0, the hold is NOT a breakdown, it's customs. "
    "If the vehicle is held at customs (not a breakdown), support h_customs_inspection "
    "and ELIMINATE h_customs_docs_incomplete (the docs issue is a GST matter, not transport). "
    "If breakdown_claimed=1, eliminate h_customs_inspection (it IS a breakdown, not customs). "
    "Quote exact facts (e.g. status=customs_hold, breakdown_claimed=0)."
)


def make_node(hypothesis_id: str, eligible_ids: list[str] | None = None):
    """Create an investigator node for a specific transport-related hypothesis."""
    prompts = {
        "h_transport_breakdown": _SYSTEM_PROMPT_BREAKDOWN,
        "h_compliance_license_expired": _SYSTEM_PROMPT_LICENSE,
        "h_payment_hold_buyer_default": _SYSTEM_PROMPT_BUYER_DEFAULT,
        "h_customs_inspection": _SYSTEM_PROMPT_CUSTOMS,
    }
    prompt = prompts.get(hypothesis_id, _SYSTEM_PROMPT_BREAKDOWN)
    return build_node(
        system_prompt=prompt,
        tool=_query_transport,
        hypothesis_id=hypothesis_id,
        eligible_ids=eligible_ids or [hypothesis_id],
    )


# Default node for backward compatibility
node = make_node("h_transport_breakdown")
