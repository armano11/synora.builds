/* ORBIT app.js — v2 (Vercel/Linear aesthetic rewrite)
 *
 * Senior-level SSE architecture:
 * - Exponential backoff reconnect (no hanging)
 * - Stale-event timeout detection (30s badge)
 * - Clean state machine: IDLE → INGESTING → INVESTIGATING → CHALLENGING → AWAITING → EXECUTING → CLOSED
 * - SVG strings drawn between portal cards and hypothesis chips
 * - No AI slop: zero gratuitous decoration
 */

// ─── State ────────────────────────────────────────────────────────────
let state = {
  phase: 'IDLE',
  activeCaseId: null,
  eventSource: null,
  reconnectTimer: null,
  reconnectAttempts: 0,
  MAX_RECONNECT: 6,
  staleTimer: null,
  STALE_TIMEOUT_MS: 35000,
  timerInterval: null,
  startTime: null,
  pendingCaseId: null,
  stringMap: {},          // hypoId → SVGPathElement
  anchorMap: {},          // portalId → {x, y} and hypoId → {x, y}
  hypotheses: [],
};

// ─── Investigator → portal mapping ────────────────────────────────────
const INV_PORTAL = {
  gst: 'portal-gst', query_gst: 'portal-gst',
  inventory: 'portal-tally', query_inventory: 'portal-tally', query_tally: 'portal-tally',
  warehouse: 'portal-tally',
  transport: 'portal-transport', query_transport: 'portal-transport',
  delhivery: 'portal-delhivery', query_delhivery: 'portal-delhivery',
};
const HYPO_PORTAL = {
  h_eway_bill_expired: 'portal-gst',
  h_inventory_damage: 'portal-tally',
  h_dispatch_failure: 'portal-tally',
  h_transport_breakdown: 'portal-transport',
};

// ─── Boot ──────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', boot);

function boot() {
  // Buttons
  document.getElementById('investigate-btn').addEventListener('click', triggerManual);
  document.getElementById('order-id-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') triggerManual();
  });
  document.getElementById('refresh-cases-btn').addEventListener('click', loadCases);
  document.getElementById('approve-btn').addEventListener('click', () => sendApproval(true));
  document.getElementById('reject-btn').addEventListener('click', () => sendApproval(false));
  document.getElementById('clear-log-btn').addEventListener('click', clearLog);

  // Pending banner
  document.getElementById('pending-investigate-btn').addEventListener('click', () => {
    if (state.pendingCaseId) triggerPending(state.pendingCaseId);
  });

  // String redraw on resize
  window.addEventListener('resize', debounce(redrawStrings, 200));

  loadCases();
  pollForPendingCases();
}

// ─── Debounce ──────────────────────────────────────────────────────────
function debounce(fn, ms) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// ─── Phase machine ─────────────────────────────────────────────────────
const PHASE_CLASSES = {
  'IDLE':        'phase-idle',
  'INGESTING':   'phase-ingesting',
  'INVESTIGATING':'phase-investigating',
  'CHALLENGING': 'phase-challenging',
  'AWAITING APPROVAL': 'phase-approval',
  'EXECUTING':   'phase-executing',
  'CLOSED':      'phase-closed',
  'ERROR':       'phase-error',
};
const PHASE_LABELS = {
  'IDLE':'IDLE','INGESTING':'INGESTING','INVESTIGATING':'INVESTIGATING',
  'CHALLENGING':'CHALLENGING','AWAITING APPROVAL':'AWAITING','EXECUTING':'EXECUTING',
  'CLOSED':'CLOSED','ERROR':'ERROR',
};
function setPhase(p) {
  state.phase = p;
  const pill = document.getElementById('phase-pill');
  const txt  = document.getElementById('phase-text');
  pill.className = 'phase-pill ' + (PHASE_CLASSES[p] || 'phase-idle');
  txt.textContent = PHASE_LABELS[p] || p;
}

