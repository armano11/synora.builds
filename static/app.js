/* ORBIT app.js — v3 (multi-case + confidence meter + action previews)
 *
 * Clean SSE architecture with multi-case type support.
 * No AI slop. Every element earns its place.
 */

// ─── State ────────────────────────────────────────────────────────────────
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
  stringMap: {},
  anchorMap: {},
  hypotheses: [],
  confidence: 0,
};

// ─── Case type config ─────────────────────────────────────────────────────
const CASE_TYPES = {
  payment_hold:        { order: '501', symptom: 'payment held by bank for delivered order, buyer threatening legal action' },
  inventory_mismatch:  { order: '502', symptom: 'stock mismatch between system and physical count, order short by 10 units' },
  customs_block:       { order: '503', symptom: 'customs hold at Mumbai port, documents incomplete, clearance stuck 5 days' },
  invoice_dispute:     { order: '504', symptom: 'invoice amount dispute, buyer refusing payment, billing error of 4000 rupees' },
  compliance_block:    { order: '505', symptom: 'transport license expired, vehicle grounded, shipment not moving' },
};

// ─── Investigator → portal mapping ────────────────────────────────────────
const INV_PORTAL = {
  gst: 'portal-gst', query_gst: 'portal-gst',
  inventory: 'portal-tally', query_inventory: 'portal-tally', query_tally: 'portal-tally', query_tally_order: 'portal-tally',
  warehouse: 'portal-tally',
  transport: 'portal-transport', query_transport: 'portal-transport',
  delhivery: 'portal-delhivery', query_delhivery: 'portal-delhivery',
};

// All hypothesis IDs across all case types → portal
const HYPO_PORTAL = {
  // shipment_delay
  h_eway_bill_expired: 'portal-gst',
  h_inventory_damage: 'portal-tally',
  h_dispatch_failure: 'portal-tally',
  h_transport_breakdown: 'portal-transport',
  // payment_hold
  h_payment_hold_bank_recon: 'portal-tally',
  h_payment_hold_buyer_default: 'portal-transport',
  // inventory_mismatch
  h_inventory_count_error: 'portal-tally',
  h_inventory_damage_loss: 'portal-tally',
  // customs_block
  h_customs_docs_incomplete: 'portal-gst',
  h_customs_inspection: 'portal-transport',
  // invoice_dispute
  h_invoice_amount_mismatch: 'portal-tally',
  h_invoice_tax_error: 'portal-gst',
  // compliance_block
  h_compliance_license_expired: 'portal-transport',
  h_compliance_docs_missing: 'portal-gst',
};

// ─── Phase mapping ────────────────────────────────────────────────────────
const EV_PHASE = {
  case_ingested:'INGESTING', hypotheses_ready:'INVESTIGATING',
  investigator_start:'INVESTIGATING', evidence_found:'INVESTIGATING',
  hypothesis_ruled_out:'INVESTIGATING', portal_stamped:'INVESTIGATING',
  verdict_draft:'INVESTIGATING', challenge_start:'CHALLENGING',
  challenge_result:'CHALLENGING', approval_required:'AWAITING APPROVAL',
  verdict_locked:'EXECUTING', execution_done:'EXECUTING',
  action_done:'EXECUTING', case_closed:'CLOSED',
};

// ─── Boot ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', boot);

function boot() {
  document.getElementById('investigate-btn').addEventListener('click', triggerManual);
  document.getElementById('order-id-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') triggerManual();
  });
  document.getElementById('case-type-select').addEventListener('change', onCaseTypeChange);
  document.getElementById('approve-btn').addEventListener('click', () => sendApproval(true));
  document.getElementById('reject-btn').addEventListener('click', () => sendApproval(false));
  document.getElementById('clear-log-btn').addEventListener('click', clearLog);
  document.getElementById('refresh-cases-btn').addEventListener('click', loadCases);
  document.getElementById('pending-investigate-btn').addEventListener('click', () => {
    if (state.pendingCaseId) triggerPending(state.pendingCaseId);
  });

  // Set default order ID from case type
  onCaseTypeChange();

  loadCases();
  setInterval(loadCases, 5000);
  tickClock();
}

function onCaseTypeChange() {
  const select = document.getElementById('case-type-select');
  const ct = select.value;
  const config = CASE_TYPES[ct];
  if (config) {
    document.getElementById('order-id-input').value = config.order;
    document.getElementById('order-id-input').placeholder = config.order;
  }
}

