# ORBIT — Technical Requirements Document
**CODE KUDLA 2026 · Synora Builds · 4 builders · v4.1 (domestic India anchor case) · Pairs with ORBIT_PRD.md v4.1**
*v4.1 changes: Tally/GST/Delhivery/Transport mock systems · e-way bill expiry root cause · GST investigator replaces Finance · research-backed TRD structure (DISQO pattern)*

---

## 0. Document Context (DISQO pattern)

| Field | Value |
|-------|-------|
| **Status** | APPROVED FOR BUILD |
| **Scope** | 12-hour MVP + stretch goals |
| **Related Docs** | ORBIT_PRD.md (product spec), PROMPTS.md (build playbook), PROJECT_CONTEXT.md (read-first) |
| **Frozen Contracts** | contracts.py (H1 freeze, no changes without team consensus) |

---

## 1. Architecture Overview

```
INGEST (P0)              BRAIN (LangGraph)                          ACTIONS
┌────────────┐    ┌────────────────────────────────────────────┐
│ Angry email│───▶│ router (reads playbook.yaml)               │    ┌─────────────────────┐
│ (Gmail poll│    │   └─Send() fan-out───────────────┐         │    │ telegram_bot.py     │
│  10s)      │    │     ┌ investigator_gst ──────────┤         │    │  alert + INVESTIGATE│
└─────┬──────┘    │     ├ investigator_inventory ────┤         │    │  button + callback  │
      ▼           │     └ (warehouse/transport       │         │    │  poll (getUpdates)  │
┌────────────┐    │        stretch)              ▼   │         ├─────────────────────┤
│ intent_    │    │   synthesizer (early exit, stamps)         │    │ gmail_drafter.py    │
│ classifier │    │   challenger (TOOL-EQUIPPED adversary)     │    │ eta_recalc.py       │
│ (LLM, ~500 │    │   ⏸ interrupt() ← AUTHORIZE FIX? (console) │    └─────────────────────┘
│  tokens)   │    │   executor (e-way bill renewal + verify)   │───▶          ▲
└─────┬──────┘    │   action_drafter                           │    ┌───────┴─────────────┐
      ▼           └──────┬─────────────────────────────────────┘    │ executor.py         │
┌────────────┐           │ astream_events(v2)                       │ eway_bill: expired→ │
│ Telegram   │           ▼                tool calls (SQL)          │ renewal_requested   │
│ alert +    │    ┌────────────────────┐◀──────────────┐           └─────────────────────┘
│ [🔍 INVEST-│    │ FastAPI SSE server  │              │
│  IGATE]    │    └────────┬───────────┘   ┌───────────┴──────────┐
└─────┬──────┘             ▼               │ MOCK ENTERPRISE      │
      │ tap         ┌────────────────────┐ │ (4 SQLite DBs)       │
      └──────────▶│ Console (Evidence   │ │ tally_erp.db         │
   callback poll  │ Board, static)      │ │ gst_portal.db        │
                  │ + backup Investigate│ │ delhivery.db         │
                  │   button on pending │ │ transport.db         │
                  └────────────────────┘ └──────────────────────┘
                                           ┌────────────────────┐
                                           │ playbook.yaml      │
                                           │ — THE MOAT         │
                                           └────────────────────┘
```

**Validation:** topology matches AWS's official RCA reference architecture (`aws-samples/sample-rca-deep-investigations`): incident → HITL interrupt() → structured hypothesis generation → Send() per hypothesis → parallel subagents → synthesis. We adopt their interrupt pattern; we add what they lack: adversarial challenger, contradiction-collapse UX, execution with verification.

---

## 2. Tech Stack (locked)

| Layer | Choice | Why |
|-------|--------|-----|
| Orchestration | LangGraph (Python) | Send fan-out, interrupt() for HITL, astream_events v2 — matches winner + reference-arch patterns |
| LLM | GPT-4o-mini primary / DeepSeek V4 Flash fallback | `get_llm()` factory; structured output reliability on the live path |
| Backend | FastAPI + uvicorn | Native SSE |
| Frontend | Single-page HTML + vanilla JS + Tailwind CDN | Streamlit can't do state-driven animation; zero build step |
| Mock enterprise | 4× SQLite (Tally, GST portal, Delhivery, Transport) | Contradictions in DATA, correlation in BRAIN |
| Moat artifact | `playbook.yaml` | Declarative investigation logic — inspectable, generalizable |
| Tracing | LangSmith free tier | Judges inspect real reasoning — credibility weapon |
| Trigger | **Gmail poller (10s) + intent classifier + Telegram INVESTIGATE button** (P0); console backup button + CLI injection (fallbacks) | Email is the natural entry point; Telegram button = HITL beat #1. getUpdates polling = no public webhook needed (venue NAT-safe) |
| Actions | Telegram Bot API + Gmail draft | Internal executes, external drafts |

