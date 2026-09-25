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
    earnings: null,
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
    case 'company':
      s.meta.company = payload.name;
      break;
    case 'earnings_soon':
      s.earnings = payload;
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
  await launchHero();
  if (listening) listening.abort();
  Object.keys(waitTicks).forEach(stopWait);
  state = emptyState({ ticker, shape, context, created_at: new Date().toISOString() });
  history.replaceState(null, '', '/index.html');
  saveState();
  showRun();
  noteEarlierRuns(state);

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
      // The stream closed without a "done": the connection dropped quietly.
      state.status = 'detached';
      saveState();
      renderAll();
      watchForResult();
    }
  } catch (err) {
    if (controller.signal.aborted || state !== mine) return;
    // Usually the phone locked or the browser paused the page. The run
    // carries on on the server; watch for it to land in History.
    state.status = 'detached';
    saveState();
    renderAll();
    watchForResult();
  } finally {
    if (listening === controller) listening = null;
  }
}

function newRun() {
  extrasFor = null;
  if (watchTimer) { clearTimeout(watchTimer); watchTimer = null; }
  document.getElementById('share-btn').hidden = true;
  document.getElementById('share-card').hidden = true;
  document.getElementById('changes').hidden = true;
  document.getElementById('price-card').hidden = true;
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
  document.getElementById('question-field').hidden = true;
  document.getElementById('question-toggle').textContent = '+ Add a question';
  document.getElementById('question-toggle').setAttribute('aria-expanded', 'false');
  loadHeroPicks();
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
    company: run.company || null,
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

  s.resolutions = Object.fromEntries(Object.entries(run.terms || {}).map(([t, x]) => [t, x.resolution]));
  s.reality = replay.reality_anchor || null;
  s.debate = replay.debate || [];
  s.risk = replay.risk || null;
  s.earnings = replay.earnings || null;
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
  renderShare();
  renderChanges();
  renderPriceChart();
  loadRunExtras();
  if (openSeatId) renderDrawer(openSeatId);
}

