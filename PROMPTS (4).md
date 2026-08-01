# ORBIT — OpenCode Prompt Playbook
**Synora Builds · CODE KUDLA 2026 · v4.1 (domestic India case + research-backed prompt patterns)**
*Run prompts in order. Each is copy-paste ready. Do not skip gates.*
*v4.1 changes: domestic Mangaluru→Mumbai case · GST investigator replaces Finance · e-way bill expiry root cause · ambient context pattern (PROJECT_CONTEXT.md as CLAUDE.md equivalent) · checkpoint commits with rollback commands · "what breaks first" per module*

---

## 0. How to Use This File (read first — 2 minutes)

**The golden rules (from prompt engineering research + hackathon experience):**

1. **One prompt = one module.** Never "build the whole project." Free models lose coherence across >2 files.
2. **Paste `contracts.py` into every prompt after P1.** The model cannot drift from schemas it's looking at. This is the single highest-leverage habit.
3. **Every prompt has: GOAL → CONTEXT → EXACT SPEC → CONSTRAINTS → DONE-WHEN.** If a prompt has a `[PASTE]` marker, fill it before sending.
4. **Run the DONE-WHEN check after every prompt.** On failure: *"This failed with: [error]. Fix only what's needed to make the check pass. Do not refactor anything else."*
5. **Commit after every green gate:** `git add -A && git commit -m "P<n>: <name>"`. Rollback command if a later prompt breaks something: `git reset --hard HEAD~1` (or `git log` to find the last green commit).
6. **Model strategy:** DeepSeek V4 Flash Free primary → Nemotron 3 Ultra Free when throttled (context carries over). Non-thinking for routine prompts; thinking mode only for P5 (graph) and P7 (challenger).

**Ambient context pattern (research-backed):** PROJECT_CONTEXT.md serves as your CLAUDE.md equivalent — read it at the start of every OpenCode session (P0 does this). It contains the one-sentence identity, architecture in one paragraph, and the 7 rules that outrank everything.

---

## 1. Skills Mapped

| Skill | Used in |
|---|---|
| `using-superpowers` | P0 (every session start) |
| `spec-driven-development` | P1 (contracts) |
| `planning-and-task-breakdown` | P0.5 (plan validation) |
| `test-driven-development` | P2–P8 (all backend) |
| `subagent-driven-development` | P5, P7 (brain + challenger — highest risk) |
| `incremental-implementation` | Throughout |
| `fullstack-dev` | P9 (server) |
| `frontend-ui-engineering` | P10 (console) |
| `browser-testing-with-devtools` + `vision-analysis` | P11 (visual audit) |
| `requesting-code-review` | P12 (pre-demo review) |
| `git-workflow-and-versioning` | After every gate |
| `doubt-driven-development` | P7 (challenger — fittingly) |

---

## 2. Folder Setup (before opening OpenCode)

```
orbit/
├── PROJECT_CONTEXT.md    ← read-first file (ambient context)
├── ORBIT_PRD.md
├── ORBIT_TRD.md
├── PROMPTS.md
└── NOTES.md              ← empty decision log
```
`cd orbit && git init && opencode`

---

## 3. The Prompts

### P0 — Session bootstrap (EVERY new session)

```
SKILL: using-superpowers

You are building Orbit, an AI detective for stuck business operations, at a 24-hour hackathon. Read these files in the repo root before anything else:
- PROJECT_CONTEXT.md (identity, rules, architecture in one paragraph)
- ORBIT_TRD.md (full technical spec)
- NOTES.md (decisions so far)

Rules for every task this session:
1. Python 3.11+, LangGraph, FastAPI, SQLite, vanilla JS. No new dependencies without asking.
2. contracts.py is the frozen source of truth — never invent fields. SSE payloads are fully specified there.
3. playbook.yaml is the investigation-logic moat — hypotheses and elimination rules come from it, never hardcoded in prompts.
4. One module per task. Run the verification check after each. Stop and report instead of improvising.
5. Deterministic over clever. Graceful degradation everywhere — every external call returns failure dicts, never raises. This demo must not die on stage.

Confirm by summarizing the architecture in 5 bullets. Then wait for my task.
```

