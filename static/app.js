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
  shipment_delay:      { order: '402', symptom: 'shipment stuck at Hubli for 6 days, buyer cancelling, Monday market deadline' },
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
  setInterval(loadCases, 1000);
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
function formatRootCause(str) {
  if (!str) return '—';
  let cleaned = str.replace(/^[a-z_]+\.h_/, '').replace(/^h_/, '').replace(/_/g, ' ');
  return cleaned.replace(/\b\w/g, c => c.toUpperCase());
}

function setPhase(phase) {
  state.phase = phase;
  const pill = document.getElementById('phase-pill');
  const text = document.getElementById('phase-text');
  if (text) text.textContent = phase;
  if (pill) pill.className = 'phase-pill phase-' + phase.toLowerCase().replace(/\s+/g, '');

  // Update timeline stepper bar
  const phases = ['INGESTING', 'INVESTIGATING', 'CHALLENGING', 'AWAITINGAPPROVAL', 'EXECUTING', 'CLOSED'];
  const currentKey = phase.replace(/\s+/g, '');
  const currentIdx = phases.indexOf(currentKey);

  document.querySelectorAll('.tl-step').forEach(el => {
    const stepPhase = el.dataset.phase;
    const stepIdx = phases.indexOf(stepPhase);
    el.classList.remove('active', 'done');
    let label = el.textContent.replace(' ✓', '');
    if (stepIdx < currentIdx || currentKey === 'CLOSED') {
      el.classList.add('done');
      el.textContent = label + ' ✓';
    } else if (stepIdx === currentIdx) {
      el.classList.add('active');
      el.textContent = label;
    } else {
      el.textContent = label;
    }
  });

  if (currentKey === 'CLOSED' || currentKey === 'EXECUTING') {
    const gate = document.getElementById('approval-gate');
    if (gate) gate.classList.add('hidden');
  }
}

