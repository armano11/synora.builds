/* ORBIT Evidence Board — Core JS App logic
 *
 * FIXES applied:
 * - Removed null-ref: confidence-ellipse doesn't exist in HTML
 * - Fixed verdict-folder class: uses 'hidden' (matches HTML), not 'translate-y-full'
 * - resetBoard() now consistently removes 'hidden' for verdict-folder
 * - loadCases() renders 🔍 INVESTIGATE button for pending cases
 */

let eventSource = null;
let activeCaseId = null;
let timerInterval = null;
let startTime = null;
let stringPaths = {};

document.addEventListener("DOMContentLoaded", () => {
  initApp();
});

function initApp() {
  document.getElementById("investigate-btn").addEventListener("click", triggerManualInvestigation);
  document.getElementById("refresh-cases-btn").addEventListener("click", loadCases);
  document.getElementById("approve-btn").addEventListener("click", () => sendApproval(true));
  document.getElementById("reject-btn").addEventListener("click", () => sendApproval(false));

  loadCases();
  window.addEventListener("resize", redrawAllStrings);
}

// ---------------------------------------------------------------------------
// Case Intake / Investigation triggers
// ---------------------------------------------------------------------------

async function triggerManualInvestigation() {
  const orderId = document.getElementById("order-id-input").value.trim();
  if (!orderId) return alert("Please enter an Order ID");

  try {
    const res = await fetch("/api/investigate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order_id: orderId })
    });
    const data = await res.json();
    if (data.case_id) {
      startLiveStream(data.case_id);
    }
  } catch (err) {
    console.error("Manual investigation trigger failed", err);
  }
}

async function triggerPendingCase(caseId) {
  try {
    const res = await fetch(`/api/investigate/${caseId}`, { method: "POST" });
    const data = await res.json();
    if (data.case_id) {
      startLiveStream(data.case_id);
    }
  } catch (err) {
    console.error("Pending case trigger failed", err);
  }
}

// ---------------------------------------------------------------------------
// SSE Streaming & Event Routing
// ---------------------------------------------------------------------------

function startLiveStream(caseId, isReplay = false) {
  resetBoard();
  activeCaseId = caseId;

  if (eventSource) eventSource.close();

  const url = isReplay ? `/api/replay/${caseId}` : `/api/stream/${caseId}`;
  eventSource = new EventSource(url);

  document.getElementById("replay-badge").classList.toggle("hidden", !isReplay);

  startTime = Date.now();
  clearInterval(timerInterval);
  timerInterval = setInterval(updateClock, 50);

  eventSource.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    handleSSEEvent(payload);
  };

  eventSource.onerror = (err) => {
    console.error("SSE stream error/closed", err);
    eventSource.close();
    clearInterval(timerInterval);
    document.getElementById("server-status").innerText = "DISCONNECTED";
    document.getElementById("server-status").className = "font-mono text-red-500";
  };

  document.getElementById("server-status").innerText = "STREAMING";
  document.getElementById("server-status").className = "font-mono text-amber-500";
}

