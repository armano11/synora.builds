"""P5 stub challenger — the real adversarial verifier lands in P7.

Attacks the verdict once; the stub always survives (+0.06 bonus). Replaced
in place by P7 without touching the graph topology.
"""

from __future__ import annotations

from langgraph.config import get_stream_writer

from contracts import ChallengeResult


def _emit(payload: dict) -> None:
    """Emit an SSE event; silently skip when called outside a graph runtime."""
    try:
        get_stream_writer()(payload)
    except RuntimeError:
        pass


def challenger_node(state: dict) -> dict:
    """One attack round on the draft verdict (stub: survives unconditionally)."""
    _emit({"event": "challenge_start", "attack_preview": "stub challenger (P7 replaces)"})
    _emit(
        {
            "event": "challenge_result",
            "attack": "stub",
            "evidence_checked": [],
            "survived": True,
            "confidence_delta": 0.06,
        }
    )
    return {
        "challenge": ChallengeResult(
            attack="stub",
            evidence_checked=[],
            survived=True,
            confidence_delta=0.06,
            reasoning="stub — real adversarial verifier lands in P7",
        ),
        "trace": ["> challenger: stub survived, +0.06"],
    }