**DONE-WHEN:** summary matches PROJECT_CONTEXT.md architecture paragraph.

---

### P0.5 — Plan validation (optional but recommended)

```
SKILL: planning-and-task-breakdown

Read PROMPTS.md and cross-check against ORBIT_TRD.md. Report only:
1. Steps whose DONE-WHEN can't verify the step's output
2. Missing dependencies between steps
3. Steps too big for one focused session
Numbered issues only. If none: "plan is sound".
```

---

### P1 — Contracts + LLM factory (frozen schemas + full SSE payloads)

```
SKILL: spec-driven-development

GOAL: Create contracts.py (frozen Pydantic schemas + SSE event payloads) and llm.py (model factory).

CONTEXT: 4-system investigation engine. These schemas coordinate 4 builders and multiple AI sessions. Frozen after this task.

EXACT SPEC — create contracts.py with EXACTLY these models from TRD §3:
[PASTE TRD §3 contracts code block verbatim — CasePayload, Hypothesis, Evidence, PortalStamp, Verdict, ChallengeResult, ExecutionResult, ActionResult]

PLUS the SSE payload spec as a frozen dict SSE_EVENTS mapping each of the 16 event names to its payload field list, copied from TRD §3:
[PASTE TRD §3 SSE event payload list verbatim]

ALSO llm.py: single get_llm() factory — reads MODEL_PROVIDER, MODEL_NAME, MODEL_BASE_URL, and the API key from env; returns a LangChain ChatOpenAI. Defaults: base_url https://api.deepseek.com, model deepseek-v4-flash. If LANGSMITH_API_KEY is set, enable LangSmith tracing via env vars (LANGCHAIN_TRACING_V2=true). One function, nothing else.

CONSTRAINTS:
- Pydantic v2. No business logic in contracts.py. Docstring with one-line example per model.

DONE-WHEN: `python -c "from contracts import CasePayload, Verdict, ChallengeResult, ExecutionResult, ActionResult, PortalStamp, SSE_EVENTS; from llm import get_llm; print('contracts OK')"` runs clean.
```

**Commit: `P1: contracts + llm factory`**
**Rollback if broken:** `git reset --hard HEAD~1`

---

### P2 — Mock enterprise systems (4 SQLite DBs, contradictions by design)

```
SKILL: test-driven-development

GOAL: enterprise/seed.py building 4 SQLite DBs with deliberately contradictory data for Order #402, plus enterprise/query.py read tools.

CONTEXT: [PASTE contracts.py] + TRD §6 ground truth:
[PASTE TRD §6 mock enterprise table verbatim]

EXACT SPEC:
- seed.py creates enterprise/dbs/{tally_erp,gst_portal,delhivery,transport}.db
- Realistic schemas + 8–12 filler rows per DB (other orders/customers) so #402 isn't staged
- #402 ground truth exactly per table: Tally "Dispatched ✓" (order-level), GST eway_bill=expired gstr3b_filed=false, Delhivery last scan 6 days ago, Transport breakdown claimed
- gst_portal eway_bills is WRITABLE (executor renews later)
- 5 closed fixture cases (#002–#006) with pre-written verdicts in a cases table
- query.py: query_tally, query_gst, query_delhivery, query_transport — read-only, return dicts

CONSTRAINTS:
- Test first: tests/test_enterprise.py asserts all 4 contradictions BEFORE seed.py exists
- Plain sqlite3, no ORM. Deterministic — hardcoded July 2026 dates, no random, no now()
- Idempotent rebuild

DONE-WHEN: `pytest tests/test_enterprise.py -v` green + `python enterprise/seed.py` rebuilds cleanly.
```

**Commit: `P2: mock enterprise systems`**
**What breaks first:** GST portal schema mismatch with executor's renewal write — test the write path in P2, not later.

---

### P2.5 — playbook.yaml (THE MOAT)

