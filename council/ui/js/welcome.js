// First sign-in (#125): three slides showing a finished sample run, then a
// setup wizard that walks through each key (the AI key, then the two free
// data keys) with the steps, a paste box and a live check. Skipping a key is
// always allowed, but first says what it costs. It ends on "run your first
// stock". After that, a Getting started checklist on the Run page ticks
// itself off. What's been seen and done is kept on the server per account
// (/api/onboarding); the step someone is on is kept in this browser, so
// leaving to make a key and coming back picks up where they were.

let onboard = null;
let tourStep = 0;
let skipAsk = null;          // what the "skip?" question is about, while it shows
let lastCheck = null;        // the last key check's message: { name, text, kind }
let keyBusy = false;
const skipped = new Set();   // what was skipped this time through
const setup = { provider: null };  // 'gemini' or 'claude', once picked

const SETUP_SAVE = 'council_setup';
const SEAT_COUNT = 12;

function keySet(name) { return !!onboard?.keys?.[name]; }
function hasAnyAiKey() { return !!onboard?.steps?.key; }
function aiKeyName() { return setup.provider === 'claude' ? 'anthropic_api_key' : 'google_api_key'; }

// What skipping each step gives up, said before they skip it.
const SKIP_COST = {
  ai: {
    title: 'Skip the AI key?',
    body: 'Without one, the Council can only replay the sample answers about NVDA you just saw. It can\'t read any other stock. You can add a key later in Settings.',
  },
  close: {
    title: 'Leave setup without an AI key?',
    body: 'Without one, the Council can only replay the sample answers about NVDA you just saw. It can\'t read any other stock. You can finish setup later from the Run page.',
  },
  fred_api_key: {
    title: 'Skip the FRED key?',
    body: 'The Economy seat will sit out every run: interest rates, inflation and jobs data won\'t count toward the verdict. The other seats still run.',
  },
  alpha_vantage_api_key: {
    title: 'Skip the Alpha Vantage key?',
    body: 'The Congress Trades seat will sit out every run: stock trades by members of Congress won\'t count toward the verdict. The other seats still run.',
  },
};

// ---- the sample run the first slides draw (sample-run.js) ------------------------------

function sampleBars() {
  return ['short', 'medium', 'long'].map(term => {
    const t = SAMPLE_RUN.terms[term];
    const dots = t.ticks.map(x => ({ p: x.p_bullish, weight: x.weight, name: seatName(x.seat_id) }));
    return `
      <div class="sample-term">
        <div class="sample-term-head"><b>${escapeHtml(t.window[0].toUpperCase() + t.window.slice(1))}</b><span class="${dirOf(t.p_bullish)}">${leanWords(t.p_bullish)}</span></div>
        ${leanBarHtml(t.p_bullish, { dots, expert: false, small: term !== 'short' })}
        ${priceTargetHtml(t.price_target, { compact: true })}
      </div>`;
  }).join('');
}

function sampleSeats() {
  return SAMPLE_RUN.seats.map(s => `
    <div class="sample-seat">
      <div class="sample-seat-head"><b>${escapeHtml(seatName(s.seat_id))}</b><span class="chip ${dirOf(s.p_bullish)}">${arrowOf(s.p_bullish)} ${leanWords(s.p_bullish)}</span></div>
      <p>${escapeHtml(s.rationale)}</p>
    </div>`).join('');
}

// ---- the steps ------------------------------------------------------------------