// ─── Phase ────────────────────────────────────────────────────────────────
function setPhase(phase) {
  state.phase = phase;
  const pill = document.getElementById('phase-pill');
  const text = document.getElementById('phase-text');
  text.textContent = phase;
  pill.className = 'phase-pill phase-' + phase.toLowerCase().replace(/\s+/g, '');
}

// ─── SSE Connection ───────────────────────────────────────────────────────
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
      if (payload === null) { es.close(); state.eventSource = null; return; }
      if (payload.replay) document.getElementById('replay-badge').classList.remove('hidden');
      handleEvent(payload);
      resetStaleTimer();
    } catch {}
  };

  es.onerror = () => {
    setSseStatus('error', 'disconnected');
    es.close();
    state.eventSource = null;
    if (state.phase === 'CLOSED') return;
    scheduleReconnect(caseId, isReplay);
  };
}

function scheduleReconnect(caseId, isReplay) {
  if (state.reconnectAttempts >= state.MAX_RECONNECT) {
    setSseStatus('error', 'gave up');
    toast('SSE connection failed. Use REPLAY to review.', 'error');
    return;
  }
  const delay = Math.min(1000 * Math.pow(2, state.reconnectAttempts), 8000);
  state.reconnectAttempts++;
  setSseStatus('connecting', `retry ${state.reconnectAttempts}`);
  state.reconnectTimer = setTimeout(() => connectSSE(caseId, isReplay), delay);
}

function resetStaleTimer() {
  clearTimeout(state.staleTimer);
  state.staleTimer = setTimeout(() => {
    if (state.phase !== 'CLOSED' && state.phase !== 'IDLE')
      setSseStatus('stale', 'stale');
  }, state.STALE_TIMEOUT_MS);
}

function setSseStatus(cls, label) {
  const dot = document.getElementById('sse-dot');
  const lbl = document.getElementById('sse-label');
  const colors = { streaming: '#22c55e', connecting: '#f5a623', error: '#ef4444', stale: '#f5a623' };
  dot.style.background = colors[cls] || '#ffffff';
  dot.style.opacity = cls === 'streaming' ? '1' : '0.6';
  lbl.textContent = label;
}

// ─── Trigger investigation ────────────────────────────────────────────────
async function triggerManual() {
  const orderId = document.getElementById('order-id-input').value.trim();
  const caseType = document.getElementById('case-type-select').value;
  if (!orderId) { toast('Enter an order ID', 'warn'); return; }

  try {
    const r = await fetch('/api/investigate', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ order_id: orderId, case_type: caseType }),
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
  } catch (e) { toast('Server unreachable.', 'error'); }
}

function startCase(caseId) {
  state.activeCaseId = caseId;
  state.startTime = Date.now();
  state.confidence = 0;
  setPhase('INGESTING');
  document.getElementById('confidence-section').classList.remove('hidden');
  connectSSE(caseId);
}

// ─── Event handler ────────────────────────────────────────────────────────
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

// ─── Event handlers ───────────────────────────────────────────────────────
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
    chip.innerHTML = `<span class="hypo-dot"></span><span>${escHtml(h.label)}</span>`;
    chip.title = h.rationale || '';
    row.appendChild(chip);
  });
  addTrace('router', `${p.hypotheses.length} hypotheses formulated`, 'tag-router');
  setTimeout(drawStrings, 300);
}

function onInvestigatorStart(p) {
  const portalId = INV_PORTAL[p.investigator];
  if (portalId) {
    const el = document.getElementById(portalId);
    if (el) { el.classList.add('ring-1','ring-blue-500/40'); setTimeout(() => el.classList.remove('ring-1','ring-blue-500/40'), 2500); }
  }
  const hypoEl = document.getElementById(`hypo-${p.hypothesis_id}`);
  if (hypoEl) hypoEl.classList.add('investigating');
  addTrace(p.investigator, `checking ${p.hypothesis_id.replace('h_','').replace(/_/g,' ')}`, 'tag-invest');
}