const EV_PHASE = {
  case_ingested:'INGESTING', hypotheses_ready:'INVESTIGATING',
  investigator_start:'INVESTIGATING', evidence_found:'INVESTIGATING',
  hypothesis_ruled_out:'INVESTIGATING', portal_stamped:'INVESTIGATING',
  verdict_draft:'INVESTIGATING', challenge_start:'CHALLENGING',
  challenge_result:'CHALLENGING', approval_required:'AWAITING APPROVAL',
  verdict_locked:'EXECUTING', execution_done:'EXECUTING',
  action_done:'EXECUTING', case_closed:'CLOSED',
};

// ─── SSE connection with exponential backoff ───────────────────────────
function connectSSE(caseId, isReplay = false) {
  if (state.eventSource) { state.eventSource.close(); state.eventSource = null; }
  clearTimeout(state.reconnectTimer);

  const url = isReplay ? `/api/replay/${caseId}` : `/api/stream/${caseId}`;
  const es = new EventSource(url);
  state.eventSource = es;

  setSseStatus('streaming', caseId ? 'streaming' : 'connecting');

  es.onopen = () => {
    state.reconnectAttempts = 0;
    setSseStatus('streaming', 'live');
    resetStaleTimer();
  };

  es.onmessage = (e) => {
    try {
      const payload = JSON.parse(e.data);
      if (payload.replay) document.getElementById('replay-badge').classList.remove('hidden');
      handleEvent(payload);
      resetStaleTimer();
    } catch {}
  };

  es.onerror = () => {
    setSseStatus('error', 'disconnected');
    es.close();
    state.eventSource = null;
    if (state.phase === 'CLOSED') return; // normal — stream ended
    scheduleReconnect(caseId, isReplay);
  };
}

function scheduleReconnect(caseId, isReplay) {
  if (state.reconnectAttempts >= state.MAX_RECONNECT) {
    setSseStatus('error', 'gave up');
    toast('SSE connection failed. Use REPLAY to review.', 'error');
    return;
  }
  const backoff = Math.min(1000 * Math.pow(1.8, state.reconnectAttempts), 15000);
  state.reconnectAttempts++;
  setSseStatus('reconnecting', `retry ${state.reconnectAttempts}`);
  state.reconnectTimer = setTimeout(() => connectSSE(caseId, isReplay), backoff);
}

function resetStaleTimer() {
  clearTimeout(state.staleTimer);
  if (state.phase === 'CLOSED' || state.phase === 'IDLE') return;
  state.staleTimer = setTimeout(() => {
    if (state.phase !== 'CLOSED' && state.phase !== 'IDLE') {
      setSseStatus('error', 'stale');
      toast('No events for 30s — investigation may be stalled.', 'warn');
    }
  }, state.STALE_TIMEOUT_MS);
}

function setSseStatus(status, label) {
  const dot  = document.getElementById('sse-dot');
  const lbl  = document.getElementById('sse-label');
  dot.className = `w-1.5 h-1.5 rounded-full ${status}`;
  lbl.textContent = label;
  lbl.className = `font-mono text-[10px] ${
    status === 'streaming' ? 'text-[#f5a623]' :
    status === 'connected' ? 'text-green-400' :
    status === 'error'     ? 'text-red-400/70' :
    status === 'reconnecting' ? 'text-amber-400' :
    'text-white/30'
  }`;
}

// ─── Investigation triggers ────────────────────────────────────────────
async function triggerManual() {
  const orderId = document.getElementById('order-id-input').value.trim();
  if (!orderId) { document.getElementById('order-id-input').focus(); return; }
  try {
    const r = await fetch('/api/investigate', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({order_id: orderId}),
    });
    const d = await r.json();
    if (d.case_id) { resetBoard(); startCase(d.case_id); }
    else toast('Could not start investigation.', 'error');
  } catch (e) { toast('Server unreachable.', 'error'); }
}

async function triggerPending(caseId) {
  try {
    const r = await fetch(`/api/investigate/${caseId}`, {method:'POST'});
    const d = await r.json();
    if (d.case_id) { resetBoard(); startCase(d.case_id); hidePendingBanner(); }
  } catch { toast('Failed to start investigation.', 'error'); }
}