```
GOAL: Create playbook.yaml — the declarative investigation logic that makes Orbit generalizable.

CONTEXT: This file is the answer to "why not just a chatbot?" — encoded investigation logic: which evidence eliminates which hypothesis, per case type. The router loads it; nothing investigation-related is hardcoded in LLM prompts.

EXACT SPEC: create playbook.yaml following TRD §4 exactly:
[PASTE TRD §4 playbook.yaml block verbatim]
Complete all 6 case types: shipment_delay (full detail), payment_hold, inventory_mismatch, customs_block, invoice_dispute, compliance_block (these 5 need hypotheses + checks matching the fixture verdicts seeded in P2).

ALSO playbook.py: load_playbook() → parsed dict; hypotheses_for(case_type) → list[Hypothesis] (from contracts.py); stamp_rules_for(case_type) → portal stamp rules.

CONSTRAINTS:
- YAML must parse to the exact Hypothesis schema in contracts.py
- tests/test_playbook.py: all 6 case types load; shipment_delay yields the 4 canonical hypothesis IDs (h_eway_bill_expired, h_inventory_damage, h_dispatch_failure, h_transport_breakdown)

DONE-WHEN: `pytest tests/test_playbook.py -v` green.
```

**Commit: `P2.5: investigation playbook — the moat`**

---

### P3 — Investigators (parallel evidence-gatherers)

```
SKILL: test-driven-development

GOAL: investigators/gst.py and investigators/inventory.py — LangGraph nodes that query their OWN mock system via tools and return Evidence.

CONTEXT: [PASTE contracts.py] + [PASTE enterprise/query.py]

EXACT SPEC per investigator:
- LangChain tool wrapping its query function (gst→gst_portal ONLY, inventory→tally_erp inventory ONLY)
- Node function: receives InvestigationState, LLM with tool binding, calls its tool (max 2 calls), interprets rows, returns {"evidence": [Evidence(...)]}
- Evidence.eliminates/supports reference playbook hypothesis IDs
- GST on #402: found=True, detail contains "eway_bill=expired", supports=["h_eway_bill_expired"]
- Inventory on #402: found=True, detail contains "stock=12", eliminates=["h_inventory_damage"]

CONSTRAINTS:
- LLM must call the tool — never put query results in the prompt (honesty contract)
- tests/test_investigators.py with REAL LLM calls (~2K tokens, cheap)

DONE-WHEN: `pytest tests/test_investigators.py -v` green with real LLM.
```

**Commit: `P3: GST + inventory investigators`**

---

### P4 — Router (playbook-driven) + synthesizer

```
SKILL: test-driven-development

GOAL: graph.py router_node + synthesizer_node + route_after_synthesis — not yet wired.

CONTEXT: [PASTE contracts.py] + [PASTE playbook.py]

EXACT SPEC:
- router_node(state): classify case type (one cheap LLM call) → hypotheses_for(case_type) from playbook → LLM adds one-line rationale per hypothesis (structured output, 1 retry on validation failure)
- synthesizer_node(state): deterministic rules ONLY:
  1. Culprit = hypothesis with supporting Evidence(found=True)
  2. Confidence via EXACT TRD §5 formula: [PASTE TRD §5 formula]
  3. portal_verdicts from playbook stamp_rules evaluated against evidence (not hardcoded)
  4. Verdict with wall_clock_s = time.time() - state["started_at"]  # HONEST clock
- route_after_synthesis: confidence >= 0.9 → "challenger"; else → "router" (loop_count < 1)

CONSTRAINTS:
- Confidence NEVER from the LLM
- tests/test_router_synth.py: synthetic evidence → assert confidence math to 2 decimals + routing

DONE-WHEN: `pytest tests/test_router_synth.py -v` green.
```

**Commit: `P4: router + synthesizer`**

---

### P5 — Full graph wiring with Send fan-out (H6 GATE — the brain)

