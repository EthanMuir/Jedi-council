// Shared across every screen: nav bar, audio blips (off by default), and a
// small SSE consumption helper.

const NAV_LINKS = [
  { href: '/classic/index.html', label: 'The Chamber' },
  { href: '/classic/crypt.html', label: 'The Crypt' },
  { href: '/classic/archives.html', label: 'The Archives' },
  { href: '/classic/settings.html', label: 'Settings' },
  { href: '/classic/guide.html', label: 'The Guide' },
];

// Every run covers all three terms. Mirrored from council/engine/horizons.py.
const TERMS = ['short', 'medium', 'long'];
const TERM_NAMES = { short: 'Short term', medium: 'Medium term', long: 'Long term' };
const TERM_WINDOWS = { short: 'the next week', medium: 'the next 3 months', long: 'the next year and beyond' };
const TERM_LETTERS = { short: 'S', medium: 'M', long: 'L' };

// How much each seat's lean counts on each term (0-1, never 0 -- every
// seat weighs in on every term). Mirrored from
// council/engine/horizons.py::COMPETENCE_MATRIX.
const COMPETENCE_MATRIX = {
  technician:          { short: 1.0, medium: 0.6, long: 0.3 },
  fundamentalist:      { short: 0.2, medium: 0.6, long: 1.0 },
  catalyst_seer:       { short: 1.0, medium: 0.7, long: 0.4 },
  insider_reader:      { short: 0.4, medium: 0.8, long: 0.9 },
  senate_watcher:      { short: 0.3, medium: 0.7, long: 0.8 },
  flow_cartographer:   { short: 0.4, medium: 0.8, long: 0.9 },
  oracle_options:      { short: 0.9, medium: 0.6, long: 0.3 },
  macro_sage:          { short: 0.3, medium: 0.8, long: 1.0 },
  cross_market:        { short: 0.8, medium: 0.6, long: 0.4 },
  estimate_scribe:     { short: 0.5, medium: 0.9, long: 0.9 },
  analyst_ratings:     { short: 0.6, medium: 0.8, long: 0.9 },
  structure_archivist: { short: 0.5, medium: 0.7, long: 0.8 },
};

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

// How far a position leans, in the same bands as
// council/engine/aggregation.py::lean_label -- a weak lean must read as weak.
function leanStrength(pBullish) {
  const d = Math.abs(Math.round((pBullish - 0.5) * 1000) / 1000);
  if (d === 0) return 'even';
  if (d < 0.02) return 'barely';
  if (d < 0.05) return 'leaning';
  if (d < 0.10) return 'plain';
  return 'strong';
}

function leanLabel(pBullish) {
  const strength = leanStrength(pBullish);
  if (strength === 'even') return 'Dead even';
  const side = pBullish > 0.5 ? 'bullish' : 'bearish';
  return {
    barely: `Barely ${side}`,
    leaning: `Leaning ${side}`,
    plain: side[0].toUpperCase() + side.slice(1),
    strong: `Strongly ${side}`,
  }[strength];
}

function voteFromP(pBullish) {
  const d = Math.round((pBullish - 0.5) * 1000);
  if (d === 0) return 'NO_CONVICTION';
  return d > 0 ? 'BULLISH' : 'BEARISH';
}

// "54% chance of rising" / "58% chance of falling" / "50/50".
function chanceText(pBullish) {
  const vote = voteFromP(pBullish);
  if (vote === 'NO_CONVICTION') return '50/50';
  return vote === 'BULLISH'
    ? `${Math.round(pBullish * 100)}% chance of rising`
    : `${Math.round((1 - pBullish) * 100)}% chance of falling`;
}

// The bearish<->bullish bar spans 25%-75% chance of rising: seats rarely
// go past that, and a narrower scale keeps their ticks apart. Anything
// beyond sits pinned at the end.
const BAR_MIN = 0.25;
const BAR_MAX = 0.75;

function barPos(pBullish) {
  const x = (pBullish - BAR_MIN) / (BAR_MAX - BAR_MIN);
  return Math.max(0, Math.min(1, x)) * 100;
}

