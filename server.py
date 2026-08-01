"""ORBIT FastAPI server — SSE streaming, triggers, approval, replay mode.

TRD §7 API surface. Serves the Evidence Board console (static/), triggers
investigations, streams SSE events, handles interrupt() approval, and
provides replay mode for offline demos.

FIXES applied:
- SSE sentinel (None) now sent after case_closed in ALL paths (not just resume)
- Removed useless _streams.setdefault line (race condition)
- SSE queue correctly handles approval_required (keeps stream open for resume)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from uuid import uuid4

from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from contracts import CasePayload
from enterprise import query as eq
from enterprise.seed import rebuild as rebuild_dbs
from graph import investigate
from ingest.pending import create_pending_case, get_pending_case

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("orbit.server")

app = FastAPI(title="ORBIT — AI Detective for Stuck Operations")

STATIC_DIR = Path(__file__).resolve().parent / "static"
REPLAY_DIR = Path(__file__).resolve().parent / "replay"
REPLAY_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# In-memory case & stream tracking
# ---------------------------------------------------------------------------

_cases: dict[str, dict] = {}
_streams: dict[str, asyncio.Queue] = {}

def _register_case(case: CasePayload, status: str = "active") -> dict:
    info = {
        "case_id": case.case_id,
        "order_id": case.order_id,
        "symptom": case.symptom,
        "source": case.source,
        "status": status,
        "started_at": time.time(),
        "events": [],
    }
    _cases[case.case_id] = info
    return info

# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------

class InvestigateRequest(BaseModel):
    order_id: str | None = None
    case_type: str | None = None
    symptom: str | None = None

class ApproveRequest(BaseModel):
    approved: bool

# ---------------------------------------------------------------------------
# POST /api/investigate — manual/console trigger
# ---------------------------------------------------------------------------

@app.post("/api/investigate")
async def api_investigate(req: InvestigateRequest):
    # Case-type-based injection
    from ingest.inject_email import CASE_SYMPTOMS, CASE_ORDERS
    if req.case_type and req.case_type in CASE_SYMPTOMS:
        order_id = req.order_id or CASE_ORDERS.get(req.case_type, "402")
        symptom = req.symptom or CASE_SYMPTOMS[req.case_type]
    else:
        order_id = req.order_id or "501"
        symptom = req.symptom or "payment held by bank"
    
    case_id = f"manual-{order_id}-{uuid4().hex[:8]}"
    case = CasePayload(
        case_id=case_id,
        order_id=order_id,
        symptom=symptom,
        source="manual",
    )
    _register_case(case)
    asyncio.create_task(_run_investigation(case))
    return {"case_id": case_id}

# ---------------------------------------------------------------------------
# POST /api/investigate/{case_id} — fire investigation for a pending case
# ---------------------------------------------------------------------------

@app.post("/api/investigate/{case_id}")
async def api_investigate_pending(case_id: str):
    if case_id in _cases and _cases[case_id]["status"] == "active":
        raise HTTPException(400, "investigation already running")

    pending = get_pending_case(case_id)
    if not pending:
        raise HTTPException(404, f"case {case_id} not found")

    case = CasePayload(
        case_id=case_id,
        order_id=pending["order_id"],
        symptom="operations issue reported",
        source="cli" if case_id.startswith("cli-") else "email",
    )
    _register_case(case)
    asyncio.create_task(_run_investigation(case))
    return {"case_id": case_id, "status": "started"}

# ---------------------------------------------------------------------------
# GET /api/stream/{case_id} — SSE event stream
# FIX: removed useless setdefault; single queue per active client
# ---------------------------------------------------------------------------

@app.get("/api/stream/{case_id}")
async def api_stream(case_id: str):
    queue: asyncio.Queue = asyncio.Queue()
    # FIX: direct assignment (no setdefault race)
    _streams[case_id] = queue

    # Replay already-seen events for this case (browser reconnect safety)
    if case_id in _cases:
        for ev in _cases[case_id].get("events", []):
            await queue.put(ev)
        # If case is already closed, immediately signal end
        if _cases[case_id].get("status") == "closed":
            await queue.put(None)

    async def event_generator():
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                if event is None:  # sentinel — stream done
                    return
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                yield f": keepalive\n\n"
            except asyncio.CancelledError:
                return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

class SendDraftRequest(BaseModel):
    draft_id: str

@app.post("/api/send_draft")
async def api_send_draft(req: SendDraftRequest):
    from actions.telegram_bot import send_gmail_draft
    result = await send_gmail_draft(req.draft_id)
    if result.status == "sent":
        return {"status": "sent", "ref": result.ref}
    else:
        raise HTTPException(400, f"Send email draft failed: {result.error}")

# ---------------------------------------------------------------------------
# POST /api/approve/{case_id} — resume the interrupt
# ---------------------------------------------------------------------------

@app.post("/api/approve/{case_id}")
async def api_approve(case_id: str, req: ApproveRequest):
    if case_id not in _cases:
        raise HTTPException(404, f"case {case_id} not found")

    case = CasePayload(
        case_id=case_id,
        order_id=_cases[case_id]["order_id"],
        symptom=_cases[case_id]["symptom"],
        source=_cases[case_id]["source"],
    )
    asyncio.create_task(_run_investigation(case, resume={"approved": req.approved}))
    return {"case_id": case_id, "approved": req.approved}

# ---------------------------------------------------------------------------
# GET /api/cases — list all cases (live + pending + fixtures)
# ---------------------------------------------------------------------------

@app.get("/api/cases")
async def api_cases():
    cases = []

    # Fixture cases (closed)
    for fc in eq.list_closed_cases():
        cases.append({
            "case_id": fc["case_id"],
            "order_id": fc["order_id"],
            "case_type": fc.get("case_type"),
            "root_cause": fc.get("root_cause"),
            "confidence": fc.get("confidence"),
            "status": fc.get("status", "closed"),
            "created_at": fc.get("created_at"),
            "verdict_summary": fc.get("verdict_summary"),
        })

    # In-memory active/closed cases
    for info in _cases.values():
        cases.append({
            "case_id": info["case_id"],
            "order_id": info["order_id"],
            "status": info["status"],
            "source": info.get("source"),
        })

    # Pending cases from DB
    try:
        import sqlite3
        from enterprise.seed import DB_DIR
        conn = sqlite3.connect(DB_DIR / "cases.db")
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM cases WHERE status = 'pending' ORDER BY created_at DESC"
        ).fetchall()
        conn.close()
        for row in rows:
            row_dict = dict(row)
            if row_dict["case_id"] not in _cases:
                cases.append({
                    "case_id": row_dict["case_id"],
                    "order_id": row_dict["order_id"],
                    "status": "pending",
                    "created_at": row_dict.get("created_at"),
                })
    except Exception:
        pass

    return {"cases": cases}

@app.get("/api/cases/{case_id}")
async def api_case_detail(case_id: str):
    if case_id in _cases:
        return _cases[case_id]
    for fc in eq.list_closed_cases():
        if fc["case_id"] == case_id:
            return fc
    raise HTTPException(404, f"case {case_id} not found")

# ---------------------------------------------------------------------------
# GET /api/replay/{case_id} — recorded event stream (offline fallback)
# ---------------------------------------------------------------------------

@app.get("/api/replay/{case_id}")
async def api_replay(case_id: str):
    replay_file = REPLAY_DIR / f"{case_id}.json"
    if not replay_file.exists():
        raise HTTPException(404, f"no replay for {case_id}")

    events = json.loads(replay_file.read_text(encoding="utf-8"))

    async def replay_generator():
        for ev in events:
            ev["replay"] = True
            yield f"data: {json.dumps(ev)}\n\n"
            await asyncio.sleep(0.3)

    return StreamingResponse(
        replay_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

# ---------------------------------------------------------------------------
# Static serving
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>ORBIT — Evidence Board loading...</h1>")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---------------------------------------------------------------------------
# Investigation runner — FIX: sentinel now sent after case_closed in ALL paths
# ---------------------------------------------------------------------------

async def _run_investigation(case: CasePayload, resume: dict | None = None):
    """Run the graph and push each SSE event to connected stream clients."""
    case_id = case.case_id
    events: list[dict] = []
    last_event_name: str | None = None

    try:
        async for ev in investigate(case, resume=resume):
            events.append(ev)
            last_event_name = ev.get("event")
            if case_id in _cases:
                _cases[case_id]["events"].append(ev)
            q = _streams.get(case_id)
            if q:
                await q.put(ev)
            if case_id in _cases:
                if last_event_name == "case_closed":
                    _cases[case_id]["status"] = "closed"
                elif last_event_name == "approval_required":
                    _cases[case_id]["status"] = "awaiting_approval"
    except Exception as exc:
        error_ev = {"event": "error", "where": "server", "message": str(exc), "degraded": True}
        events.append(error_ev)
        last_event_name = "error"
        q = _streams.get(case_id)
        if q:
            await q.put(error_ev)
        log.error(f"Investigation {case_id} failed: {exc}")
    finally:
        q = _streams.get(case_id)
        if q and last_event_name != "approval_required":
            await q.put(None)

    # Save replay file (merge with existing on resume)
    try:
        replay_file = REPLAY_DIR / f"{case_id}.json"
        if resume and replay_file.exists():
            existing = json.loads(replay_file.read_text(encoding="utf-8"))
            events = existing + events
        replay_file.write_text(json.dumps(events, default=str), encoding="utf-8")
    except Exception as exc:
        log.warning(f"Failed to save replay for {case_id}: {exc}")

# ---------------------------------------------------------------------------
# Startup — background tasks
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    rebuild_dbs()
    log.info("Enterprise DBs rebuilt")
    asyncio.create_task(_start_gmail_poller())
    asyncio.create_task(_start_telegram_poller())
    log.info("ORBIT server started — http://localhost:8000")

async def _start_gmail_poller():
    """Background: poll Gmail for trigger emails -> create pending cases + alert."""
    try:
        from actions.telegram_bot import send_initial_alert
        from ingest.gmail_poller import poll_inbox
        from ingest.pending import create_pending_case

        async def on_email(case: CasePayload):
            result = create_pending_case(case)
            if isinstance(result, str):
                log.info(f"Pending case created from email: {result}")
                await send_initial_alert(case)
            else:
                log.warning(f"Pending case failed: {result}")

        await poll_inbox(on_email, interval=10)
    except Exception as exc:
        log.warning(f"Gmail poller disabled: {exc}")

async def _start_telegram_poller():
    """Background: poll Telegram for button presses -> trigger/resume investigations."""
    try:
        from actions.telegram_bot import poll_callbacks

        async def on_callback(case_id: str, is_approval: bool = False, approved: bool = False):
            if is_approval:
                # Approval/rejection from Telegram verdict alert
                if case_id in _cases:
                    _cases[case_id]["status"] = "active"
                    order_id = _cases[case_id]["order_id"]
                    symptom = _cases[case_id]["symptom"]
                    source = _cases[case_id]["source"]
                else:
                    pending = get_pending_case(case_id)
                    order_id = pending["order_id"] if pending else "unknown"
                    symptom = pending.get("symptom", "operations issue") if pending else "operations issue"
                    source = "email"
                case = CasePayload(
                    case_id=case_id,
                    order_id=order_id,
                    symptom=symptom,
                    source=source,
                )
                await _run_investigation(case, resume={"approved": approved})
            else:
                # INVESTIGATE button pressed — use real symptom from pending case
                pending = get_pending_case(case_id)
                if pending:
                    order_id = pending["order_id"]
                    # Get the real symptom from the case_id or order mapping
                    from ingest.inject_email import CASE_SYMPTOMS, CASE_ORDERS
                    symptom = pending.get("symptom")
                    if not symptom:
                        # Reverse lookup: order_id -> case_type -> symptom
                        for ct, oid in CASE_ORDERS.items():
                            if str(oid) == str(order_id):
                                symptom = CASE_SYMPTOMS.get(ct, "operations issue reported")
                                break
                        else:
                            symptom = "operations issue reported"
                    case = CasePayload(
                        case_id=case_id,
                        order_id=order_id,
                        symptom=symptom,
                        source="email",
                    )
                    _register_case(case)
                    await _run_investigation(case)
                elif case_id in _cases:
                    case = CasePayload(
                        case_id=case_id,
                        order_id=_cases[case_id]["order_id"],
                        symptom=_cases[case_id]["symptom"],
                        source=_cases[case_id]["source"],
                    )
                    await _run_investigation(case)
                else:
                    log.warning(f"Telegram investigate: case {case_id} not found")

        await poll_callbacks(on_callback, interval=2)
    except Exception as exc:
        log.warning(f"Telegram poller disabled: {exc}")

