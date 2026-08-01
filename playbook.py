"""Loader for playbook.yaml — the declarative investigation logic.

The router and synthesizer read ALL investigation structure from here;
nothing is hardcoded in LLM prompts (rule 3).
"""

from __future__ import annotations

import functools
from pathlib import Path

import yaml

from contracts import Hypothesis

PLAYBOOK_PATH = Path(__file__).resolve().parent / "playbook.yaml"


@functools.lru_cache(maxsize=1)
def load_playbook() -> dict:
    with open(PLAYBOOK_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def hypotheses_for(case_type: str) -> list[Hypothesis]:
    """Map the yaml entries to the frozen Hypothesis schema.

    rationale is empty here — the router's LLM adds one rationale per
    hypothesis at investigation time (P4).
    """
    entry = load_playbook()["case_types"][case_type]
    return [
        Hypothesis(
            id=h["id"],
            label=h["label"],
            rationale="",
            investigator=h["investigator"],
        )
        for h in entry["hypotheses"]
    ]


def stamp_rules_for(case_type: str) -> dict[str, dict]:
    """Portal stamp rules: {portal: {if, stamp, reason}}."""
    return load_playbook()["case_types"][case_type]["portal_stamp_rules"]


def eliminations_for(case_type: str) -> dict[str, list[str]]:
    """Which hypothesis IDs are eliminated when a check comes back clean."""
    entry = load_playbook()["case_types"][case_type]
    return {
        h["id"]: list(h.get("eliminates_if_clean", []))
        for h in entry["hypotheses"]
    }