function keyStepHtml(name, intro) {
  const guide = KEY_GUIDE_BY_NAME[name];
  const { steps } = guide;
  const msg = lastCheck?.name === name ? lastCheck
    : keySet(name) ? { text: `✓ Your ${guide.label} key is added. Paste a new one to replace it, or go on.`, kind: 'ok' }
    : { text: '', kind: '' };
  return `
    ${intro ? `<p class="setup-intro">${escapeHtml(intro)}</p>` : ''}
    <ol class="setup-steps">${steps.map(s => `
      <li><span>${escapeHtml(s.text.replace('paste it into the box', 'paste it below'))}</span>${s.link
        ? `<a class="btn btn-small" href="${s.link}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.linkText)} ↗</a>` : ''}</li>`).join('')}
    </ol>
    <form class="setup-key" data-key="${name}" novalidate>
      <label class="visually-hidden" for="setup-key-input">${escapeHtml(guide.label)} key</label>
      <input class="input" id="setup-key-input" type="text" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" placeholder="${escapeHtml(guide.placeholder)}" />
      <div class="setup-key-actions">
        ${navigator.clipboard?.readText ? '<button class="btn" type="button" data-tour="paste">Paste</button>' : ''}
        <button class="btn btn-primary" type="submit">Check key</button>
      </div>
    </form>
    <p class="setup-msg ${msg.kind}" id="setup-msg" role="status">${escapeHtml(msg.text)}</p>
    ${guide.cost === 'Free.' ? '' : `<p class="setup-cost faint">${escapeHtml(guide.cost)}</p>`}`;
}

function finishHtml() {
  const out = [];
  if (!keySet('fred_api_key')) out.push({ seat: 'macro_sage', step: 'fred', why: 'needs a free FRED key' });
  if (!keySet('alpha_vantage_api_key')) out.push({ seat: 'senate_watcher', step: 'av', why: 'needs a free Alpha Vantage key' });
  const ai = hasAnyAiKey();
  const mode = !ai ? 'Sample answers only. Add an AI key to read real stocks.'
    : keySet('anthropic_api_key') ? 'Runs use Claude.'
    : onboard.free_mode ? 'Runs use Google Gemini for free (Free Mode is on).'
    : 'Runs use your AI key.';
  return `
    <div class="setup-ready">
      <div class="setup-count"><b>${ai ? SEAT_COUNT - out.length : 0}</b><span>of ${SEAT_COUNT} seats reading real data</span></div>
      <p>${escapeHtml(mode)}</p>
      ${!ai ? '<p class="setup-out-one"><button class="linklike" type="button" data-tour="goto" data-step="choose">Add an AI key</button></p>' : ''}
      ${ai && out.length ? `<ul class="setup-out">${out.map(o => `
        <li><span><b>${escapeHtml(seatName(o.seat))}</b> sits out: ${o.why}.</span>
          <button class="linklike" type="button" data-tour="goto" data-step="${o.step}">Add it</button></li>`).join('')}</ul>` : ''}
    </div>
    <form class="setup-run" id="setup-run" novalidate>
      <label for="setup-ticker">${ai ? 'Run your first stock' : 'Run the sample'}</label>
      <div class="setup-run-row">
        <input class="input input-ticker" id="setup-ticker" value="NVDA" maxlength="8" spellcheck="false" autocapitalize="characters" ${ai ? '' : 'readonly'} />
        <button class="btn btn-primary" type="submit">Run</button>
      </div>
      <p class="faint">${ai ? 'Any US ticker works. A full run takes a few minutes, and you can lock your phone meanwhile.' : 'Without an AI key, every run replays the NVDA sample.'}</p>
    </form>`;
}