function startCase(caseId) {
  state.activeCaseId = caseId;
  state.startTime = Date.now();
  clearInterval(state.timerInterval);
  state.timerInterval = setInterval(tickClock, 50);
  setPhase('INGESTING');
  connectSSE(caseId);
  document.getElementById('replay-badge').classList.add('hidden');
}

// ─── Event router ──────────────────────────────────────────────────────
function handleEvent(p) {
  const ev = p.event;
  if (EV_PHASE[ev]) setPhase(EV_PHASE[ev]);
  if (p.wall_clock_s) document.getElementById('wall-clock').textContent = `${p.wall_clock_s.toFixed(2)}s`;

  switch(ev) {
    case 'case_ingested':       onCaseIngested(p); break;
    case 'hypotheses_ready':    onHypothesesReady(p); break;
    case 'investigator_start':  onInvestigatorStart(p); break;
    case 'evidence_found':      onEvidenceFound(p); break;
    case 'hypothesis_ruled_out':onHypothesisRuledOut(p); break;
    case 'portal_stamped':      onPortalStamped(p); break;
    case 'verdict_draft':       addTrace('synth', 'drafting verdict…', 'tag-synth'); break;
    case 'challenge_start':     onChallengeStart(p); break;
    case 'challenge_result':    onChallengeResult(p); break;
    case 'approval_required':   onApprovalRequired(p); break;
    case 'verdict_locked':      onVerdictLocked(p); break;
    case 'execution_done':      onExecutionDone(p); break;
    case 'action_done':         onActionDone(p); break;
    case 'case_closed':         onCaseClosed(p); break;
    case 'error':
      addTrace('error', `[${p.where}] ${p.message}`, 'tag-error');
      if (p.degraded) toast(`Degraded: ${p.message.substring(0,60)}`, 'warn');
      break;
  }
}

// ─── Event handlers ────────────────────────────────────────────────────
function onCaseIngested(p) {
  document.querySelectorAll('.order-id-label').forEach(el => el.textContent = `Order #${p.order_id}`);
  addTrace('orbit', `case ingested: #${p.order_id} · ${p.symptom}`, 'tag-synth');
}

function onHypothesesReady(p) {
  state.hypotheses = p.hypotheses;
  const row = document.getElementById('hypotheses-row');
  row.innerHTML = '';
  p.hypotheses.forEach(h => {
    const chip = document.createElement('div');
    chip.id = `hypo-${h.id}`;
    chip.className = 'hypo-chip';
    chip.innerHTML = `<span class="hypo-dot"></span><span>${h.label}</span>`;
    chip.title = h.rationale;
    row.appendChild(chip);
  });
  addTrace('router', `${p.hypotheses.length} hypotheses formulated`, 'tag-router');
  setTimeout(drawStrings, 300);
}

function onInvestigatorStart(p) {
  // Highlight portal
  const portalId = INV_PORTAL[p.investigator];
  if (portalId) {
    const el = document.getElementById(portalId);
    if (el) { el.classList.add('ring-1','ring-blue-500/40'); setTimeout(() => el.classList.remove('ring-1','ring-blue-500/40'), 2500); }
  }
  // Highlight hypothesis
  const hypoEl = document.getElementById(`hypo-${p.hypothesis_id}`);
  if (hypoEl) hypoEl.classList.add('investigating');
  addTrace(p.investigator, `checking ${p.hypothesis_id.replace('h_','').replace(/_/g,' ')}`, 'tag-invest');
}

function onEvidenceFound(p) {
  const ev = p.evidence;
  // Add to evidence list
  const list = document.getElementById('evidence-list');
  const blankMsg = list.querySelector('.font-mono.italic');
  if (blankMsg) blankMsg.remove();

  const item = document.createElement('div');
  const isCulprit = ev.found && ev.supports && ev.supports.length > 0;
  item.className = `evidence-item${isCulprit ? ' culprit' : ''}`;
  item.innerHTML = `
    <span class="ev-source">${ev.source.replace('query_','')}</span>
    <span class="ev-detail">${ev.detail}</span>
  `;
  list.appendChild(item);

  // Mark culprit portal
  if (isCulprit) {
    const portalId = INV_PORTAL[p.investigator] || INV_PORTAL[ev.source];
    if (portalId) document.getElementById(portalId)?.classList.add('active-culprit');
    document.getElementById(`hypo-${ev.supports[0]}`)?.classList.replace('investigating','culprit');
  }

  addTrace(p.investigator || ev.source, ev.detail, 'tag-invest');
}

