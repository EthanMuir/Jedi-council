// THE CHAMBER -- ticker input, horizon toggles, the council ring, and the
// live SSE-driven deliberation.

const TIER_I_SEATS = [
  { id: 'technician', title: 'Keeper of the Charts' },
  { id: 'fundamentalist', title: 'Keeper of the Ledgers' },
  { id: 'catalyst_seer', title: 'Watcher of Omens' },
  { id: 'insider_reader', title: 'Student of the Inner Circle' },
  { id: 'senate_watcher', title: 'Reader of the Republic' },
  { id: 'flow_cartographer', title: 'Reader of Great Tides' },
  { id: 'oracle_options', title: 'Reader of Probabilities' },
  { id: 'macro_sage', title: 'Keeper of the Outer Rim' },
  { id: 'cross_market', title: 'Reader of Distant Stars' },
  { id: 'estimate_scribe', title: 'Keeper of Expectations' },
  { id: 'transcript_linguist', title: 'Listener to the Council of Officers' },
  { id: 'structure_archivist', title: 'Keeper of Charters' },
];

let selectedHorizon = '1w';
let seatDetails = {}; // seat_id -> full seat_result payload, for the holo-panel

// Navigating to another screen and back used to reset the Chamber to a
// blank "AWAITING DELIBERATION" state every time, discarding whatever had
// just been shown -- even though the deliberation itself keeps running
// server-side regardless of whether this tab is still listening (the SSE
// stream's backing task isn't cancelled by a client disconnect). Everything
// needed to re-render the last-seen state is snapshotted into
// sessionStorage on every update and restored on load, so leaving the
// Chamber and coming back shows what was last visible instead of nothing.
// sessionStorage (not localStorage) on purpose: this is "don't lose it
// within this session", not permanent history -- the Crypt is already the
// permanent record of completed deliberations.
const CHAMBER_STATE_KEY = 'chamberState';
let debateRoundsLog = [];
let lastRealityAnchor = null;
let lastGatesPayload = null;
let lastRiskPayload = null;
let lastGrandMaster = null;
let lastStatusText = '';

function saveChamberState() {
  const state = {
    ticker: document.getElementById('ticker-input')?.value ?? '',
    horizon: selectedHorizon,
    statusText: lastStatusText,
    seatDetails,
    realityAnchor: lastRealityAnchor,
    debateRounds: debateRoundsLog,
    gatesPayload: lastGatesPayload,
    riskPayload: lastRiskPayload,
    grandMaster: lastGrandMaster,
  };
  try {
    sessionStorage.setItem(CHAMBER_STATE_KEY, JSON.stringify(state));
  } catch (e) {
    /* storage unavailable (private browsing, quota, ...) -- degrade to the old behaviour */
  }
}

function restoreChamberState() {
  let raw;
  try {
    raw = sessionStorage.getItem(CHAMBER_STATE_KEY);
  } catch (e) {
    return false;
  }
  if (!raw) return false;

  let state;
  try {
    state = JSON.parse(raw);
  } catch (e) {
    return false;
  }

  if (state.ticker) document.getElementById('ticker-input').value = state.ticker;
  if (state.horizon) {
    selectedHorizon = state.horizon;
    document.querySelectorAll('#horizon-toggle .toggle-option').forEach(b => {
      b.classList.toggle('active', b.dataset.horizon === state.horizon);
    });
  }

  resetRing(); // lays out the idle/deliberating baseline for this horizon first
  for (const payload of Object.values(state.seatDetails || {})) {
    updateSeatChair(payload, /* silent */ true);
  }
  if (state.realityAnchor) renderRealityAnchor(state.realityAnchor);
  for (const payload of state.debateRounds || []) appendDebateRound(payload);
  if (state.gatesPayload || state.riskPayload) {
    renderAuditPanel(state.gatesPayload, state.riskPayload);
  }
  if (state.grandMaster) renderGrandMaster(state.grandMaster);

  const status = document.getElementById('status-line');
  if (state.statusText) {
    // The stream itself can't be resumed client-side (a page navigation
    // aborts the fetch), so this is honest about being a snapshot, not a
    // live view -- the deliberation may already have finished server-side
    // even if it looked mid-flight when you left.
    status.textContent = `${state.statusText} (restored -- may have finished since you left)`;
  }
  lastStatusText = state.statusText || '';
  return true;
}