function onEvidenceFound(p) {
  const ev = p.evidence;
  const list = document.getElementById('evidence-list');
  const blankMsg = list.querySelector('.italic');
  if (blankMsg) blankMsg.remove();

  const isCulprit = ev.found && ev.supports && ev.supports.length > 0;
  const item = document.createElement('div');
  item.className = `evidence-item${isCulprit ? ' culprit' : ''}`;
  item.innerHTML = `
    <span class="ev-source">${escHtml(ev.source.replace('query_',''))}</span>
    <span class="ev-status ${isCulprit ? 'text-[#f5a623]' : 'text-white/40'}">${isCulprit ? '◆ SUPPORTS' : ev.eliminates && ev.eliminates.length ? '✕ ELIMINATES' : '○ NEUTRAL'}</span>
    <span class="ev-detail">${escHtml(ev.detail.substring(0, 120))}</span>
  `;
  list.appendChild(item);
  list.scrollTop = list.scrollHeight;

  // Update portal card with raw data
  updatePortalFromEvidence(ev);

  // Update hypothesis chip
  if (ev.supports) ev.supports.forEach(hid => {
    const el = document.getElementById(`hypo-${hid}`);
    if (el) el.classList.add('supported');
  });
  if (ev.eliminates) ev.eliminates.forEach(hid => {
    const el = document.getElementById(`hypo-${hid}`);
    if (el) { el.classList.add('ruled-out'); }
  });

  addTrace(ev.source, ev.detail.substring(0, 100), isCulprit ? 'tag-culprit' : 'tag-invest');
}

function updatePortalFromEvidence(ev) {
  const raw = ev.raw || {};
  const portalId = INV_PORTAL[ev.source];
  if (!portalId) return;

  if (ev.source.includes('gst') || ev.source.includes('query_gst')) {
    if (raw.eway_status) {
      const el = document.getElementById('gst-eway');
      el.textContent = raw.eway_status.charAt(0).toUpperCase() + raw.eway_status.slice(1);
      el.className = 'portal-status ' + (raw.eway_status === 'expired' ? 'text-[#ef4444]' : raw.eway_status === 'active' ? 'text-[#22c55e]' : 'text-[#f5a623]');
    }
  }
  if (ev.source.includes('tally') || ev.source.includes('query_tally') || ev.source.includes('warehouse')) {
    if (raw.status) {
      const el = document.getElementById('tally-status');
      el.textContent = raw.status;
      el.className = 'portal-status ' + (raw.status === 'Dispatched' ? 'text-[#22c55e]' : 'text-white/60');
    }
  }
  if (ev.source.includes('delhivery')) {
    if (raw.status) {
      const el = document.getElementById('delhivery-status');
      el.textContent = raw.status;
      el.className = 'portal-status ' + (raw.status === 'Delivered' ? 'text-[#22c55e]' : 'text-[#f5a623]');
    }
    if (raw.last_scan_location) {
      const el = document.querySelector('#portal-delhivery .portal-sub');
      if (el) el.textContent = raw.last_scan_location;
    }
  }
  if (ev.source.includes('transport') || ev.source.includes('query_transport')) {
    if (raw.status) {
      const el = document.getElementById('transport-status');
      el.textContent = raw.status.replace(/_/g, ' ');
      el.className = 'portal-status ' + (raw.status === 'delivered' ? 'text-[#22c55e]' : raw.status === 'breakdown' || raw.status === 'grounded' || raw.status === 'customs_hold' ? 'text-[#ef4444]' : 'text-[#f5a623]');
    }
    if (raw.vehicle_no) {
      const el = document.querySelector('#portal-transport .portal-sub');
      if (el) el.textContent = raw.vehicle_no;
    }
  }
}

function onHypothesisRuledOut(p) {
  const el = document.getElementById(`hypo-${p.hypothesis_id}`);
  if (el) el.classList.add('ruled-out');

  const list = document.getElementById('ruled-out-list');
  const item = document.createElement('div');
  item.className = 'font-mono text-[10px] text-white/30';
  item.innerHTML = `<span class="text-white/20">✕</span> ${escHtml(p.hypothesis_id.replace('h_','').replace(/_/g,' '))} <span class="text-white/15">— ruled out by ${escHtml(p.by_evidence_source || 'investigator')}</span>`;
  list.appendChild(item);
}

function onPortalStamped(p) {
  const portalMap = { tally: 'stamp-tally', gst: 'stamp-gst', delhivery: 'stamp-delhivery', transport: 'stamp-transport' };
  const stampId = portalMap[p.portal];
  if (!stampId) return;
  const el = document.getElementById(stampId);
  const stamp = p.stamp || {};
  const verdict = stamp.verdict || '?';
  const colors = { TRUE: '#22c55e', STALE: '#f5a623', MISLEADING: '#ef4444' };
  el.textContent = verdict;
  el.style.color = colors[verdict] || '#ffffff';
  el.style.borderColor = (colors[verdict] || '#ffffff') + '30';
  el.style.background = (colors[verdict] || '#ffffff') + '08';
  el.classList.remove('hidden');
  el.title = stamp.reason || '';

  addTrace('synth', `stamp: ${p.portal} → ${verdict}`, 'tag-synth');

  // Update confidence after stamps
  updateConfidenceFromStamps();
}