function onHypothesisRuledOut(p) {
  const chip = document.getElementById(`hypo-${p.hypothesis_id}`);
  if (chip) chip.className = 'hypo-chip ruled-out';

  // Snap string: fade it, make it dashed
  const path = state.stringMap[p.hypothesis_id];
  if (path) {
    path.classList.add('ruled-out-string');
    path.style.opacity = '0.25';
    path.style.transition = 'opacity 0.5s ease';
  }

  // Add to ruled-out list
  const list = document.getElementById('ruled-out-list');
  const blankMsg = list.querySelector('.font-mono.italic');
  if (blankMsg) blankMsg.remove();
  const item = document.createElement('div');
  item.className = 'ruled-item';
  item.textContent = p.hypothesis_id.replace('h_','').replace(/_/g,' ');
  list.appendChild(item);

  addTrace('synth', `${p.hypothesis_id.replace('h_','')} → eliminated`, 'tag-synth');
}

function onPortalStamped(p) {
  const stampsMap = { tally:'stamp-tally', gst:'stamp-gst', delhivery:'stamp-delhivery', transport:'stamp-transport' };
  const stampEl = document.getElementById(stampsMap[p.portal]);
  const portalEl = document.getElementById(`portal-${p.portal}`);
  if (!stampEl || !portalEl) return;

  stampEl.className = `stamp-badge stamp-${p.stamp.verdict}`;
  stampEl.textContent = p.stamp.verdict;
  stampEl.title = p.stamp.reason;
  stampEl.classList.remove('hidden');

  // Portal card state
  portalEl.classList.remove('stamped-stale','stamped-misleading','stamped-true');
  portalEl.classList.add(`stamped-${p.stamp.verdict.toLowerCase()}`);

  // E-way flip
  if (p.portal === 'gst' && p.stamp.verdict === 'TRUE') {
    const eway = document.getElementById('gst-eway');
    if (eway) { eway.className = 'portal-status text-red-400'; eway.textContent = 'E-Way EXPIRED ← culprit'; }
  }

  addTrace('synth', `${p.portal} → ${p.stamp.verdict}: ${p.stamp.reason}`, 'tag-synth');
}

function onChallengeStart(p) {
  const modal = document.getElementById('challenger-modal');
  document.getElementById('challenger-text').textContent = p.attack_preview;
  document.getElementById('challenger-evidence').innerHTML = '';
  modal.classList.remove('hidden');
  addTrace('challenger', p.attack_preview, 'tag-challenge');
}

function onChallengeResult(p) {
  const modal = document.getElementById('challenger-modal');
  const evDiv = document.getElementById('challenger-evidence');
  if (p.evidence_checked?.length) {
    evDiv.innerHTML = `<div class="text-white/30 mb-1">Queried: ${p.evidence_checked.join(', ')}</div>`;
  }
  setTimeout(() => modal.classList.add('hidden'), p.survived ? 1600 : 2400);
  addTrace('challenger', `survived=${p.survived} · delta=${p.confidence_delta}`, 'tag-challenge');
  if (!p.survived) toast('Verdict challenged — re-investigating', 'warn');
}

function onApprovalRequired(p) {
  document.getElementById('approval-gate').classList.remove('hidden');
  document.getElementById('approval-action-text').textContent = p.proposed_action;
  addTrace('gate', 'awaiting human authorization', 'tag-synth');
  toast('Authorization required — check the sidebar.', 'warn');
}