const TOUR_STEPS = [
  {
    id: 'hello',
    group: 'intro',
    title: 'Welcome to Ticker Council',
    body: 'Twelve AI analysts each read one kind of data about a stock, debate, and give you one verdict for the next week, the next 3 months and the next year. Here\'s a finished run, on sample data.',
    art: () => `
      <div class="tour-art sample-head">
        <div class="sample-ticker"><b>${escapeHtml(SAMPLE_RUN.ticker)}</b><span class="pill pill-warn">Sample</span></div>
        <p>${escapeHtml(SAMPLE_RUN.headline)}</p>
      </div>`,
  },
  {
    id: 'bars',
    group: 'intro',
    title: 'One verdict for each period',
    body: 'Left is down, right is up, the middle is a toss-up; the dots are the twelve seats. Under each bar: the most likely price, and a range with the chance the price ends inside it. A knob near the middle means the Council isn\'t sure, and it shows that on purpose.',
    art: () => `<div class="tour-art sample-terms">${sampleBars()}</div>`,
  },
  {
    id: 'seats',
    group: 'intro',
    title: 'Every seat shows its reasoning',
    body: 'Tap any seat for its full reasoning and evidence. Every run is saved in History and scored when its time is up, so you can see whether the Council was right.',
    art: () => `<div class="tour-art sample-seats">${sampleSeats()}</div>`,
  },
  {
    id: 'choose',
    group: 'setup',
    skip: 'ai',
    title: 'Choose the AI your Council runs on',
    body: 'You bring your own AI key, so you only pay your AI provider for what you use. You can add the other one later.',
    art: () => `
      <div class="setup-choices">
        <button class="setup-choice ${setup.provider === 'gemini' ? 'on' : ''}" type="button" data-tour="pick" data-provider="gemini">
          <span class="setup-choice-head"><b>Google Gemini</b><span class="pill pill-free">Free</span></span>
          <span>No card needed, about 2 minutes to set up. Answers are weaker and runs slower, and Google may use what you send on its free tier.</span>
        </button>
        <button class="setup-choice ${setup.provider === 'claude' ? 'on' : ''}" type="button" data-tour="pick" data-provider="claude">
          <span class="setup-choice-head"><b>Anthropic Claude</b><span class="pill pill-accent">Best answers</span></span>
          <span>Pay as you go: a typical run costs well under a dollar, and $5 of credit is plenty to start. Needs a card.</span>
        </button>
      </div>
      ${hasAnyAiKey() ? '<p class="setup-msg ok">✓ You already have an AI key added. Pick one to add another, or go on.</p>' : ''}`,
    ready: hasAnyAiKey,
  },
  {
    id: 'ai',
    group: 'setup',
    skip: 'ai',
    title: () => setup.provider === 'claude' ? 'Add your Claude key' : 'Add your free Gemini key',
    art: () => keyStepHtml(aiKeyName(), setup.provider === 'claude'
      ? 'Each button opens Anthropic\'s site in a new tab. Come back here with the key.'
      : 'Each button opens Google AI Studio in a new tab. Sign in with any Google account, then come back here with the key.'),
    ready: () => keySet(aiKeyName()) || hasAnyAiKey(),
  },
  {
    id: 'fred',
    group: 'setup',
    skip: 'fred_api_key',
    title: 'Economy seat: a free FRED key',
    art: () => keyStepHtml('fred_api_key', 'The Economy seat reads interest rates, inflation and jobs data from the St. Louis Fed. Free, about 2 minutes.'),
    ready: () => keySet('fred_api_key'),
  },
  {
    id: 'av',
    group: 'setup',
    skip: 'alpha_vantage_api_key',
    title: 'Congress Trades seat: a free Alpha Vantage key',
    art: () => keyStepHtml('alpha_vantage_api_key', 'The Congress Trades seat reads stock trades disclosed by members of the House and Senate. Free, about 1 minute.'),
    ready: () => keySet('alpha_vantage_api_key'),
  },
  {
    id: 'finish',
    group: 'setup',
    title: () => hasAnyAiKey() ? 'Your Council is ready' : 'You can look around',
    art: finishHtml,
  },
];

function stepIndex(id) { return TOUR_STEPS.findIndex(s => s.id === id); }

// ---- saving -------------------------------------------------------------------------

async function saveOnboarding(changes) {
  try {
    onboard = await fetchJSON('/api/onboarding', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes),
    });
  } catch (e) { /* not saved; it will ask again next time */ }
  renderChecklist();
}

async function refreshOnboarding() {
  try { onboard = await fetchJSON('/api/onboarding'); } catch (e) { /* keep what we had */ }
  renderChecklist();
}

