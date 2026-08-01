/* ORBIT Evidence Board — Core JS App logic */

let eventSource = null;
let activeCaseId = null;
let timerInterval = null;
let startTime = null;
let stringPaths = {}; // maps portalId-hypoId -> SVG path element

document.addEventListener("DOMContentLoaded", () => {
  initApp();
});

function initApp() {
  // Event listeners
  document.getElementById("investigate-btn").addEventListener("click", triggerManualInvestigation);
  document.getElementById("refresh-cases-btn").addEventListener("click", loadCases);
  document.getElementById("approve-btn").addEventListener("click", () => sendApproval(true));
  document.getElementById("reject-btn").addEventListener("click", () => sendApproval(false));

  // Initialize closed cases list
  loadCases();

  // Handle window resizing to redraw strings
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
  // Clear previous state
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

  // Update wall clock & cost if present in payload
  if (payload.wall_clock_s) {
    document.getElementById("wall-clock").innerText = `${payload.wall_clock_s.toFixed(2)}s`;
  }
  if (payload.llm_cost_usd !== undefined) {
    document.getElementById("llm-cost").innerText = `$${payload.llm_cost_usd.toFixed(4)}`;
  }

  // Update phase banner
  const phase = mapEventToPhase(ev);
  if (phase) {
    const banner = document.getElementById("phase-banner");
    banner.innerText = phase;
    // Set banner color based on phase
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

  // Route event
  switch (ev) {
    case "case_ingested":
      handleCaseIngested(payload);
      break;
    case "hypotheses_ready":
      handleHypothesesReady(payload);
      break;
    case "investigator_start":
      handleInvestigatorStart(payload);
      break;
    case "evidence_found":
      handleEvidenceFound(payload);
      break;
    case "hypothesis_ruled_out":
      handleHypothesisRuledOut(payload);
      break;
    case "portal_stamped":
      handlePortalStamped(payload);
      break;
    case "verdict_draft":
      appendTrace(`Drafting verdict: ${payload.partial_root_cause}`);
      break;
    case "challenge_start":
      handleChallengeStart(payload);
      break;
    case "challenge_result":
      handleChallengeResult(payload);
      break;
    case "approval_required":
      handleApprovalRequired(payload);
      break;
    case "verdict_locked":
      handleVerdictLocked(payload);
      break;
    case "execution_done":
      handleExecutionDone(payload);
      break;
    case "action_done":
      handleActionDone(payload);
      break;
    case "case_closed":
      handleCaseClosed(payload);
      break;
    case "error":
      appendTrace(`ERROR: [${payload.where}] ${payload.message}`);
      break;
  }
}

// ---------------------------------------------------------------------------
// Event Handlers
// ---------------------------------------------------------------------------

function handleCaseIngested(payload) {
  document.querySelectorAll(".order-id").forEach(el => el.innerText = payload.order_id);
  appendTrace(`Case Ingested: #${payload.order_id} Symptom: "${payload.symptom}"`);
}

function handleHypothesesReady(payload) {
  const container = document.getElementById("hypotheses-container");
  container.innerHTML = "";

  payload.hypotheses.forEach(h => {
    const note = document.createElement("div");
    note.id = `hypo-${h.id}`;
    note.className = "hypothesis-note relative p-4 max-w-xs text-xs font-typewriter rounded shadow-lg transform rotate-1";
    // Slight random rotation
    const rotation = (Math.random() * 3 - 1.5).toFixed(1);
    note.style.transform = `rotate(${rotation}deg)`;
    note.innerHTML = `
      <div class="pushpin"></div>
      <div class="font-bold border-b border-[#bba482] pb-1 mb-2 uppercase text-[10px] text-zinc-700">${h.label}</div>
      <div class="text-[#3d3121] leading-relaxed mb-1">${h.rationale}</div>
      <div class="text-[9px] text-[#806f57] font-sans font-semibold mt-2">Investigator: ${h.investigator}</div>
    `;
    container.appendChild(note);
  });

  appendTrace(`Formulated ${payload.hypotheses.length} hypotheses.`);
  
  // Wait for DOM layout to complete, then draw strings
  setTimeout(drawAllStrings, 200);
}

function handleInvestigatorStart(payload) {
  appendTrace(`Investigator [${payload.investigator}] starting for hypothesis ${payload.hypothesis_id}`);
  
  // Highlight target portal and hypothesis note
  const portalMap = {
    "gst": "portal-gst",
    "inventory": "portal-tally",
    "warehouse": "portal-tally",
    "transport": "portal-transport",
    "delhivery": "portal-delhivery"
  };

  const portalId = portalMap[payload.investigator];
  if (portalId) {
    const portal = document.getElementById(portalId);
    portal.classList.add("ring-2", "ring-red-700", "ring-offset-2", "ring-offset-[#2a2118]");
    setTimeout(() => portal.classList.remove("ring-2", "ring-red-700"), 2000);
  }

  const hypoNote = document.getElementById(`hypo-${payload.hypothesis_id}`);
  if (hypoNote) {
    hypoNote.classList.add("ring-2", "ring-red-700", "ring-offset-2", "ring-offset-[#2a2118]");
    setTimeout(() => hypoNote.classList.remove("ring-2", "ring-red-700"), 2000);
  }
}

function handleEvidenceFound(payload) {
  const ev = payload.evidence;
  appendTrace(`Evidence: [${payload.investigator}] ${ev.detail}`);

  // Create Polaroid
  const container = document.getElementById("evidence-board");
  const polaroid = document.createElement("div");
  polaroid.className = "polaroid flex flex-col items-center justify-between";
  polaroid.innerHTML = `
    <div class="w-full aspect-[4/3] bg-zinc-800 flex flex-col justify-center items-center p-2 rounded border border-zinc-900 mb-2 overflow-hidden">
      <div class="text-[8px] font-mono text-zinc-400 w-full break-all whitespace-pre-wrap">${JSON.stringify(ev.raw, null, 2)}</div>
    </div>
    <div class="font-typewriter text-[9px] text-zinc-700 leading-tight text-center">${ev.detail}</div>
  `;
  container.appendChild(polaroid);
  container.scrollLeft = container.scrollWidth;
}

function handleHypothesisRuledOut(payload) {
  appendTrace(`Hypothesis Ruled Out: ${payload.hypothesis_id} by ${payload.by_evidence_source}`);
  const note = document.getElementById(`hypo-${payload.hypothesis_id}`);
  if (note) {
    note.classList.add("ruled-out");
  }

  // Snap strings connected to this hypothesis
  Object.keys(stringPaths).forEach(key => {
    if (key.endsWith(`-${payload.hypothesis_id}`)) {
      snapString(key);
    }
  });
}

function handlePortalStamped(payload) {
  const stamp = payload.stamp;
  appendTrace(`Portal [${payload.portal}] stamp: ${stamp.verdict} (${stamp.reason})`);

  const portalMap = {
    "tally": "portal-tally",
    "gst": "portal-gst",
    "delhivery": "portal-delhivery",
    "transport": "portal-transport"
  };

  const portalId = portalMap[payload.portal];
  if (portalId) {
    const portal = document.getElementById(portalId);
    const slot = portal.querySelector(".stamp-slot");
    
    // Clear previous
    slot.innerHTML = "";
    
    const stampEl = document.createElement("div");
    const label = stamp.verdict.toUpperCase();
    const typeClass = stamp.verdict === "TRUE" ? "stamp-true" : 
                      stamp.verdict === "STALE" ? "stamp-stale" : "stamp-misleading";

    stampEl.className = `rubber-stamp ${typeClass}`;
    stampEl.innerText = label;
    slot.appendChild(stampEl);
  }
}

function handleChallengeStart(payload) {
  appendTrace(`Challenger threat assessment: ${payload.attack_preview}`);
  
  const overlay = document.getElementById("challenger-overlay");
  const threatText = document.getElementById("challenger-threat-text");
  
  threatText.innerText = payload.attack_preview;
  overlay.classList.remove("opacity-0", "pointer-events-none");
}

function handleChallengeResult(payload) {
  const res = payload;
  appendTrace(`Challenge ended. Survived: ${res.survived}, Delta: ${res.confidence_delta}`);
  
  const overlay = document.getElementById("challenger-overlay");
  const box = document.getElementById("challenger-alert-box");

  // Play Screen Shake & Stamp Cleared
  document.body.classList.add("screen-shake");
  
  const stamp = document.createElement("div");
  stamp.className = "rubber-stamp stamp-true mt-4 text-3xl transform rotate-[-12deg]";
  stamp.innerText = res.survived ? "CLEARED" : "FAILED";
  box.appendChild(stamp);

  setTimeout(() => {
    document.body.classList.remove("screen-shake");
    overlay.classList.add("opacity-0", "pointer-events-none");
    stamp.remove();
  }, 1800);
}

function handleApprovalRequired(payload) {
  appendTrace("Approval Gate Interrupt: awaiting human verification.");
  const banner = document.getElementById("approval-banner");
  document.getElementById("approval-proposed-action").innerText = payload.proposed_action;
  banner.classList.remove("translate-y-full");
}

function handleVerdictLocked(payload) {
  appendTrace(`Verdict Locked: ${payload.verdict.root_cause}`);
  
  const folder = document.getElementById("verdict-folder");
  document.getElementById("verdict-root-cause").innerText = `Verdict: ${payload.verdict.root_cause}`;
  document.getElementById("verdict-confidence").innerText = `${(payload.verdict.confidence * 100).toFixed(0)}%`;

  // Ruled out list
  const ruledOutUl = document.getElementById("verdict-ruled-out");
  ruledOutUl.innerHTML = "";
  payload.verdict.ruled_out.forEach(h => {
    const li = document.createElement("li");
    li.innerText = h;
    ruledOutUl.appendChild(li);
  });

  // Slide up manila folder
  folder.classList.remove("translate-y-full");

  // Start confidence handwriting circle SVG animation
  const ellipse = document.getElementById("confidence-ellipse");
  ellipse.style.strokeDashoffset = "0";
  ellipse.style.transition = "stroke-dashoffset 1s ease-out";
}

function handleExecutionDone(payload) {
  const exec = payload.execution;
  appendTrace(`Executed: ${exec.action}. Verified: ${exec.verified}`);

  const container = document.getElementById("verdict-execution");
  container.innerHTML = `
    <div><strong>Action:</strong> ${exec.action}</div>
    <div><strong>Verified:</strong> ${exec.verified ? "YES" : "NO"}</div>
    <div><strong>State change:</strong> ${JSON.stringify(exec.before)} ➔ ${JSON.stringify(exec.after)}</div>
  `;

  // Flip e-way card if matching
  if (exec.action.includes("eway_bill") || exec.after.eway_bill) {
    const card = document.getElementById("portal-gst");
    if (card) {
      card.style.transform = "rotateY(180deg)";
      setTimeout(() => {
        card.style.transform = "rotate(1.5deg)";
        document.getElementById("gst-eway-status").innerText = exec.after.eway_bill || "renewed";
        document.getElementById("gst-eway-status").className = "text-[#2d6a4f] font-bold";
      }, 300);
    }
  }
}

function handleActionDone(payload) {
  const act = payload.action;
  appendTrace(`Action fired: [${act.type}] status=${act.status}`);

  const container = document.getElementById("verdict-actions");
  const p = document.createElement("div");
  p.className = "border-t border-[#d5c9ad]/40 pt-1 mt-1";
  p.innerHTML = `
    <div><strong>${act.type.toUpperCase()}:</strong> ${act.status}</div>
    ${act.ref ? `<div class="text-[10px] text-zinc-500 break-all">Ref: ${act.ref}</div>` : ""}
    ${act.error ? `<div class="text-[10px] text-red-700">Error: ${act.error}</div>` : ""}
  `;
  container.appendChild(p);
}

function handleCaseClosed(payload) {
  appendTrace(`Case closed. Time elapsed: ${payload.wall_clock_s}s`);
  clearInterval(timerInterval);
  eventSource.close();
  
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
// Case Drawer
// ---------------------------------------------------------------------------

async function loadCases() {
  try {
    const res = await fetch("/api/cases");
    const data = await res.json();
    const container = document.getElementById("cases-list");
    container.innerHTML = "";

    data.cases.forEach(c => {
      const el = document.createElement("div");
      
      let statusColor = "bg-zinc-800 text-zinc-400";
      if (c.status === "closed") statusColor = "bg-green-950/40 text-green-400 border border-green-900/30";
      if (c.status === "pending") statusColor = "bg-amber-950/40 text-amber-400 border border-amber-900/30";
      if (c.status === "awaiting_approval") statusColor = "bg-red-950/40 text-red-400 border border-red-900/30";

      el.className = `p-3 rounded cursor-pointer hover:bg-zinc-800 transition-all border border-zinc-800 ${c.case_id === activeCaseId ? 'ring-1 ring-red-700' : ''}`;
      el.innerHTML = `
        <div class="flex justify-between items-center mb-1">
          <span class="font-mono text-xs text-white font-semibold">${c.case_id}</span>
          <span class="text-[8px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded ${statusColor}">${c.status}</span>
        </div>
        <div class="text-[10px] text-zinc-500">Order: #${c.order_id}</div>
        ${c.verdict_summary ? `<div class="text-[9px] font-typewriter text-zinc-400 mt-1 border-t border-zinc-800/50 pt-1">${c.verdict_summary}</div>` : ""}
      `;

      el.addEventListener("click", () => {
        startLiveStream(c.case_id, c.status === "closed");
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

  // Build connection mapping based on hypotheses in DOM
  const hypotheses = document.querySelectorAll(".hypothesis-note");
  if (hypotheses.length === 0) return;

  // Map investigator strings to specific portal cards
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

  // Anchors: center-bottom of portal card, center-top of hypothesis card
  const x1 = (pRect.left + pRect.right) / 2 - overlayRect.left;
  const y1 = pRect.bottom - overlayRect.top;

  const x2 = (hRect.left + hRect.right) / 2 - overlayRect.left;
  const y2 = hRect.top - overlayRect.top;

  // Curvature (catenary sag)
  const cx = (x1 + x2) / 2;
  const cy = (y1 + y2) / 2 + 30; // slight sag

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
  const coords = d.split(" ");
  // Parse M x1 y1 Q cx cy x2 y2
  const x1 = parseFloat(coords[1]);
  const y1 = parseFloat(coords[2]);
  const cx = parseFloat(coords[4]);
  const cy = parseFloat(coords[5]);
  const x2 = parseFloat(coords[6]);
  const y2 = parseFloat(coords[7]);

  // Make it look loose: heavily increased sag, dashed pattern, low opacity
  path.setAttribute("d", `M ${x1} ${y1} Q ${cx} ${cy + 90} ${x2} ${y2 + 35}`);
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
  // Clear stamps
  document.querySelectorAll(".stamp-slot").forEach(el => el.innerHTML = "");
  // Reset rotated 3D cards
  document.querySelectorAll(".portal-card").forEach(el => {
    el.style.transform = "";
  });
  // Clear hypotheses notes
  document.getElementById("hypotheses-container").innerHTML = "";
  // Clear polaroids
  document.getElementById("evidence-board").innerHTML = "";
  // Clear logs
  document.getElementById("trace-lines").innerHTML = "";
  // Reset overlays & sliders
  document.getElementById("approval-banner").classList.add("translate-y-full");
  document.getElementById("verdict-folder").classList.add("translate-y-full");
  document.getElementById("challenger-overlay").classList.add("opacity-0", "pointer-events-none");
  
  // Clear SVG strings
  const overlay = document.getElementById("string-overlay");
  overlay.innerHTML = "";
  stringPaths = {};

  // Reset clock & cost
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
  p.innerText = text;
  lines.appendChild(p);
  const logBox = document.getElementById("trace-log");
  logBox.scrollTop = logBox.scrollHeight;
}

function updateClock() {
  if (!startTime) return;
  const elapsed = (Date.now() - startTime) / 1000;
  document.getElementById("wall-clock").innerText = `${elapsed.toFixed(2)}s`;
}