// One term's bar: the council's position as a marker, a tick for every
// seat's lean (taller = counts for more on this term), and the term's
// warnings under it. `term` is a key of TERMS; `data` is one entry of the
// phase_f_synthesis event's `terms` (or the same shape rebuilt from the
// Crypt). opts.compact drops the ticks, warnings and note.
function termBarHtml(term, data, opts = {}) {
  const p = data.p_bullish;
  const strength = data.seats_counted === 0 ? 'even' : leanStrength(p);
  const vote = data.seats_counted === 0 ? 'NO_CONVICTION' : voteFromP(p);
  const pos = barPos(p);
  const fillLeft = Math.min(pos, 50);
  const fillWidth = Math.abs(pos - 50);
  const label = data.lean_label || leanLabel(p);

  const ticks = opts.compact ? '' : (data.ticks || [])
    .filter(t => t.p_bullish !== null && t.p_bullish !== undefined)
    .map(t => {
      const height = 8 + Math.round(16 * Math.min(1, t.weight ?? 0.5));
      const title = `${t.title}: ${chanceText(t.p_bullish)}` +
        (t.weight !== undefined ? ` (counts ${Number(t.weight).toFixed(2)} on this term)` : '');
      return `<span class="term-tick tick-${voteClass(voteFromP(t.p_bullish)).replace('status-', '')}"
        style="left:${barPos(t.p_bullish).toFixed(1)}%; height:${height}px" title="${escapeHtml(title)}"></span>`;
    }).join('');

  const warnings = opts.compact ? '' : (data.warnings || [])
    .map(w => `<div class="term-warning">&#9888; ${escapeHtml(w.message)}</div>`).join('');

  const agree = data.consensus_pct ? ` &middot; ${Math.round(data.consensus_pct)}% of leaning seats agree` : '';
  const foot = data.seats_counted === 0
    ? 'No seat could read its data for this term.'
    : `${chanceText(p)}${agree}`;

  return `
    <div class="term-bar-row lean-${strength}${opts.compact ? ' compact' : ''}">
      <div class="term-bar-head">
        <span class="term-bar-name">${TERM_NAMES[term]}</span>
        <span class="term-bar-window">${TERM_WINDOWS[term]}</span>
        <span class="term-bar-lean ${voteClass(vote)}">${escapeHtml(label)}</span>
      </div>
      <div class="term-bar-body">
        <div class="term-bar">
          <div class="term-bar-track">
            <div class="term-bar-fill fill-${voteClass(vote).replace('status-', '')}"
              style="left:${fillLeft.toFixed(1)}%; width:${fillWidth.toFixed(1)}%"></div>
            <div class="term-bar-center"></div>
            ${ticks}
            <div class="term-bar-marker marker-${voteClass(vote).replace('status-', '')}" style="left:${pos.toFixed(1)}%"></div>
          </div>
          <div class="term-bar-scale"><span>&#9664; Bearish</span><span>50/50</span><span>Bullish &#9654;</span></div>
          <div class="term-bar-foot">${foot}</div>
        </div>
      </div>
      ${warnings ? `<div class="term-bar-warnings">${warnings}</div>` : ''}
      ${!opts.compact && data.note ? `<div class="term-bar-note">${escapeHtml(data.note)}</div>` : ''}
    </div>
  `;
}

function renderNav(activeHref) {
  const nav = document.createElement('div');
  nav.className = 'nav';
  const brand = document.createElement('span');
  brand.className = 'brand';
  brand.textContent = 'THE HIGH COUNCIL';
  nav.appendChild(brand);

  // Only visible on narrow screens (theme.css), where the links collapse
  // into a dropdown instead of pushing the page wider than the screen.
  const toggle = document.createElement('button');
  toggle.className = 'btn nav-toggle';
  toggle.textContent = 'MENU';
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', 'nav-links');
  toggle.onclick = () => {
    const open = nav.classList.toggle('open');
    toggle.setAttribute('aria-expanded', String(open));
    toggle.textContent = open ? 'CLOSE' : 'MENU';
  };
  nav.appendChild(toggle);

  const links = document.createElement('div');
  links.className = 'nav-links';
  links.id = 'nav-links';
  nav.appendChild(links);

  for (const link of NAV_LINKS) {
    const a = document.createElement('a');
    a.href = link.href;
    a.textContent = link.label;
    if (link.href === activeHref) a.classList.add('active');
    links.appendChild(a);
  }

  const spacer = document.createElement('span');
  spacer.className = 'spacer';
  links.appendChild(spacer);

  const audioBtn = document.createElement('button');
  audioBtn.className = 'btn';
  audioBtn.style.fontSize = '9px';
  audioBtn.textContent = AudioBlips.enabled ? 'SOUND: ON' : 'SOUND: OFF';
  audioBtn.onclick = () => {
    AudioBlips.enabled = !AudioBlips.enabled;
    audioBtn.textContent = AudioBlips.enabled ? 'SOUND: ON' : 'SOUND: OFF';
    if (AudioBlips.enabled) AudioBlips.blip();
  };
  links.appendChild(audioBtn);

  // council_logged_in is a non-HttpOnly flag cookie set alongside the real
  // (HttpOnly, unreadable from JS on purpose) session cookie -- with auth
  // disabled (the local/default case) neither cookie is ever set, so this
  // stays invisible and the nav looks exactly as it always did.
  if (document.cookie.includes('council_logged_in=')) {
    const logoutLink = document.createElement('a');
    logoutLink.href = '/logout';
    logoutLink.textContent = 'LOGOUT';
    logoutLink.style.marginLeft = '4px';
    links.appendChild(logoutLink);
  }

  document.body.prepend(nav);

  const overlay = document.createElement('div');
  overlay.className = 'crt-overlay';
  document.body.appendChild(overlay);
}