```
SKILL: subagent-driven-development

GOAL: Complete graph.py — router → Send() parallel investigators → synthesizer → challenger → interrupt() approval → executor → action_drafter. TRD §5 topology:
[PASTE TRD §5 state + topology verbatim]

EXACT SPEC:
- InvestigationState exactly per TRD §5 (evidence/trace/actions use Annotated[list, operator.add])
- Router fans out: return [Send("investigator_gst", {...}), Send("investigator_inventory", {...})]
- Early exit: only dispatch investigators for hypotheses not yet ruled out
- challenger_node: STUB for now (real one comes in P7) — returns survived=True, confidence_delta=+0.06, evidence_checked=[]
- interrupt gate: interrupt({"type": "approval_required", "proposed_action": ...}) before executor; resume via Command(resume={"approved": bool}); if rejected → skip executor, note in trace
- executor_node: root_cause contains "eway_bill.expired" → UPDATE gst_portal SET eway_bill='renewal_requested' WHERE order_id → RE-READ to verify → ExecutionResult(verified=True)
- action_drafter_node: 3 ActionResult dicts (telegram, gmail_draft, eta_recalc) — content only, senders wired in P8
- build_graph() with checkpointer (MemorySaver) — REQUIRED for interrupt()
- investigate(case) async generator: maps graph.astream_events(version="v2") to our 16-event SSE vocabulary with EXACT payloads from contracts.SSE_EVENTS
- Every node appends trace lines ("> gst: eway_bill=expired → culprit")

CONSTRAINTS:
- Subagent workflow: implementer → spec-reviewer → quality-reviewer. No skipped reviews.
- tests/test_e2e.py: full run on CasePayload(order_id="402", source="manual") with resume approved → root_cause contains "eway_bill.expired", confidence >= 0.9, execution verified. Real LLM, real DBs.

DONE-WHEN: `pytest tests/test_e2e.py -v` green. **H6 GATE — brain works in CLI.**
```

**Commit: `P5: full graph — H6 GATE`**
**What breaks first:** Send() fan-out with reducer conflicts — if evidence doesn't accumulate, check operator.add annotation on evidence field.

---

### P6 — Actions: Telegram + Gmail drafter + ETA

```
SKILL: doubt-driven-development

GOAL: actions/telegram_bot.py, actions/gmail_drafter.py, actions/eta_recalc.py.

CONTEXT: [PASTE contracts.py ActionResult]

EXACT SPEC:
- telegram_bot.py: send_manager_alert(verdict, case) → python-telegram-bot v20+ async, token/chat_id from env. Format: case ID, root cause, confidence, actions, new ETA. Returns ActionResult(status="sent"|"failed") — NEVER raises. (P6.5 extends this with INVESTIGATE button flow.)
- gmail_drafter.py: create_buyer_draft(verdict, case, thread_id) → Gmail users.drafts.create, threaded. Empathetic: cause found (e-way bill renewed), new ETA, apology. DRAFT ONLY — external actions are approval-gated (our trust story).
- eta_recalc.py: recalc_eta(case) → promised + 3 days.
- Each module: comment listing its 3 most likely failure modes, each handled explicitly.

DONE-WHEN: `pytest tests/test_actions.py -v` green (eta unit-tested; others import-clean + failure-path tested with mocked APIs).
```

**Commit: `P6: action layer`**

---

### P6.5 — Ingest: Gmail poller + intent classifier + Telegram INVESTIGATE button (P0 — the demo's entry point)