function layoutRing() {
  const ring = document.getElementById('chamber-ring');
  // Seat Wizards' chairs are taller than every other chair style (a
  // standing figure needs more room than a flat bust-bar), so vertically
  // adjacent chairs need more separation too or they overlap -- widen the
  // ring itself for this style rather than shrinking the character back
  // down to fit the old spacing.
  const baseRadius = getSeatChairStyle() === 'wizard' ? 282 : 260;
  const radius = ring.clientWidth < 500 ? ring.clientWidth * 0.38 : baseRadius;
  const n = TIER_I_SEATS.length;

  TIER_I_SEATS.forEach((seat, i) => {
    const angle = (i / n) * 2 * Math.PI - Math.PI / 2;
    const x = Math.cos(angle) * radius;
    const y = Math.sin(angle) * radius;

    const chair = document.createElement('div');
    chair.className = `${chairClassBase()} state-idle`;
    chair.id = `chair-${seat.id}`;
    chair.style.left = `calc(50% + ${x}px)`;
    chair.style.top = `calc(50% + ${y}px)`;
    chair.innerHTML = getSeatChairStyle() === 'wizard'
      ? `
        <div class="wiz-chair-stage">
          <div class="wiz-chair-glow"></div>
          <canvas class="wiz-chair-canvas" id="wizcanvas-${seat.id}" width="36" height="46"></canvas>
        </div>
        <div class="seat-title">${seat.title}</div>
        <div class="seat-vote dim">idle</div>
      `
      : `
        <div class="seat-bust"><div class="seat-bust-fill" id="fill-${seat.id}"></div></div>
        <div class="seat-title">${seat.title}</div>
        <div class="seat-vote dim">idle</div>
      `;
    chair.onclick = () => openHoloPanel(seat.id, seat.title);
    ring.appendChild(chair);
    redrawWizardChair(seat.id, 'idle');
  });
}

// No-op unless the wizard chair style is selected (and its canvas exists
// yet) -- every state-change call site below calls this unconditionally
// rather than checking the style itself, the same way the bust-fill
// updates already tolerate a missing #fill-<id> element for non-classic
// styles.
function redrawWizardChair(seatId, chairState) {
  const canvas = document.getElementById(`wizcanvas-${seatId}`);
  if (!canvas) return;
  drawWizardBust(canvas, wizardStateFor(chairState));
}

function resetRing() {
  seatDetails = {};
  debateRoundsLog = [];
  lastRealityAnchor = null;
  lastGatesPayload = null;
  lastRiskPayload = null;
  lastGrandMaster = null;
  for (const seat of TIER_I_SEATS) {
    const chair = document.getElementById(`chair-${seat.id}`);
    const fill = document.getElementById(`fill-${seat.id}`); // absent for chair-style-wizard, by design
    if (fill) {
      fill.className = 'seat-bust-fill';
      fill.style.width = '0%';
    }
    if (!isCompetent(seat.id, selectedHorizon)) {
      // This seat has 0 competence at the selected horizon -- the backend
      // never calls it at all (see council/engine/horizons.py), so it will
      // never emit a seat_result event. Mark it up front instead of leaving
      // it stuck on "deliberating..." forever.
      chair.className = `${chairClassBase()} state-idle`;
      chair.querySelector('.seat-vote').textContent = `not called @ ${selectedHorizon}`;
      chair.querySelector('.seat-vote').className = 'seat-vote dim';
      redrawWizardChair(seat.id, 'idle');
      continue;
    }
    chair.className = `${chairClassBase()} state-deliberating`;
    chair.querySelector('.seat-vote').textContent = 'deliberating...';
    chair.querySelector('.seat-vote').className = 'seat-vote cyan';
    if (fill) fill.className = 'seat-bust-fill stage-active';
    redrawWizardChair(seat.id, 'deliberating');
  }
  setHolocronVerdict(null);
  document.getElementById('holocron-label').innerHTML = 'DELIBERATING';
  document.getElementById('reality-anchor').innerHTML = '<span class="dim">Awaiting Phase B...</span>';
  document.getElementById('dissent-map').innerHTML = '<span class="dim">Awaiting verdicts...</span>';
  document.getElementById('debate-transcript').innerHTML = '<span class="dim">Awaiting Phase C...</span>';
  const auditPanel = document.getElementById('audit-panel');
  auditPanel.innerHTML = '<span class="dim">Awaiting Phase E...</span>';
  auditPanel.dataset.gates = ''; // stale merge state from a prior run/restore must not leak in
  document.getElementById('grand-master-verdict').innerHTML = '<span class="dim">The council is deliberating...</span>';
}