function renderHead() {
  const m = state.meta;
  document.getElementById('run-ticker').innerHTML = `${escapeHtml(m.ticker)}${m.company ? ` <span class="run-company">${escapeHtml(m.company)}</span>` : ''}`;
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

function reportLink() {
  const q = new URLSearchParams({ kind: 'broken', from: '/index.html' });
  if (state?.meta?.ticker && !state.invalid) q.set('ticker', state.meta.ticker);
  if (state?.meta?.run_id) q.set('run', state.meta.run_id);
  return `/guide.html?${q}#report`;
}

function renderAlerts() {
  const box = document.getElementById('alerts');
  const extra = [];
  if (state.earnings) {
    extra.push({ kind: 'warn', title: 'Earnings soon.', message: state.earnings.message });
  }
  if (state.status === 'detached') {
    extra.push({ kind: 'info', title: 'Still running.', message: state.watchGaveUp
      ? 'This run hasn\'t shown up in History yet. It may still be going (runs on free models can take a while); check History later.'
      : 'The live view lost its connection (a locked phone does this), but the run carries on. The result will open here as soon as it\'s saved.' });
  }
  box.innerHTML = [...state.alerts, ...extra].map(a => `
    <div class="alert ${a.kind === 'stop' ? 'alert-stop' : a.kind === 'warn' ? 'alert-warn' : ''}" role="${a.kind === 'stop' ? 'alert' : 'status'}">
      ${a.title ? `<b>${escapeHtml(a.title)}</b> ` : ''}${aiText(a.message)}
      ${a.link ? ` <a href="${escapeHtml(a.link)}" target="_blank" rel="noopener noreferrer">Add credit ↗</a>` : ''}
      ${a.kind === 'stop' ? ` <a href="${reportLink()}">Report this problem</a>` : ''}
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
    // Once scored: whether the direction was right comes first, then the range.
    const res = s.resolutions?.[term];
    const scored = res && res.direction_correct !== null && res.direction_correct !== undefined
      ? `<div class="pos-scored ${res.direction_correct ? 'up' : 'down'}">${res.direction_correct ? '✓ Right' : '✗ Wrong'}: the price ${res.realised_move_pct >= 0 ? 'rose' : 'fell'} ${Math.abs(res.realised_move_pct).toFixed(1)}%</div>`
      : '';
    const target = p !== null ? priceTargetHtml(final?.price_target, { realised: res?.realised_move_pct ?? null, note: term === 'short' }) : '';
    return `
      <div class="pos">
        <div class="pos-term"><b>${TERM_LABEL[term]}</b><span>${{ short: 'Scored in 7 days', medium: 'Scored in 3 months', long: 'Scored in a year' }[term]}</span></div>
        <div class="pos-bar">${leanBarHtml(p, { dots })}</div>
        <div class="pos-value">${value}</div>
        ${noteText || warnings || extra || target || scored ? `<div class="pos-extra">${scored}${noteText ? `<p class="pos-note">${aiText(noteText)}</p>` : ''}${target}${extra}${warnings}</div>` : ''}
      </div>`;
  }).join('');
  document.getElementById('positions-sub').textContent = isExpert()
    ? 'Chance the price is higher at the end of each period · dots are seats'
    : 'Which way the price is likely to go · dots are seats';
}

// ---- picking a run back up after the connection drops ----------------------------

// The ticker's runs already in History when this run started, so the new
// one can be told apart when it lands.
function noteEarlierRuns(s) {
  fetchJSON(`/api/predictions?ticker=${encodeURIComponent(s.meta.ticker)}&limit=10`)
    .then(r => { s.meta.earlier = r.runs.map(x => x.run_id); saveState(); })
    .catch(() => {});
}

let watchTimer = null;
const WATCH_EVERY_MS = 8000;
const WATCH_FOR_MS = 30 * 60 * 1000;

function watchForResult() {
  if (watchTimer || !state || state.status !== 'detached') return;
  const mine = state;
  const started = Date.parse(mine.meta.created_at) || Date.now();
  async function check() {
    watchTimer = null;
    if (state !== mine || mine.status !== 'detached') return;
    try {
      const r = await fetchJSON(`/api/predictions?ticker=${encodeURIComponent(mine.meta.ticker)}&limit=5`);
      const earlier = new Set(mine.meta.earlier || []);
      // Without the list of earlier runs, fall back to "saved after this
      // run started" (with slack for the phone's clock being off).
      const found = r.runs.find(x => mine.meta.earlier
        ? !earlier.has(x.run_id)
        : Date.parse(x.created_at + (x.created_at.endsWith('Z') ? '' : 'Z')) > started - 10 * 60 * 1000);
      if (found && state === mine) {
        clearSavedState();
        history.replaceState(null, '', `/index.html?run=${encodeURIComponent(found.run_id)}`);
        openSavedRun(found.run_id);
        return;
      }
    } catch (e) { /* offline for now; try again */ }
    if (Date.now() - started > WATCH_FOR_MS) {
      mine.watchGaveUp = true;
      saveState();
      renderAlerts();
      return;
    }
    watchTimer = setTimeout(check, WATCH_EVERY_MS);
  }
  watchTimer = setTimeout(check, 1500);
}

// Coming back to the page (unlocking the phone) checks straight away.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible' || !state || state.status !== 'detached') return;
  if (watchTimer) { clearTimeout(watchTimer); watchTimer = null; }
  watchForResult();
});

// ---- sharing and "what changed" ------------------------------------------------

// Loaded once per finished run: whether it already has a share link, and
// how it compares with the same ticker's previous run.
let extrasFor = null;

function loadRunExtras() {
  const id = state?.meta?.run_id;
  if (!id || state.status !== 'done' || extrasFor === id) return;
  extrasFor = id;
  const base = `/api/runs/${encodeURIComponent(id)}`;
  fetchJSON(`${base}/share`).then(r => { state.share = r; renderShare(); }).catch(() => {});
  fetchJSON(`${base}/changes`).then(r => { state.changes = r; renderChanges(); }).catch(() => {});
  fetchJSON(`${base}/prices`).then(r => { state.prices = r; renderPriceChart(); }).catch(() => {});
}

function toggleShare() {
  state.shareOpen = !state.shareOpen;
  renderShare();
  if (state.shareOpen) document.getElementById('share-card').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

async function setShared(on) {
  const url = `/api/runs/${encodeURIComponent(state.meta.run_id)}/share`;
  try {
    state.share = await fetchJSON(url, { method: on ? 'POST' : 'DELETE' });
  } catch (err) {
    state.share = { ...(state.share || {}), error: 'Couldn\'t change sharing. Try again.' };
  }
  renderShare();
}

// A story-sized image of the result (council/share_card.py), shared through
// the phone's share sheet where it can take files, else saved.
function shareImageUrl() {
  return `/api/runs/${encodeURIComponent(state.meta.run_id)}/image.png`;
}

async function shareImage(btn) {
  const name = `${state.meta.ticker}-ticker-council.png`;
  btn.disabled = true;
  try {
    const blob = await (await fetch(shareImageUrl(), { credentials: 'same-origin' })).blob();
    const file = new File([blob], name, { type: 'image/png' });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      await navigator.share({ files: [file], title: `${state.meta.ticker} · Ticker Council` });
    } else {
      const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: name });
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    }
  } catch (err) {
    if (err?.name !== 'AbortError') state.share = { ...(state.share || {}), error: 'Couldn\'t make the image. Try again.' };
    if (err?.name !== 'AbortError') renderShare();
  } finally {
    btn.disabled = false;
  }
}

function renderShare() {
  const btn = document.getElementById('share-btn');
  const card = document.getElementById('share-card');
  const ready = !!(state && state.status === 'done' && state.meta.run_id);
  btn.hidden = !ready;
  btn.setAttribute('aria-expanded', String(!!state?.shareOpen));
  if (!ready || !state.shareOpen) { card.hidden = true; return; }
  card.hidden = false;
  const sh = state.share || {};
  const note = 'It shows the call, the summary and how each seat leaned. Never your name, your other runs or your keys.';
  const error = sh.error ? `<p class="share-error">${escapeHtml(sh.error)}</p>` : '';
  const canShareFiles = !!(navigator.canShare && window.File);
  const imageSection = `
    <div class="share-image">
      <a class="share-thumb" href="${shareImageUrl()}" target="_blank" rel="noopener" aria-label="Open the image full size">
        <img src="${shareImageUrl()}" alt="The result as a story image: ${escapeHtml(state.meta.ticker)}'s three bars" loading="lazy" />
      </a>
      <div class="share-image-text">
        <b>Post it as an image</b>
        <p class="faint">Story-sized for Instagram, TikTok and Snapchat: the bars, the headline and the price targets. ${sh.shared ? 'Add your link as a link sticker.' : 'Create a link below to add as a link sticker.'}</p>
        <div class="share-actions">
          <button class="btn btn-primary btn-small" type="button" id="share-image">${canShareFiles ? 'Share image' : 'Save image'}</button>
          ${canShareFiles ? '<button class="btn btn-small" type="button" id="save-image">Save image</button>' : ''}
        </div>
      </div>
    </div>`;
  const linkSection = !sh.shared ? `
      <div class="share-body">
        <div><b>Share it with a link</b><p class="faint">${note} The link shows a preview of the bars when you send it.</p></div>
        <div class="share-actions">
          <button class="btn btn-small" type="button" id="share-make">Create link</button>
        </div>
      </div>` : `
      <div class="share-body">
        <div><b>Anyone with this link can see this result</b><p class="faint">${note}</p></div>
        <div class="share-link">
          <input class="input" type="text" readonly value="${escapeHtml(sh.url)}" id="share-url" aria-label="Share link" />
          <button class="btn btn-primary btn-small" type="button" id="share-copy">Copy</button>
        </div>
        <div class="share-actions">
          ${navigator.share ? '<button class="btn btn-small" type="button" id="share-native">Send link…</button>' : ''}
          <a class="btn btn-small" href="${escapeHtml(sh.url)}" target="_blank" rel="noopener">Preview</a>
          <button class="btn btn-small btn-quiet" type="button" id="share-stop">Stop sharing</button>
        </div>
      </div>`;
  card.innerHTML = `
    ${imageSection}
    ${linkSection}
    ${error}
    <div class="share-actions share-foot"><button class="btn btn-small btn-quiet" type="button" id="share-close">Close</button></div>`;

  document.getElementById('share-image').onclick = e => shareImage(e.currentTarget);
  const save = document.getElementById('save-image');
  if (save) {
    save.onclick = async () => {
      const blob = await (await fetch(shareImageUrl(), { credentials: 'same-origin' })).blob();
      const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: `${state.meta.ticker}-ticker-council.png` });
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    };
  }
  if (!sh.shared) {
    document.getElementById('share-make').onclick = () => setShared(true);
  } else {
    const input = document.getElementById('share-url');
    input.onfocus = () => input.select();
    document.getElementById('share-copy').onclick = async (e) => {
      try { await navigator.clipboard.writeText(sh.url); } catch (err) { input.select(); document.execCommand('copy'); }
      e.target.textContent = 'Copied';
      setTimeout(() => { e.target.textContent = 'Copy'; }, 1600);
    };
    const nativeBtn = document.getElementById('share-native');
    if (nativeBtn) {
      nativeBtn.onclick = () => navigator.share({
        title: `${state.meta.ticker} · Ticker Council`,
        text: `Twelve AIs debated ${state.meta.ticker}${state.meta.company ? ` (${state.meta.company})` : ''}. Here's the verdict:`,
        url: sh.url,
      }).catch(() => {});
    }
    document.getElementById('share-stop').onclick = () => setShared(false);
  }
  document.getElementById('share-close').onclick = toggleShare;
}