```
SKILL: test-driven-development

GOAL: Create the full ingest + alert layer: ingest/gmail_poller.py, ingest/parser.py, ingest/intent_classifier.py, ingest/inject_email.py, and extend actions/telegram_bot.py with the INVESTIGATE button flow.

CONTEXT: [PASTE contracts.py — note CasePayload now has intent/urgency/summary fields] + TRD §7b: [PASTE TRD §7b ingest layer spec verbatim]

EXACT SPEC:
1. intent_classifier.py: classify_email(subject, body) → structured output {intent: "angry_customer"|"inquiry"|"spam"|"other", urgency: "low"|"medium"|"high", summary: str (one line), symptom: str}. One LLM call (~500 tokens), with_structured_output, 1 retry on validation failure.
2. parser.py: parse_trigger_email(subject, body, sender, thread_id) → CasePayload | None. Regex order ID (#\d+ or "order 402"); if found → classify intent; return CasePayload(source="email", intent/urgency/summary filled). None if no order ID or intent=="spam".
3. gmail_poller.py: async poll_inbox(callback, interval=10) — Gmail API, creds from env GMAIL_CREDENTIALS_PATH + token.json, unread INBOX newer than last poll, callback(case) per hit, mark read. NEVER crashes: credential/API failure → log once, keep polling.
4. telegram_bot.py additions (dual-role):
   - send_alert(case) → "🚨 CUSTOMER ISSUE — Order #{order_id}\n{summary}\nUrgency: {urgency}" + InlineKeyboardMarkup([[InlineKeyboardButton("🔍 INVESTIGATE", callback_data=f"investigate:{case.case_id}")]]). Returns message_id or failure dict.
   - async poll_callbacks(on_investigate, interval=2): getUpdates with offset tracking; on CallbackQuery matching "investigate:*" → answer_callback_query, edit message to "🔍 Investigation started — watch the console", await on_investigate(case_id). NO webhook — polling only (venue NAT-safe).
   - ALL functions return failure dicts, never raise.
5. inject_email.py: CLI — `python -m ingest.inject_email --order 402` builds identical CasePayload (source="cli", pre-filled intent fields) and calls the same pending-case creation path. Dev fallback — judges never see it.

CONSTRAINTS:
- tests/test_parser.py: 6 sample emails (2 triggers, 4 non-triggers incl. spam) — mock the classifier
- tests/test_intent_classifier.py: 3 real LLM calls (angry, neutral, spam) — assert structured fields
- Telegram bot manual test at H6 checkpoint (BotFather token + chat_id, 5 min setup)

DONE-WHEN: parser + classifier tests green + `python -m ingest.inject_email --order 402` creates pending case + Telegram alert with working INVESTIGATE button received in manual test.
```

**Commit: `P6.5: email ingest + intent + Telegram button — DEMO ENTRY POINT`**
**What breaks first:** Telegram callback poll latency (2-5s) — rehearse the pause narration: "manager taps Investigate..." (feels intentional, not broken).

---

### P7 — Real Challenger (tool-equipped adversary)

```
SKILL: subagent-driven-development + doubt-driven-development

GOAL: Replace the P5 challenger stub with the real adversarial verifier in challenger.py.

CONTEXT: [PASTE contracts.py ChallengeResult] + [PASTE enterprise/query.py] + TRD §5 mechanism 3:
[PASTE TRD §5 "Tool-equipped Challenger" paragraph]

EXACT SPEC:
- Challenger gets read-only tools to ALL 4 DBs (it's the cross-examiner — investigators see one system, the challenger sees everything)
- Input: verdict + full evidence_trail. Task: construct the STRONGEST alternative explanation and TEST IT with tool calls (e.g., "transport breakdown happened first" → query transport DB for breakdown log, query warehouse dispatch)
- Max 3 tool calls, then must conclude: survived=True (confidence_delta=+0.06) or refuted (re-open, loop_count check prevents cycles)
- evidence_checked lists every DB/table it actually queried — shown in the UI. This is what makes it real, not theater.
- Emits challenge_start (attack_preview) and challenge_result SSE events per contracts.SSE_EVENTS

CONSTRAINTS:
- Subagent workflow with both reviews
- tests/test_challenger.py: on #402 verdict, assert survived=True AND len(evidence_checked) >= 1 (it really queried). Real LLM.

DONE-WHEN: `pytest tests/test_challenger.py -v` green + `pytest tests/test_e2e.py -v` still green.
```

**Commit: `P7: real challenger — adversarial verification`**

---

### P8 — Wire actions into graph

```
GOAL: action_drafter_node calls the real action modules; SSE events per result.

CONTEXT: [PASTE graph.py] + [PASTE actions/ modules]

EXACT SPEC:
- After executor: telegram (await), gmail_draft (only if case.thread_id), eta_recalc → ActionResult each
- action_done SSE event per action (payload per contracts.SSE_EVENTS)
- Any failure → trace "> action failed: <name> — continuing", status="failed", keep going

DONE-WHEN: e2e green + state has 3 ActionResults (senders mocked in test).
```

**Commit: `P8: actions wired`**

---

### P9 — FastAPI server + SSE + replay mode