// Maps a "seat_stage" event to a fill percentage. Gathering data is usually
// quick relative to the N sampled LLM calls that follow, so it only claims
// a small slice up front; the rest is spent scaling smoothly across however
// many samples this seat actually takes (n_samples_per_seat), so a seat
// sampled more times doesn't look like it's stalling between updates.
function stageToPct(payload) {
  if (payload.stage === 'gathering') return 15;
  if (payload.stage === 'deliberating') {
    const total = payload.samples_total || 1;
    const done = payload.samples_done || 0;
    return 20 + Math.round((done / total) * 70);
  }
  return 0;
}

function updateSeatProgress(payload) {
  const fill = document.getElementById(`fill-${payload.seat_id}`);
  if (!fill) return;
  fill.className = 'seat-bust-fill stage-active';
  fill.style.width = `${stageToPct(payload)}%`;
}

function updateSeatChair(payload, silent = false) {
  seatDetails[payload.seat_id] = payload;
  const chair = document.getElementById(`chair-${payload.seat_id}`);
  if (!chair) return;
  const voteEl = chair.querySelector('.seat-vote');
  const fill = document.getElementById(`fill-${payload.seat_id}`);

  if (payload.vote === 'BULLISH') {
    chair.className = `${chairClassBase()} state-bullish`;
    voteEl.textContent = `BULLISH ${payload.probability}`;
    voteEl.className = 'seat-vote status-bullish';
    if (fill) fill.className = 'seat-bust-fill vote-bullish';
    redrawWizardChair(payload.seat_id, 'bullish');
    if (!silent) AudioBlips.blip(1046, 0.05);
  } else if (payload.vote === 'BEARISH') {
    chair.className = `${chairClassBase()} state-bearish`;
    voteEl.textContent = `BEARISH ${payload.probability}`;
    voteEl.className = 'seat-vote status-bearish';
    if (fill) fill.className = 'seat-bust-fill vote-bearish';
    redrawWizardChair(payload.seat_id, 'bearish');
    if (!silent) AudioBlips.blip(392, 0.05);
  } else {
    chair.className = `${chairClassBase()} state-noread`;
    voteEl.textContent = 'NO_READ';
    voteEl.className = 'seat-vote status-noread';
    if (fill) fill.className = 'seat-bust-fill vote-noread';
    redrawWizardChair(payload.seat_id, 'noread');
    if (!silent) AudioBlips.blip(220, 0.04);
  }
  if (fill) fill.style.width = '100%';
}