// The stock's closing price around the run, with the run marked. Each part
// of the line is green or red by which way it went: into the run, then since
// it (the part before goes softer once there's a move since to show).
function renderPriceChart() {
  const card = document.getElementById('price-card');
  const data = state?.prices;
  const bars = data?.bars || [];
  if (bars.length < 5) { card.hidden = true; return; }
  card.hidden = false;
  const W = 640, H = 210, L = 10, R = 58, T = 16, B = 26;
  const closes = bars.map(b => b.c);
  let lo = Math.min(...closes), hi = Math.max(...closes);
  const pad = (hi - lo) * 0.08 || hi * 0.02;
  lo -= pad; hi += pad;
  const x = i => L + (i / (bars.length - 1)) * (W - L - R);
  const y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  // The run's bar: the last trading day on or before the run date.
  let runIdx = -1;
  bars.forEach((b, i) => { if (b.d <= data.run_date) runIdx = i; });
  const runPrice = runIdx >= 0 ? bars[runIdx].c : null;
  const last = bars[bars.length - 1];
  const after = runIdx >= 0 && runIdx < bars.length - 1;
  const change = after && runPrice ? (last.c / runPrice - 1) * 100 : null;
  const dir = change === null ? 'even' : change > 0 ? 'up' : change < 0 ? 'down' : 'even';
  const path = (from, to) => bars.slice(from, to + 1).map((b, k) => `${k ? 'L' : 'M'}${x(from + k).toFixed(1)},${y(b.c).toFixed(1)}`).join('');
  const before = runIdx >= 0 ? path(0, runIdx) : path(0, bars.length - 1);
  const beforeEnd = runIdx >= 0 ? bars[runIdx].c : last.c;
  const beforeDir = beforeEnd > bars[0].c ? 'up' : beforeEnd < bars[0].c ? 'down' : 'even';
  const afterPath = after ? path(runIdx, bars.length - 1) : '';
  const area = after ? `${afterPath}L${x(bars.length - 1).toFixed(1)},${H - B}L${x(runIdx).toFixed(1)},${H - B}Z` : '';
  const ticks = [hi - pad, (hi + lo) / 2, lo + pad];
  const money = v => v >= 1000 ? `$${Math.round(v).toLocaleString()}` : `$${v.toFixed(2)}`;
  const shortDate = d => new Date(d + 'T12:00:00Z').toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  const scored = TERMS.map(t => [t, (data.resolves?.[t] || '').slice(0, 10)])
    .filter(([, d]) => d && d <= last.d && runIdx >= 0 && d > data.run_date)
    .map(([t, d]) => {
      let i = bars.findIndex(b => b.d >= d);
      if (i < 0) i = bars.length - 1;
      return `<line x1="${x(i)}" x2="${x(i)}" y1="${H - B}" y2="${H - B + 5}" stroke="var(--faint)" />
        <text x="${x(i)}" y="${H - 4}" text-anchor="middle" class="pc-axis">${TERM_SHORT[t]} scored</text>`;
    }).join('');
  const headline = change === null
    ? `<span class="faint">The four months before this run. The line carries on here as time passes.</span>`
    : `${money(runPrice)} at the run <span class="faint">→</span> ${money(last.c)} on ${shortDate(last.d)} <b class="${dir}">${change > 0 ? '+' : ''}${change.toFixed(1)}%</b>`;
  card.innerHTML = `
    <div class="card-head"><h2>Price</h2><small>${escapeHtml(data.ticker)} closing price</small></div>
    <p class="pc-line">${headline}</p>
    <div class="pc-wrap">
      <svg class="pc" viewBox="0 0 ${W} ${H}" role="img" aria-label="${escapeHtml(data.ticker)} closing price from ${shortDate(bars[0].d)} to ${shortDate(last.d)}${change === null ? '' : `, ${change > 0 ? 'up' : 'down'} ${Math.abs(change).toFixed(1)}% since the run`}">
        ${ticks.map(v => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="pc-grid" />
          <text x="${W - R + 8}" y="${y(v) + 4}" class="pc-axis">${money(v)}</text>`).join('')}
        ${area ? `<path d="${area}" class="pc-area ${dir}" />` : ''}
        <path d="${before}" class="pc-before ${beforeDir}${after ? ' soft' : ''}" />
        ${afterPath ? `<path d="${afterPath}" class="pc-after ${dir}" />` : ''}
        ${runPrice !== null && after ? `<line x1="${x(runIdx)}" x2="${W - R}" y1="${y(runPrice)}" y2="${y(runPrice)}" class="pc-ref" />` : ''}
        ${runIdx >= 0 ? `<line x1="${x(runIdx)}" x2="${x(runIdx)}" y1="${T}" y2="${H - B}" class="pc-run" />
          <circle cx="${x(runIdx)}" cy="${y(runPrice)}" r="4.5" class="pc-dot" />
          <text x="${x(runIdx)}" y="${T - 4}" text-anchor="middle" class="pc-label">Run</text>` : ''}
        <text x="${L}" y="${H - 4}" class="pc-axis">${shortDate(bars[0].d)}</text>
        ${scored}
        <text x="${W - R}" y="${H - 4}" text-anchor="end" class="pc-axis">${shortDate(last.d)}</text>
        <g class="pc-hover" hidden><line class="pc-cross" y1="${T}" y2="${H - B}" /><circle r="4" class="pc-hdot" /></g>
        <rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" class="pc-hit" />
      </svg>
      <div class="pc-tip" hidden></div>
    </div>`;
  const svg = card.querySelector('svg');
  const hover = svg.querySelector('.pc-hover');
  const tip = card.querySelector('.pc-tip');
  const move = ev => {
    const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
    const loc = pt.matrixTransform(svg.getScreenCTM().inverse());
    const i = Math.max(0, Math.min(bars.length - 1, Math.round((loc.x - L) / (W - L - R) * (bars.length - 1))));
    const b = bars[i];
    hover.hidden = false;
    hover.querySelector('line').setAttribute('x1', x(i)); hover.querySelector('line').setAttribute('x2', x(i));
    hover.querySelector('circle').setAttribute('cx', x(i)); hover.querySelector('circle').setAttribute('cy', y(b.c));
    const vsRun = runPrice && i > runIdx ? ` <span class="${b.c >= runPrice ? 'up' : 'down'}">${b.c >= runPrice ? '+' : ''}${((b.c / runPrice - 1) * 100).toFixed(1)}%</span>` : '';
    tip.innerHTML = `<b>${money(b.c)}</b>${vsRun}<br><span class="faint">${shortDate(b.d)}${i === runIdx ? ' · run' : ''}</span>`;
    tip.hidden = false;
    const box = card.querySelector('.pc-wrap').getBoundingClientRect();
    const px = (x(i) / W) * box.width;
    tip.style.left = `${Math.min(Math.max(0, px - tip.offsetWidth / 2), box.width - tip.offsetWidth)}px`;
  };
  const hit = svg.querySelector('.pc-hit');
  hit.addEventListener('pointermove', move);
  hit.addEventListener('pointerdown', move);
  hit.addEventListener('pointerleave', () => { hover.hidden = true; tip.hidden = true; });
}

function renderChanges() {
  const card = document.getElementById('changes');
  const c = state?.changes;
  if (!c || !c.previous) { card.hidden = true; return; }
  card.hidden = false;
  const expert = isExpert();
  const rows = TERMS.filter(t => c.terms[t]).map(t => {
    const { before, after, change, flipped } = c.terms[t];
    const delta = expert && change !== null
      ? `<span class="chg-delta num">${change > 0 ? '+' : ''}${(change * 100).toFixed(1)} pts</span>` : '';
    return `
      <div class="chg-row">
        <b>${TERM_LABEL[t]}</b>
        <span class="chg-move">
          <span class="${dirOf(before)}">${arrowOf(before)} ${leanWords(before)}</span>
          <span class="chg-arrow" aria-hidden="true">→</span>
          <span class="${dirOf(after)}">${arrowOf(after)} ${leanWords(after)}</span>
          ${delta}
        </span>
        ${flipped ? '<span class="pill pill-warn">Changed direction</span>' : '<span class="pill">Same direction</span>'}
      </div>`;
  }).join('');
  const flips = c.flips.length
    ? `<ul class="chg-flips">${c.flips.map(f => `
        <li><b>${escapeHtml(seatName(f.seat_id))}</b> <span class="faint">· ${TERM_LABEL[f.term]}</span>
          <span class="${dirOf(f.before)}">${leanWords(f.before)}</span> → <span class="${dirOf(f.after)}">${leanWords(f.after)}</span></li>`).join('')}</ul>`
    : '<p class="faint chg-none">No seat changed direction.</p>';
  card.innerHTML = `
    <div class="card-head"><h2>What changed</h2>
      <small>Since your last ${escapeHtml(state.meta.ticker)} run, <a href="/index.html?run=${encodeURIComponent(c.previous.run_id)}">${fmtDate(c.previous.created_at, true)}</a></small></div>
    <div class="chg-body">
      ${rows}
      <h3 class="chg-sub">Seats that changed their mind</h3>
      ${flips}
    </div>`;
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
  document.dispatchEvent(new CustomEvent('seatopened', { detail: id }));
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

// ---- the hero: the centred Run box -------------------------------------------------

const POPULAR = ['NVDA', 'AAPL', 'TSLA', 'MSFT', 'AMZN'];

// The twelve seats as dots on an ellipse around the box, idling out of step.
function buildHeroRing() {
  const ring = document.getElementById('hero-ring');
  if (!ring) return;
  ring.innerHTML = SEATS.map((seat, i) => {
    const a = (i / SEATS.length) * Math.PI * 2 - Math.PI / 2;
    const x = 50 + 48 * Math.cos(a);
    const y = 50 + 44 * Math.sin(a);
    return `<span class="hero-seat" style="left:${x.toFixed(2)}%;top:${y.toFixed(2)}%;animation-delay:${(i * 0.4).toFixed(1)}s"></span>`;
  }).join('');
}

// Pressing Run: the seats light up one by one, then the run screen opens.
async function launchHero() {
  const hero = document.getElementById('composer');
  if (hero.hidden || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  hero.querySelectorAll('.hero-seat').forEach((dot, i) => { dot.style.animationDelay = `${i * 40}ms`; });
  hero.classList.add('launch');
  await new Promise(r => setTimeout(r, 720));
  hero.classList.remove('launch');
  buildHeroRing();
}

// "Your recent" tickers to tap, or popular ones before there are any.
async function loadHeroPicks() {
  const box = document.getElementById('hero-picks');
  let tickers = [];
  try {
    const data = await fetchJSON('/api/predictions?limit=30');
    tickers = [...new Set(data.runs.map(r => r.ticker))].slice(0, 5);
  } catch (e) { /* fall back to popular ones */ }
  const recent = tickers.length > 0;
  if (!recent) tickers = POPULAR;
  box.innerHTML = `<span>${recent ? 'Recent:' : 'Try:'}</span>${tickers.map(t => `<button class="hero-pick" type="button" data-ticker="${escapeHtml(t)}">${escapeHtml(t)}</button>`).join('')}`;
  box.hidden = false;
  box.querySelectorAll('.hero-pick').forEach(b => {
    b.onclick = () => {
      const input = document.getElementById('ticker-input');
      input.value = b.dataset.ticker;
      document.getElementById('run-btn').focus();
    };
  });
}

// One quiet line with the Council's record on your scored calls.
async function loadHeroRecord() {
  const line = document.getElementById('hero-record');
  try {
    const final = (await fetchJSON('/api/archives')).benchmark?.council_final;
    if (!final?.n || final.hit_rate === null || final.hit_rate === undefined) return;
    const right = Math.round(final.hit_rate * final.n);
    line.innerHTML = `The Council has been right on <b>${right} of ${final.n}</b> of your scored calls.`;
    line.hidden = false;
  } catch (e) { /* no line */ }
}

function toggleQuestion() {
  const field = document.getElementById('question-field');
  const btn = document.getElementById('question-toggle');
  field.hidden = !field.hidden;
  btn.setAttribute('aria-expanded', String(!field.hidden));
  btn.textContent = field.hidden ? '+ Add a question' : '− No question';
  if (field.hidden) document.getElementById('context-input').value = '';
  else document.getElementById('context-input').focus();
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
  document.getElementById('question-toggle').onclick = toggleQuestion;
  buildHeroRing();
  loadHeroPicks();
  loadHeroRecord();
  document.getElementById('new-run-btn').onclick = newRun;
  document.getElementById('share-btn').onclick = toggleShare;
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
    // The live stream can't be picked back up after leaving the page,
    // but the run carries on: watch for it to land in History.
    if (state.status === 'running') state.status = 'detached';
    state.stages = {};
    showRun();
    if (state.status === 'detached') watchForResult();
    return;
  }
  // Free Mode's limits go further with Lite runs, so that's its default.
  fetchJSON('/api/settings/free-mode').then(f => { if (f.enabled) setShape('lite', paint); }).catch(() => {});
  document.getElementById('ticker-input').focus();
});
