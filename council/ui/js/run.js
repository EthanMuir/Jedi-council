// The Run page: start a run, watch the seats fill in live, and read the
// finished result -- or reopen a saved run from History (?run=<id>).
//
// Everything on screen is drawn from one `state` object, updated by the
// server's live events (or rebuilt from a saved run), so a finished run
// and a reopened one look exactly the same.

let state = null;
let shape = 'full';
let listening = null; // AbortController for the live stream
const waitTicks = {}; // seat_id -> interval for a rate-limit countdown
const STATE_KEY = 'council_run_state';
const PLAIN_TINY = { even: 'A toss-up', barely: 'Close to a toss-up', leaning: 'Slightly more likely to {dir}', plain: 'More likely to {dir}', strong: 'Much more likely to {dir}' };

function emptyState(meta) {
  return {
    meta, // { ticker, shape, context, run_mode, created_at, cost, run_id, reopened }
    status: 'running', // running | done | stopped | error | detached
    step: 'Starting…',
    seats: {},
    stages: {},
    reality: null,
    debate: [],
    positions: null,
    warnings: null,
    risk: null,
    synthesis: null,
    alerts: [],
  };
}

// ---- saving across page changes -------------------------------------------------

function saveState() {
  if (!state || state.meta.reopened) return;
  try { sessionStorage.setItem(STATE_KEY, JSON.stringify(state)); } catch (e) { /* storage off */ }
}