function updateConfidenceFromStamps() {
  const stamps = document.querySelectorAll('.stamp-badge:not(.hidden)');
  const total = stamps.length;
  if (total === 0) return;
  // Base confidence: 0.5 (culprit) + partial coverage
  // This is a visual approximation; the real math is in the synthesizer
  const base = 0.5;
  const stampBonus = Math.min(0.2, total * 0.05);
  state.confidence = Math.min(0.85, base + stampBonus);
  updateConfidenceBar();
}

function updateConfidenceBar() {
  const bar = document.getElementById('confidence-bar');
  const pct = document.getElementById('confidence-pct');
  const formula = document.getElementById('confidence-formula');
  const val = Math.round(state.confidence * 100);
  bar.style.width = val + '%';
  pct.textContent = val + '%';
  pct.style.color = val >= 90 ? '#22c55e' : val >= 80 ? '#f5a623' : '#ffffff60';
  if (val >= 80) {
    formula.textContent = `0.5 + 0.3×(elim/total) + 0.2×(stamps/total)${val >= 90 ? ' + 0.06 challenge' : ''}`;
  }
}

function onChallengeStart(p) {
  const text = document.getElementById('challenger-text');
  text.textContent = p.attack_preview || 'Cross-examining the verdict…';
  document.getElementById('challenger-evidence').innerHTML = '';
  addTrace('challenger', `attack: ${(p.attack_preview || '').substring(0, 80)}`, 'tag-challenger');
}

function onChallengeResult(p) {
  const text = document.getElementById('challenger-text');
  const evList = document.getElementById('challenger-evidence');
  const survived = p.survived;
  const delta = p.confidence_delta || 0;

  text.innerHTML = `<span style="color:${survived ? '#22c55e' : '#ef4444'}">${survived ? '✓ VERDICT SURVIVED' : '✕ VERDICT REFUTED'}</span><br><span class="text-white/50 text-xs">${escHtml(p.reasoning || '')}</span>`;

  if (p.evidence_checked && p.evidence_checked.length) {
    evList.innerHTML = '<div class="text-white/20 mb-1">databases checked:</div>' +
      p.evidence_checked.map(e => `<div>· ${escHtml(e)}</div>`).join('');
  }

  if (survived && delta > 0) {
    state.confidence = Math.min(0.99, state.confidence + delta);
    updateConfidenceBar();
  }
  addTrace('challenger', `${survived ? 'SURVIVED' : 'REFUTED'} (Δ${delta.toFixed(2)})`, survived ? 'tag-survived' : 'tag-refuted');
}

function onApprovalRequired(p) {
  document.getElementById('approval-gate').classList.remove('hidden');
  document.getElementById('approval-action-text').textContent = p.proposed_action || '—';
  addTrace('gate', 'approval required — awaiting human authorization', 'tag-gate');
}

function onVerdictLocked(p) {
  const v = p.verdict || {};
  document.getElementById('verdict-bar').classList.remove('hidden');
  const rc = v.root_cause || '—';
  const rcDisplay = rc.includes('.') ? rc.split('.').pop().replace(/_/g, ' ') : rc;
  document.getElementById('verdict-root-cause').textContent = rcDisplay;

  if (v.confidence) {
    state.confidence = v.confidence;
    updateConfidenceBar();
  }
  addTrace('synth', `verdict locked: ${rcDisplay} @ ${Math.round((v.confidence || 0) * 100)}%`, 'tag-verdict');
}

function onExecutionDone(p) {
  document.getElementById('execution-section').classList.remove('hidden');
  const exec = p.execution || {};
  const detail = document.getElementById('execution-detail');
  const verified = exec.verified;
  detail.innerHTML = `
    <div class="flex items-center gap-3">
      <span class="font-mono text-[9px] tracking-[0.2em] text-white/25">EXECUTION</span>
      <span class="font-mono text-sm ${verified ? 'text-[#22c55e]' : 'text-[#ef4444]'}">${verified ? '✓' : '✕'} ${escHtml(exec.action || '—')}</span>
      ${exec.before ? `<span class="font-mono text-[10px] text-white/30">${JSON.stringify(exec.before).replace(/"/g,'')} → ${JSON.stringify(exec.after).replace(/"/g,'')}</span>` : ''}
    </div>
  `;
  addTrace('executor', `${exec.action} verified=${verified}`, verified ? 'tag-executed' : 'tag-error');
}

