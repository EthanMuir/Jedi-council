// First sign-in (#125): four short welcome slides, then a Getting started
// checklist on the Run page that ticks itself off. What's been seen and
// done is kept on the server per account (/api/onboarding).

let onboard = null;
let tourStep = 0;

const TOUR_STEPS = [
  {
    title: 'Welcome to Ticker Council',
    body: 'Twelve AI analysts each read one kind of data about a stock: the price chart, earnings, analyst targets, insider and Congress trades, the options market and more. They debate, and the Council gives you one verdict for the next week, the next 3 months and the next year.',
    art: () => `<div class="tour-art tour-seats">${['Price Chart', 'Congress Trades', 'Options Market', 'Insider Trades', 'News'].map((n, i) => `<span class="chip ${['up', 'up', 'up', 'down', 'even'][i]}">${n} ${['▲', '▲', '▲', '▼', '●'][i]}</span>`).join('')}<span class="chip">+ 7 more</span></div>`,
  },
  {
    title: 'Add an AI key',
    body: 'The Council runs on your own AI key, so you only pay your AI provider for what you use. A free Google Gemini key is enough to start and takes about two minutes. Until you add one, runs use sample answers so you can look around.',
    art: () => `<div class="tour-art tour-key"><span class="tour-key-row"><b>Google Gemini</b><span class="pill pill-free">Free</span></span><span class="tour-key-row"><b>Anthropic</b><span class="pill">Best answers</span></span></div>`,
    action: { label: 'Add a free key', href: '/settings.html#keys' },
  },
  {
    title: 'Run a stock, read the verdict',
    body: 'Type a ticker like NVDA and press Run. Each period gets a bar: left is down, right is up, and the middle is a toss-up. The dots are the seats. A knob near the middle means the Council isn\'t sure, and it shows that on purpose.',
    art: () => `<div class="tour-art">${leanBarHtml(0.556, {
      dots: [0.61, 0.58, 0.57, 0.55, 0.5, 0.5, 0.47, 0.6, 0.54, 0.53, 0.45, 0.56].map((p, i) => ({ p, weight: 0.4 + (i % 3) * 0.3, name: SEATS[i].name })),
      expert: false,
    })}</div>`,
  },
  {
    title: 'Dig in, then check back',
    body: 'Tap any seat for its full reasoning and evidence. Every run is saved in History and scored when its time is up, so you can see whether the Council was right. Tip: add Ticker Council to your Home Screen to open it like an app.',
    art: () => `<div class="tour-art tour-steps"><span>Tap a seat</span><span aria-hidden="true">→</span><span>Saved in History</span><span aria-hidden="true">→</span><span>Scored</span></div>`,
    action: { label: 'How to add it to your Home Screen', href: '/guide.html#install', quiet: true },
  },
];

async function saveOnboarding(changes) {
  try {
    onboard = await fetchJSON('/api/onboarding', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes),
    });
  } catch (e) { /* not saved; it will ask again next time */ }
  renderChecklist();
}

// ---- the welcome slides -----------------------------------------------------------

function showTour() {
  tourStep = 0;
  document.getElementById('tour').hidden = false;
  document.getElementById('tour-backdrop').hidden = false;
  requestAnimationFrame(() => {
    document.getElementById('tour').classList.add('open');
    document.getElementById('tour-backdrop').classList.add('open');
  });
  renderTour();
  document.addEventListener('keydown', tourKeys);
}

function closeTour() {
  const tour = document.getElementById('tour');
  tour.classList.remove('open');
  document.getElementById('tour-backdrop').classList.remove('open');
  setTimeout(() => { tour.hidden = true; document.getElementById('tour-backdrop').hidden = true; }, 200);
  document.removeEventListener('keydown', tourKeys);
  if (!onboard?.tour_done) saveOnboarding({ tour_done: true });
  if (new URLSearchParams(location.search).has('tour')) history.replaceState(null, '', '/index.html');
}

function tourKeys(e) {
  if (e.key === 'Escape') closeTour();
  if (e.key === 'ArrowRight' && tourStep < TOUR_STEPS.length - 1) { tourStep += 1; renderTour(); }
  if (e.key === 'ArrowLeft' && tourStep > 0) { tourStep -= 1; renderTour(); }
}

