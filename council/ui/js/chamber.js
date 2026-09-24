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
let selectedShape = 'full'; // 'full' | 'lite' -- see SHAPE_HINTS
let seatDetails = {}; // seat_id -> full seat_result payload, for the holo-panel
let runAlerts = []; // { kind: 'notice' | 'stopped', message, link }
let runInfo = null; // { ticker, horizon, shape } of the run shown on the ring

const HORIZON_NAMES = { '1d': 'Next Day', '1w': 'Next Week', '1m': 'Next Month', '1y': 'Next Year' };

const SHAPE_HINTS = {
  full: 'Full: every seat answers 3 times and the debate runs 2 rounds (43 model calls).',
  lite: 'Lite: every seat answers once and the debate runs 1 round (16 model calls) -- faster and cheaper, a little less thorough.',
};

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
    shape: selectedShape,
    runAlerts,
    runInfo,
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

  if (state.shape) setShape(state.shape, /* quiet */ true);
  runAlerts = state.runAlerts || [];
  renderRunAlerts();

  resetRing(); // lays out the idle/deliberating baseline for this horizon first
  for (const payload of Object.values(state.seatDetails || {})) {
    updateSeatChair(payload, /* silent */ true);
  }
  if (state.realityAnchor) renderRealityAnchor(state.realityAnchor);
  for (const payload of state.debateRounds || []) appendDebateRound(payload);
  if (state.gatesPayload || state.riskPayload) {
    renderAuditPanel(state.gatesPayload, state.riskPayload);
  }
  runInfo = state.runInfo || null;
  renderRunInfo();
  if (state.grandMaster) renderGrandMaster(state.grandMaster);

  const status = document.getElementById('status-line');
  // A finished run speaks for itself on the ring -- only an unfinished
  // one needs the "may have finished since you left" caveat.
  if (state.statusText && !state.grandMaster) {
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
  // Seat Wizards' chairs are taller than every other chair style, so
  // chamber.css gives the ring a wider radius for them (.ring-wizard)
  // rather than shrinking the character back down to fit the old spacing.
  ring.classList.toggle('ring-wizard', getSeatChairStyle() === 'wizard');
  ring.querySelectorAll('.seat-chair').forEach(chair => chair.remove()); // safe to call again
  const n = TIER_I_SEATS.length;

  TIER_I_SEATS.forEach((seat, i) => {
    const angle = (i / n) * 2 * Math.PI - Math.PI / 2;

    const chair = document.createElement('div');
    chair.className = `${chairClassBase()} state-idle`;
    chair.id = `chair-${seat.id}`;
    // A point on the unit circle -- chamber.css multiplies it by the ring
    // radius, or ignores it entirely on phones, where the ring becomes a
    // grid. Keeping the radius in CSS is what lets that switch happen on
    // resize/rotation without any JS re-layout.
    chair.style.setProperty('--cx', Math.cos(angle).toFixed(4));
    chair.style.setProperty('--cy', Math.sin(angle).toFixed(4));
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
  Object.keys(waitTimers).forEach(clearWait);
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
  document.getElementById('ring-spoken').hidden = true;
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

// seat_id -> interval id for a seat's "waiting on a rate limit" countdown.
const waitTimers = {};

function clearWait(seatId) {
  if (waitTimers[seatId]) {
    clearInterval(waitTimers[seatId]);
    delete waitTimers[seatId];
  }
}

// A free tier's per-minute limit makes a seat sit out a pause the provider
// names ("try again in 40s"). Say so on the seat, counting down, instead
// of leaving it on a "deliberating..." that looks stuck.
function showSeatWaiting(payload) {
  const chair = document.getElementById(`chair-${payload.seat_id}`);
  const resumeAt = Date.now() + (payload.resume_in || 0) * 1000;
  const label = () => {
    const left = Math.max(0, Math.round((resumeAt - Date.now()) / 1000));
    return left > 0 ? `waiting on ${payload.provider} limit · ${left}s` : 'deliberating...';
  };
  if (!chair) {
    // Debate / Prosecutor / Grand Master have no chair on the ring.
    setStatus(`Waiting on ${payload.provider}'s rate limit -- resumes in ${payload.resume_in}s.`);
    return;
  }
  const voteEl = chair.querySelector('.seat-vote');
  clearWait(payload.seat_id);
  voteEl.className = 'seat-vote yellow';
  voteEl.textContent = label();
  waitTimers[payload.seat_id] = setInterval(() => {
    voteEl.textContent = label();
    if (Date.now() >= resumeAt) {
      voteEl.className = 'seat-vote cyan';
      clearWait(payload.seat_id);
    }
  }, 1000);
}

function updateSeatProgress(payload) {
  if (payload.stage === 'waiting') {
    showSeatWaiting(payload);
    return;
  }
  if (waitTimers[payload.seat_id]) {
    // Work resumed before the countdown ran out.
    clearWait(payload.seat_id);
    const voteEl = document.querySelector(`#chair-${payload.seat_id} .seat-vote`);
    if (voteEl) {
      voteEl.textContent = 'deliberating...';
      voteEl.className = 'seat-vote cyan';
    }
  }
  const fill = document.getElementById(`fill-${payload.seat_id}`);
  if (!fill) return;
  fill.className = 'seat-bust-fill stage-active';
  fill.style.width = `${stageToPct(payload)}%`;
}

function updateSeatChair(payload, silent = false) {
  clearWait(payload.seat_id);
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
    // NO_CONVICTION: read its data, landed in the middle. NO_READ: couldn't
    // read at all (API error, malformed answer, or no data).
    voteEl.textContent = payload.vote === 'NO_CONVICTION' ? 'NO_CONVICTION' : 'NO_READ';
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

  document.getElementById('ring-spoken').hidden = false;

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

// Which run the ring is showing -- the ticker and horizon it was convened
// with, not whatever is typed in the box now.
function renderRunInfo() {
  const meta = document.getElementById('ring-meta');
  meta.hidden = !runInfo;
  if (!runInfo) return;
  document.getElementById('ring-ticker').textContent = runInfo.ticker;
  const shape = runInfo.shape === 'lite' ? ' · Lite run' : '';
  document.getElementById('ring-horizon').textContent = `${HORIZON_NAMES[runInfo.horizon] || runInfo.horizon}${shape}`;
}

function openSynthesis() {
  if (!lastGrandMaster) return;
  const modal = document.getElementById('holo-modal');
  const heading = runInfo ? `${runInfo.ticker} · ${HORIZON_NAMES[runInfo.horizon] || runInfo.horizon}` : '';
  modal.innerHTML = `
    <button class="btn close-btn" onclick="closeHoloPanel()">CLOSE</button>
    <h2>The Grand Master's Synthesis</h2>
    <div class="dim" style="margin-bottom:12px;">${heading}</div>
    ${document.getElementById('grand-master-verdict').innerHTML}
  `;
  document.getElementById('holo-backdrop').classList.add('open');
}

function closeHoloPanel() {
  document.getElementById('holo-backdrop').classList.remove('open');
}

function setStatus(text) {
  lastStatusText = text;
  document.getElementById('status-line').textContent = text;
}

function setShape(shape, quiet = false) {
  selectedShape = shape === 'lite' ? 'lite' : 'full';
  document.querySelectorAll('#shape-toggle .toggle-option').forEach(b => {
    b.classList.toggle('active', b.dataset.shape === selectedShape);
  });
  document.getElementById('shape-hint').textContent = SHAPE_HINTS[selectedShape];
  if (!quiet) AudioBlips.blip(660, 0.03);
}

// Notices (e.g. "Gemini's free limit ran out, switching to Groq") and a
// stopped run's reason, shown under the status line.
function renderRunAlerts() {
  const box = document.getElementById('run-alerts');
  box.replaceChildren();
  for (const alert of runAlerts) {
    const div = document.createElement('div');
    div.className = `run-alert run-alert-${alert.kind}`;
    div.setAttribute('role', alert.kind === 'notice' ? 'status' : 'alert');
    const label = document.createElement('b');
    label.textContent = { stopped: 'Run stopped. ', invalid: 'Ticker not found. ' }[alert.kind] || 'Note: ';
    div.append(label, alert.message);
    if (alert.kind === 'stopped') {
      div.append(' Nothing from this run was saved to the Crypt.');
    }
    if (alert.link) {
      const a = document.createElement('a');
      a.href = alert.link;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = 'Add credit ↗';
      div.append(' ', a);
    }
    box.appendChild(div);
  }
}

function addRunAlert(kind, message, link = null) {
  runAlerts.push({ kind, message, link });
  renderRunAlerts();
}

async function convene() {
  const ticker = document.getElementById('ticker-input').value.trim().toUpperCase();
  if (!ticker) return;
  const btn = document.getElementById('convene-btn');
  btn.disabled = true;
  resetRing();
  runAlerts = [];
  renderRunAlerts();
  runInfo = { ticker, horizon: selectedHorizon, shape: selectedShape };
  renderRunInfo();

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
  if (selectedShape === 'lite') url += '&lite=true';

  try {
    await consumeSSE(url, (event, payload) => {
      if (event === 'mode') setStatus(`${lastStatusText} ${payload.message}.`);
      else if (event === 'notice') addRunAlert('notice', payload.message);
      else if (event === 'invalid_ticker') {
        addRunAlert('invalid', payload.message);
        runInfo = null;
        renderRunInfo();
        layoutRing(); // back to the idle ring -- nothing ran
        setHolocronVerdict(null);
        document.getElementById('holocron-label').innerHTML = 'AWAITING<br/>DELIBERATION';
        setStatus('');
      }
      else if (event === 'stopped') {
        addRunAlert('stopped', payload.message, payload.link);
        setStatus(`Run stopped -- ${ticker} @ ${selectedHorizon}`);
      }
      else if (event === 'seat_result') updateSeatChair(payload);
      else if (event === 'seat_stage') updateSeatProgress(payload);
      else if (event === 'phase_b_reality_anchor') renderRealityAnchor(payload);
      else if (event === 'debate_round') appendDebateRound(payload);
      else if (event === 'phase_e_gates') renderAuditPanel(payload, null);
      else if (event === 'risk_warden') renderAuditPanel(null, payload);
      else if (event === 'phase_f_synthesis') renderGrandMaster(payload);
      else if (event === 'phase_g_crypt_write') setStatus(`Written to the Crypt: ${payload.prediction_id}`);
      else if (event === 'error') setStatus(`ERROR: ${payload.message}`);
      // The ring itself now shows the finished run (ticker/horizon top right,
      // "The Council has spoken" bottom left), so the status line clears.
      else if (event === 'done') setStatus('');
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

  document.querySelectorAll('#shape-toggle .toggle-option').forEach(btn => {
    btn.onclick = () => setShape(btn.dataset.shape);
  });
  setShape('full', true);

  document.getElementById('convene-btn').onclick = convene;
  document.getElementById('view-synthesis-btn').onclick = openSynthesis;
  document.getElementById('holo-backdrop').onclick = (e) => {
    if (e.target.id === 'holo-backdrop') closeHoloPanel();
  };

  const restored = restoreChamberState();
  // Free Mode's limits go further with Lite runs, so that's its default --
  // unless this session already picked one.
  if (!restored) {
    fetchJSON('/api/settings/free-mode')
      .then(freeMode => { if (freeMode.enabled) setShape('lite', true); })
      .catch(() => {});
  }
});