```
SKILL: fullstack-dev

GOAL: server.py — console serving, triggers, SSE stream, approval endpoint, replay mode.

CONTEXT: [PASTE contracts.py SSE_EVENTS] + [PASTE graph.py investigate()] + TRD §7:
[PASTE TRD §7 API table]

EXACT SPEC:
- POST /api/investigate {order_id} → creates CasePayload(source="manual"), starts investigation task, returns {case_id}
- POST /api/investigate/{case_id} → fires investigation for a PENDING case (created by email/CLI ingest). Called by BOTH the Telegram callback poll AND the console backup button
- Pending cases: email/CLI ingest creates a case row with status="pending" (no investigation yet); GET /api/cases includes these so the console can show them with an Investigate button
- GET /api/stream/{case_id} → StreamingResponse(text/event-stream), exact 16-event payloads, headers Cache-Control: no-cache + X-Accel-Buffering: no
- POST /api/approve/{case_id} {approved} → resumes the interrupt via Command(resume=...)
- GET /api/cases, /api/cases/{id} → live + pending + fixture cases
- GET /api/replay/{case_id} → streams a RECORDED event sequence from replay/case_001.json (labeled replay: true in every frame) — offline fallback
- Record mode: every live investigation also appends its event stream to replay/{case_id}.json
- Startup background tasks: gmail_poller (feeds pending-case creation) + telegram poll_callbacks (feeds /api/investigate/{case_id})
- Static: GET / → static/index.html
- run.py: uvicorn server:app --host 0.0.0.0 --port 8000

CONSTRAINTS:
- SSE <200ms from emission; concurrent tabs isolated per case_id
- tests/test_server.py: TestClient — investigate returns case_id; cases lists fixtures

DONE-WHEN: tests green + `python run.py` serves console (curl check).
```

**Commit: `P9: server + SSE + replay`**

---

### P10 — The console — "THE EVIDENCE BOARD" (WOW GATE)