function handleSSEEvent(payload) {
  const ev = payload.event;
  console.log("SSE Event:", ev, payload);

  if (payload.wall_clock_s) {
    document.getElementById("wall-clock").innerText = `${payload.wall_clock_s.toFixed(2)}s`;
  }
  if (payload.llm_cost_usd !== undefined) {
    document.getElementById("llm-cost").innerText = `$${payload.llm_cost_usd.toFixed(4)}`;
  }

  const phase = mapEventToPhase(ev);
  if (phase) {
    const banner = document.getElementById("phase-banner");
    banner.innerText = phase;
    if (phase === "CHALLENGING") {
      banner.className = "text-xs font-semibold text-red-400 bg-red-950 px-2.5 py-1 rounded";
    } else if (phase === "AWAITING APPROVAL") {
      banner.className = "text-xs font-semibold text-amber-400 bg-amber-950 px-2.5 py-1 rounded";
    } else if (phase === "CLOSED") {
      banner.className = "text-xs font-semibold text-green-400 bg-green-950 px-2.5 py-1 rounded";
    } else {
      banner.className = "text-xs font-semibold text-zinc-400 bg-zinc-800 px-2.5 py-1 rounded";
    }
  }

  switch (ev) {
    case "case_ingested":      handleCaseIngested(payload); break;
    case "hypotheses_ready":   handleHypothesesReady(payload); break;
    case "investigator_start": handleInvestigatorStart(payload); break;
    case "evidence_found":     handleEvidenceFound(payload); break;
    case "hypothesis_ruled_out": handleHypothesisRuledOut(payload); break;
    case "portal_stamped":     handlePortalStamped(payload); break;
    case "verdict_draft":      appendTrace(`Drafting verdict: ${payload.partial_root_cause}`); break;
    case "challenge_start":    handleChallengeStart(payload); break;
    case "challenge_result":   handleChallengeResult(payload); break;
    case "approval_required":  handleApprovalRequired(payload); break;
    case "verdict_locked":     handleVerdictLocked(payload); break;
    case "execution_done":     handleExecutionDone(payload); break;
    case "action_done":        handleActionDone(payload); break;
    case "case_closed":        handleCaseClosed(payload); break;
    case "error":              appendTrace(`ERROR: [${payload.where}] ${payload.message}`); break;
  }
}

// ---------------------------------------------------------------------------
// Event Handlers
// ---------------------------------------------------------------------------

function handleCaseIngested(payload) {
  document.querySelectorAll(".order-id").forEach(el => el.innerText = payload.order_id);
  appendTrace(`Case Ingested: #${payload.order_id} — "${payload.symptom}"`);
}

function handleHypothesesReady(payload) {
  const container = document.getElementById("hypotheses-container");
  container.innerHTML = "";

  payload.hypotheses.forEach(h => {
    const note = document.createElement("div");
    note.id = `hypo-${h.id}`;
    note.className = "hypothesis-note relative p-4 max-w-xs text-xs font-typewriter rounded shadow-lg";
    const rotation = (Math.random() * 3 - 1.5).toFixed(1);
    note.style.transform = `rotate(${rotation}deg)`;
    note.innerHTML = `
      <div class="font-bold mb-1">${h.label}</div>
      <div class="text-[11px] opacity-80 mb-2">${h.rationale}</div>
      <div class="text-[10px] opacity-60">Investigator: ${h.investigator}</div>
    `;
    container.appendChild(note);
  });

  appendTrace(`Formulated ${payload.hypotheses.length} hypotheses.`);
  setTimeout(drawAllStrings, 200);
}

function handleInvestigatorStart(payload) {
  appendTrace(`Investigator [${payload.investigator}] → ${payload.hypothesis_id}`);

  const portalMap = {
    "gst": "portal-gst",
    "inventory": "portal-tally",
    "warehouse": "portal-tally",
    "transport": "portal-transport",
    "delhivery": "portal-delhivery",
    "query_gst": "portal-gst",
    "query_inventory": "portal-tally",
    "query_tally": "portal-tally",
    "query_transport": "portal-transport",
    "query_delhivery": "portal-delhivery"
  };

  const portalId = portalMap[payload.investigator];
  if (portalId) {
    const portal = document.getElementById(portalId);
    if (portal) {
      portal.classList.add("ring-2", "ring-red-700", "ring-offset-2", "ring-offset-[#2a2118]");
      setTimeout(() => portal.classList.remove("ring-2", "ring-red-700", "ring-offset-2", "ring-offset-[#2a2118]"), 2000);
    }
  }

  const hypoNote = document.getElementById(`hypo-${payload.hypothesis_id}`);
  if (hypoNote) {
    hypoNote.classList.add("ring-2", "ring-red-700", "ring-offset-2", "ring-offset-[#2a2118]");
    setTimeout(() => hypoNote.classList.remove("ring-2", "ring-red-700", "ring-offset-2", "ring-offset-[#2a2118]"), 2000);
  }
}