function onVerdictLocked(p) {
  const v = p.verdict;
  document.getElementById('approval-gate').classList.add('hidden');

  // Root cause
  const rcEl = document.getElementById('verdict-root-cause');
  rcEl.textContent = v.root_cause;
  rcEl.className = 'font-mono text-sm text-[#f5a623] leading-snug mt-1.5 font-medium';

  // Confidence
  document.getElementById('confidence-pct').textContent = `${(v.confidence * 100).toFixed(0)}%`;
  document.getElementById('confidence-pct').className = 'font-mono text-xs font-semibold text-[#f5a623]';
  const bar = document.getElementById('confidence-bar');
  bar.style.width = `${(v.confidence * 100).toFixed(0)}%`;
  if (v.confidence >= 0.9) bar.classList.replace('bg-[#f5a623]', 'bg-green-400');

  addTrace('verdict', `${v.root_cause} · ${(v.confidence*100).toFixed(0)}%`, 'tag-synth');
}

function onExecutionDone(p) {
  const exec = p.execution;
  const sec = document.getElementById('execution-section');
  const detail = document.getElementById('execution-detail');
  sec.classList.remove('hidden');
  detail.innerHTML = `
    <div><span class="text-white/40">action:</span> ${exec.action}</div>
    <div><span class="text-white/40">verified:</span> ${exec.verified ? '✓ yes' : '✗ no'}</div>
    <div><span class="text-white/40">change:</span> ${JSON.stringify(exec.before)} → ${JSON.stringify(exec.after)}</div>
  `;

  // Flip GST card
  if (exec.action?.includes('eway')) {
    const eway = document.getElementById('gst-eway');
    if (eway) {
      eway.className = 'portal-status text-green-400';
      eway.textContent = 'E-Way RENEWED ✓';
    }
    const card = document.getElementById('portal-gst');
    if (card) {
      card.classList.remove('active-culprit','stamped-misleading','stamped-stale');
      card.classList.add('stamped-true');
    }
  }
  addTrace('executor', `${exec.action} · verified=${exec.verified}`, 'tag-execute');
}

function onActionDone(p) {
  const act = p.action;
  const list = document.getElementById('actions-list');
  const blank = list.querySelector('.font-mono.italic');
  if (blank) blank.remove();

  const icons = { telegram:'📱', gmail_draft:'✉️', eta_recalc:'📅' };
  const chip = document.createElement('div');
  chip.className = `action-chip ${act.status}`;
  chip.innerHTML = `
    <span>${icons[act.type] || '·'}</span>
    <span class="font-mono text-[10px]">${act.type}</span>
    <span class="font-mono text-[9px] opacity-70">${act.status}</span>
    ${act.error ? `<span class="text-red-400/60 text-[9px] truncate max-w-[100px]">${act.error}</span>` : ''}
  `;
  list.appendChild(chip);

  if (act.status === 'sent') toast(`${icons[act.type]} ${act.type.replace('_',' ')} sent`, 'success');
  addTrace('action', `${act.type} → ${act.status}`, 'tag-action');
}

function onCaseClosed(p) {
  clearInterval(state.timerInterval);
  document.getElementById('wall-clock').textContent = `${p.wall_clock_s.toFixed(2)}s`;
  setSseStatus('connected', 'closed');
  addTrace('orbit', `case closed · ${p.wall_clock_s.toFixed(2)}s`, 'tag-execute');
  toast('Investigation complete.', 'success');
  loadCases();
}

// ─── Approval ──────────────────────────────────────────────────────────
async function sendApproval(approved) {
  document.getElementById('approval-gate').classList.add('hidden');
  try {
    await fetch(`/api/approve/${state.activeCaseId}`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({approved}),
    });
    addTrace('gate', `decision: ${approved ? 'authorized' : 'rejected'}`, 'tag-synth');
  } catch { toast('Failed to send decision.', 'error'); }
}

