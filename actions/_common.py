"""Internal shared helpers for the P6 action layer — no public API here.

Everything in this module is import-time clean: no env reads, no clients.
"""

from __future__ import annotations


def eway_bill_culprit(verdict) -> bool:
    """True when the verdict's root cause is the e-way-bill-expired hypothesis.

    Uses the same derivation as graph.py's executor
    (``root_cause.rsplit(".", 1)[-1]``) so every module agrees on the culprit
    without duplicating the hypothesis id string.
    """
    return verdict.root_cause.rsplit(".", 1)[-1] == "h_eway_bill_expired"
