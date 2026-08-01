# ORBIT — Project Context (READ FIRST)

**Synora Builds · CODE KUDLA 2026 · 4 builders · 24 hours · v4.1 (domestic India anchor case)**

Companion docs: `ORBIT_PRD.md` (product spec), `ORBIT_TRD.md` (technical spec), `PROMPTS.md` (build playbook), `NOTES.md` (decision log).

## Identity

Orbit is an AI detective for stuck business operations — an angry customer email arrives, Orbit understands it, alerts the manager on Telegram, and on one tap investigates across disconnected systems, survives adversarial cross-examination, gets approval, and executes the fix — live.

Demo sentence: *"A textile shipment is stuck at Hubli for 6 days. Tally says dispatched, Delhivery says in transit, GST portal says e-way bill expired, transport says breakdown. Four systems, four answers. Orbit finds the truth, proves it against attack, and fixes it — in 90 seconds."*

One word: Detective.

## Architecture (one paragraph)

An angry customer email enters through Gmail (poller + intent classifier) → Telegram alert with a [🔍 INVESTIGATE] button → manager taps → a LangGraph investigation begins: a router loads hypotheses from `playbook.yaml` (the declarative moat), fans out parallel investigators (GST, Inventory) that query their own mock SQLite DBs via tools, a synthesizer applies deterministic stamp rules + confidence math, a tool-equipped Challenger re-queries all DBs to attack the verdict, an `interrupt()` approval gate pauses for human authorization on the console, and the executor renews the e-way bill and verifies by re-read. FastAPI streams SSE events to the Evidence Board console; actions (Telegram, Gmail draft, ETA recalc) execute with graceful failure. The 4 enterprise systems are deterministic SQLite fixtures — everything else runs 100% live.

## The 7 rules that outrank everything

1. **One prompt = one module.** Never "build the whole project." Free models lose coherence across >2 files.
2. **`contracts.py` is the frozen source of truth.** Paste it into every prompt after P1. Never invent fields. SSE payloads are fully specified there.
3. **`playbook.yaml` is the investigation-logic moat.** Hypotheses and elimination rules come from it, never hardcoded in prompts. This file is the answer to "why not just a chatbot?"
4. **Every prompt has GOAL → CONTEXT → EXACT SPEC → CONSTRAINTS → DONE-WHEN.** Run the DONE-WHEN check after every prompt. On failure: fix only what's needed to make the check pass — no refactoring.
5. **Commit after every green gate.** `git add -A && git commit -m "P<n>: <name>"`. Rollback: `git reset --hard HEAD~1`.
6. **Deterministic over clever; stub the backend, never the detective.** Every external call returns failure dicts, never raises. This demo must not die on stage. Confidence is auditable math, never LLM.
7. **Model strategy:** DeepSeek V4 Flash Free primary → Nemotron 3 Ultra Free when throttled (context carries over). Non-thinking for routine prompts; thinking only for P5 (graph) and P7 (challenger).

## Tech stack (locked)

Python 3.11+ · LangGraph · GPT-4o-mini / DeepSeek fallback via `get_llm()` · FastAPI + SSE · vanilla JS + Tailwind CDN · 4× SQLite fixtures · LangSmith tracing · Telegram Bot API (polling, no webhook) · localhost only.