function rememberStep() {
  try { localStorage.setItem(SETUP_SAVE, JSON.stringify({ step: TOUR_STEPS[tourStep].id, provider: setup.provider })); } catch (e) { /* fine */ }
}

function forgetStep() {
  try { localStorage.removeItem(SETUP_SAVE); } catch (e) { /* fine */ }
}

function savedStep() {
  try { return JSON.parse(localStorage.getItem(SETUP_SAVE) || 'null'); } catch (e) { return null; }
}

// ---- opening, moving, closing ----------------------------------------------------------

function showTour(startAt = 'hello') {
  tourStep = Math.max(0, stepIndex(startAt));
  skipAsk = null;
  const tour = document.getElementById('tour');
  const backdrop = document.getElementById('tour-backdrop');
  tour.hidden = false;
  backdrop.hidden = false;
  requestAnimationFrame(() => { tour.classList.add('open'); backdrop.classList.add('open'); });
  renderTour();
  document.addEventListener('keydown', tourKeys);
}

function goTo(id) {
  tourStep = stepIndex(id);
  skipAsk = null;
  renderTour();
  document.getElementById('tour').scrollTop = 0;
}

function nextStep() {
  // Past the choice without picking (they already have a key): no key step.
  if (TOUR_STEPS[tourStep].id === 'choose' && !setup.provider) { goTo('fred'); return; }
  if (tourStep < TOUR_STEPS.length - 1) goTo(TOUR_STEPS[tourStep + 1].id);
}

function prevStep() {
  if (TOUR_STEPS[tourStep].id === 'fred' && !setup.provider) { goTo('choose'); return; }
  if (tourStep > 0) goTo(TOUR_STEPS[tourStep - 1].id);
}

// Moving on from a key step without its key asks first.
function tryNext() {
  const step = TOUR_STEPS[tourStep];
  if (step.skip && !(step.ready && step.ready())) { skipAsk = step.skip; renderTour(); return; }
  nextStep();
}

function confirmSkip() {
  const what = skipAsk;
  skipped.add(what);
  skipAsk = null;
  if (what === 'ai') goTo('fred');  // skipping the AI key skips the choice too
  else nextStep();
}

function closeTour() {
  const tour = document.getElementById('tour');
  tour.classList.remove('open');
  document.getElementById('tour-backdrop').classList.remove('open');
  setTimeout(() => { tour.hidden = true; document.getElementById('tour-backdrop').hidden = true; }, 200);
  document.removeEventListener('keydown', tourKeys);
  forgetStep();
  if (!onboard?.tour_done || !onboard?.setup_done) saveOnboarding({ tour_done: true, setup_done: true });
  const params = new URLSearchParams(location.search);
  if (params.has('tour') || params.has('setup')) history.replaceState(null, '', '/index.html');
}

// Leaving partway through setup without an AI key asks first, like skipping.
function tryClose() {
  const step = TOUR_STEPS[tourStep];
  if (step.group === 'setup' && step.id !== 'finish' && !hasAnyAiKey() && !skipped.has('ai')) {
    skipAsk = 'close';
    renderTour();
    return;
  }
  closeTour();
}

function tourKeys(e) {
  if (e.target.closest?.('input')) return;
  const intro = TOUR_STEPS[tourStep].group === 'intro';
  if (e.key === 'Escape') {
    if (skipAsk) { skipAsk = null; renderTour(); } else if (intro) goTo('choose'); else tryClose();
  }
  if (intro && e.key === 'ArrowRight') nextStep();
  if (intro && e.key === 'ArrowLeft' && tourStep > 0) prevStep();
}

// ---- drawing ------------------------------------------------------------------------

function textOf(value) { return typeof value === 'function' ? value() : value; }