function renderTour() {
  const step = TOUR_STEPS[tourStep];
  const last = tourStep === TOUR_STEPS.length - 1;
  const action = step.action
    ? `<a class="btn ${step.action.quiet ? 'btn-quiet' : ''} tour-action" href="${step.action.href}" data-tour-leave>${step.action.label}</a>` : '';
  document.getElementById('tour').innerHTML = `
    <div class="tour-top">
      <p class="tour-count">${tourStep + 1} of ${TOUR_STEPS.length}</p>
      <button class="btn btn-small btn-quiet" type="button" data-tour="skip">${last ? 'Close' : 'Skip'}</button>
    </div>
    ${step.art()}
    <div class="tour-text">
      <h2 id="tour-title">${step.title}</h2>
      <p>${step.body}</p>
    </div>
    ${action}
    <div class="tour-foot">
      <div class="tour-dots" aria-hidden="true">${TOUR_STEPS.map((_, i) => `<span class="${i === tourStep ? 'on' : ''}"></span>`).join('')}</div>
      <div class="tour-nav">
        ${tourStep ? '<button class="btn btn-small" type="button" data-tour="back">Back</button>' : ''}
        <button class="btn btn-small btn-primary" type="button" data-tour="${last ? 'done' : 'next'}">${last ? 'Run your first stock' : 'Next'}</button>
      </div>
    </div>`;
  document.querySelector('#tour [data-tour="' + (last ? 'done' : 'next') + '"]').focus({ preventScroll: true });
}

function onTourClick(e) {
  if (e.target.closest('[data-tour-leave]')) { saveOnboarding({ tour_done: true }); return; }
  const what = e.target.closest('[data-tour]')?.dataset.tour;
  if (!what) return;
  if (what === 'next') { tourStep += 1; renderTour(); }
  if (what === 'back') { tourStep -= 1; renderTour(); }
  if (what === 'skip') closeTour();
  if (what === 'done') {
    closeTour();
    document.getElementById('ticker-input')?.focus();
  }
}

// ---- the Getting started checklist ---------------------------------------------------

function renderChecklist() {
  const card = document.getElementById('onboard');
  if (!card || !onboard) return;
  const composerShown = !document.getElementById('composer').hidden;
  const s = onboard.steps;
  const items = [
    { done: s.key, title: 'Add an AI key', hint: 'A free Google Gemini key works.', link: ['Add a key', '/settings.html#keys'] },
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
    <ul class="onboard-list">${items.map((it, i) => `
      <li class="${it.done ? 'done' : ''}">
        <span class="onboard-check" aria-hidden="true">${it.done ? '✓' : ''}</span>
        <span class="onboard-text"><b>${it.title}</b>${it.done ? '' : `<span class="faint">${it.hint}</span>`}</span>
        <span class="onboard-act">${it.done ? '' : [
          it.link ? `<a href="${it.link[1]}">${it.link[0]}</a>` : '',
          it.manual ? `<button class="linklike" type="button" data-onboard="home">Done</button>` : '',
        ].filter(Boolean).join(' · ')}</span>
      </li>`).join('')}
    </ul>
    <p class="onboard-foot"><button class="linklike" type="button" data-onboard="tour">Show the welcome tour again</button></p>`;
}

function onChecklistClick(e) {
  const what = e.target.closest('[data-onboard]')?.dataset.onboard;
  if (what === 'hide') saveOnboarding({ checklist_hidden: true });
  if (what === 'home') saveOnboarding({ home_screen: true });
  if (what === 'tour') showTour();
}

document.addEventListener('DOMContentLoaded', async () => {
  const card = document.getElementById('onboard');
  if (!card) return;
  card.addEventListener('click', onChecklistClick);
  document.getElementById('tour').addEventListener('click', onTourClick);
  document.getElementById('tour-backdrop').addEventListener('click', closeTour);
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
  if (params.has('tour') || (!onboard.tour_done && !params.has('run'))) showTour();
});