function renderRealityAnchor(payload) {
  lastRealityAnchor = payload;
  document.getElementById('reality-anchor').innerHTML = `
    max plausible move: <span class="amber">${fmtPct(payload.max_plausible_move_pct)}</span><br/>
    hit-rate-up: <span class="cyan">${fmtNum(payload.hit_rate_up)}</span><br/>
    options-implied move: <span class="cyan">${fmtPct(payload.options_implied_move_pct)}</span><br/>
    ${Object.entries(payload.plausibility_flags).filter(([, f]) => f === 'IMPLAUSIBLE').map(([sid]) =>
      `<div class="crimson">&#9650; ${sid} target flagged IMPLAUSIBLE</div>`).join('') || '<span class="dim">no implausible targets</span>'}
  `;
}

function appendDebateRound(payload) {
  debateRoundsLog.push(payload);
  const el = document.getElementById('debate-transcript');
  if (el.querySelector('.dim')) el.innerHTML = '';
  const div = document.createElement('div');
  div.className = 'panel-inset';
  div.style.marginBottom = '10px';
  div.innerHTML = `
    <h3>Round ${payload.round_n}</h3>
    ${payload.bull_argument ? `<div style="margin-bottom:6px;"><span class="green">BULL:</span> ${payload.bull_argument}</div>` : ''}
    ${payload.bear_argument ? `<div style="margin-bottom:6px;"><span class="crimson">BEAR:</span> ${payload.bear_argument}</div>` : ''}
    <div class="dim">PROSECUTOR: veto=${payload.prosecutor_veto} ${payload.prosecutor_findings.map(f => `<div>&#8226; ${f}</div>`).join('')}</div>
  `;
  el.appendChild(div);
}

function renderAuditPanel(gatesPayload, riskPayload) {
  if (gatesPayload) lastGatesPayload = gatesPayload;
  if (riskPayload) lastRiskPayload = riskPayload;
  const el = document.getElementById('audit-panel');
  const existing = el.dataset.gates ? JSON.parse(el.dataset.gates) : {};
  const merged = { ...existing, ...(gatesPayload || {}), ...(riskPayload ? { risk: riskPayload } : {}) };
  el.dataset.gates = JSON.stringify(merged);

  el.innerHTML = `
    ${merged.gates_passed !== undefined ? `Gates passed: <span class="${merged.gates_passed ? 'status-bullish' : 'status-bearish'}">${merged.gates_passed}</span>` : ''}
    ${(merged.reasons || []).map(r => `<div class="crimson">&#9650; ${r}</div>`).join('')}
    ${merged.risk ? `<div style="margin-top:8px;">position size: <span class="cyan">${fmtPct(merged.risk.position_size_pct_of_book)}</span> of book</div>` : ''}
    ${merged.risk && merged.risk.concentration_warning ? `<div class="crimson">&#9650; ${merged.risk.concentration_warning}</div>` : ''}
  `;
}

// Swaps the holocron's verdict-* class in and out while leaving its
// 'holocron' base class and 'style-*' centerpiece class alone -- the
// naive holocron.className = 'holocron verdict-bullish' this replaced
// would silently wipe out whichever centerpiece style was built in, since
// that's tracked as a class too, not a separate attribute.
function setHolocronVerdict(verdictClass) {
  const holocron = document.getElementById('holocron');
  const kept = holocron.className.split(' ').filter(c => c && !c.startsWith('verdict-'));
  if (verdictClass) kept.push(verdictClass);
  holocron.className = kept.join(' ');
}

function renderGrandMaster(payload) {
  lastGrandMaster = payload;
  const label = document.getElementById('holocron-label');
  if (payload.vote === 'BULLISH') {
    setHolocronVerdict('verdict-bullish');
    label.innerHTML = `BULLISH<br/>${fmtNum(payload.confidence)}`;
  } else if (payload.vote === 'BEARISH') {
    setHolocronVerdict('verdict-bearish');
    label.innerHTML = `BEARISH<br/>${fmtNum(payload.confidence)}`;
  } else {
    setHolocronVerdict('verdict-noconviction');
    label.innerHTML = `NO<br/>CONVICTION`;
  }

  document.getElementById('grand-master-verdict').innerHTML = `
    <div><b class="${voteClass(payload.vote)}">${payload.vote}</b> confidence=${fmtNum(payload.confidence)}</div>
    <div style="margin-top:8px;"><b>Dissent summary:</b> ${payload.dissent_summary}</div>
    ${payload.correlated_evidence_warning ? `<div class="crimson" style="margin-top:8px;"><b>Correlated evidence:</b> ${payload.correlated_evidence_warning}</div>` : ''}
    <div style="margin-top:8px;"><b>Reasoning:</b> ${payload.reasoning}</div>
  `;

  document.getElementById('dissent-map').innerHTML = Object.entries(seatDetails).map(([sid, d]) => {
    const seat = TIER_I_SEATS.find(s => s.id === sid);
    return `<div>${d.vote === 'BULLISH' ? '&#9650;' : d.vote === 'BEARISH' ? '&#9660;' : '&#9679;'}
      <span class="${voteClass(d.vote)}">${seat ? seat.title : sid}</span> -- ${d.vote}</div>`;
  }).join('');
}

function openHoloPanel(seatId, title) {
  const d = seatDetails[seatId];
  const modal = document.getElementById('holo-modal');
  if (!d) {
    modal.innerHTML = `<button class="btn close-btn" onclick="closeHoloPanel()">CLOSE</button><h2>${title}</h2><p class="dim">Not deliberated yet.</p>`;
  } else {
    modal.innerHTML = `
      <button class="btn close-btn" onclick="closeHoloPanel()">CLOSE</button>
      <h2>${title}</h2>
      <div class="${voteClass(d.vote)}" style="font-family:var(--font-header); font-size:12px; margin-bottom:10px;">${d.vote} -- p=${d.probability}</div>
      <div>data_quality: <span class="cyan">${d.data_quality}</span> &nbsp; dispersion: <span class="cyan">${d.dispersion}</span></div>
      <div style="margin-top:12px;">${d.thesis}</div>
    `;
  }
  document.getElementById('holo-backdrop').classList.add('open');
}

function closeHoloPanel() {
  document.getElementById('holo-backdrop').classList.remove('open');
}

function setStatus(text) {
  lastStatusText = text;
  document.getElementById('status-line').textContent = text;
}

async function convene() {
  const ticker = document.getElementById('ticker-input').value.trim().toUpperCase();
  if (!ticker) return;
  const btn = document.getElementById('convene-btn');
  btn.disabled = true;
  resetRing();

  const contextEl = document.getElementById('context-input');
  const context = contextEl ? contextEl.value.trim() : '';
  setStatus(
    context
      ? `Convening the council for ${ticker} @ ${selectedHorizon} with your question...`
      : `Convening the council for ${ticker} @ ${selectedHorizon}...`
  );
  saveChamberState(); // persisted immediately -- if the tab is left right now, this (not a blank chamber) is what's restored

  let url = `/api/deliberate/stream?ticker=${encodeURIComponent(ticker)}&horizon=${selectedHorizon}`;
  if (context) url += `&context=${encodeURIComponent(context)}`;

  try {
    await consumeSSE(url, (event, payload) => {
      if (event === 'seat_result') updateSeatChair(payload);
      else if (event === 'seat_stage') updateSeatProgress(payload);
      else if (event === 'phase_b_reality_anchor') renderRealityAnchor(payload);
      else if (event === 'debate_round') appendDebateRound(payload);
      else if (event === 'phase_e_gates') renderAuditPanel(payload, null);
      else if (event === 'risk_warden') renderAuditPanel(null, payload);
      else if (event === 'phase_f_synthesis') renderGrandMaster(payload);
      else if (event === 'phase_g_crypt_write') setStatus(`Written to the Crypt: ${payload.prediction_id}`);
      else if (event === 'error') setStatus(`ERROR: ${payload.message}`);
      else if (event === 'done') setStatus(`Deliberation complete -- ${ticker} @ ${selectedHorizon}`);
      saveChamberState();
    });
  } catch (e) {
    setStatus(`ERROR: ${e.message}`);
    saveChamberState();
  } finally {
    btn.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const path = window.location.pathname === '/' ? '/index.html' : window.location.pathname;
  renderNav(path);
  layoutRing();
  buildHolocronInto(document.getElementById('holocron'), getCenterpieceStyle(), {
    labelHtml: 'AWAITING<br/>DELIBERATION',
  });

  document.querySelectorAll('#horizon-toggle .toggle-option').forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll('#horizon-toggle .toggle-option').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      selectedHorizon = btn.dataset.horizon;
      AudioBlips.blip(660, 0.03);
    };
  });

  document.getElementById('convene-btn').onclick = convene;
  document.getElementById('holo-backdrop').onclick = (e) => {
    if (e.target.id === 'holo-backdrop') closeHoloPanel();
  };

  restoreChamberState();
});