function skipSheetHtml() {
  const cost = SKIP_COST[skipAsk];
  const aboutAi = skipAsk === 'ai' || skipAsk === 'close';
  return `
    <div class="skip-sheet" role="alertdialog" aria-labelledby="skip-title" aria-describedby="skip-body">
      <h3 id="skip-title">${escapeHtml(cost.title)}</h3>
      <p id="skip-body">${escapeHtml(cost.body)}</p>
      <div class="skip-actions">
        <button class="btn btn-primary" type="button" data-tour="unskip">${aboutAi ? 'Add a key' : 'Add the key'}</button>
        <button class="btn btn-quiet" type="button" data-tour="${skipAsk === 'close' ? 'close-anyway' : 'skip-anyway'}">${skipAsk === 'close' ? 'Leave anyway' : 'Skip anyway'}</button>
      </div>
    </div>`;
}

function renderTour() {
  rememberStep();
  const step = TOUR_STEPS[tourStep];
  const group = TOUR_STEPS.filter(s => s.group === step.group);
  const pos = group.indexOf(step);
  const isSetup = step.group === 'setup';
  const ready = step.ready ? step.ready() : true;

  const count = isSetup ? `Setup · ${pos + 1} of ${group.length}` : `${pos + 1} of ${group.length}`;
  const topButton = isSetup
    ? `<button class="btn btn-small btn-quiet" type="button" data-tour="close">${step.id === 'finish' ? 'Close' : 'Finish later'}</button>`
    : '<button class="btn btn-small btn-quiet" type="button" data-tour="to-setup">Skip intro</button>';
  const primaryLabel = step.id === 'seats' ? 'Set up my Council' : 'Next';
  const primary = step.id === 'finish' || (step.skip && !ready) ? ''
    : `<button class="btn btn-small btn-primary" type="button" data-tour="next">${primaryLabel}</button>`;
  const skipLink = step.skip && !ready && !skipAsk
    ? `<button class="btn btn-small btn-quiet" type="button" data-tour="try-skip">${step.skip === 'ai' ? 'Set up later' : 'Skip'}</button>` : '';
  const body = textOf(step.body);

  document.getElementById('tour').innerHTML = `
    <div class="tour-top">
      <p class="tour-count">${count}</p>
      ${topButton}
    </div>
    ${isSetup ? '' : step.art()}
    <div class="tour-text">
      <h2 id="tour-title" tabindex="-1">${escapeHtml(textOf(step.title))}</h2>
      ${body ? `<p>${escapeHtml(body)}</p>` : ''}
    </div>
    ${isSetup ? `<div class="setup-body">${step.art()}</div>` : ''}
    ${skipAsk ? skipSheetHtml() : ''}
    <div class="tour-foot">
      <div class="tour-dots" aria-hidden="true">${group.map((_, i) => `<span class="${i === pos ? 'on' : ''}"></span>`).join('')}</div>
      <div class="tour-nav">
        ${tourStep ? '<button class="btn btn-small" type="button" data-tour="back">Back</button>' : ''}
        ${skipLink}
        ${primary}
      </div>
    </div>`;

  if (skipAsk) {
    const sheet = document.querySelector('#tour .skip-sheet');
    sheet.scrollIntoView({ block: 'nearest' });
    sheet.querySelector('.btn-primary').focus({ preventScroll: true });
    return;
  }
  // On phones, don't pop the keyboard open on arrival.
  const el = document.querySelector('#tour [data-tour="next"], #tour #setup-key-input, #tour #setup-ticker') || document.querySelector('#tour-title');
  if (el && !(el.matches('input') && matchMedia('(pointer: coarse)').matches)) el.focus({ preventScroll: true });
}

function setupMsg(text, kind = '') {
  const el = document.getElementById('setup-msg');
  if (!el) return;
  el.textContent = text;
  el.className = `setup-msg ${kind}`;
}

// fetchJSON errors read '400: {"detail":"..."}' -- show just the detail.
function friendlyKeyError(err) {
  const match = /^\d+: (.*)$/s.exec(err.message);
  if (!match) return 'Couldn\'t save the key. Check your connection and try again.';
  try { return JSON.parse(match[1]).detail || match[1]; } catch (e) { return match[1]; }
}