```
SKILL: frontend-ui-engineering

GOAL: static/index.html, static/app.js, static/styles.css — the Orbit console, designed as a detective's EVIDENCE BOARD, not a dashboard.

CONTEXT: TRD §8 design spec: [PASTE TRD §8 "THE EVIDENCE BOARD" verbatim] + PRD §5 demo beats: [PASTE PRD §5 table] + the 16 SSE payloads from contracts.py: [PASTE SSE_EVENTS]

DESIGN SYSTEM (follow exactly — this is the anti-generic-AI brief, research-backed):
- CANVAS: full-viewport cork-board — warm dark brown #2a2118 with CSS-generated noise (SVG feTurbulence data-URI, NOT an image file). Physical, tactile.
- COLOR DISCIPLINE (4 colors, no exceptions): #b93324 red (string/danger) · #d4a017 amber (stale) · #2d6a4f green (true/renewed) · paper tones #f4efe4/#e8dcc0. NO purple, NO gradients, NO neon.
- FONTS (3 max, Google Fonts): Special Elite (typewriter — evidence/verdict, THE brand) · Caveat (handwriting — challenger scrawl) · condensed system sans (labels only).
- PORTAL CARDS (4): pinned "case photos" — paper #f4efe4, rotation fixed per card (-2deg to +2deg, NEVER more), pushpin dot top-center, torn edge (clip-path). Status starts as plain typewriter text; on portal_stamped it gets the RUBBER STAMP treatment: SVG feTurbulence distressed-ink filter, rotated -8deg, color-coded (green TRUE / amber STALE / red MISLEADING).
- RED STRING: SVG <path> threads (#b93324, 2px, slight catenary sag via quadratic bezier control point) connecting portal cards to hypothesis notes. Drawn with stroke-dashoffset animation on hypotheses_ready. On hypothesis_ruled_out: the string SNAPS — split the path at midpoint, animate both halves falling (rotate + translateY + fade to 35% opacity). ~40 lines JS: fixed anchor points per card, recompute paths per event.
- HYPOTHESIS NOTES: small kraft-paper notes (#e8dcc0) pinned center-board, Special Elite font. Ruled out → red X scrawled (SVG stroke-dashoffset) + string snap.
- EVIDENCE POLAROIDS: white border (thicker bottom), mini clothespin clip; raw DB row as monospace "photographed document", slight skew.
- VERDICT FOLDER: manila case-file slides open from board bottom; verdict types in Special Elite; "CONFIDENTIAL" stamp top-right; confidence % circled by red SVG ellipse stroke animation (600ms).
- CHALLENGER: board dims (overlay rgba(120,0,0,0.25)); attack scrawls in Caveat red; each evidence_checked item pins a counter-note; survived → "CLEARED" stamp slams center + 2px/150ms shake (transform-only keyframes).
- APPROVAL: wax-seal circular button (deep red #b93324, radial emboss via inset box-shadow) — presses in on click (scale 0.92).
- EXECUTION: e-way bill card 3D-flips (rotateY 180deg, preserve-3d, backface-hidden) red EXPIRED → green RENEWAL REQUESTED.
- CASE DRAWER: right-edge tab "CLOSED CASES" slides out 5 manila folder stubs with verdict stamps.
- TOP BAR + FOOTER STAY CONVENTIONAL (Awwwards rule: usability = creativity): order-ID input, phase banner, wall-clock, cost — findable in 2 seconds, no novelty.
- PENDING CASE BANNER: when GET /api/cases returns a case with status="pending" (arrived via email/CLI), show a slim banner under the top bar: "📨 CASE #001 — Order #402 — angry customer email received · [🔍 INVESTIGATE]" — clicking POSTs /api/investigate/{case_id} then opens the EventSource. This is the console backup trigger (used only if the Telegram button path fails).

RESTRAINT RULES (non-negotiable, from brutalist-failure research):
1. Every animation encodes information. Zero decorative animation.
2. 60fps or cut it — transform/opacity animations ONLY, no width/height/top/left animation.
3. prefers-reduced-motion: one CSS block disabling shake/snap/scrawl (accessibility is a judging criterion).
4. Mobile fallback <900px: board collapses to clean vertical stack — information survives without theater.
5. Sound: CUT unless everything else is done — 3 preloaded mp3s max, mute default ON.

LAYOUT: full-viewport board | top bar: ORBIT wordmark (Special Elite) + case ID + phase banner + order-ID input + Investigate | right drawer: closed cases | footer: wall-clock, LLM cost, LangSmith link.

EVENT WIRING per SSE_EVENTS exactly:
  - case_ingested → case folder stub pins to board top
  - hypotheses_ready → 4 kraft notes pin in + strings draw from portals
  - evidence_found → polaroid clips on + trace line types
  - hypothesis_ruled_out → red X scrawl + string snap
  - portal_stamped → rubber stamp slams onto that portal photo
  - challenge_start → red dim + scrawl begins
  - challenge_result → CLEARED stamp + shake (or re-open: strings re-draw)
  - approval_required → wax seal pulses
  - verdict_locked → folder slides open, typewriter, red circle on confidence
  - execution_done → e-way bill card 3D flip
  - action_done → small stamped chits pin to actions area (failed = torn edge, still shown)
  - case_closed → footer updates; board settles

CONSTRAINTS:
- Vanilla JS + SVG + CSS 3D transforms + Google Fonts. Tailwind CDN for layout utilities ONLY — all theming custom CSS. No canvas libs, no build step.
- 1280px projector-safe, no horizontal scroll. SSE auto-reconnect + RECONNECTING badge. REPLAY badge on /api/replay/.
- BUILD ORDER (protected): static board → SSE wiring → strings → stamps → flip → polish. If time squeezes, protect string-snap and hold-flip above all else.

DONE-WHEN: localhost:8000 → type 402 → full sequence (pin → strings → snaps → stamps → challenge → approve → flip) completes with zero console errors, animations hold 60fps, and a stranger glancing at any moment can name the current phase.
```

**Commit: `P10: evidence board console — WOW GATE`**
**What breaks first:** SVG string anchor point calculation — if strings don't connect portals to hypotheses correctly, check getBoundingClientRect() timing (must run after DOM layout).

---

### P11 — Browser verification + visual audit