function onActionDone(p) {
  const action = p.action || {};
  const list = document.getElementById('actions-list');
  const item = document.createElement('div');
  const statusColor = action.status === 'sent' ? '#22c55e' : action.status === 'drafted' ? '#f5a623' : action.status === 'done' ? '#22c55e' : '#ef4444';
  const icon = action.type === 'telegram' ? '📨' : action.type === 'gmail_draft' ? '✉' : '⏱';
  item.className = 'flex items-center gap-2 font-mono text-[11px]';
  item.innerHTML = `
    <span class="text-white/30">${icon}</span>
    <span class="text-white/50">${escHtml(action.type)}</span>
    <span style="color:${statusColor}">${escHtml(action.status)}</span>
    ${action.ref ? `<span class="text-white/20 text-[10px]">ref: ${escHtml(String(action.ref).substring(0,20))}</span>` : ''}
    ${action.error ? `<span class="text-[#ef4444]/60 text-[10px]">${escHtml(action.error.substring(0,60))}</span>` : ''}
  `;
  list.appendChild(item);

  // Toast for important actions
  if (action.status === 'sent' && action.type === 'telegram') {
    toast('Telegram alert sent ✓', 'success');
  }
  if (action.status === 'done' && action.type === 'eta_recalc') {
    toast(`New ETA: ${action.ref}`, 'success');
  }

  addTrace('action', `${action.type}: ${action.status}`, 'tag-action');
}

function onCaseClosed(p) {
  if (p.wall_clock_s) {
    addTrace('orbit', `case closed in ${p.wall_clock_s}s`, 'tag-closed');
    toast(`Case closed in ${p.wall_clock_s}s`, 'success');
  }
  if (state.eventSource) { state.eventSource.close(); state.eventSource = null; }
  setSseStatus('streaming', 'done');
  setTimeout(loadCases, 1000);
}

// ─── Approval ─────────────────────────────────────────────────────────────
async function sendApproval(approved) {
  if (!state.activeCaseId) return;
  document.getElementById('approval-gate').classList.add('hidden');
  try {
    await fetch(`/api/approve/${state.activeCaseId}`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ approved }),
    });
    addTrace('gate', approved ? 'approved — executing fix' : 'rejected — no execution', 'tag-gate');
    connectSSE(state.activeCaseId);
  } catch (e) { toast('Approval failed.', 'error'); }
}