async function checkAndSaveKey(form) {
  if (keyBusy) return;
  const name = form.dataset.key;
  const input = form.querySelector('input');
  const value = input.value.trim();
  if (!value) { setupMsg('Paste the key into the box first.', 'bad'); input.focus(); return; }
  keyBusy = true;
  form.querySelectorAll('button').forEach(b => { b.disabled = true; });
  setupMsg('Checking the key…');
  try {
    const data = await fetchJSON('/api/settings/keys', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, value, check: true }),
    });
    // With only free keys the server turns Free Mode on by itself. Someone
    // who just chose Claude wants Claude, so a Free Mode left on from before
    // goes off.
    if (name === 'anthropic_api_key' && onboard?.free_mode) {
      try {
        await fetchJSON('/api/settings/free-mode', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: false }),
        });
      } catch (e) { /* the last step says which is in use */ }
    }
    await refreshOnboarding();
    const freeMode = name === 'google_api_key' && onboard?.free_mode;
    skipped.delete(name);
    const check = data.check || { status: 'ok' };
    lastCheck = check.status === 'ok'
      ? { name, kind: 'ok', text: freeMode ? '✓ Key works. Free Mode is on, so every seat uses Gemini.' : '✓ Key works and is saved.' }
      : { name, kind: 'warn', text: check.message };
    renderTour();
  } catch (err) {
    setupMsg(friendlyKeyError(err), 'bad');
    form.querySelectorAll('button').forEach(b => { b.disabled = false; });
  } finally {
    keyBusy = false;
  }
}

async function pasteKey() {
  const input = document.getElementById('setup-key-input');
  try {
    input.value = (await navigator.clipboard.readText()).trim();
    if (input.value) checkAndSaveKey(input.closest('form'));
    else setupMsg('Nothing copied yet. Copy the key first, then tap Paste.', 'bad');
  } catch (e) {
    setupMsg('Your browser didn\'t allow pasting from here. Tap the box and paste instead.', 'bad');
    input.focus();
  }
}

function startFirstRun(form) {
  const ticker = form.querySelector('input').value.trim().toUpperCase() || 'NVDA';
  closeTour();
  const box = document.getElementById('ticker-input');
  if (!box) return;
  box.value = ticker;
  document.getElementById('run-form').requestSubmit();
}

function onTourClick(e) {
  const btn = e.target.closest('[data-tour]');
  if (!btn) return;
  const what = btn.dataset.tour;
  if (what === 'next') tryNext();
  if (what === 'back') prevStep();
  if (what === 'to-setup') goTo('choose');
  if (what === 'close') tryClose();
  if (what === 'try-skip') tryNext();
  if (what === 'skip-anyway') confirmSkip();
  if (what === 'close-anyway') { skipped.add('ai'); closeTour(); }
  if (what === 'unskip') {
    const was = skipAsk;
    skipAsk = null;
    if (was === 'close' && TOUR_STEPS[tourStep].id !== 'ai') goTo('choose');
    else renderTour();
  }
  if (what === 'pick') { setup.provider = btn.dataset.provider; goTo('ai'); }
  if (what === 'goto') goTo(btn.dataset.step);
  if (what === 'paste') pasteKey();
}

function onTourSubmit(e) {
  e.preventDefault();
  if (e.target.matches('.setup-key')) checkAndSaveKey(e.target);
  if (e.target.matches('#setup-run')) startFirstRun(e.target);
}

// ---- the Getting started checklist ---------------------------------------------------