---

## 3. Contracts (frozen H1) — with FULL SSE payloads

```python
class CasePayload(BaseModel):
    case_id: str; order_id: str; symptom: str
    source: Literal["email", "manual", "cli"]
    sender: str | None = None; thread_id: str | None = None
    intent: str | None = None          # "angry_customer" — from intent classifier
    urgency: Literal["low", "medium", "high"] | None = None
    summary: str | None = None         # one-line summary for Telegram alert

class Hypothesis(BaseModel):
    id: str; label: str; rationale: str; investigator: str

class Evidence(BaseModel):
    source: str; found: bool; detail: str
    eliminates: list[str]; supports: list[str]; raw: dict

class PortalStamp(BaseModel):
    verdict: Literal["TRUE", "STALE", "MISLEADING"]; reason: str

class Verdict(BaseModel):
    root_cause: str; confidence: float
    evidence_trail: list[Evidence]; ruled_out: list[str]
    portal_verdicts: dict[str, PortalStamp]; wall_clock_s: float

class ChallengeResult(BaseModel):
    attack: str; evidence_checked: list[str]   # which DBs the challenger re-queried
    survived: bool; confidence_delta: float; reasoning: str

class ExecutionResult(BaseModel):
    action: str; before: dict; after: dict; verified: bool

class ActionResult(BaseModel):
    type: Literal["telegram", "gmail_draft", "eta_recalc"]
    status: Literal["sent", "drafted", "done", "failed"]
    ref: str | None; error: str | None = None
```

**SSE events with frozen payloads** (console codes against these, no improvisation):
```
case_ingested      {case_id, order_id, symptom, source}
hypotheses_ready   {hypotheses: [{id, label, rationale, investigator}]}
investigator_start {investigator, hypothesis_id}
evidence_found     {investigator, evidence: Evidence, trace_line}
hypothesis_ruled_out {hypothesis_id, by_evidence_source}
portal_stamped     {portal, stamp: PortalStamp}
verdict_draft      {partial_root_cause}
challenge_start    {attack_preview}
challenge_result   {attack, evidence_checked, survived, confidence_delta}
approval_required  {proposed_action, before: {eway_bill: "expired"}}        # interrupt()
verdict_locked     {verdict: Verdict}
execution_done     {execution: ExecutionResult}
action_done        {action: ActionResult}
case_closed        {case_id, wall_clock_s, llm_cost_usd}
error              {where, message, degraded: bool}
```

---

## 4. playbook.yaml (the moat)

```yaml
case_types:
  shipment_delay:
    triggers: ["shipment stuck", "order late", "no tracking", "vehicle breakdown"]
    hypotheses:
      - id: h_eway_bill_expired
        investigator: gst
        check: {db: gst_portal, table: eway_bills, key: order_id}
        eliminates_if_clean: [h_eway_bill_expired]
      - id: h_inventory_damage
        investigator: inventory
        check: {db: tally_erp, table: inventory, key: sku}
        eliminates_if_clean: [h_inventory_damage]
      - id: h_dispatch_failure
        investigator: warehouse      # stretch
      - id: h_transport_breakdown
        investigator: transport      # stretch
    portal_stamp_rules:
      tally: {if: "order_status=Dispatched and transport_booking=none", stamp: STALE,
              reason: "order status ≠ shipment status"}
      transport: {if: "breakdown_claimed and eway_bill=expired", stamp: MISLEADING,
                  reason: "breakdown is excuse — waiting for e-way bill renewal"}
      gst: {if: "eway_bill=expired", stamp: TRUE, reason: "validity lapsed, can't cross border"}
      delhivery: {if: "last_scan_age_days > 3", stamp: STALE,
                  reason: "last scan 6 days old"}
  payment_hold: {...}      # fixture case #002
  inventory_mismatch: {...} # #003 — 6 archetypes total
```
Router loads this → LLM adds rationale → hypotheses. **"Why not a chatbot?" answer: this file.** Six case types, one brain, declarative logic a judge can read in 60 seconds.