```
SKILL: browser-testing-with-devtools

GOAL: Verify the demo loop in a real browser; fix what's actually broken.

STEPS:
1. Server running, open console with DevTools
2. Trigger 402; watch every SSE event in Network tab; note any event that fired but didn't animate
3. Screenshot each phase: INGESTING, INVESTIGATING, CHALLENGING, AWAITING APPROVAL, CLOSED
4. Check 1280px layout, console errors, reconnect behavior

Then SKILL: vision-analysis — compare the 5 phase screenshots against TRD §8's 5-second rule. List concrete fixes only.

DONE-WHEN: zero console errors + all 5 phases pass the 5-second test.
```

**Commit: `P11: browser-verified`**

---

### P12 — Pre-demo hardening + review

```
SKILL: requesting-code-review

GOAL: Final review against ORBIT_TRD.md §11 (Definition of Done). Lenses:
1. STAGE-SAFETY: trace what happens when LLM times out (15s), Telegram fails, SSE disconnects, approval is REJECTED. Each must degrade gracefully.
2. CONTRACT DRIFT: any module inventing fields not in contracts.py? Any event payload diverging from SSE_EVENTS?
3. DEMO FIT: walk PRD §5's beat table against the actual event sequence — every beat has an event + animation?
4. JUDGE-PROOFING: graph.py + one fixture + playbook.yaml openable side-by-side in 10s? LangSmith trace link works?

Output: CRITICAL (demo-breaking) / POLISH numbered list. Fix all CRITICAL. NO new features.

DONE-WHEN: zero CRITICAL + 3 consecutive clean runs (email → Telegram button → approval → fix verified, <90s each).
```

**Commit: `P12: DEMO READY`**

---

### P13 — CLI email injection (fallback dev tool — build anytime after P6.5, 15 min)

```
GOAL: Verify ingest/inject_email.py works as the silent fallback.

Already built in P6.5. This gate just verifies: `python -m ingest.inject_email --order 402` → pending case created → Telegram alert arrives with INVESTIGATE button → tap → investigation runs identically to the email path.

DONE-WHEN: full loop via CLI injection completes once. This is the demo insurance policy — rehearse it.
```

**Commit: `P13: CLI fallback verified`**

---

## 4. Failure Drills (after P12, before the event)

1. Wi-Fi off → hotspot → rerun (<15s)
2. LLM key invalid → app logs, doesn't crash → swap key
3. Telegram invalid → demo completes, action shows "failed" gracefully
4. **Telegram INVESTIGATE button fails** → console backup: pending case shows its own Investigate button → demo continues
5. **Gmail OAuth dead at venue** → `python -m ingest.inject_email --order 402` → identical demo
6. Approval REJECTED → executor skipped, trace notes it, demo continues
7. Full offline → replay mode → same UI, labeled REPLAY

---

## 5. Throttle Protocol (Zen free cuts off)

1. `esc` → switch DeepSeek V4 Flash ↔ Nemotron 3 Ultra (context carries over)
2. Both throttled → MiMo V2.5 for mechanical fixes only (never graph logic)
3. Resume: *"Continue from where we stopped. Last completed gate: P<n>. Current task: <rest of prompt>"*

---

## 6. Checkpoint Prep (per Code Kudla agenda)

| Checkpoint | Time | What to show | 2-min script |
|-----------|------|--------------|--------------|
| Scope Validation | 11:45 AM Day 1 | Problem statement + repo compliance | "AI detective for stuck B2B ops. LangGraph + FastAPI, localhost demo. Domestic India case: textile shipment stuck at Hubli, e-way bill expired." |
| Progress Review | 5:00 PM Day 1 | contracts.py + mock DBs working | "Contracts frozen, 4 mock systems seeded with contradictory data. Brain skeleton in progress." |
| Technical Review | 11:00 PM Day 1 | graph.py CLI investigation working (P5 done) | "Full investigation loop works in CLI: router → parallel investigators → synthesizer → challenger → executor. Now wiring console." |
| Final Readiness | 9:00 AM Day 2 | Full loop + video + slides | "Demo ready. 3 clean runs. Backup video recorded. Slides done." |