function renderChecklist() {
  const card = document.getElementById('onboard');
  if (!card || !onboard) return;
  const composerShown = !document.getElementById('composer').hidden;
  const s = onboard.steps;
  const items = [
    { done: s.key, title: 'Add an AI key', hint: 'A free Google Gemini key works.', setup: ['Set it up', 'choose'] },
    { done: s.run, title: 'Run your first stock', hint: 'Type a ticker below, like NVDA.' },
    { done: s.seat, title: 'Tap a seat to read its reasoning', hint: 'After a run, tap any seat card.' },
    { done: s.home, title: 'Add Ticker Council to your Home Screen', hint: 'Opens full screen, like an app.', link: ['How', '/guide.html#install'], manual: true },
  ];
  const doneCount = items.filter(i => i.done).length;
  if (onboard.checklist_hidden || doneCount === items.length || !composerShown) { card.hidden = true; return; }
  card.hidden = false;
  card.innerHTML = `
    <div class="card-head"><h2>Getting started</h2><small>${doneCount} of ${items.length} done · <button class="linklike" type="button" data-onboard="hide">Hide</button></small></div>
    <div class="onboard-progress" aria-hidden="true"><span style="width:${(doneCount / items.length) * 100}%"></span></div>
    <ul class="onboard-list">${items.map(it => `
      <li class="${it.done ? 'done' : ''}">
        <span class="onboard-check" aria-hidden="true">${it.done ? '✓' : ''}</span>
        <span class="onboard-text"><b>${it.title}</b>${it.done ? '' : `<span class="faint">${it.hint}</span>`}</span>
        <span class="onboard-act">${it.done ? '' : [
          it.setup ? `<button class="linklike" type="button" data-onboard="setup" data-step="${it.setup[1]}">${it.setup[0]}</button>` : '',
          it.link ? `<a href="${it.link[1]}">${it.link[0]}</a>` : '',
          it.manual ? '<button class="linklike" type="button" data-onboard="home">Done</button>' : '',
        ].filter(Boolean).join(' · ')}</span>
      </li>`).join('')}
    </ul>
    <p class="onboard-foot"><button class="linklike" type="button" data-onboard="tour">Show the welcome and setup again</button></p>`;
}

function onChecklistClick(e) {
  const btn = e.target.closest('[data-onboard]');
  const what = btn?.dataset.onboard;
  if (what === 'hide') saveOnboarding({ checklist_hidden: true });
  if (what === 'home') saveOnboarding({ home_screen: true });
  if (what === 'tour') showTour();
  if (what === 'setup') showTour(btn.dataset.step);
}

document.addEventListener('DOMContentLoaded', async () => {
  const card = document.getElementById('onboard');
  if (!card) return;
  card.addEventListener('click', onChecklistClick);
  const tour = document.getElementById('tour');
  tour.addEventListener('click', onTourClick);
  tour.addEventListener('submit', onTourSubmit);
  // Tapping outside the sheet never closes setup (too easy to lose a
  // half-pasted key); during the intro it moves on to setup.
  document.getElementById('tour-backdrop').addEventListener('click', () => {
    if (TOUR_STEPS[tourStep].group === 'intro') goTo('choose');
  });
  // The checklist belongs with the ticker box: hide it while a run is shown.
  new MutationObserver(renderChecklist).observe(document.getElementById('composer'), { attributes: true, attributeFilter: ['hidden'] });
  // Opening a seat's reasoning, and opening the installed app, tick themselves off.
  document.addEventListener('seatopened', () => { if (onboard && !onboard.steps.seat) saveOnboarding({ seat_opened: true }); });
  try {
    onboard = await fetchJSON('/api/onboarding');
  } catch (e) { return; }
  const installed = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  if (installed && !onboard.steps.home) saveOnboarding({ home_screen: true });
  renderChecklist();

  const params = new URLSearchParams(location.search);
  if (params.has('setup')) { showTour('choose'); return; }
  if (params.has('tour')) { showTour(); return; }
  if (!onboard.show_setup || params.has('run')) return;
  // Back from making a key in another tab, or the page reloaded: carry on.
  const saved = savedStep();
  if (saved && stepIndex(saved.step) >= 0) {
    setup.provider = saved.provider || null;
    showTour(saved.step === 'ai' && !setup.provider ? 'choose' : saved.step);
  } else {
    // Someone who got past the old tour without a key goes straight to setup.
    showTour(onboard.tour_done ? 'choose' : 'hello');
  }
});