---

## 5. LangGraph Design

### State
```python
class InvestigationState(TypedDict):
    case: CasePayload
    hypotheses: list[Hypothesis]
    evidence: Annotated[list[Evidence], operator.add]
    verdict: Verdict | None
    challenge: ChallengeResult | None
    approved: bool | None            # set by interrupt() resume
    execution: ExecutionResult | None
    actions: Annotated[list[ActionResult], operator.add]
    trace: Annotated[list[str], operator.add]
    loop_count: int
    started_at: float
```

### Topology
```
router ──Send()──▶ inv_gst ───────┐
       ──Send()──▶ inv_inventory ─┼─▶ synthesizer ─▶ challenger ─▶ ⏸ interrupt(approval)
       (stretch: warehouse,       │      ▲    │ refuted, loop≤1        │ approved
        transport) ───────────────┘      └────┘                        ▼
                                              executor ─▶ action_drafter ─▶ END
```

### The four judge-bait mechanisms (whiteboard-ready)
1. **Send() fan-out** + `operator.add` reducer — true parallel investigation (AWS reference-arch pattern)
2. **Early exit** — synthesizer conditional edge; undispatched investigators show "SKIPPED — cause locked"
3. **Tool-equipped Challenger** — adversary gets read-only query tools to ALL 4 DBs; must attempt the strongest alternative (e.g., verify transport-first failure: check transport DB for breakdown log, check warehouse dispatch). `evidence_checked` lists what it actually queried — visible in UI. Max 1 re-open.
4. **`interrupt()` approval gate** — graph pauses before executor; console shows AUTHORIZE FIX?; resume via `Command(resume={"approved": True})`. Enterprise trust story + a LangGraph capability judges recognize.

### Confidence formula (deterministic)
```
base      = 0.50 × culprit_evidence_strength
coverage  = 0.30 × (eliminated / total_hypotheses)
agreement = 0.20 × (portals_resolved / total_portals)
challenge_bonus = +0.06 if survived
confidence = min(0.99, base + coverage + agreement + challenge_bonus)   # #402 ≈ 0.94
```

---

## 6. Mock Enterprise Systems (4 SQLite DBs — contradictions by design)

| DB | #402 ground truth |
|----|-------------------|
| `tally_erp.db` | order "Dispatched ✓" (order-level status — stale join, truck never left) |
| `gst_portal.db` | `eway_bill=expired, gstr3b_filed=false` ← **culprit**. Writable: executor sets `eway_bill="renewal_requested"` |
| `delhivery.db` | last scan 6 days ago, no movement (stale) |
| `transport.db` | breakdown claimed (misleading — excuse while waiting for e-way bill) |

8–12 filler rows per DB so #402 isn't staged. Investigators' tools see ONLY their own DB. Challenger's tools read ALL DBs (it's the cross-examiner). LLMs never see answers in prompts — tool calls only.

**Portal stamps (the collapse):** synthesizer maps evidence → portal verdicts: Tally=STALE ("order status ≠ shipment status"), Transport=MISLEADING ("breakdown is excuse"), GST=TRUE, Delhivery=STALE ("last scan 6 days old").

---

## 7. API Surface

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/investigate` | POST {order_id} | Manual/console trigger (backup) |
| `/api/investigate/{case_id}` | POST | Fire investigation for a pending case (called by Telegram callback AND console backup button) |
| `/api/stream/{case_id}` | GET SSE | Live events (frozen payloads §3) |
| `/api/approve/{case_id}` | POST {approved} | Resume interrupt |
| `/api/cases`, `/api/cases/{id}` | GET | Case board + detail (pending cases included) |
| `/api/replay/{case_id}` | GET SSE | Recorded stream (labeled REPLAY) |
| `/` | GET | Static console |

---

## 7b. Ingest Layer (P0)

**ingest/gmail_poller.py** — `async poll_inbox(callback, interval=10)`: Gmail API (google-api-python-client), creds from env `GMAIL_CREDENTIALS_PATH` + `token.json`, queries unread INBOX newer than last poll, passes each to parser, marks processed read. Never crashes: credential/API failure → log once, keep polling.

**ingest/parser.py** — `parse_trigger_email(subject, body, sender, thread_id) -> CasePayload | None`: regex order ID (`#\d+`, "order 402"), then **intent_classifier** (one LLM call, ~500 tokens, structured output): `{intent: "angry_customer"|"inquiry"|"spam"|..., urgency, summary, symptom}`. Returns None for non-triggers. source="email".