// ─── SVG strings ───────────────────────────────────────────────────────
function drawStrings() {
  const svg = document.getElementById('string-svg');
  svg.innerHTML = '';
  state.stringMap = {};

  const svgRect = svg.getBoundingClientRect();

  state.hypotheses.forEach(h => {
    const portalId = HYPO_PORTAL[h.id];
    const portalEl = document.getElementById(portalId);
    const hypoEl   = document.getElementById(`hypo-${h.id}`);
    if (!portalEl || !hypoEl) return;

    const pRect = portalEl.getBoundingClientRect();
    const hRect = hypoEl.getBoundingClientRect();

    // Portal anchor: bottom-center
    const x1 = pRect.left + pRect.width / 2 - svgRect.left;
    const y1 = pRect.bottom - svgRect.top;

    // Hypo anchor: top-center
    const x2 = hRect.left + hRect.width / 2 - svgRect.left;
    const y2 = hRect.top - svgRect.top;

    // Bezier control point for gentle arc
    const cx = (x1 + x2) / 2;
    const cy = Math.min(y1, y2) - 18;

    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', `M ${x1} ${y1} Q ${cx} ${cy} ${x2} ${y2}`);
    path.className.baseVal = 'investigation-string';

    svg.appendChild(path);
    state.stringMap[h.id] = path;
  });
}

function redrawStrings() {
  if (state.hypotheses.length > 0) drawStrings();
}

// ─── Trace log ─────────────────────────────────────────────────────────
const TAG_MAP = {
  router:'tag-router', orbit:'tag-router', synth:'tag-synth', challenger:'tag-challenge',
  gate:'tag-synth', executor:'tag-execute', action:'tag-action', error:'tag-error',
};
function addTrace(source, msg, tagClass) {
  const log  = document.getElementById('trace-log');
  const idle = log.querySelector('.trace-idle');
  if (idle) idle.remove();

  const ts   = new Date().toLocaleTimeString('en-IN',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});
  const line = document.createElement('div');
  const cls  = tagClass || TAG_MAP[source] || 'tag-action';
  line.className = 'trace-line';
  line.innerHTML = `
    <span class="tl-ts">${ts}</span>
    <span class="tl-tag font-mono ${cls}">${source.toUpperCase().replace('QUERY_','')}</span>
    <span class="tl-msg">${escHtml(msg)}</span>
  `;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

