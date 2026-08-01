"""Shared investigator machinery — one factory, two honest investigators.

Honesty contract: the LLM must CALL its tool; query results never appear
in the prompt. Max 2 tool calls, then a structured Evidence verdict.
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from contracts import Evidence
from llm import ainvoke_with_retry, get_llm


def _serialize(result: dict) -> str:
    return json.dumps(result, default=str)


async def run_investigator(
    *,
    system_prompt: str,
    task: str,
    tool,
    candidate_ids: list[str],
    max_calls: int = 2,
) -> list[Evidence]:
    """LLM calls its own tool (max `max_calls`), then emits one Evidence."""
    llm = get_llm().bind_tools([tool])
    messages: list = [SystemMessage(system_prompt), HumanMessage(task)]
    calls = 0

    for _ in range(max_calls):
        response = await ainvoke_with_retry(llm, messages)
        messages.append(response)
        if not response.tool_calls:
            break
        for call in response.tool_calls:
            if calls >= max_calls:
                break
            calls += 1
            try:
                result = tool.invoke(call["args"])
            except Exception as exc:  # tool failure → honest evidence, never crash
                result = {"tool_error": str(exc)}
            messages.append(
                ToolMessage(content=_serialize(result), tool_call_id=call["id"])
            )

    evidence_llm = get_llm().with_structured_output(Evidence)
    prompt = (
        "Write your final Evidence verdict now. found must be true whenever the "
        "tool returned an existing record (you FOUND evidence); found=false only "
        "when the order has no record at all. "
        "detail MUST quote the exact raw field values from the tool result, e.g. "
        "eway_status=expired, gstr3b_filed=0, stock=12, picked=1. "
        "supports/eliminates must ONLY use these hypothesis ids: "
        f"{json.dumps(candidate_ids)}. "
        "Supports the investigated hypothesis id if the facts implicate it; "
        "otherwise eliminate per playbook. Never invent ids."
    )
    evidence = await ainvoke_with_retry(evidence_llm, messages + [HumanMessage(prompt)])
    return [evidence]


def build_node(system_prompt: str, tool, hypothesis_id: str, eligible_ids: list[str]):
    """Return an async LangGraph node: (state) -> {"evidence": [Evidence]}.

    state must carry {"case": CasePayload, "hypothesis": Hypothesis}.
    """

    async def node(state: dict) -> dict:
        case = state["case"]
        hypothesis = state["hypothesis"]
        task = (
            f"Order #{case.order_id} — case symptom: {case.symptom}. "
            f"Your hypothesis to check: {hypothesis.label} ({hypothesis.id}). "
            f"YOU MUST call {tool.name} for this order before concluding."
        )
        ids = [hypothesis.id] + [i for i in eligible_ids if i != hypothesis.id]
        evidence = await run_investigator(
            system_prompt=system_prompt,
            task=task,
            tool=tool,
            candidate_ids=ids,
        )
        return {"evidence": evidence}

    return node