**ingest/inject_email.py** — CLI fallback: `python -m ingest.inject_email --order 402` builds the identical CasePayload (source="cli") and posts to the same pending-case path. Dev tool — judges never see it.

**actions/telegram_bot.py** — the dual-role bot:
1. `send_alert(case: CasePayload) -> message_id`: sends summary alert with `InlineKeyboardMarkup([[InlineKeyboardButton("🔍 INVESTIGATE", callback_data=f"investigate:{case.case_id}")]])`
2. `async poll_callbacks(on_investigate, interval=2)`: polls `getUpdates` (offset-tracked), on `CallbackQuery` with `investigate:*` → `answer_callback_query` (removes loading state), edits message to "🔍 Investigation started — watch the console", calls `on_investigate(case_id)` → POST `/api/investigate/{case_id}`. **No webhook — polling works behind venue NAT.**
3. `send_verdict_alert(verdict, case)`: post-investigation confirmation (root cause, confidence, actions).
All functions return failure dicts, never raise.

---

## 8. Console UX Spec — "THE EVIDENCE BOARD" (diegetic design, not a dashboard)

**Concept:** a detective's investigation board — pinned case photos, red string, rubber stamps — reimagined for systems. The UI *is* the story. No generic AI-dashboard cards.

**Research basis (verified):** 2026 design roundups confirm skeuomorphism/tactile UI is trending as a reaction against flat AI-generated sameness; Awwwards weights Design 40% / Usability 30% / Creativity 20% / Content 10% — so the board is theater, but trigger input + phase banner + footer stay conventional and findable in 2 seconds.

**Visual system:**
- **Canvas:** cork-board texture (CSS noise, not an image), warm dark brown `#2a2118` — physical, not flat black
- **Portal cards (4):** pinned "case photos" — paper `#f4efe4`, slight rotation (±2° max), pushpin, torn-paper edge; each status shown as a distressed **rubber-stamp imprint**
- **Red string:** SVG animated threads `#b93324` connecting portals → hypotheses, drawn live as evidence lands. Ruled-out hypothesis → its string **snaps** (animated break, falls slack). THE signature visual.
- **Evidence:** arrives as **polaroids** clipped to the board, raw DB row visible like a photographed document
- **Verdict:** types onto a sliding **case-file folder**, typewriter font, "CONFIDENTIAL" stamp; confidence % circled in red handwriting (SVG stroke animation)
- **Challenger:** board dims red; attack scrawled in a second handwriting font; survived → red **"CLEARED"** stamp slam + 2px/150ms screen shake
- **Approval gate:** wax-seal "AUTHORIZE FIX" button, presses like a stamp
- **Execution:** e-way bill card **3D-flips** red EXPIRED → green RENEWAL REQUESTED
- **Case board:** 5 closed cases as filed folders in a slide-out drawer
- **Typography (3 fonts max):** Special Elite typewriter (evidence/verdict — the brand) · Caveat handwriting (challenger scrawl) · condensed system sans (labels)
- **Color discipline:** `#b93324` red (string/danger) · `#d4a017` amber (stale) · `#2d6a4f` green (true/renewed) · paper tones. No purple, no gradients, no neon.
- **Sound:** CUT unless P10 finishes early — 3 preloaded mp3s max (paper, stamp, snap), mute default ON (projector safety)

**Restraint rules (from brutalist-failure research):**
1. Every animation encodes information — string snap = eliminated, stamp = reconciled, flip = executed. Zero decorative animation.
2. **60fps or cut it** — transform/opacity only, no layout-thrashing animations
3. **`prefers-reduced-motion` media query** — one CSS block disables shake/snap/scrawl animations (accessibility is a judging criterion)
4. **Mobile fallback <900px:** board collapses to clean vertical stack — information survives even if theater doesn't
5. Max 2 rotations per element, max 3 fonts, max 4 colors