function clearLog() {
  document.getElementById('trace-log').innerHTML = '';
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ─── Reset board ───────────────────────────────────────────────────────
function resetBoard() {
  // Clear hypotheses
  document.getElementById('hypotheses-row').innerHTML = '<div class="font-mono text-[10px] text-white/15 italic">awaiting investigation…</div>';
  state.hypotheses = [];

  // Clear strings
  document.getElementById('string-svg').innerHTML = '';
  state.stringMap = {};

  // Clear evidence + actions
  document.getElementById('evidence-list').innerHTML   = '<div class="font-mono text-[10px] text-white/20 italic">—</div>';
  document.getElementById('ruled-out-list').innerHTML  = '<div class="font-mono text-[10px] text-white/20 italic">—</div>';
  document.getElementById('actions-list').innerHTML    = '<div class="font-mono text-[10px] text-white/20 italic">—</div>';
  document.getElementById('execution-section').classList.add('hidden');

  // Clear verdict
  document.getElementById('verdict-root-cause').textContent = '— awaiting investigation';
  document.getElementById('verdict-root-cause').className = 'font-mono text-sm text-white/40 leading-snug mt-1.5';
  document.getElementById('confidence-pct').textContent = '—';
  document.getElementById('confidence-pct').className = 'font-mono text-xs font-semibold text-white/40';
  document.getElementById('confidence-bar').style.width = '0%';
  document.getElementById('confidence-bar').className = 'h-full bg-[#f5a623] rounded-full transition-all duration-700';

  // Hide approval
  document.getElementById('approval-gate').classList.add('hidden');

  // Hide challenger
  document.getElementById('challenger-modal').classList.add('hidden');

  // Stamps
  ['tally','gst','delhivery','transport'].forEach(id => {
    const s = document.getElementById(`stamp-${id}`);
    if (s) { s.classList.add('hidden'); s.className = 'stamp-badge hidden'; }
    const c = document.getElementById(`portal-${id}`);
    if (c) { c.classList.remove('active-culprit','stamped-stale','stamped-misleading','stamped-true','ring-1','ring-blue-500/40'); }
  });

  // GST eway reset
  const eway = document.getElementById('gst-eway');
  if (eway) { eway.className = 'portal-status text-[#ef4444]'; eway.textContent = 'E-Way Expired'; }

  // Clock
  clearInterval(state.timerInterval);
  document.getElementById('wall-clock').textContent = '0.00s';

  // Replay badge
  document.getElementById('replay-badge').classList.add('hidden');

  setPhase('INGESTING');
}

// ─── Clock ─────────────────────────────────────────────────────────────
function tickClock() {
  if (!state.startTime) return;
  const s = (Date.now() - state.startTime) / 1000;
  document.getElementById('wall-clock').textContent = `${s.toFixed(2)}s`;
}

// ─── Cases list ────────────────────────────────────────────────────────
async function loadCases() {
  try {
    const r = await fetch('/api/cases');
    const d = await r.json();
    renderCases(d.cases || []);
  } catch {}
}

function renderCases(cases) {
  const container = document.getElementById('cases-list');
  container.innerHTML = '';

  if (!cases.length) {
    container.innerHTML = '<div class="py-6 text-center"><span class="font-mono text-[10px] text-white/15">no cases</span></div>';
    return;
  }

  cases.slice().reverse().forEach(c => {
    const el  = document.createElement('div');
    const active = c.case_id === state.activeCaseId;
    el.className = `case-item${active ? ' active' : ''}`;

    const statusClsMap = { active:'cs-active', awaiting_approval:'cs-awaiting', closed:'cs-closed', pending:'cs-pending' };
    const statusCls = statusClsMap[c.status] || 'cs-active';

    el.innerHTML = `
      <div class="flex items-center justify-between mb-0.5">
        <span class="ci-order">#${c.order_id}</span>
        <span class="case-status-pill ${statusCls}">${c.status}</span>
      </div>
      <div class="ci-id truncate">${c.case_id}</div>
      ${c.verdict_summary ? `<div class="font-mono text-[9px] text-white/20 mt-1 truncate">${c.verdict_summary}</div>` : ''}
      ${c.status === 'pending' ? `<button class="pending-invest-btn" data-id="${c.case_id}">🔍 INVESTIGATE</button>` : ''}
    `;

    // Click: open or replay
    el.addEventListener('click', (e) => {
      if (e.target.classList.contains('pending-invest-btn') || e.target.closest('.pending-invest-btn')) return;
      if (c.status === 'closed') { resetBoard(); state.activeCaseId = c.case_id; connectSSE(c.case_id, true); }
      else if (c.status !== 'pending') { state.activeCaseId = c.case_id; connectSSE(c.case_id); }
    });

    // Pending investigate button
    const pendBtn = el.querySelector('.pending-invest-btn');
    if (pendBtn) pendBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      triggerPending(c.case_id);
    });

    container.appendChild(el);
  });
}

// ─── Pending case poll ─────────────────────────────────────────────────
async function pollForPendingCases() {
  setInterval(async () => {
    try {
      const r = await fetch('/api/cases');
      const d = await r.json();
      const pending = (d.cases||[]).find(c => c.status === 'pending');
      if (pending && pending.case_id !== state.activeCaseId) {
        showPendingBanner(pending);
      }
      // Also refresh the list silently
      renderCases(d.cases || []);
    } catch {}
  }, 8000);
}

function showPendingBanner(c) {
  state.pendingCaseId = c.case_id;
  const banner = document.getElementById('pending-banner');
  document.getElementById('pending-text').textContent = `Email trigger: Order #${c.order_id}`;
  banner.classList.remove('hidden');
}
function hidePendingBanner() {
  document.getElementById('pending-banner').classList.add('hidden');
  state.pendingCaseId = null;
}

// ─── Toast ─────────────────────────────────────────────────────────────
function toast(msg, type = 'info') {
  const container = document.getElementById('toast-container');
  const t = document.createElement('div');
  const typeClass = type === 'success' ? 'toast-success' : type === 'warn' ? 'toast-warn' : type === 'error' ? 'toast-error' : '';
  t.className = `toast ${typeClass}`;
  t.textContent = msg;
  container.appendChild(t);
  setTimeout(() => {
    t.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
    t.style.opacity = '0'; t.style.transform = 'translateX(16px)';
    setTimeout(() => t.remove(), 300);
  }, 3500);
}