// ─── SVG strings ──────────────────────────────────────────────────────────
function drawStrings() {
  const svg = document.getElementById('string-svg');
  svg.innerHTML = '';
  state.stringMap = {};
  state.anchorMap = {};

  state.hypotheses.forEach(h => {
    const portalId = HYPO_PORTAL[h.id];
    const hypoEl = document.getElementById(`hypo-${h.id}`);
    const portalEl = portalId ? document.getElementById(portalId) : null;
    if (!hypoEl || !portalEl) return;

    const hRect = hypoEl.getBoundingClientRect();
    const pRect = portalEl.getBoundingClientRect();
    const svgRect = svg.getBoundingClientRect();

    const x1 = hRect.left + hRect.width / 2 - svgRect.left;
    const y1 = hRect.top - svgRect.top;
    const x2 = pRect.left + pRect.width / 2 - svgRect.left;
    const y2 = pRect.bottom - svgRect.top;

    const midY = (y1 + y2) / 2;
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`);
    path.setAttribute('class', 'string-line');
    path.setAttribute('stroke', 'rgba(255,255,255,0.08)');
    path.setAttribute('stroke-width', '1');
    path.setAttribute('fill', 'none');
    svg.appendChild(path);
    state.stringMap[h.id] = path;
  });
}

// ─── Trace log ────────────────────────────────────────────────────────────
function addTrace(source, msg, cls) {
  const log = document.getElementById('trace-log');
  const time = ((Date.now() - state.startTime) / 1000).toFixed(1);
  const item = document.createElement('div');
  item.className = `trace-line ${cls || ''}`;
  item.innerHTML = `<span class="trace-time">${time}s</span> <span class="trace-src">${escHtml(source)}</span> <span class="trace-msg">${escHtml(msg)}</span>`;
  log.appendChild(item);
  log.scrollTop = log.scrollHeight;
}

function clearLog() {
  document.getElementById('trace-log').innerHTML = '';
  document.getElementById('evidence-list').innerHTML = '<div class="font-mono italic text-[10px] text-white/15 text-center py-4">awaiting evidence…</div>';
  document.getElementById('ruled-out-list').innerHTML = '';
}

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

// ─── Reset board ──────────────────────────────────────────────────────────
function resetBoard() {
  clearLog();
  document.getElementById('hypotheses-row').innerHTML = '';
  document.getElementById('verdict-bar').classList.add('hidden');
  document.getElementById('execution-section').classList.add('hidden');
  document.getElementById('execution-detail').innerHTML = '';
  document.getElementById('actions-list').innerHTML = '';
  document.getElementById('approval-gate').classList.add('hidden');
  document.getElementById('challenger-text').textContent = '';
  document.getElementById('challenger-evidence').innerHTML = '';
  document.getElementById('confidence-bar').style.width = '0%';
  document.getElementById('confidence-pct').textContent = '—';
  document.getElementById('confidence-formula').textContent = '';
  document.querySelectorAll('.stamp-badge').forEach(el => el.classList.add('hidden'));
  document.querySelectorAll('.portal-status').forEach(el => { el.textContent = '—'; el.className = 'portal-status text-white/30'; });
  document.querySelectorAll('.portal-sub').forEach(el => el.textContent = '—');
  state.confidence = 0;
  state.startTime = Date.now();
}

// ─── Clock ────────────────────────────────────────────────────────────────
function tickClock() {
  setInterval(() => {
    if (state.startTime && state.phase !== 'CLOSED' && state.phase !== 'IDLE') {
      const elapsed = ((Date.now() - state.startTime) / 1000).toFixed(2);
      document.getElementById('wall-clock').textContent = elapsed + 's';
    }
  }, 100);
}

// ─── Case board ───────────────────────────────────────────────────────────
async function loadCases() {
  try {
    const r = await fetch('/api/cases');
    const d = await r.json();
    renderCases(d.cases || []);
    // Check for pending cases
    const pending = (d.cases || []).filter(c => c.status === 'pending');
    if (pending.length > 0 && !state.activeCaseId) {
      showPendingBanner(pending[0]);
    } else if (pending.length === 0) {
      hidePendingBanner();
    }
  } catch {}
}

function renderCases(cases) {
  const list = document.getElementById('cases-list');
  if (!cases.length) {
    list.innerHTML = '<div class="py-6 text-center"><span class="font-mono text-[10px] text-white/15">no cases</span></div>';
    return;
  }
  list.innerHTML = cases.map(c => {
    const statusColor = c.status === 'closed' ? '#22c55e' : c.status === 'active' ? '#f5a623' : '#ffffff40';
    const conf = c.confidence ? Math.round(c.confidence * 100) + '%' : '';
    return `
      <div class="case-item" data-case-id="${escHtml(c.case_id)}">
        <div class="flex items-center gap-1.5">
          <div class="w-1.5 h-1.5 rounded-full shrink-0" style="background:${statusColor}"></div>
          <span class="font-mono text-[10px] text-white/60 truncate">#${escHtml(c.order_id)}</span>
          ${conf ? `<span class="font-mono text-[9px] text-white/25 ml-auto">${conf}</span>` : ''}
        </div>
        ${c.case_type ? `<div class="font-mono text-[9px] text-white/20 mt-0.5 ml-3">${escHtml(c.case_type.replace(/_/g,' '))}</div>` : ''}
        ${c.verdict_summary ? `<div class="font-mono text-[9px] text-white/15 mt-0.5 ml-3 truncate">${escHtml(c.verdict_summary.substring(0,50))}</div>` : ''}
      </div>
    `;
  }).join('');
}

function showPendingBanner(c) {
  state.pendingCaseId = c.case_id;
  const banner = document.getElementById('pending-banner');
  banner.classList.remove('hidden');
  document.getElementById('pending-text').textContent = `pending: #${c.order_id}`;
}

function hidePendingBanner() {
  state.pendingCaseId = null;
  document.getElementById('pending-banner').classList.add('hidden');
}

// ─── Toast ────────────────────────────────────────────────────────────────
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
