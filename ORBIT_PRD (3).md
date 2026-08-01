# ORBIT — Product Requirements Document
**CODE KUDLA 2026 · Synora Builds · 4 builders · 24 hours · v4.1 (domestic India anchor case)**
*v4.1 changes: domestic Mangaluru→Mumbai textile case · Tally/Delhivery/GST/Transport systems · e-way bill expiry root cause · research-backed PRD structure (Atlassian/Jama/DISQO patterns)*

---

## 0. Document Context (DISQO pattern)

| Field | Value |
|-------|-------|
| **Status** | APPROVED FOR BUILD |
| **Target Release** | Code Kudla 2026 Demo (11:15 AM, Day 2) |
| **Participants** | Aflal (brain), Shabil (data/actions), M3 (server), M4 (console) |
| **Related Docs** | ORBIT_TRD.md (technical spec), PROMPTS.md (build playbook), PROJECT_CONTEXT.md (read-first) |

---

## 1. Product Identity

**One sentence:** Orbit is an AI detective for stuck business operations — an angry customer email arrives, Orbit understands it, alerts the manager on Telegram, and on one tap investigates across disconnected systems, survives adversarial cross-examination, gets approval, and executes the fix — live.

**Demo sentence (memorized by all 4):** *"A textile shipment is stuck at Hubli for 6 days. Tally says dispatched, Delhivery says in transit, GST portal says e-way bill expired, transport says breakdown. Four systems, four answers. Orbit finds the truth, proves it against attack, and fixes it — in 90 seconds."*

**One word:** Detective. Not tracker, not dashboard, not automation.

---

## 2. Problem

B2B operations fail at the **seams between systems**. A stuck order spans ERP, logistics, tax compliance, and transport — each internally consistent, mutually contradictory, impossible to correlate under time pressure.

**Anchor case (only case demoed live):** Order #402 — textile shipment, Mangaluru→Mumbai, 6 days stuck at Hubli checkpoint, ₹4,200/day bleeding (demurrage + buyer penalty), Mumbai retailer needs stock for Monday market, threatening cancellation.

| System | Says | Reality |
|--------|------|---------|
| **Tally ERP** | "Dispatched ✓" | Order marked dispatched, truck never left |
| **Delhivery tracker** | "In Transit" | Last scan 6 days ago, no movement |
| **GST portal** | "E-way bill EXPIRED" | **THE BLOCKER** — validity lapsed, can't cross state border |
| **Transport company** | "Vehicle breakdown" | Excuse — actually waiting for e-way bill renewal |

**Truth:** E-way bill expired because finance didn't file GSTR-3B on time → GST portal blocked renewal → truck stuck at Hubli.

**Generalization (said in close, never demoed):** the failure shape is universal — symptom, suspects, evidence across disconnected systems. One engine, encoded in `playbook.yaml`, handles 6 archetypes.

---

## 3. Assumptions (Atlassian pattern — explicit, not hidden)