**Layout:** full-viewport board canvas | top bar (CONVENTIONAL, findable in 2s): case ID + phase banner + order-ID input | right slide-out: case-file drawer | footer (conventional): wall-clock, LLM cost, LangSmith link
**Phase banner:** INGESTING / INVESTIGATING / CHALLENGING / AWAITING APPROVAL / EXECUTING / CLOSED
**5-second rule:** stranger knows what's happening at any glance
**Honesty badges:** real wall-clock · "REPLAY" label · "DEGRADED" on cached verdict
**Tech:** vanilla JS + SVG paths for strings (~40 lines) + CSS 3D transforms + Google Fonts. No canvas libs, no build step. 1280px projector-safe.
**Build order (protected):** static board → SSE wiring → strings → stamps → flip → polish. If time squeezes: protect **string-snap** and **hold-flip** — the two moments judges retell.

---

## 9. Resilience (rehearsed, not hoped)

| Failure | Fallback | Narration |
|---------|----------|-----------|
| LLM timeout 15s | Cached verdict + DEGRADED badge | Honesty |
| Wi-Fi dead | Hotspot (tested H0) → replay mode | "Recorded real run" |
| Telegram/Gmail down | Failure dict + on-screen panel | "Graceful by design" |
| SSE disconnect | Auto-reconnect + badge | — |

**Drills at H20:** Wi-Fi off · LLM key invalid · Telegram invalid · Telegram button fails (console backup) · Gmail OAuth dead (CLI injection) · Approval REJECTED · Full offline (replay mode) — each recovers <15s.

---

## 10. Repo Layout & Ownership

```
orbit/
├── contracts.py          # ALL (frozen H1)
├── playbook.yaml         # Aflal (moat)
├── graph.py              # Aflal — router, synthesizer, wiring, interrupt
├── challenger.py         # Aflal — tool-equipped adversary
├── investigators/        # Aflal — gst.py, inventory.py (stretch: warehouse, transport)
├── enterprise/seed.py, query.py   # Shabil — 4 DBs + fixtures
├── executor.py           # Shabil — e-way bill renewal + verify
├── actions/              # Shabil — telegram_bot (alert + INVESTIGATE button + callback poll + verdict alert), gmail_drafter, eta_recalc
├── ingest/               # Shabil — P0: gmail_poller, parser, intent_classifier, inject_email (CLI fallback)
├── server.py             # M3 — FastAPI, SSE, approve endpoint, replay
├── replay/               # M3 — recorded event streams
├── static/               # M4 — index.html, app.js, styles.css
└── tests/                # M3 — test_e2e.py + module tests
```

---

## 11. Definition of Done (H12 gate)

- [ ] `pytest tests/test_e2e.py` green: #402 → root_cause "eway_bill.expired", confidence ≥0.9, challenge survived with non-empty evidence_checked, interrupt resumes on approve, execution verified (re-read = renewal_requested)
- [ ] Email trigger → Telegram button → full console animation → approval click → Telegram + Gmail draft + e-way bill renewed, <90s
- [ ] 3 consecutive clean runs · `graph.py` + fixture + playbook.yaml walkthrough ready
- [ ] LangSmith trace link works

---

## 12. Capacity Planning (DISQO pattern)

| Metric | Target | Notes |
|--------|--------|-------|
| Concurrent investigations | 1 (demo), 3 (stress test) | SQLite handles 3 concurrent read/write; LangGraph state isolated per case_id |
| SSE latency | <200ms from emission | StreamingResponse with X-Accel-Buffering: no |
| LLM tokens per investigation | ~15-30K | GPT-4o-mini: ~₹2-4 per run; 500+ rehearsals on ₹600 budget |
| Demo duration | <90s end-to-end | Streaming masks LLM latency; honest wall-clock badge |

---

## 13. Testing Strategy (DISQO pattern)

| Level | Coverage | Tools |
|-------|----------|-------|
| Unit | contracts, confidence formula, eta_recalc, parser | pytest |
| Integration | investigators (real LLM), challenger (real LLM), e2e graph | pytest + real API calls |
| Manual | Telegram bot, Gmail poller, SSE stream, console animations | Browser DevTools + phone |
| Failure | Wi-Fi off, LLM timeout, Telegram down, approval rejected | Rehearsed drills at H20 |

**Test count target:** 12+ tests (mention in Q&A: "Our investigation logic has 12 tests" — LORE winner had 43, judges noticed).

---

## 14. Explicitly Cut (say so if asked)

Next.js, auth, real ERP APIs, 6 live cases, auto-send external, RAG/vector stores, Docker, K8s, message queues, Telegram webhook (polling is NAT-safe), international trade (customs/HS codes/DGFT), deployment (localhost only). venv + run.py is enough.