function handleEvidenceFound(payload) {
  const ev = payload.evidence;
  appendTrace(`Evidence: [${payload.investigator}] ${ev.detail}`);

  const container = document.getElementById("evidence-board");
  const polaroid = document.createElement("div");
  polaroid.className = "polaroid flex flex-col items-center justify-between";
  polaroid.innerHTML = `
    <pre class="text-[8px] text-[#3d3121] overflow-hidden w-full leading-tight mb-1">${JSON.stringify(ev.raw, null, 2).substring(0, 200)}</pre>
    <div class="text-[9px] text-center text-[#3d3121] font-handwriting">${ev.detail}</div>
  `;
  container.appendChild(polaroid);
  container.scrollLeft = container.scrollWidth;
}

function handleHypothesisRuledOut(payload) {
  appendTrace(`Ruled Out: ${payload.hypothesis_id} by ${payload.by_evidence_source}`);
  const note = document.getElementById(`hypo-${payload.hypothesis_id}`);
  if (note) {
    note.classList.add("ruled-out");
  }

  Object.keys(stringPaths).forEach(key => {
    if (key.endsWith(`-${payload.hypothesis_id}`)) {
      snapString(key);
    }
  });
}

function handlePortalStamped(payload) {
  const stamp = payload.stamp;
  appendTrace(`Portal [${payload.portal}] → ${stamp.verdict}: ${stamp.reason}`);

  const portalMap = {
    "tally": "portal-tally",
    "gst": "portal-gst",
    "delhivery": "portal-delhivery",
    "transport": "portal-transport"
  };

  const portalId = portalMap[payload.portal];
  if (portalId) {
    const portal = document.getElementById(portalId);
    if (!portal) return;
    const slot = portal.querySelector(".stamp-slot");
    if (!slot) return;

    slot.innerHTML = "";
    const stampEl = document.createElement("div");
    const typeClass = stamp.verdict === "TRUE" ? "stamp-true" :
                      stamp.verdict === "STALE" ? "stamp-stale" : "stamp-misleading";
    stampEl.className = `rubber-stamp ${typeClass}`;
    stampEl.innerText = stamp.verdict.toUpperCase();
    stampEl.title = stamp.reason;
    slot.appendChild(stampEl);
  }
}

function handleChallengeStart(payload) {
  appendTrace(`Challenger: ${payload.attack_preview}`);

  const overlay = document.getElementById("challenger-overlay");
  const threatText = document.getElementById("challenger-threat-text");
  threatText.innerText = payload.attack_preview;
  overlay.classList.remove("opacity-0", "pointer-events-none");
}

function handleChallengeResult(payload) {
  appendTrace(`Challenge: survived=${payload.survived}, delta=${payload.confidence_delta}`);

  const overlay = document.getElementById("challenger-overlay");
  const box = document.getElementById("challenger-alert-box");

  document.body.classList.add("screen-shake");

  const stamp = document.createElement("div");
  stamp.className = "rubber-stamp stamp-true mt-4 text-3xl transform rotate-[-12deg]";
  stamp.innerText = payload.survived ? "CLEARED" : "REFUTED";
  box.appendChild(stamp);

  setTimeout(() => {
    document.body.classList.remove("screen-shake");
    overlay.classList.add("opacity-0", "pointer-events-none");
    stamp.remove();
  }, 1800);
}

function handleApprovalRequired(payload) {
  appendTrace("Approval Gate: awaiting human verification.");
  const banner = document.getElementById("approval-banner");
  document.getElementById("approval-proposed-action").innerText = payload.proposed_action;
  banner.classList.remove("translate-y-full");
}

