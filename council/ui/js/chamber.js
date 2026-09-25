// THE CHAMBER -- ticker input, the council ring, and the live SSE-driven
// deliberation. Every run reads all three terms (short / medium / long);
// each chair shows a seat's lean on each, and the council's position on
// each term is a bearish<->bullish bar.

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
  { id: 'analyst_ratings', title: "Reader of the Guild's Targets" },
  { id: 'structure_archivist', title: 'Keeper of Charters' },
];

let selectedShape = 'full'; // 'full' | 'lite' -- see SHAPE_HINTS
let seatDetails = {}; // seat_id -> full seat_result payload, for the holo-panel
let runAlerts = []; // { kind: 'notice' | 'stopped', message, link }
let runInfo = null; // { ticker, shape } of the run shown on the ring

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
let lastPositions = null; // phase_d_weighted_vote's per-term positions
let lastWarnings = null; // phase_e_warnings' per-term warnings
let lastRiskPayload = null;
let lastGrandMaster = null; // phase_f_synthesis: headline, notes and each term's full bar
let lastStatusText = '';

function saveChamberState() {
  const state = {
    ticker: document.getElementById('ticker-input')?.value ?? '',
    shape: selectedShape,
    runAlerts,
    runInfo,
    statusText: lastStatusText,
    seatDetails,
    realityAnchor: lastRealityAnchor,
    debateRounds: debateRoundsLog,
    positions: lastPositions,
    warnings: lastWarnings,
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
  // A snapshot from before runs covered three terms can't be redrawn.
  if (Object.values(state.seatDetails || {}).some(d => !d.terms)) return false;

  if (state.ticker) document.getElementById('ticker-input').value = state.ticker;
  if (state.shape) setShape(state.shape, /* quiet */ true);
  runAlerts = state.runAlerts || [];
  renderRunAlerts();

  resetRing(); // lays out the deliberating baseline first
  for (const payload of Object.values(state.seatDetails || {})) {
    updateSeatChair(payload, /* silent */ true);
  }
  if (state.realityAnchor) renderRealityAnchor(state.realityAnchor);
  for (const payload of state.debateRounds || []) appendDebateRound(payload);
  if (state.positions) renderPositionsEvent(state.positions);
  if (state.warnings) renderWarningsEvent(state.warnings);
  if (state.riskPayload) renderRiskWarden(state.riskPayload);
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
  lastPositions = null;
  lastWarnings = null;
  lastRiskPayload = null;
  lastGrandMaster = null;
  // Every seat is called on every run -- all twelve start deliberating.
  for (const seat of TIER_I_SEATS) {
    const chair = document.getElementById(`chair-${seat.id}`);
    const fill = document.getElementById(`fill-${seat.id}`); // absent for chair-style-wizard, by design
    if (fill) {
      fill.className = 'seat-bust-fill stage-active';
      fill.style.width = '0%';
    }
    chair.className = `${chairClassBase()} state-deliberating`;
    chair.querySelector('.seat-vote').textContent = 'deliberating...';
    chair.querySelector('.seat-vote').className = 'seat-vote cyan';
    redrawWizardChair(seat.id, 'deliberating');
  }
  setHolocronVerdict(null);
  document.getElementById('ring-spoken').hidden = true;
  document.getElementById('holocron-label').innerHTML = 'DELIBERATING';
  document.getElementById('positions').innerHTML = '<span class="dim">Awaiting the seats\' leans...</span>';
  document.getElementById('reality-anchor').innerHTML = '<span class="dim">Awaiting Phase B...</span>';
  document.getElementById('dissent-map').innerHTML = '<span class="dim">Awaiting the seats\' leans...</span>';
  document.getElementById('debate-transcript').innerHTML = '<span class="dim">Awaiting Phase C...</span>';
  document.getElementById('audit-panel').innerHTML = '<span class="dim">Awaiting the Risk Warden...</span>';
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
  const queued = payload.stage === 'queued';
  const label = () => {
    const left = Math.max(0, Math.round((resumeAt - Date.now()) / 1000));
    if (left <= 0) return 'deliberating...';
    return queued ? `queued for ${payload.provider} · ${left}s` : `waiting on ${payload.provider} limit · ${left}s`;
  };
  if (!chair) {
    // Debate / Prosecutor / Grand Master have no chair on the ring.
    setStatus(queued
      ? `Queued for ${payload.provider} (free tier pace) -- starts in ${payload.resume_in}s.`
      : `Waiting on ${payload.provider}'s rate limit -- resumes in ${payload.resume_in}s.`);
    return;
  }
  const voteEl = chair.querySelector('.seat-vote');
  clearWait(payload.seat_id);
  voteEl.className = queued ? 'seat-vote dim' : 'seat-vote yellow';
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
  if (payload.stage === 'waiting' || payload.stage === 'queued') {
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

// "S M L" pips: one small coloured letter per term -- green for a
// bullish lean, crimson for bearish, yellow for dead even, grey for no read.
function termPipsHtml(terms) {
  return TERMS.map(t => {
    const vote = terms[t]?.vote;
    const cls = vote === 'BULLISH' ? 'pip-bullish' : vote === 'BEARISH' ? 'pip-bearish'
      : vote === 'NO_CONVICTION' ? 'pip-even' : 'pip-noread';
    const arrow = vote === 'BULLISH' ? '&#9650;' : vote === 'BEARISH' ? '&#9660;' : '&#9679;';
    return `<span class="term-pip ${cls}" title="${TERM_NAMES[t]}: ${vote || 'no read'}">${TERM_LETTERS[t]}${arrow}</span>`;
  }).join('');
}

function updateSeatChair(payload, silent = false) {
  clearWait(payload.seat_id);
  seatDetails[payload.seat_id] = payload;
  const chair = document.getElementById(`chair-${payload.seat_id}`);
  if (!chair) return;
  const voteEl = chair.querySelector('.seat-vote');
  const fill = document.getElementById(`fill-${payload.seat_id}`);

  // The chair's colour is the seat's overall lean: its three terms,
  // weighted by how much each counts for this seat.
  // How strongly it glows follows how far that lean is from even, so a
  // seat at 50.1% doesn't blaze like one at 65%.
  const state = { BULLISH: 'bullish', BEARISH: 'bearish' }[payload.vote] || 'noread';
  const strength = payload.lean_p_bullish === null || payload.lean_p_bullish === undefined
    ? 'even' : leanStrength(payload.lean_p_bullish);
  chair.className = `${chairClassBase()} state-${state} chair-lean-${strength}`;
  if (fill) fill.className = `seat-bust-fill vote-${state}`;
  redrawWizardChair(payload.seat_id, state);
  if (payload.vote === 'NO_READ') {
    voteEl.textContent = 'NO READ';
    voteEl.className = 'seat-vote status-noread';
  } else {
    voteEl.innerHTML = termPipsHtml(payload.terms || {});
    voteEl.className = 'seat-vote term-pips';
  }
  if (!silent) AudioBlips.blip({ bullish: 1046, bearish: 392 }[state] || 220, 0.05);
  if (fill) fill.style.width = '100%';
}

function renderRealityAnchor(payload) {
  lastRealityAnchor = payload;
  const terms = payload.terms || {};
  document.getElementById('reality-anchor').innerHTML = TERMS.filter(t => terms[t]).map(t => {
    const a = terms[t];
    const implausible = Object.entries(a.plausibility_flags || {}).filter(([, f]) => f === 'IMPLAUSIBLE').map(([sid]) => sid);
    return `
      <div class="anchor-term">
        <div class="anchor-term-name">${TERM_NAMES[t]}</div>
        usually moves up to <span class="amber">${fmtPct(a.max_plausible_move_pct).replace('+', '&plusmn;')}</span>
        ${a.hit_rate_up !== null && a.hit_rate_up !== undefined ? ` &middot; rose in <span class="cyan">${Math.round(a.hit_rate_up * 100)}%</span> of past windows` : ''}
        ${a.options_implied_move_pct ? ` &middot; options imply <span class="cyan">${fmtPct(a.options_implied_move_pct).replace('+', '&plusmn;')}</span>` : ''}
        ${implausible.length ? `<div class="crimson">&#9650; bigger than usual: ${implausible.join(', ')}</div>` : ''}
      </div>`;
  }).join('');
}

function appendDebateRound(payload) {
  debateRoundsLog.push(payload);
  const el = document.getElementById('debate-transcript');
  // The first round replaces the "Awaiting Phase C..." placeholder; later
  // rounds append under it. (Checking for any .dim element here used to
  // match round 1's own Prosecutor line and wipe it when round 2 arrived.)
  if (debateRoundsLog.length === 1) el.innerHTML = '';
  const div = document.createElement('div');
  div.className = 'panel-inset';
  div.style.marginBottom = '10px';
  const vetoTerms = (payload.prosecutor_veto_terms || []).map(t => TERM_NAMES[t] || t).join(', ');
  div.innerHTML = `
    <h3>Round ${payload.round_n}</h3>
    ${payload.bull_argument ? `<div style="margin-bottom:6px;"><span class="green">BULL:</span> ${escapeHtml(payload.bull_argument)}</div>` : ''}
    ${payload.bear_argument ? `<div style="margin-bottom:6px;"><span class="crimson">BEAR:</span> ${escapeHtml(payload.bear_argument)}</div>` : ''}
    <div class="dim">PROSECUTOR: ${payload.prosecutor_veto ? `<span class="amber">objects${vetoTerms ? ` (${vetoTerms})` : ''}</span>` : 'no objection'}
      ${(payload.prosecutor_findings || []).map(f => `<div>&#8226; ${escapeHtml(f)}</div>`).join('')}</div>
  `;
  el.appendChild(div);
}

function renderRiskWarden(riskPayload) {
  lastRiskPayload = riskPayload;
  document.getElementById('audit-panel').innerHTML = `
    <div>position size: <span class="cyan">${fmtPct(riskPayload.position_size_pct_of_book)}</span> of book</div>
    <div class="dim" style="margin-top:6px; font-size:12px;">Sized from the stock's volatility alone -- the Risk Warden never sees which way the council leans.</div>
    ${riskPayload.concentration_warning ? `<div class="crimson" style="margin-top:6px;">&#9650; ${escapeHtml(riskPayload.concentration_warning)}</div>` : ''}
  `;
}

// The three bars, drawn as soon as the positions are computed (Phase D)
// with ticks from the seats' own leans, then redrawn with the weights and
// the Grand Master's notes once the synthesis lands.
function currentBars() {
  if (lastGrandMaster?.terms) return lastGrandMaster.terms;
  if (!lastPositions?.terms) return null;
  const bars = {};
  for (const t of TERMS) {
    const pos = lastPositions.terms[t];
    if (!pos) continue;
    bars[t] = {
      ...pos,
      warnings: lastWarnings?.terms?.[t] || [],
      ticks: TIER_I_SEATS.map(s => {
        const lean = seatDetails[s.id]?.terms?.[t];
        return { seat_id: s.id, title: s.title, p_bullish: lean?.p_bullish ?? null, weight: COMPETENCE_MATRIX[s.id]?.[t] };
      }),
    };
  }
  return bars;
}

function renderPositions() {
  const bars = currentBars();
  if (!bars) return;
  document.getElementById('positions').innerHTML = TERMS.filter(t => bars[t]).map(t => termBarHtml(t, bars[t])).join('');
  renderHolocron(bars);
}

function renderPositionsEvent(payload) {
  lastPositions = payload;
  renderPositions();
}

function renderWarningsEvent(payload) {
  lastWarnings = payload;
  renderPositions();
}

// The holocron shows all three terms at once, e.g. "S ▲ 54%" -- coloured
// by the average of the three.
function renderHolocron(bars) {
  const label = document.getElementById('holocron-label');
  const ps = TERMS.filter(t => bars[t]).map(t => bars[t].p_bullish);
  if (!ps.length) return;
  const mean = ps.reduce((a, b) => a + b, 0) / ps.length;
  const vote = voteFromP(mean);
  setHolocronVerdict(vote === 'BULLISH' ? 'verdict-bullish' : vote === 'BEARISH' ? 'verdict-bearish' : 'verdict-noconviction');
  label.innerHTML = TERMS.filter(t => bars[t]).map(t => {
    const p = bars[t].p_bullish;
    const v = voteFromP(p);
    const arrow = v === 'BULLISH' ? '&#9650;' : v === 'BEARISH' ? '&#9660;' : '&#9679;';
    const pct = v === 'NO_CONVICTION' ? '50/50' : `${Math.round(Math.max(p, 1 - p) * 100)}%`;
    return `${TERM_LETTERS[t]} ${arrow} ${pct}`;
  }).join('<br/>');
}

function dissentHtml(bars) {
  return TERMS.filter(t => bars[t]).map(t => {
    const groups = { BULLISH: [], BEARISH: [], NO_CONVICTION: [], NO_READ: [] };
    for (const tick of bars[t].ticks || []) {
      const vote = tick.p_bullish === null || tick.p_bullish === undefined ? 'NO_READ' : voteFromP(tick.p_bullish);
      groups[vote].push(tick.title);
    }
    const line = (vote, word) => groups[vote].length
      ? `<div class="${voteClass(vote)}">${word} (${groups[vote].length}): <span class="dim">${groups[vote].map(escapeHtml).join(', ')}</span></div>` : '';
    return `
      <div class="anchor-term">
        <div class="anchor-term-name">${TERM_NAMES[t]}</div>
        ${line('BULLISH', '&#9650; bullish')}${line('BEARISH', '&#9660; bearish')}
        ${line('NO_CONVICTION', '&#9679; dead even')}${line('NO_READ', '&#9679; couldn\'t read')}
      </div>`;
  }).join('');
}

function synthesisHtml(payload) {
  return `
    <div class="gm-headline">${escapeHtml(payload.headline)}</div>
    ${TERMS.map(t => `
      <div class="gm-note"><b>${TERM_NAMES[t]}:</b> ${escapeHtml(payload[t] || payload.terms?.[t]?.note || '')}</div>`).join('')}
    <div class="gm-note" style="margin-top:10px;"><b>Who disagreed:</b> ${escapeHtml(payload.dissent_summary)}</div>
    ${payload.correlated_evidence_warning ? `<div class="gm-note crimson"><b>Correlated evidence:</b> ${escapeHtml(payload.correlated_evidence_warning)}</div>` : ''}
    <div class="gm-note"><b>Reasoning:</b> ${escapeHtml(payload.reasoning)}</div>
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
  renderPositions();
  document.getElementById('grand-master-verdict').innerHTML = synthesisHtml(payload);
  document.getElementById('dissent-map').innerHTML = dissentHtml(payload.terms || {});
  document.getElementById('ring-spoken').hidden = false;
}

function openHoloPanel(seatId, title) {
  const d = seatDetails[seatId];
  const modal = document.getElementById('holo-modal');
  modal.classList.remove('synthesis-modal');
  if (!d) {
    modal.innerHTML = `<button class="btn close-btn" onclick="closeHoloPanel()">CLOSE</button><h2>${escapeHtml(title)}</h2><p class="dim">Not deliberated yet.</p>`;
  } else if (d.vote === 'NO_READ') {
    modal.innerHTML = `
      <button class="btn close-btn" onclick="closeHoloPanel()">CLOSE</button>
      <h2>${escapeHtml(title)}</h2>
      <div class="status-noread" style="font-family:var(--font-header); font-size:12px; margin-bottom:10px;">NO READ</div>
      <div>${escapeHtml(d.thesis)}</div>
    `;
  } else {
    const rows = TERMS.map(t => {
      const lean = d.terms?.[t] || {};
      const counts = COMPETENCE_MATRIX[seatId]?.[t];
      const p = lean.p_bullish;
      const leanText = p === null || p === undefined ? 'no read'
        : `${leanLabel(p)} &middot; ${chanceText(p)}`;
      return `
        <div class="seat-term">
          ${termBarHtml(t, { p_bullish: p ?? 0.5, seats_counted: p === null || p === undefined ? 0 : 1, lean_label: leanLabel(p ?? 0.5) }, { compact: true })}
          <div class="seat-term-detail">
            <span class="${voteClass(lean.vote)}">${leanText}</span>
            ${lean.vote === 'BULLISH' || lean.vote === 'BEARISH' ? ` &middot; expected move &plusmn;${Number(lean.expected_move_pct).toFixed(1)}%` : ''}
            ${counts !== undefined ? ` <span class="dim">&middot; counts ${counts.toFixed(1)} on this term</span>` : ''}
            ${lean.rationale ? `<div class="dim" style="margin-top:3px;">${escapeHtml(lean.rationale)}</div>` : ''}
          </div>
        </div>`;
    }).join('');
    modal.innerHTML = `
      <button class="btn close-btn" onclick="closeHoloPanel()">CLOSE</button>
      <h2>${escapeHtml(title)}</h2>
      <div style="margin-bottom:10px;">data quality: <span class="cyan">${d.data_quality}</span></div>
      ${rows}
      <div style="margin-top:12px;">${escapeHtml(d.thesis)}</div>
    `;
  }
  document.getElementById('holo-backdrop').classList.add('open');
}

// Which run the ring is showing -- the ticker it was convened with, not
// whatever is typed in the box now.
function renderRunInfo() {
  const meta = document.getElementById('ring-meta');
  meta.hidden = !runInfo;
  if (!runInfo) return;
  document.getElementById('ring-ticker').textContent = runInfo.ticker;
  document.getElementById('ring-shape').textContent = runInfo.shape === 'lite' ? 'Short · Medium · Long · Lite run' : 'Short · Medium · Long';
}

function openSynthesis() {
  if (!lastGrandMaster) return;
  const modal = document.getElementById('holo-modal');
  const bars = lastGrandMaster.terms || {};
  modal.innerHTML = `
    <button class="btn close-btn" onclick="closeHoloPanel()">CLOSE</button>
    <h2>The Grand Master's Synthesis</h2>
    <div class="dim" style="margin-bottom:12px;">${runInfo ? escapeHtml(runInfo.ticker) : ''}</div>
    <div class="gm-headline">${escapeHtml(lastGrandMaster.headline)}</div>
    ${TERMS.filter(t => bars[t]).map(t => termBarHtml(t, bars[t])).join('')}
    <div class="gm-note" style="margin-top:10px;"><b>Who disagreed:</b> ${escapeHtml(lastGrandMaster.dissent_summary)}</div>
    ${lastGrandMaster.correlated_evidence_warning ? `<div class="gm-note crimson"><b>Correlated evidence:</b> ${escapeHtml(lastGrandMaster.correlated_evidence_warning)}</div>` : ''}
    <div class="gm-note"><b>Reasoning:</b> ${escapeHtml(lastGrandMaster.reasoning)}</div>
  `;
  modal.classList.add('synthesis-modal');
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
  runInfo = { ticker, shape: selectedShape };
  renderRunInfo();

  const contextEl = document.getElementById('context-input');
  const context = contextEl ? contextEl.value.trim() : '';
  setStatus(
    context
      ? `Convening the council for ${ticker} with your question...`
      : `Convening the council for ${ticker}...`
  );
  saveChamberState(); // persisted immediately -- if the tab is left right now, this (not a blank chamber) is what's restored

  let url = `/api/deliberate/stream?ticker=${encodeURIComponent(ticker)}`;
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
        setStatus(`Run stopped -- ${ticker}`);
      }
      else if (event === 'seat_result') updateSeatChair(payload);
      else if (event === 'seat_stage') updateSeatProgress(payload);
      else if (event === 'phase_b_reality_anchor') renderRealityAnchor(payload);
      else if (event === 'debate_round') appendDebateRound(payload);
      else if (event === 'phase_d_weighted_vote') renderPositionsEvent(payload);
      else if (event === 'phase_e_warnings') renderWarningsEvent(payload);
      else if (event === 'risk_warden') renderRiskWarden(payload);
      else if (event === 'phase_f_synthesis') renderGrandMaster(payload);
      else if (event === 'phase_g_crypt_write') setStatus('Written to the Crypt.');
      else if (event === 'error') setStatus(`ERROR: ${payload.message}`);
      // The ring itself now shows the finished run (ticker top left,
      // "The Council has spoken" bottom right), so the status line clears.
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