// Settings and the Guide show one .page-section at a time, picked from the
// .section-nav menu. The URL hash says which (#keys, #models, ...) so links
// and reloads land on the right section. Sections carry the name in
// data-section rather than as an element id, so the browser never jumps
// the page down to them on its own.
function initSections() {
  const sections = [...document.querySelectorAll('.page-section')];
  const nav = document.querySelector('.section-nav');
  if (!sections.length || !nav) return;

  for (const section of sections) {
    const a = document.createElement('a');
    a.href = `#${section.dataset.section}`;
    a.textContent = section.dataset.title;
    nav.appendChild(a);
  }

  const show = () => {
    const wanted = decodeURIComponent(location.hash.slice(1));
    const target = sections.find(s => s.dataset.section === wanted) || sections[0];
    for (const s of sections) s.hidden = s !== target;
    for (const a of nav.querySelectorAll('a')) {
      const active = a.getAttribute('href') === `#${target.dataset.section}`;
      a.classList.toggle('active', active);
      if (active) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    }
  };

  window.addEventListener('hashchange', () => {
    show();
    const top = nav.closest('.sectioned').getBoundingClientRect().top;
    if (top < 0) window.scrollBy(0, top - 12);
  });
  show();
}

// Subtle UI blips, synthesized (no audio file dependency), off by default.
const AudioBlips = {
  enabled: false,
  _ctx: null,
  _get() {
    if (!this._ctx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      this._ctx = new Ctx();
    }
    return this._ctx;
  },
  blip(freq = 880, duration = 0.06) {
    if (!this.enabled) return;
    try {
      const ctx = this._get();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'square';
      osc.frequency.value = freq;
      gain.gain.value = 0.03;
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + duration);
      osc.stop(ctx.currentTime + duration);
    } catch (e) {
      /* audio not available -- silently ignore, it's optional */
    }
  },
};

async function fetchJSON(url, options) {
  const resp = await fetch(url, options);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`${resp.status}: ${text}`);
  }
  return resp.json();
}

// Consumes a text/event-stream response, calling onEvent(eventName, data) for
// each "event: ...\ndata: ...\n\n" block.
async function consumeSSE(url, onEvent) {
  const resp = await fetch(url);
  if (!resp.ok) {
    throw new Error(`${resp.status}: ${await resp.text()}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split('\n\n');
    buffer = blocks.pop();
    for (const block of blocks) {
      const lines = block.split('\n');
      let event = 'message';
      let data = '';
      for (const line of lines) {
        if (line.startsWith('event: ')) event = line.slice(7);
        else if (line.startsWith('data: ')) data = line.slice(6);
      }
      if (data) {
        try {
          onEvent(event, JSON.parse(data));
        } catch (e) {
          console.error('bad SSE payload', e, data);
        }
      }
    }
  }
}

function fmtPct(v, digits = 1) {
  if (v === null || v === undefined) return '--';
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`;
}

function fmtNum(v, digits = 3) {
  if (v === null || v === undefined) return '--';
  return v.toFixed(digits);
}

function voteClass(vote) {
  if (vote === 'BULLISH') return 'status-bullish';
  if (vote === 'BEARISH') return 'status-bearish';
  if (vote === 'NO_CONVICTION') return 'status-noread';
  return 'status-noread';
}
