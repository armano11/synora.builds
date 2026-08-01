"""Transport investigator — checks the breakdown claim in transport.db (TRD §6).

Hypothesis h_transport_breakdown: vehicle broke down. The booking record shows
breakdown_claimed=1 — the claim is TRUE (the truck did break down) but the e-way
bill expiry is the real blocker; this check neither supports nor eliminates.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from enterprise import query as eq
from investigators.base import build_node

HYPOTHESIS_ID = "h_transport_breakdown"

_query_transport = StructuredTool.from_function(
    func=eq.query_transport,
    name="query_transport",
    description=(
        "Query the transport system for an order's vehicle booking. Returns: "
        "order_id, vehicle_no, driver, status, breakdown_claimed (0 or 1), "
        "breakdown_reason. Use for hypothesis: transport breakdown."
    ),
)

TOOLS = [_query_transport]

_SYSTEM_PROMPT = (
    "You are the transport investigator on an operations-detective team. "
    "Your ONLY tool is query_transport — it reads the vehicle booking record "
    "for an order. "
    f"Investigate hypothesis {HYPOTHESIS_ID}: transport breakdown. "
    "DECISIVE RULE: breakdown_claimed=1 confirms the breakdown claim is TRUE — "
    "the hypothesis is not eliminated. But a breakdown does not get decided "
    "here: the truck is held at a checkpoint because the e-way bill expired, "
    "so do NOT support the hypothesis either. Report the facts and let the "
    "synthesizer weigh the portals. "
    "Quote exact facts in detail (e.g. breakdown_claimed=1, status=breakdown) "
    "and include the COMPLETE tool result as raw."
)

node = build_node(
    system_prompt=_SYSTEM_PROMPT,
    tool=_query_transport,
    hypothesis_id=HYPOTHESIS_ID,
    eligible_ids=[HYPOTHESIS_ID],
)