function handleVerdictLocked(payload) {
  appendTrace(`Verdict Locked: ${payload.verdict.root_cause} (${(payload.verdict.confidence * 100).toFixed(0)}%)`);

  // Update verdict panel (always visible in right rail)
  document.getElementById("verdict-root-cause").innerText = `Verdict: ${payload.verdict.root_cause}`;
  document.getElementById("verdict-confidence").innerText = `${(payload.verdict.confidence * 100).toFixed(0)}%`;

  // Ruled out list
  const ruledOutUl = document.getElementById("verdict-ruled-out");
  ruledOutUl.innerHTML = "";
  (payload.verdict.ruled_out || []).forEach(h => {
    const li = document.createElement("li");
    li.innerText = h;
    ruledOutUl.appendChild(li);
  });

  // FIX: verdict-folder uses 'hidden' class in HTML (not translate-y-full)
  const folder = document.getElementById("verdict-folder");
  folder.classList.remove("hidden");

  // FIX: removed confidence-ellipse null-ref (element doesn't exist in HTML)
  // The confidence is already shown as text above — no SVG needed
}

function handleExecutionDone(payload) {
  const exec = payload.execution;
  appendTrace(`Executed: ${exec.action} — verified: ${exec.verified}`);

  const container = document.getElementById("verdict-execution");
  container.innerHTML = `
    <div><strong>Action:</strong> ${exec.action}</div>
    <div><strong>Verified:</strong> ${exec.verified ? "✓ YES" : "✗ NO"}</div>
    <div><strong>Change:</strong> ${JSON.stringify(exec.before)} → ${JSON.stringify(exec.after)}</div>
  `;

  // Flip e-way card on GST portal
  if (exec.action && (exec.action.includes("eway_bill") || exec.action.includes("renew"))) {
    const card = document.getElementById("portal-gst");
    if (card) {
      card.style.transform = "rotateY(180deg)";
      setTimeout(() => {
        card.style.transform = "rotate(1.5deg)";
        const ewayEl = document.getElementById("gst-eway-status");
        if (ewayEl) {
          ewayEl.innerText = (exec.after && exec.after.eway_bill) || "renewal_requested";
          ewayEl.className = "text-[#2d6a4f] font-bold";
        }
      }, 350);
    }
  }
}

function handleActionDone(payload) {
  const act = payload.action;
  appendTrace(`Action: [${act.type}] ${act.status}${act.error ? " — " + act.error : ""}`);

  const container = document.getElementById("verdict-actions");
  const p = document.createElement("div");
  p.className = "border-t border-[#d5c9ad]/40 pt-1 mt-1";
  const statusColor = act.status === "sent" || act.status === "drafted" || act.status === "done"
    ? "text-green-400" : "text-red-400";
  p.innerHTML = `
    <span class="font-bold">${act.type.toUpperCase()}:</span>
    <span class="${statusColor}">${act.status}</span>
    ${act.ref ? `<span class="opacity-60 text-[9px]"> · ${act.ref}</span>` : ""}
    ${act.error ? `<div class="text-red-400 text-[9px]">${act.error}</div>` : ""}
  `;
  container.appendChild(p);
}

function handleCaseClosed(payload) {
  appendTrace(`Case CLOSED — ${payload.wall_clock_s}s elapsed`);
  document.getElementById("wall-clock").innerText = `${payload.wall_clock_s.toFixed(2)}s`;
  clearInterval(timerInterval);

  if (eventSource) eventSource.close();

  document.getElementById("server-status").innerText = "CLOSED";
  document.getElementById("server-status").className = "font-mono text-green-500";

  loadCases();
}

// ---------------------------------------------------------------------------
// Resume Interrupt (HITL decision)
// ---------------------------------------------------------------------------