function loadSavedState() {
  try {
    const raw = sessionStorage.getItem(STATE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function clearSavedState() {
  try { sessionStorage.removeItem(STATE_KEY); } catch (e) { /* storage off */ }
}

// ---- live events ------------------------------------------------------------------

function onEvent(event, payload) {
  const s = state;
  switch (event) {
    case 'mode':
      s.meta.run_mode = payload.run_mode;
      if (payload.run_mode === 'sample') {
        s.alerts.push({ kind: 'info', message: 'No AI keys added yet, so this run uses sample answers and costs nothing. Add a key under Settings to run it for real.' });
      }
      break;
    case 'notice':
      s.alerts.push({ kind: 'info', message: payload.message });
      break;
    case 'invalid_ticker':
      s.alerts.push({ kind: 'stop', title: 'Ticker not found.', message: payload.message });
      s.status = 'stopped';
      s.invalid = true;
      break;
    case 'stopped':
      s.alerts.push({ kind: 'stop', title: 'Run stopped.', message: `${payload.message} Nothing from this run was saved.`, link: payload.link });
      s.status = 'stopped';
      break;
    case 'error':
      s.alerts.push({ kind: 'stop', title: 'Something went wrong.', message: payload.message });
      s.status = 'error';
      break;
    case 'seat_stage':
      onStage(payload);
      return; // no full redraw for progress ticks
    case 'seat_result':
      stopWait(payload.seat_id);
      s.seats[payload.seat_id] = payload;
      delete s.stages[payload.seat_id];
      break;
    case 'phase_b_reality_anchor':
      s.reality = payload;
      s.step = 'Debating';
      break;
    case 'debate_round':
      s.debate.push(payload);
      s.step = `Debate round ${payload.round_n} done`;
      break;
    case 'phase_d_weighted_vote':
      s.positions = payload;
      s.step = 'Checking the result';
      break;
    case 'phase_e_warnings':
      s.warnings = payload;
      break;
    case 'risk_warden':
      s.risk = payload;
      s.step = 'Writing the summary';
      break;
    case 'phase_f_synthesis':
      s.synthesis = payload;
      s.step = 'Saving to History';
      break;
    case 'phase_g_crypt_write':
      s.meta.run_id = payload.run_id;
      fetchJSON(`/api/runs/${encodeURIComponent(payload.run_id)}`).then(run => {
        s.meta.cost = run.terms?.short?.prediction?.total_cost_usd ?? null;
        s.meta.created_at = run.created_at;
        s.meta.run_mode = run.run_mode;
        renderHead();
        renderSections();
        saveState();
      }).catch(() => {});
      break;
    case 'done':
      if (s.status === 'running') s.status = 'done';
      break;
    default:
      return;
  }
  saveState();
  renderAll();
}

function onStage(p) {
  const s = state;
  const seat = SEAT_BY_ID[p.seat_id];
  if (!seat) {
    // The debate, Challenger and Summary have no card; say it in the bar.
    if (p.stage === 'waiting' || p.stage === 'queued') {
      s.step = p.stage === 'queued'
        ? `Queued for ${p.provider}'s free tier · starts in ${p.resume_in}s`
        : `Waiting on ${p.provider}'s rate limit · ${p.resume_in}s`;
      renderProgress();
    }
    return;
  }
  if (p.stage === 'waiting' || p.stage === 'queued') {
    s.stages[p.seat_id] = { ...(s.stages[p.seat_id] || {}), wait: p.stage, until: Date.now() + (p.resume_in || 0) * 1000, provider: p.provider };
    startWait(p.seat_id);
  } else {
    stopWait(p.seat_id);
    s.stages[p.seat_id] = { stage: p.stage, done: p.samples_done || 0, total: p.samples_total || 1 };
  }
  updateSeatCard(p.seat_id);
  renderProgress();
}

function startWait(id) {
  stopWait(id);
  waitTicks[id] = setInterval(() => {
    const st = state?.stages[id];
    if (!st?.wait || Date.now() >= st.until) {
      if (st) delete st.wait;
      stopWait(id);
    }
    updateSeatCard(id);
  }, 1000);
}

function stopWait(id) {
  if (waitTicks[id]) {
    clearInterval(waitTicks[id]);
    delete waitTicks[id];
  }
}

// ---- starting and clearing --------------------------------------------------------

async function startRun(e) {
  e.preventDefault();
  const ticker = document.getElementById('ticker-input').value.trim().toUpperCase();
  if (!ticker) return;
  const context = document.getElementById('context-input').value.trim();
  if (listening) listening.abort();
  Object.keys(waitTicks).forEach(stopWait);
  state = emptyState({ ticker, shape, context, created_at: new Date().toISOString() });
  history.replaceState(null, '', '/index.html');
  saveState();
  showRun();

  let url = `/api/deliberate/stream?ticker=${encodeURIComponent(ticker)}`;
  if (context) url += `&context=${encodeURIComponent(context)}`;
  if (shape === 'lite') url += '&lite=true';

  const controller = new AbortController();
  listening = controller;
  const mine = state;
  try {
    await consumeSSE(url, (event, payload) => {
      if (state !== mine) return; // a newer run replaced this one
      onEvent(event, payload);
    }, { signal: controller.signal });
    if (state === mine && state.status === 'running') {
      state.status = 'done';
      saveState();
      renderAll();
    }
  } catch (err) {
    if (controller.signal.aborted || state !== mine) return;
    state.alerts.push({ kind: 'stop', title: 'Lost the connection.', message: `${err.message} The run may still finish -- check History in a few minutes.` });
    state.status = 'error';
    saveState();
    renderAll();
  } finally {
    if (listening === controller) listening = null;
  }
}

function newRun() {
  if (listening) listening.abort();
  listening = null;
  Object.keys(waitTicks).forEach(stopWait);
  state = null;
  clearSavedState();
  history.replaceState(null, '', '/index.html');
  closeDrawer();
  document.getElementById('run').hidden = true;
  document.getElementById('composer').hidden = false;
  const input = document.getElementById('ticker-input');
  input.value = '';
  document.getElementById('context-input').value = '';
  input.focus();
  window.scrollTo({ top: 0 });
}

function showRun() {
  document.getElementById('composer').hidden = true;
  document.getElementById('run').hidden = false;
  buildSeatGrid();
  renderAll();
  window.scrollTo({ top: 0 });
}

// ---- reopening a saved run -------------------------------------------------------

// A saved run carries its synthesis, and (for runs saved since the clean
// look) a replay of what the live page showed. Older runs are rebuilt from
// their per-term seat votes.
function stateFromSaved(run) {
  const synth = run.synthesis || null;
  const replay = synth?.replay || {};
  const first = run.terms?.short?.prediction || Object.values(run.terms || {})[0]?.prediction || {};
  const s = emptyState({
    ticker: run.ticker,
    shape: run.run_shape,
    run_mode: run.run_mode,
    created_at: run.created_at,
    cost: first.total_cost_usd ?? null,
    run_id: run.run_id,
    reopened: true,
  });
  s.status = 'done';

  if (replay.seats?.length) {
    for (const seat of replay.seats) s.seats[seat.seat_id] = seat;
  } else {
    for (const seat of SEATS) {
      const terms = {};
      let thesis = '';
      let quality = null;
      let evidence = [];
      for (const t of TERMS) {
        const vote = run.terms?.[t]?.seat_votes?.find(v => v.seat_id === seat.id);
        if (!vote) continue;
        const v = vote.verdict;
        const pb = v.vote === 'NO_READ' ? null : v.vote === 'NO_CONVICTION' ? 0.5
          : v.vote === 'BULLISH' ? v.probability : 1 - v.probability;
        terms[t] = { vote: v.vote, probability: v.probability, p_bullish: pb, expected_move_pct: v.expected_move_pct, rationale: v.term_rationale || v.abstain_reason };
        if (!thesis) { thesis = v.thesis; quality = v.data_quality; evidence = v.key_evidence || []; }
      }
      if (!Object.keys(terms).length) continue;
      const read = Object.values(terms).filter(x => x.p_bullish !== null);
      const mean = read.length ? read.reduce((a, x) => a + x.p_bullish, 0) / read.length : null;
      s.seats[seat.id] = {
        seat_id: seat.id, thesis, data_quality: quality, key_evidence: evidence, terms,
        vote: mean === null ? 'NO_READ' : { up: 'BULLISH', down: 'BEARISH', even: 'NO_CONVICTION' }[dirOf(mean)],
        lean_p_bullish: mean,
      };
    }
  }

  s.reality = replay.reality_anchor || null;
  s.debate = replay.debate || [];
  s.risk = replay.risk || null;
  if (synth?.terms) {
    s.synthesis = synth;
    s.positions = { terms: synth.terms };
  } else {
    // A run from before the three terms: its one row is all there is.
    const terms = {};
    for (const [key, t] of Object.entries(run.terms || {})) {
      const p = t.prediction;
      const pb = p.p_raw ?? (p.council_vote === 'BULLISH' ? p.council_confidence : p.council_vote === 'BEARISH' ? 1 - p.council_confidence : 0.5);
      if (TERMS.includes(key)) terms[key] = { p_bullish: pb, seats_counted: 1, consensus_pct: null };
    }
    s.positions = { terms };
  }
  return s;
}

async function openSavedRun(runId) {
  document.getElementById('composer').hidden = true;
  document.getElementById('run').hidden = false;
  try {
    const run = await fetchJSON(`/api/runs/${encodeURIComponent(runId)}`);
    state = stateFromSaved(run);
    showRun();
  } catch (err) {
    state = emptyState({ ticker: 'Run not found', reopened: true });
    state.status = 'error';
    state.alerts.push({ kind: 'stop', title: 'Couldn\'t open this run.', message: err.message });
    showRun();
  }
}

// ---- drawing ------------------------------------------------------------------------

function renderAll() {
  if (!state) return;
  renderHead();
  renderProgress();
  renderAlerts();
  renderHeadline();
  renderPositions();
  for (const seat of SEATS) updateSeatCard(seat.id);
  renderSections();
  if (openSeatId) renderDrawer(openSeatId);
}

function renderHead() {
  const m = state.meta;
  document.getElementById('run-ticker').textContent = m.ticker;
  const pills = [];
  if (m.created_at) pills.push(`<span>${fmtDate(m.created_at, true)}</span>`);
  pills.push(`<span class="pill">${m.shape === 'lite' ? 'Lite run' : 'Full run'}</span>`);
  if (m.run_mode === 'free') pills.push('<span class="pill pill-free">Free models</span>');
  if (m.run_mode === 'sample') pills.push('<span class="pill pill-warn">Sample answers</span>');
  if (m.cost) pills.push(`<span class="pill">${fmtMoney(m.cost)}</span>`);
  if (m.reopened) pills.push('<a class="pill pill-accent" href="/history.html">From History</a>');
  document.getElementById('run-meta').innerHTML = pills.join('');
  document.querySelectorAll('#detail-seg button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.detail === detailMode())));
}

function seatsDone() {
  return SEATS.filter(s => state.seats[s.id]).length;
}

function progressPct() {
  const s = state;
  let seatPart = 0;
  for (const seat of SEATS) {
    if (s.seats[seat.id]) { seatPart += 1; continue; }
    const st = s.stages[seat.id];
    if (!st) continue;
    seatPart += st.stage === 'deliberating' ? 0.15 + 0.8 * (st.done / (st.total || 1)) : 0.08;
  }
  let pctDone = (seatPart / SEATS.length) * 65;
  if (s.reality) pctDone = 68;
  if (s.debate.length) pctDone = 68 + Math.min(2, s.debate.length) * 9;
  if (s.positions) pctDone = 88;
  if (s.risk) pctDone = 91;
  if (s.synthesis) pctDone = 98;
  if (s.meta.run_id) pctDone = 100;
  return pctDone;
}

function renderProgress() {
  const box = document.getElementById('progress');
  const s = state;
  box.hidden = s.status !== 'running';
  if (box.hidden) return;
  const done = seatsDone();
  const text = done < SEATS.length && !s.reality
    ? `Seats reading their data · ${done} of ${SEATS.length} done`
    : s.step;
  document.getElementById('progress-text').textContent = text;
  document.getElementById('progress-step').textContent = done < SEATS.length && !s.reality && s.step !== 'Starting…' ? s.step : '';
  document.getElementById('progress-fill').style.width = `${progressPct().toFixed(1)}%`;
}

function renderAlerts() {
  const box = document.getElementById('alerts');
  const extra = [];
  if (state.status === 'detached') {
    extra.push({ kind: 'info', title: 'Still running.', message: 'You left this page while the run was going. It carries on without you and will be in History when it finishes.' });
  }
  box.innerHTML = [...state.alerts, ...extra].map(a => `
    <div class="alert ${a.kind === 'stop' ? 'alert-stop' : a.kind === 'warn' ? 'alert-warn' : ''}" role="${a.kind === 'stop' ? 'alert' : 'status'}">
      ${a.title ? `<b>${escapeHtml(a.title)}</b> ` : ''}${aiText(a.message)}
      ${a.link ? ` <a href="${escapeHtml(a.link)}" target="_blank" rel="noopener noreferrer">Add credit ↗</a>` : ''}
    </div>`).join('');
}

function renderHeadline() {
  const el = document.getElementById('headline');
  const syn = state.synthesis;
  el.hidden = !syn;
  if (!syn) return;
  el.textContent = plainText(isExpert() ? syn.headline : (syn.plain_headline || syn.headline));
}

// Each term's bar: before the positions are in, its seats' dots gather on
// an empty track; after, the Council's lean fills it.
function termBar(term) {
  const s = state;
  const final = s.synthesis?.terms?.[term] || s.positions?.terms?.[term] || null;
  const tickWeights = Object.fromEntries((s.synthesis?.terms?.[term]?.ticks || []).map(t => [t.seat_id, t.weight]));
  const dots = SEATS.map(seat => ({
    p: s.seats[seat.id]?.terms?.[term]?.p_bullish ?? null,
    weight: tickWeights[seat.id] ?? COMPETENCE[seat.id][term],
    name: seat.name,
  }));
  const p = final && final.seats_counted !== 0 ? final.p_bullish : null;
  return { final, p, dots };
}

function renderPositions() {
  const s = state;
  const body = document.getElementById('positions-body');
  body.innerHTML = TERMS.map(term => {
    const { final, p, dots } = termBar(term);
    const expert = isExpert();
    let value = '';
    if (!final) {
      value = `<span class="pos-waiting">${s.status === 'running' ? 'Waiting for the seats' : '–'}</span>`;
    } else if (p === null) {
      value = '<span class="big noread">No read</span><span class="tiny">No seat could read its data</span>';
    } else {
      const dir = dirOf(p);
      const strength = leanStrength(p);
      const tiny = PLAIN_TINY[strength].replace('{dir}', dir === 'up' ? 'rise' : 'fall');
      value = expert
        ? `<span class="big num ${dir}">${pct(p, 1)}</span><span class="small ${dir}">${leanWords(p)}</span>
           ${final.consensus_pct ? `<span class="tiny">${Math.round(final.consensus_pct)}% of leaning seats agree</span>` : ''}`
        : `<span class="big ${dir}">${leanWords(p)}</span><span class="tiny">${tiny}</span>`;
    }
    const warnings = (s.synthesis?.terms?.[term]?.warnings || s.warnings?.terms?.[term] || [])
      .map(w => `<div class="pos-warn">${aiText(w.message)}</div>`).join('');
    const syn = s.synthesis;
    const noteText = syn ? (expert ? (syn[term] || syn.terms?.[term]?.note) : (syn[`plain_${term}`] || syn[term] || syn.terms?.[term]?.note)) : '';
    const extra = expert && final?.expected_move_pct ? `<div class="faint" style="font-size:12px">Expected move about ±${Number(final.expected_move_pct).toFixed(1)}%</div>` : '';
    return `
      <div class="pos">
        <div class="pos-term"><b>${TERM_LABEL[term]}</b><span>${{ short: 'Scored in 7 days', medium: 'Scored in 3 months', long: 'Scored in a year' }[term]}</span></div>
        <div class="pos-bar">${leanBarHtml(p, { dots })}</div>
        <div class="pos-value">${value}</div>
        ${noteText || warnings || extra ? `<div class="pos-extra">${noteText ? `<p class="pos-note">${aiText(noteText)}</p>` : ''}${extra}${warnings}</div>` : ''}
      </div>`;
  }).join('');
  document.getElementById('positions-sub').textContent = isExpert()
    ? 'Chance the price is higher at the end of each period · dots are seats'
    : 'Which way the price is likely to go · dots are seats';
}

// ---- seat cards --------------------------------------------------------------

function buildSeatGrid() {
  const grid = document.getElementById('seat-grid');
  grid.innerHTML = SEATS.map(seat => `
    <button type="button" class="seat idle" id="seat-${seat.id}" data-seat="${seat.id}">
      <div class="seat-top"><span class="seat-dot"></span><span class="seat-name">${seat.name}</span><span class="seat-status"></span></div>
      <div class="chips"></div>
      <div class="seat-text seat-reads"></div>
      <span class="seat-line"></span>
    </button>`).join('');
  grid.querySelectorAll('.seat').forEach(el => { el.onclick = () => openDrawer(el.dataset.seat, el); });
}

function updateSeatCard(id) {
  const el = document.getElementById(`seat-${id}`);
  if (!el || !state) return;
  const seat = SEAT_BY_ID[id];
  const result = state.seats[id];
  const st = state.stages[id];
  const status = el.querySelector('.seat-status');
  const line = el.querySelector('.seat-line');
  const chips = el.querySelector('.chips');
  const reason = el.querySelector('.seat-text');
  const dot = el.querySelector('.seat-dot');

  if (result) {
    const noread = result.vote === 'NO_READ';
    el.className = `seat done${noread ? ' noread' : ''}`;
    dot.className = `seat-dot ${noread ? '' : dirOf(result.lean_p_bullish)}`;
    status.className = 'seat-status';
    status.textContent = noread ? 'No read' : '';
    chips.innerHTML = TERMS.map(t => chipHtml(t, result.terms?.[t]?.p_bullish ?? null)).join('');
    reason.className = 'seat-text seat-reason';
    reason.textContent = firstSentence(result.thesis);
    line.style.width = '100%';
    return;
  }

  const running = state.status === 'running';
  el.className = `seat idle${running && st ? ' running' : ''}`;
  dot.className = 'seat-dot';
  chips.innerHTML = TERMS.map(t => `<span class="chip"><span class="t">${TERM_SHORT[t]}</span>·</span>`).join('');
  reason.className = 'seat-text seat-reads';
  reason.textContent = seat.reads;
  if (!running) {
    status.className = 'seat-status';
    status.textContent = state.status === 'detached' ? '' : 'Didn\'t finish';
    line.style.width = '0';
    return;
  }
  if (st?.wait && Date.now() < st.until) {
    const left = Math.max(0, Math.round((st.until - Date.now()) / 1000));
    status.className = 'seat-status wait';
    status.textContent = st.wait === 'queued' ? `Queued · ${left}s` : `Rate limit · ${left}s`;
    status.title = st.wait === 'queued' ? `Queued for ${st.provider}'s free tier` : `Waiting on ${st.provider}'s rate limit`;
  } else {
    status.className = 'seat-status';
    status.removeAttribute('title');
    status.textContent = !st ? 'Waiting' : st.stage === 'gathering' ? 'Reading data'
      : st.total > 1 ? `Thinking ${Math.min(st.done + 1, st.total)}/${st.total}` : 'Thinking';
  }
  const pctDone = !st ? 0 : st.stage === 'gathering' ? 12 : 20 + 75 * (st.done / (st.total || 1));
  line.style.width = `${pctDone}%`;
}

// ---- drawer ---------------------------------------------------------------------

let openSeatId = null;
let drawerReturnFocus = null;

function openDrawer(id, from) {
  openSeatId = id;
  drawerReturnFocus = from || document.activeElement;
  renderDrawer(id);
  const drawer = document.getElementById('drawer');
  drawer.hidden = false;
  // Two frames: the first lays the drawer out off-screen, the second
  // slides it in.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    drawer.classList.add('open');
    document.getElementById('drawer-backdrop').classList.add('open');
  }));
  document.getElementById('drawer-close').focus({ preventScroll: true });
}

function closeDrawer() {
  if (!openSeatId) return;
  openSeatId = null;
  const drawer = document.getElementById('drawer');
  drawer.classList.remove('open');
  document.getElementById('drawer-backdrop').classList.remove('open');
  setTimeout(() => { if (!openSeatId) drawer.hidden = true; }, 320);
  if (drawerReturnFocus) drawerReturnFocus.focus({ preventScroll: true });
}

function renderDrawer(id) {
  const seat = SEAT_BY_ID[id];
  const d = state?.seats[id];
  const expert = isExpert();
  document.getElementById('drawer-title').textContent = seat.name;
  document.getElementById('drawer-sub').textContent = seat.reads;
  const body = document.getElementById('drawer-body');
  if (!d) {
    body.innerHTML = `<p class="muted">${state?.status === 'running' ? 'Still working on its read.' : 'This seat didn\'t finish in this run.'}</p>`;
    return;
  }
  if (d.vote === 'NO_READ') {
    body.innerHTML = `
      <div class="drawer-section"><h3>No read</h3><p>${aiText(d.thesis)}</p></div>
      <p class="faint" style="font-size:13px">A seat that can't read its data sits the run out. It doesn't count toward the Council's position.</p>`;
    return;
  }
  const terms = TERMS.map(t => {
    const lean = d.terms?.[t] || {};
    const p = lean.p_bullish ?? null;
    const dir = dirOf(p);
    const counts = COMPETENCE[id]?.[t];
    const meta = [];
    if (expert && (lean.vote === 'BULLISH' || lean.vote === 'BEARISH') && lean.expected_move_pct) meta.push(`Expected move ±${Number(lean.expected_move_pct).toFixed(1)}%`);
    if (expert && counts !== undefined) meta.push(`counts ${counts.toFixed(1)} on this period`);
    if (expert && lean.dispersion) meta.push(`answers varied by ${Number(lean.dispersion).toFixed(2)}`);
    return `
      <div class="drawer-term">
        <div class="drawer-term-top"><b>${TERM_LABEL[t]}</b><span class="${dir}">${expert ? (p === null ? 'No read' : chanceWords(p)) : leanWords(p)}</span></div>
        ${leanBarHtml(p, { small: true })}
        ${lean.rationale ? `<p>${aiText(lean.rationale)}</p>` : ''}
        ${meta.length ? `<div class="meta">${meta.join(' · ')}</div>` : ''}
      </div>`;
  }).join('');
  const evidence = (d.key_evidence || []).map(e => `
    <li>${aiText(e.claim)}<small>${escapeHtml(prettySource(e.source))}${e.as_of ? ` · ${escapeHtml(e.as_of)}` : ''}</small></li>`).join('');
  const changeMind = d.what_would_change_my_mind && d.what_would_change_my_mind !== 'N/A' ? d.what_would_change_my_mind : '';
  body.innerHTML = `
    <div class="drawer-lean">${terms}</div>
    <div class="drawer-section"><h3>Its reasoning</h3><p>${aiText(d.thesis)}</p></div>
    ${evidence ? `<div class="drawer-section"><h3>Evidence</h3><ul class="evidence">${evidence}</ul></div>` : ''}
    ${changeMind ? `<div class="drawer-section"><h3>What would change its mind</h3><p>${aiText(changeMind)}</p></div>` : ''}
    ${expert && d.data_quality ? `<div class="drawer-section"><h3>Data quality</h3><p>${escapeHtml({ GOOD: 'Good', PARTIAL: 'Partial', POOR: 'Poor' }[d.data_quality] || d.data_quality)}</p></div>` : ''}`;
}

// "fundamentals_derived" -> "Fundamentals derived".
function prettySource(source) {
  const text = String(source || '').replace(/[_-]+/g, ' ').trim();
  return text ? text[0].toUpperCase() + text.slice(1) : '';
}

// ---- results sections ---------------------------------------------------------------

function renderSections() {
  const s = state;
  const box = document.getElementById('sections');
  box.hidden = !(s.synthesis || s.debate.length || s.reality);
  if (box.hidden) return;
  const expert = isExpert();
  const syn = s.synthesis;

  document.getElementById('why-body').innerHTML = syn ? `
    ${s.meta.context ? `<div class="note-block"><h3>Your question</h3><p>${escapeHtml(s.meta.context)}</p></div>` : ''}
    <div class="note-block"><p>${aiText(syn.reasoning)}</p></div>
    ${syn.correlated_evidence_warning ? `<div class="pos-warn">${aiText(syn.correlated_evidence_warning)}</div>` : ''}`
    : '<p class="faint">Written once the debate is done.</p>';

  const groups = term => {
    const g = { up: [], down: [], even: [], noread: [] };
    for (const seat of SEATS) {
      const r = s.seats[seat.id];
      if (!r) continue;
      const p = r.terms?.[term]?.p_bullish ?? null;
      g[dirOf(p)].push(expert && p !== null && dirOf(p) !== 'even' ? `${seat.name} ${pct(p)}` : seat.name);
    }
    const col = (key, label) => g[key].length
      ? `<div class="split-group"><b class="${key}">${label} (${g[key].length})</b><span>${g[key].join(', ')}</span></div>` : '';
    return col('up', '▲ Up') + col('down', '▼ Down') + col('even', '● Even') + col('noread', 'Couldn\'t read');
  };
  document.getElementById('split-body').innerHTML = `
    ${syn?.dissent_summary ? `<div class="note-block"><p>${aiText(syn.dissent_summary)}</p></div>` : ''}
    ${TERMS.map(t => `<div class="split-term"><h3>${TERM_LABEL[t]}</h3><div class="split-groups">${groups(t) || '<span class="faint">No seats yet.</span>'}</div></div>`).join('')}`;

  document.getElementById('debate-body').innerHTML = s.debate.length ? s.debate.map(r => {
    const vetoTerms = (r.prosecutor_veto_terms || []).map(t => TERM_LABEL[t] || t).join(', ');
    return `
      <div class="round">
        <h3>Round ${r.round_n}</h3>
        ${r.bull_argument ? `<div class="argument"><b class="up">Bull case</b><div>${aiText(r.bull_argument)}</div></div>` : ''}
        ${r.bear_argument ? `<div class="argument"><b class="down">Bear case</b><div>${aiText(r.bear_argument)}</div></div>` : ''}
        <div class="argument"><b>Challenger</b><div>${r.prosecutor_veto ? `<span class="even">Objects${vetoTerms ? ` (${vetoTerms})` : ''}</span>` : 'No objection'}
          ${(r.prosecutor_findings || []).length ? `<ul>${r.prosecutor_findings.map(f => `<li>${aiText(f)}</li>`).join('')}</ul>` : ''}</div></div>
      </div>`;
  }).join('') : `<p class="faint">${s.meta.reopened ? 'This run was saved before debates were kept.' : 'Starts once every seat has answered.'}</p>`;

  document.getElementById('reality-body').innerHTML = s.reality ? TERMS.filter(t => s.reality.terms?.[t]).map(t => {
    const a = s.reality.terms[t];
    const flagged = Object.entries(a.plausibility_flags || {}).filter(([, f]) => f === 'IMPLAUSIBLE').map(([id]) => seatName(id));
    const rows = [];
    if (a.max_plausible_move_pct) rows.push(`Usually moves up to ±${Number(a.max_plausible_move_pct).toFixed(1)}% over this period`);
    if (a.hit_rate_up !== null && a.hit_rate_up !== undefined) rows.push(`Rose in ${Math.round(a.hit_rate_up * 100)}% of past periods this long`);
    if (a.options_implied_move_pct) rows.push(`Options traders expect a move of about ±${Number(a.options_implied_move_pct).toFixed(1)}%`);
    if (flagged.length) rows.push(`<span class="even">Expect a bigger move than usual: ${flagged.join(', ')}</span>`);
    return `<div class="reality-term"><b>${TERM_LABEL[t]}</b><div class="stack" style="gap:3px">${rows.map(r => `<div>${r}</div>`).join('') || '<span class="faint">No price history.</span>'}</div></div>`;
  }).join('') : `<p class="faint">${s.meta.reopened ? 'This run was saved before the reality check was kept.' : 'Runs once every seat has answered.'}</p>`;

  const risk = s.risk;
  const m = s.meta;
  document.getElementById('risk-body').innerHTML = `
    <dl class="kv">
      ${risk ? `<dt>Position size</dt><dd>${expert ? `${Number(risk.position_size_pct_of_book).toFixed(1)}% of your portfolio` : `At most about ${Number(risk.position_size_pct_of_book).toFixed(1)}% of your portfolio`}</dd>` : ''}
      <dt>This run</dt><dd>${m.shape === 'lite' ? 'Lite' : 'Full'}${m.run_mode === 'free' ? ', free models' : m.run_mode === 'sample' ? ', sample answers' : ''}</dd>
      ${m.cost !== undefined && m.cost !== null ? `<dt>Cost</dt><dd>${fmtMoney(m.cost)}</dd>` : ''}
    </dl>
    ${risk ? '<p class="faint" style="font-size:13px">Sized only from how much the stock swings. The risk check never sees which way the Council leans.</p>' : ''}
    ${risk?.concentration_warning ? `<div class="pos-warn">${aiText(risk.concentration_warning)}</div>` : ''}`;
}

// ---- controls ------------------------------------------------------------------------

async function loadShapeHints() {
  const hint = document.getElementById('shape-hint');
  const calls = { full: 43, lite: 16 };
  const base = {
    full: 'Full: every seat answers 3 times and the debate runs 2 rounds',
    lite: 'Lite: every seat answers once and the debate runs 1 round. Faster and cheaper, a little less thorough',
  };
  const costs = {};
  const paint = () => {
    const cost = costs[shape];
    hint.textContent = `${base[shape]} (${calls[shape]} AI calls${cost !== undefined ? `, about ${fmtMoney(cost)}` : ''}).`;
  };
  paint();
  for (const s of ['full', 'lite']) {
    fetchJSON(`/api/settings/cost-estimate${s === 'lite' ? '?lite=true' : ''}`)
      .then(r => { costs[s] = r.total_cost_usd; calls[s] = r.total_calls || calls[s]; paint(); })
      .catch(() => {});
  }
  return paint;
}

function setShape(next, paint) {
  shape = next === 'lite' ? 'lite' : 'full';
  document.querySelectorAll('#shape-seg button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.shape === shape)));
  if (paint) paint();
}

document.addEventListener('DOMContentLoaded', async () => {
  renderNav('/index.html');
  const paint = await loadShapeHints();
  document.querySelectorAll('#shape-seg button').forEach(b => { b.onclick = () => setShape(b.dataset.shape, paint); });
  document.getElementById('run-form').addEventListener('submit', startRun);
  document.getElementById('new-run-btn').onclick = newRun;
  document.querySelectorAll('#detail-seg button').forEach(b => { b.onclick = () => setDetailMode(b.dataset.detail); });
  document.addEventListener('detailchange', renderAll);
  document.getElementById('drawer-close').onclick = closeDrawer;
  document.getElementById('drawer-backdrop').onclick = closeDrawer;
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });

  const runId = new URLSearchParams(location.search).get('run');
  if (runId) {
    openSavedRun(runId);
    return;
  }
  const saved = loadSavedState();
  if (saved?.meta) {
    state = saved;
    // The live stream can't be picked back up after leaving the page.
    if (state.status === 'running') state.status = 'detached';
    state.stages = {};
    showRun();
    return;
  }
  // Free Mode's limits go further with Lite runs, so that's its default.
  fetchJSON('/api/settings/free-mode').then(f => { if (f.enabled) setShape('lite', paint); }).catch(() => {});
  document.getElementById('ticker-input').focus();
});
