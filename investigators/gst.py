"""GST investigator — queries gst_portal.db ONLY (TRD §6).

Handles multiple hypotheses: e-way bill expired, customs docs incomplete,
invoice tax rate error. The hypothesis_id is passed dynamically from the graph.
"""

from __future__ import annotations

from investigators.base import build_node
from investigators.tools import query_gst_tool

TOOLS = [query_gst_tool]

_SYSTEM_PROMPT = (
    "You are the GST-portal investigator on an operations-detective team. "
    "Your ONLY tool is query_gst — it reads the e-way bill for an order. "
    "An e-way bill is a permit to transport goods across state borders in India; "
    "if expired, the truck legally cannot cross a checkpoint. "
    "A 'renewal_requested' status means finance has started renewal. "
    "Check gstr3b_filed — an unfiled GSTR-3B (0) explains why renewal was blocked. "
    "Check docs_incomplete — if 1, customs documents are missing. "
    "Check tax_rate_wrong — if 1, the GST rate applied is incorrect. "
    "When docs_incomplete=1, support h_customs_docs_incomplete and ELIMINATE "
    "h_customs_inspection (the issue is documents, not physical inspection). "
    "When tax_rate_wrong=1, support h_invoice_tax_error and ELIMINATE "
    "h_invoice_amount_mismatch (the issue is the tax rate, not the amount). "
    "Quote exact facts in detail (e.g. eway_status=expired, gstr3b_filed=0, "
    "docs_incomplete=1, tax_rate_wrong=0)."
)


def make_node(hypothesis_id: str, eligible_ids: list[str] | None = None):
    """Create an investigator node for a specific GST-related hypothesis."""
    return build_node(
        system_prompt=_SYSTEM_PROMPT,
        tool=query_gst_tool,
        hypothesis_id=hypothesis_id,
        eligible_ids=eligible_ids or [hypothesis_id],
    )


# Default node for backward compatibility (shipment_delay case)
node = make_node("h_eway_bill_expired")