| Assumption | Risk if wrong | Mitigation |
|------------|---------------|------------|
| Judges know GST/e-way bill basics | Demo confuses non-Indian judges | 10-second explainer in hook: "e-way bill = permit to transport goods across state borders in India" |
| Venue Wi-Fi reaches Telegram/Gmail | Telegram button fails | Console backup button + CLI injection (drill #4, #5) |
| LLM API latency <15s per call | Demo stalls | GPT-4o-mini primary, streaming masks latency, honest wall-clock badge |
| 4 builders available full 24h | Scope fails | Hard cut list (§3.3), H12 gate with 3-feature fallback |

---

## 4. Goals & Non-Goals

### 4.1 Goals — 12-hour MVP (ships no matter what)

| ID | Goal | Owner | Priority |
|----|------|-------|----------|
| G1 | **Email trigger (P0)**: angry customer email → Gmail poller → intent classification → Telegram alert with [🔍 INVESTIGATE] button → manager taps → investigation fires. **Console backup** + **CLI injection fallback** | Shabil | P0 |
| G2 | LangGraph investigation: router reads `playbook.yaml` → hypotheses → Send() parallel investigators (GST, Inventory) → synthesizer with early exit | Aflal | P0 |
| G3 | **Evidence Board UI**: detective's investigation board — pinned portal "case photos", animated red string (snaps on elimination), rubber-stamp reconciliation (TRUE/STALE/MISLEADING), typewriter verdict | M4 | P0 |
| G4 | **Challenger agent with tools**: adversary re-queries mock DBs to construct strongest alternative (transport-first failure). Verdict survives → confidence +delta | Aflal | P0 |
| G5 | **HITL approval gate** (LangGraph interrupt()): console shows "AUTHORIZE FIX?" wax seal → human clicks → executor renews e-way bill → status flips `expired → renewal_requested`, verified by re-read | Aflal + Shabil | P0 |
| G6 | Actions: Telegram auto-send (internal) + Gmail draft reply (external, gated) + ETA recalc | Shabil | P0 |
| G7 | Deterministic confidence formula (auditable math, never LLM) | Aflal | P0 |
| G8 | Case board: 5 closed fixture cases proving generalization | Shabil | P1 |
| G9 | **Judge-credibility weapons**: LangSmith trace link, cost counter, replay mode (offline fallback, labeled) | M3 | P1 |

### 4.2 Stretch (only after H12 gate)

- S1: Warehouse + Transport investigators (4 total)
- S2: Second live case archetype (payment hold)
- S3: Sound effects (3 preloaded mp3s, mute default ON)

### 4.3 Non-Goals — "What we're NOT doing" (Atlassian: prevents scope creep)

- ❌ No real enterprise APIs (Tally/GST/Delhivery) — deterministic fixtures, deliberate (§7)
- ❌ No auto-send to external customers — external actions are approval-gated
- ❌ No auth, multi-tenancy, mobile app, RAG/vector stores (no retrieval problem)
- ❌ No live demo of 6 cases — depth of ONE investigation beats a carousel
- ❌ No deployment — everything runs localhost
- ❌ No international trade complexity (customs, HS codes, DGFT) — domestic India only

---

## 5. Demo Narrative — 90 seconds

| Time | Beat | Room sees |
|------|------|-----------|
| 0:00 | Hook | "A textile shipment is stuck at Hubli for 6 days. Four systems, four answers. Which is true?" — Evidence Board: 4 pinned case photos, contradictory rubber stamps, red string tangled |
| 0:10 | Trigger | Presenter sends angry buyer email **from their phone**. ~10s later: Telegram alert buzzes on manager's phone (held to audience): "🚨 CUSTOMER ISSUE — Order #402 · Mumbai retailer cancelling, Monday market deadline · ₹4,200/day · [🔍 INVESTIGATE]" |
| 0:20 | Approve | Manager **taps INVESTIGATE on the phone** → board springs to life: "CASE #001 · INGESTED". 4 suspect notes pin in — router read them from `playbook.yaml` (shown briefly: "this file is the moat") |
| 0:30 | Investigation | Investigators fire in parallel. Evidence polaroids pin to board. Inventory string **snaps** — ruled out. `> gst: eway_bill=expired, gstr3b_pending → CULPRIT` |
| 0:45 | Collapse | Rubber stamps slam onto each portal photo: Tally **STALE** ("order status ≠ shipment status"), Transport **MISLEADING** ("breakdown is excuse"), GST **TRUE**, Delhivery **STALE** ("last scan 6 days old"). Four lies → one truth. |
| 0:55 | Challenge | Board dims red: Challenger **re-queries the DBs live**, attack scrawled across verdict in red handwriting ("transport breakdown happened first?"). Verdict survives → **"CLEARED"** stamp slams. Confidence locks **94%** (circled in red ink). |
| 1:05 | Approval | Wax-seal "AUTHORIZE FIX" pressed → e-way bill card **3D-flips** red EXPIRED → green RENEWAL REQUESTED. Telegram confirmation buzzes. Gmail draft shown. |
| 1:20 | Close | "Priya's shipment cleared at 16:12. She didn't call anyone. Stop tracking. Start investigating." |

**Judge Q&A weapons:** LangSmith trace URL · confidence formula · `playbook.yaml` · `graph.py` + fixture side-by-side.

---

## 6. Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-1 | Gmail poller (10s) detects trigger emails; intent classifier extracts order ID + intent + urgency + summary | P0 |
| FR-1b | Telegram alert with summary + INVESTIGATE inline button; callback poll (getUpdates, 2s) fires investigation | P0 |
| FR-1c | Console backup: pending case shows Investigate button; CLI `inject_email` fallback | P0 |
| FR-2 | Router loads hypotheses from playbook.yaml + LLM rationale | P0 |
| FR-3 | Investigators query own mock DB via tools only; return Evidence per contract | P0 |
| FR-4 | Synthesizer: early exit, deterministic confidence, portal stamps | P0 |
| FR-5 | Challenger: tool-equipped adversarial verification, max 1 re-open | P0 |
| FR-6 | HITL interrupt before execution; console approve/reject | P0 |
| FR-7 | Executor: renew e-way bill, verify by re-read, ExecutionResult | P0 |
| FR-8 | Actions: Telegram / Gmail draft / ETA recalc — graceful failure dicts | P0 |
| FR-9 | SSE stream, frozen payload schemas, <200ms | P0 |
| FR-10 | Console per TRD §8 (Evidence Board design system) | P0 |
| FR-11 | Replay mode: recorded event stream, labeled "REPLAY" | P1 |
| FR-12 | LangSmith tracing on all LLM calls; trace URL in console | P1 |
| FR-13 | Order-ID manual input in console (dev tool + demo backup) | P1 |
| FR-14 | Audit trail persisted per case | P1 |

---

## 7. Honesty Contract (judge armor)

**"Stub the backend, never the detective."** The four enterprise systems (Tally, GST portal, Delhivery, Transport) are deterministic SQLite fixtures — stated openly. Everything being judged — routing, tool calls, parallel dispatch, challenge, confidence math, execution — runs 100% live. Rehearsed: open `graph.py` + fixture side-by-side in <10s. LangSmith trace as receipts.

---

## 8. Success Metrics (tied to judging rubric)

| Metric | Target | Rubric line |
|--------|--------|-------------|
| Full loop (email → approval → fix verified) live | <90s, 3 consecutive clean runs | Functionality (15 pts) |
| "Where does 94% come from?" | Formula shown in <30s | Technical Implementation (15 pts) |
| "Is it real?" | Code + trace walkthrough in <10s | Technical Implementation |
| Failure drills (Wi-Fi off, Telegram down, Gmail dead) | Each recovers <15s | Functionality |
| Judge repeats one-sentence identity unprompted | Yes | Creativity (20 pts — top weighted) |
| Evidence Board visual impact | Judge retells string-snap or hold-flip to another judge | Creativity + UX (10 pts) |

---

## 9. Risks & Mitigations

| Risk | L | Mitigation |
|------|---|-----------|
| 4-member coordination | High | contracts.py + SSE payloads frozen H1; strict module ownership |
| LLM latency 20–40s | Med | Streaming typewriter + progressive disclosure; honest wall-clock badge; never fake speed |
| Venue Wi-Fi | High | Hotspot pre-configured; replay mode as last resort (labeled) |
| Telegram button failure | Med | Console backup Investigate button on pending case — drill at H20 |
| Gmail OAuth/setup issues | Med | CLI `inject_email` injects identical payload (dev tool, invisible to judges); OAuth validated at H2 |
| Scope creep | High (self) | Hard rule: #402 live only until H18; sound + extra investigators are stretch |
| Checkpoint time loss (agenda: 4 checkpoints) | Med | 30-min prep per checkpoint, rehearsed 2-min updates |

---

## 10. Team & 24-Hour Plan (4 members)

**H0–H1 contract freeze (everyone):** contracts.py + SSE payloads + playbook.yaml skeleton + fixture ground truth. Nothing proceeds until merged.

| Hours | Aflal — Brain | Shabil — Data & Actions | M3 — Server | M4 — Console |
|-------|---------------|------------------------|-------------|--------------|
| 1–4 | Graph skeleton, router, playbook loader | 4 mock DBs + seed + fixtures; **Gmail OAuth setup + validation (H2 checkpoint)** | FastAPI shell, SSE, static serving | Console shell, portal cards, dark theme |
| 4–8 | GST + Inventory investigators, synthesizer, early exit | **Gmail poller + intent classifier + Telegram bot (alert + INVESTIGATE button + callback poll)** | Trigger endpoints (email + console backup), case board API | SSE rendering, investigator cards, trace |
| 8–12 | Challenger (tools) + confidence formula + interrupt gate | Executor (e-way bill renewal + verify), Gmail drafter | Full pipeline wiring, replay mode | Verdict, challenge, collapse stamps, approval button |
| **H12** | **GATE: full loop via email → Telegram button → investigation → approval → fix. MVP FROZEN. 3 clean runs.** | | | |
| 12–16 | Stretch: 2 more investigators OR polish | Audit trail, cost counter | LangSmith link, failure-drill infra | Motion polish, case board UI |
| 16–20 | Buffer / sleep rotation (2+2) | | | |
| 20–24 | Rehearsal ×5 + failure drills + code-walkthrough prep | Demo script timing | Hotspot/offline drill | UI freeze |

---

## 11. FAQ (DISQO pattern — pre-answered judge questions)

**Q: Why not just use a chatbot?**
A: `playbook.yaml` — declarative investigation logic (which evidence eliminates which hypothesis). Six case types, one brain, inspectable artifact. Chatbots don't have encoded domain logic.

**Q: How is this different from Sentinel Flow / other supply chain agents?**
A: They detect and notify. Orbit investigates across contradictory systems, then survives adversarial cross-examination before acting. Watch the Challenger re-query the database live.

**Q: Is the AI real or pre-recorded?**
A: 100% live. Open `graph.py` + one fixture side-by-side. LangSmith trace URL shows real LLM calls. "Stub the backend, never the detective."

**Q: Where does 94% confidence come from?**
A: Deterministic formula: 0.50×culprit evidence + 0.30×hypotheses eliminated + 0.20×portals resolved + 0.06 challenge bonus. Not hallucinated by the LLM.

**Q: What breaks first?**
A: Telegram button callback (2-5s latency, venue Wi-Fi dependent). Mitigation: console backup button, rehearsed failover drill.

**Q: Why domestic India, not international?**
A: Judge relatability. Every Indian judge knows GST, e-way bills, state border checkpoints. "Monday market deadline" is visceral. International trade (customs, HS codes) adds explanation overhead with zero demo benefit.

---

## 12. Deliverables (per Code Kudla agenda)

| Deliverable | Due | Owner |
|-------------|-----|-------|
| GitHub repo (public, with README) | 11:00 AM Day 2 freeze | M3 |
| Backup video (90s, OBS Studio, 1080p) | 11:00 AM Day 2 freeze | M4 |
| Slides (3–5 max, reused from deck) | 11:00 AM Day 2 freeze | Shabil |
| Live demo (5 min + 3 min Q&A) | 11:15 AM Day 2 | All |