async function sendApproval(approved) {
  const banner = document.getElementById("approval-banner");
  banner.classList.add("translate-y-full");

  try {
    await fetch(`/api/approve/${activeCaseId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved })
    });
    appendTrace(`Decision sent: Approved=${approved}`);
  } catch (err) {
    console.error("Decision post failed", err);
  }
}

// ---------------------------------------------------------------------------
// Case Drawer — FIX: adds 🔍 INVESTIGATE button for pending cases
// ---------------------------------------------------------------------------

async function loadCases() {
  try {
    const res = await fetch("/api/cases");
    const data = await res.json();
    const container = document.getElementById("cases-list");
    container.innerHTML = "";

    if (!data.cases || data.cases.length === 0) {
      container.innerHTML = '<div class="text-xs text-zinc-600 font-mono px-2 py-4 text-center">No cases yet</div>';
      return;
    }

    data.cases.forEach(c => {
      const el = document.createElement("div");

      let statusColor = "bg-zinc-800 text-zinc-400";
      if (c.status === "closed") statusColor = "bg-green-950/40 text-green-400 border border-green-900/30";
      if (c.status === "pending") statusColor = "bg-amber-950/40 text-amber-400 border border-amber-900/30";
      if (c.status === "awaiting_approval") statusColor = "bg-red-950/40 text-red-400 border border-red-900/30";

      el.className = `p-3 rounded cursor-pointer hover:bg-zinc-800/50 transition-all border border-zinc-800/60 ${c.case_id === activeCaseId ? "ring-1 ring-red-700" : ""}`;

      // FIX: Pending cases get a clickable INVESTIGATE button
      const pendingButton = c.status === "pending"
        ? `<button
             onclick="event.stopPropagation(); triggerPendingCase('${c.case_id}')"
             class="mt-2 w-full text-[9px] font-typewriter tracking-wider bg-[#b93324] hover:bg-[#9a2a1e] text-white py-1.5 rounded transition-colors"
           >🔍 INVESTIGATE</button>`
        : "";

      el.innerHTML = `
        <div class="flex items-center justify-between mb-1">
          <span class="font-mono text-[10px] text-zinc-300 truncate">#${c.order_id}</span>
          <span class="text-[9px] px-1.5 py-0.5 rounded font-mono ${statusColor}">${c.status}</span>
        </div>
        <div class="text-[10px] text-zinc-500 font-mono truncate">${c.case_id}</div>
        ${c.verdict_summary ? `<div class="text-[9px] text-zinc-600 mt-1 truncate">${c.verdict_summary}</div>` : ""}
        ${pendingButton}
      `;

      el.addEventListener("click", () => {
        if (c.status === "closed") {
          startLiveStream(c.case_id, true);
        } else if (c.status === "active" || c.status === "awaiting_approval") {
          startLiveStream(c.case_id, false);
        }
      });

      container.appendChild(el);
    });
  } catch (err) {
    console.error("Load cases failed", err);
  }
}

// ---------------------------------------------------------------------------
// String Drawing overlays (SVG)
// ---------------------------------------------------------------------------

function drawAllStrings() {
  const overlay = document.getElementById("string-overlay");
  overlay.innerHTML = "";
  stringPaths = {};

  const hypotheses = document.querySelectorAll(".hypothesis-note");
  if (hypotheses.length === 0) return;

  const investigatorPortals = {
    "gst": "portal-gst",
    "inventory": "portal-tally",
    "warehouse": "portal-tally",
    "transport": "portal-transport",
    "delhivery": "portal-delhivery"
  };

  const portalMap = {
    "h_eway_bill_expired": "gst",
    "h_inventory_damage": "inventory",
    "h_dispatch_failure": "warehouse",
    "h_transport_breakdown": "transport"
  };

  hypotheses.forEach(note => {
    const hypoId = note.id.replace("hypo-", "");
    const inv = portalMap[hypoId];
    const portalId = investigatorPortals[inv];

    if (portalId) {
      const portalEl = document.getElementById(portalId);
      if (portalEl) {
        drawStringBetween(portalId, note.id, portalEl, note);
      }
    }
  });
}

function drawStringBetween(portalId, hypoId, portalEl, hypoEl) {
  const overlay = document.getElementById("string-overlay");
  const overlayRect = overlay.getBoundingClientRect();

  const pRect = portalEl.getBoundingClientRect();
  const hRect = hypoEl.getBoundingClientRect();

  const x1 = (pRect.left + pRect.right) / 2 - overlayRect.left;
  const y1 = pRect.bottom - overlayRect.top;
  const x2 = (hRect.left + hRect.right) / 2 - overlayRect.left;
  const y2 = hRect.top - overlayRect.top;

  const cx = (x1 + x2) / 2;
  const cy = (y1 + y2) / 2 + 30;

  const pathKey = `${portalId}-${hypoId.replace("hypo-", "")}`;
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", `M ${x1} ${y1} Q ${cx} ${cy} ${x2} ${y2}`);
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "#b93324");
  path.setAttribute("stroke-width", "2");
  path.setAttribute("class", "string-path");
  path.style.opacity = "0.75";

  overlay.appendChild(path);
  stringPaths[pathKey] = path;
}

function snapString(pathKey) {
  const path = stringPaths[pathKey];
  if (!path) return;

  const d = path.getAttribute("d");
  const parts = d.match(/M ([\d.]+) ([\d.]+) Q ([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+)/);
  if (!parts) return;

  const [, x1, y1, cx, cy, x2, y2] = parts.map(Number);
  path.setAttribute("d", `M ${x1} ${y1} Q ${cx} ${Number(cy) + 90} ${x2} ${Number(y2) + 35}`);
  path.setAttribute("stroke-dasharray", "8, 6");
  path.style.opacity = "0.3";
  path.style.transition = "all 0.8s cubic-bezier(0.25, 0.46, 0.45, 0.94)";
}

function redrawAllStrings() {
  drawAllStrings();
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function resetBoard() {
  document.querySelectorAll(".stamp-slot").forEach(el => el.innerHTML = "");
  document.querySelectorAll(".portal-card").forEach(el => {
    el.style.transform = "";
  });
  document.getElementById("hypotheses-container").innerHTML = "";
  document.getElementById("evidence-board").innerHTML = "";
  document.getElementById("trace-lines").innerHTML = "";
  document.getElementById("verdict-execution").innerHTML = "";
  document.getElementById("verdict-actions").innerHTML = "";
  document.getElementById("verdict-execution-detail").innerHTML = "";
  document.getElementById("verdict-actions-detail").innerHTML = "";
  document.getElementById("approval-banner").classList.add("translate-y-full");
  // FIX: verdict-folder uses 'hidden' class (not translate-y-full)
  document.getElementById("verdict-folder").classList.add("hidden");
  document.getElementById("challenger-overlay").classList.add("opacity-0", "pointer-events-none");

  const overlay = document.getElementById("string-overlay");
  overlay.innerHTML = "";
  stringPaths = {};

  document.getElementById("wall-clock").innerText = "0.00s";
  document.getElementById("llm-cost").innerText = "$0.0000";
}

function mapEventToPhase(ev) {
  const map = {
    "case_ingested": "INGESTING",
    "hypotheses_ready": "INVESTIGATING",
    "investigator_start": "INVESTIGATING",
    "evidence_found": "INVESTIGATING",
    "hypothesis_ruled_out": "INVESTIGATING",
    "portal_stamped": "INVESTIGATING",
    "verdict_draft": "INVESTIGATING",
    "challenge_start": "CHALLENGING",
    "challenge_result": "CHALLENGING",
    "approval_required": "AWAITING APPROVAL",
    "verdict_locked": "EXECUTING",
    "execution_done": "EXECUTING",
    "action_done": "EXECUTING",
    "case_closed": "CLOSED"
  };
  return map[ev] || null;
}

function appendTrace(text) {
  const lines = document.getElementById("trace-lines");
  const p = document.createElement("div");
  const ts = new Date().toLocaleTimeString("en-IN", {hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit"});
  p.innerText = `[${ts}] ${text}`;
  lines.appendChild(p);
  const logBox = document.getElementById("trace-log");
  logBox.scrollTop = logBox.scrollHeight;
}

function updateClock() {
  if (!startTime) return;
  const elapsed = (Date.now() - startTime) / 1000;
  document.getElementById("wall-clock").innerText = `${elapsed.toFixed(2)}s`;
}