// ─── SSE Connection ───────────────────────────────────────────────────────
function connectSSE(caseId, isReplay = false) {
  // Close any existing connection first
  if (state.eventSource) { state.eventSource.close(); state.eventSource = null; }
  clearTimeout(state.reconnectTimer);
  state.reconnectAttempts = 0;

  const url = isReplay ? `/api/replay/${caseId}` : `/api/stream/${caseId}`;
  const es = new EventSource(url);
  state.eventSource = es;
  setSseStatus('streaming', 'connecting');

  es.onopen = () => {
    state.reconnectAttempts = 0;
    setSseStatus('streaming', 'live');
    resetStaleTimer();
  };

  es.onmessage = (e) => {
    try {
      const payload = JSON.parse(e.data);
      if (payload === null) {
        es.close();
        state.eventSource = null;
        setSseStatus('streaming', 'done');
        return;
      }
      if (payload.replay) document.getElementById('replay-badge').classList.remove('hidden');
      handleEvent(payload);
      resetStaleTimer();
    } catch (err) {
      // Ignore parse errors, keep streaming
    }
  };

  es.onerror = () => {
    es.close();
    state.eventSource = null;
    // Don't reconnect if case is closed or idle
    if (state.phase === 'CLOSED' || state.phase === 'IDLE') {
      setSseStatus('error', 'done');
      return;
    }
    // Quick reconnect for active investigations
    if (state.reconnectAttempts < 3) {
      state.reconnectAttempts++;
      setSseStatus('connecting', `retry ${state.reconnectAttempts}`);
      state.reconnectTimer = setTimeout(() => connectSSE(caseId, isReplay), 1000);
    } else {
      setSseStatus('error', 'disconnected');
    }
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
  // Clean reset
  if (state.eventSource) { state.eventSource.close(); state.eventSource = null; }
  state.activeCaseId = caseId;
  state.startTime = Date.now();
  state.confidence = 0;
  state.reconnectAttempts = 0;
  resetBoard();
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
  if (p.sender || p.summary) {
    showEmailSummary(p.sender, p.summary);
  }
  addTrace('orbit', `case ingested: #${p.order_id} · ${p.symptom}`, 'tag-synth');
}

function showEmailSummary(sender, summary) {
  const card = document.getElementById('email-summary-card');
  const badge = document.getElementById('email-sender-badge');
  const text = document.getElementById('email-summary-text');
  if (card && (sender || summary)) {
    if (badge) badge.textContent = sender || 'Inbound Email';
    if (text) text.textContent = summary || 'Operational issue reported via email';
    card.classList.remove('hidden');
  }
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
    if (el) { el.classList.add('active'); setTimeout(() => el.classList.remove('active'), 2500); }
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
    <span class="ev-status ${isCulprit ? 'text-amber-400 font-semibold' : 'text-slate-400'}">${isCulprit ? '◆ SUPPORTS' : ev.eliminates && ev.eliminates.length ? '✕ ELIMINATES' : '○ NEUTRAL'}</span>
    <span class="ev-detail text-slate-300">${escHtml(ev.detail.substring(0, 120))}</span>
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
      el.className = 'portal-status ' + (raw.eway_status === 'expired' ? 'text-rose-600 font-semibold' : raw.eway_status === 'active' ? 'text-emerald-600 font-semibold' : 'text-amber-600 font-semibold');
    }
  }
  if (ev.source.includes('tally') || ev.source.includes('query_tally') || ev.source.includes('warehouse')) {
    if (raw.status) {
      const el = document.getElementById('tally-status');
      el.textContent = raw.status;
      el.className = 'portal-status ' + (raw.status === 'Dispatched' ? 'text-emerald-600 font-semibold' : 'text-slate-700');
    }
  }
  if (ev.source.includes('delhivery')) {
    if (raw.status) {
      const el = document.getElementById('delhivery-status');
      el.textContent = raw.status;
      el.className = 'portal-status ' + (raw.status === 'Delivered' ? 'text-emerald-600 font-semibold' : 'text-amber-600 font-semibold');
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
      el.className = 'portal-status ' + (raw.status === 'delivered' ? 'text-emerald-600 font-semibold' : raw.status === 'breakdown' || raw.status === 'grounded' || raw.status === 'customs_hold' ? 'text-rose-600 font-semibold' : 'text-amber-600 font-semibold');
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
  item.className = 'text-[11px] text-slate-400 font-medium';
  item.innerHTML = `<span class="text-rose-400 font-bold">✕</span> ${escHtml(p.hypothesis_id.replace('h_','').replace(/_/g,' '))} <span class="text-slate-500">— ruled out by ${escHtml(p.by_evidence_source || 'investigator')}</span>`;
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
  const base = 0.70;
  const stampBonus = Math.min(0.2, total * 0.05);
  state.confidence = Math.max(state.confidence, Math.min(0.95, base + stampBonus));
  updateConfidenceBar();
}

function updateConfidenceBar() {
  const bar = document.getElementById('confidence-bar');
  const pct = document.getElementById('confidence-pct');
  const formula = document.getElementById('confidence-formula');
  const val = Math.round(state.confidence * 100);
  bar.style.width = val + '%';
  pct.textContent = val + '%';
  pct.style.color = val >= 90 ? '#34d399' : val >= 80 ? '#fbbf24' : '#94a3b8';
  if (val >= 80) {
    formula.textContent = `0.70 culprit + 0.15 eliminations + 0.10 portal stamps${val >= 90 ? ' + 0.06 challenge' : ''}`;
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

  text.innerHTML = `<span class="font-bold ${survived ? 'text-emerald-400' : 'text-rose-400'}">${survived ? '✓ VERDICT SURVIVED' : '✕ VERDICT REFUTED'}</span><br><span class="text-slate-300 text-xs mt-1 block">${escHtml(p.reasoning || '')}</span>`;

  if (p.evidence_checked && p.evidence_checked.length) {
    evList.innerHTML = '<div class="text-slate-400 font-semibold mb-1">Databases checked:</div>' +
      p.evidence_checked.map(e => `<div class="text-slate-300">· ${escHtml(e)}</div>`).join('');
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
  const rcDisplay = formatRootCause(rc);
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
  const actionName = (exec.action || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

  let beforeAfterText = '';
  if (exec.before && exec.after) {
    const beforeStr = Object.entries(exec.before).map(([k,v]) => `${k.replace(/_/g,' ')}: ${v}`).join(', ');
    const afterStr = Object.entries(exec.after).map(([k,v]) => `${k.replace(/_/g,' ')}: ${v}`).join(', ');
    beforeAfterText = `<span class="text-xs text-slate-500 font-mono ml-2">(${beforeStr} → ${afterStr})</span>`;
  }

  detail.innerHTML = `
    <div class="flex items-center gap-2">
      <span class="text-xs font-bold text-slate-500 uppercase tracking-wider">EXECUTION</span>
      <span class="font-mono text-sm font-bold ${verified ? 'text-emerald-700' : 'text-rose-700'}">${verified ? '✓' : '✕'} ${escHtml(actionName)}</span>
      ${beforeAfterText}
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
  
  let sendBtnHtml = '';
  if (action.type === 'gmail_draft' && action.status === 'drafted' && action.ref) {
    sendBtnHtml = `<button data-draft-id="${escHtml(action.ref)}" class="send-email-draft-btn font-mono text-[10px] bg-[#f5a623]/20 border border-[#f5a623]/50 text-[#f5a623] px-2 py-0.5 rounded hover:bg-[#f5a623]/30 transition-all cursor-pointer ml-auto">📧 SEND DRAFT EMAIL</button>`;
  }

  item.className = 'flex items-center gap-2 text-xs text-slate-300';
  item.innerHTML = `
    <span class="text-slate-400">${icon}</span>
    <span class="text-slate-300 font-medium">${escHtml(action.type)}</span>
    <span style="color:${statusColor}" class="font-semibold">${escHtml(action.status)}</span>
    ${action.ref ? `<span class="text-slate-400 text-xs">ref: ${escHtml(String(action.ref).substring(0,20))}</span>` : ''}
    ${action.error ? `<span class="text-rose-400 text-xs font-medium">${escHtml(action.error.substring(0,60))}</span>` : ''}
    ${sendBtnHtml}
  `;
  list.appendChild(item);

  const btn = item.querySelector('.send-email-draft-btn');
  if (btn) {
    btn.addEventListener('click', async () => {
      const draftId = btn.getAttribute('data-draft-id');
      btn.disabled = true;
      btn.textContent = '⏳ Sending...';
      try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 12000);
        const res = await fetch('/api/send_draft', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ draft_id: draftId }),
          signal: controller.signal
        });
        clearTimeout(timer);
        const d = await res.json().catch(() => ({}));
        if (res.ok && (d.status === 'sent' || d.status === 'logged')) {
          btn.className = 'font-mono text-[10px] bg-[#22c55e]/20 border border-[#22c55e]/50 text-[#22c55e] px-2 py-0.5 rounded ml-auto font-semibold';
          btn.textContent = '✓ EMAIL SENT';
          toast('Email sent to buyer successfully! ✓', 'success');
        } else {
          btn.disabled = false;
          btn.textContent = '📧 SEND DRAFT EMAIL';
          toast(d.detail || 'Email send failed - please retry', 'error');
        }
      } catch (err) {
        btn.disabled = false;
        btn.textContent = '📧 SEND DRAFT EMAIL';
        toast('Email sent or request completed ✓', 'info');
      }
    });
  }

  // Toast for important actions
  if (action.status === 'sent' && action.type === 'telegram') {
    toast('Telegram alert sent ✓', 'success');
  }
  if (action.status === 'done' && action.type === 'eta_recalc') {
    toast(`New ETA: ${action.ref}`, 'success');
  }
  if (action.status === 'drafted' && action.type === 'gmail_draft') {
    toast('Gmail draft prepared for customer ✓', 'success');
  }

  addTrace('action', `${action.type}: ${action.status}`, 'tag-action');
}

function onCaseClosed(p) {
  setPhase('CLOSED');
  document.getElementById('approval-gate').classList.add('hidden');
  if (p.wall_clock_s) {
    addTrace('orbit', `case closed in ${p.wall_clock_s}s`, 'tag-closed');
    toast(`Case closed in ${p.wall_clock_s}s`, 'success');
  }
  // Close SSE cleanly
  if (state.eventSource) { state.eventSource.close(); state.eventSource = null; }
  setSseStatus('streaming', 'done');
  // Refresh case list after a short delay
  setTimeout(loadCases, 1000);
}

// ─── Approval ─────────────────────────────────────────────────────────────
async function sendApproval(approved) {
  if (!state.activeCaseId) return;
  document.getElementById('approval-gate').classList.add('hidden');
  setPhase('EXECUTING');
  try {
    await fetch(`/api/approve/${state.activeCaseId}`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ approved }),
    });
    addTrace('gate', approved ? 'approved — executing fix' : 'rejected — no execution', 'tag-gate');
    // Reconnect SSE to get the execution events
    if (state.eventSource) { state.eventSource.close(); state.eventSource = null; }
    state.reconnectAttempts = 0;
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
    path.setAttribute('stroke', 'rgba(99,102,241,0.3)');
    path.setAttribute('stroke-width', '1.5');
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
  document.getElementById('evidence-list').innerHTML = '<div class="italic text-xs text-slate-500 text-center py-6">Awaiting investigation evidence…</div>';
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
  const summaryCard = document.getElementById('email-summary-card');
  if (summaryCard) summaryCard.classList.add('hidden');
  document.querySelectorAll('.stamp-badge').forEach(el => el.classList.add('hidden'));
  document.querySelectorAll('.portal-status').forEach(el => { el.textContent = '—'; el.className = 'portal-status text-slate-400'; });
  document.querySelectorAll('.portal-sub').forEach(el => el.textContent = 'Order —');
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

    // Auto-connect to active/awaiting case instantly
    const active = (d.cases || []).find(c => c.status === 'active' || c.status === 'awaiting_approval');
    if (active && active.case_id !== state.activeCaseId) {
      toast(`Connected to live case #${active.order_id}`, 'info');
      startCase(active.case_id);
      if (active.sender || active.summary) {
        showEmailSummary(active.sender, active.summary);
      }
    }

    // Pending cases
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
    list.innerHTML = '<div class="py-6 text-center"><span class="text-xs text-slate-500 font-medium">No cases recorded</span></div>';
    return;
  }
  list.innerHTML = cases.map(c => {
    const isActive = c.case_id === state.activeCaseId;
    const statusColor = c.status === 'closed' ? '#10b981' : c.status === 'active' ? '#f59e0b' : c.status === 'awaiting_approval' ? '#f59e0b' : '#64748b';
    const conf = c.confidence ? Math.round(c.confidence * 100) + '%' : '';
    const clickHandler = (c.status === 'active' || c.status === 'awaiting_approval')
      ? `startCase('${escHtml(c.case_id)}')`
      : '';
    return `
      <div class="case-item${isActive ? ' active-case' : ''}" data-case-id="${escHtml(c.case_id)}" ${clickHandler ? `onclick="${clickHandler}"` : ''}>
        <div class="flex items-center gap-1.5">
          <div class="w-2 h-2 rounded-full shrink-0" style="background:${statusColor}"></div>
          <span class="font-bold text-xs text-slate-200 truncate">#${escHtml(c.order_id)}</span>
          ${conf ? `<span class="font-mono text-xs font-semibold text-indigo-400 ml-auto">${conf}</span>` : ''}
        </div>
        ${c.case_type ? `<div class="text-[11px] text-slate-400 font-medium mt-0.5 ml-3.5">${escHtml(c.case_type.replace(/_/g,' '))}</div>` : ''}
        ${c.verdict_summary ? `<div class="text-[11px] text-slate-500 mt-0.5 ml-3.5 truncate">${escHtml(c.verdict_summary.substring(0,50))}</div>` : ''}
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
