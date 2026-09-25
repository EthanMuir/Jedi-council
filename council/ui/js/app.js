// Shared by every page of the clean look: plain names, the lean bar, the
// nav, the Plain/Expert and theme preferences, and small fetch helpers.
// (The 8-bit look has its own copy of all this in ui/classic/js/.)

// ---- names ---------------------------------------------------------------

const SEATS = [
  { id: 'technician', name: 'Price Chart', reads: 'Price and trading volume' },
  { id: 'fundamentalist', name: 'Financials', reads: 'Revenue, profit, debt and valuation' },
  { id: 'analyst_ratings', name: 'Analyst Targets', reads: 'Analyst price targets and rating changes' },
  { id: 'estimate_scribe', name: 'Earnings Estimates', reads: 'Forecasts for future earnings and how they change' },
  { id: 'insider_reader', name: 'Insider Trades', reads: 'Shares bought and sold by the company\'s own executives' },
  { id: 'senate_watcher', name: 'Congress Trades', reads: 'Trades disclosed by members of Congress' },
  { id: 'catalyst_seer', name: 'News', reads: 'Headlines and upcoming events' },
  { id: 'structure_archivist', name: 'SEC Filings', reads: 'Company filings: buybacks, new shares, debt, lawsuits' },
  { id: 'flow_cartographer', name: 'Institutional Holdings', reads: 'What big funds hold, and short interest' },
  { id: 'oracle_options', name: 'Options Market', reads: 'What options traders are betting on' },
  { id: 'macro_sage', name: 'Economy', reads: 'Interest rates, inflation and the wider economy' },
  { id: 'cross_market', name: 'Related Markets', reads: 'Similar stocks, the index and overseas markets' },
];
const SEAT_BY_ID = Object.fromEntries(SEATS.map(s => [s.id, s]));

function seatName(id) {
  return SEAT_BY_ID[id]?.name || id;
}

// The AI seats write in the 8-bit look's vocabulary (their prompts use it),
// so text shown here swaps those names for the plain ones.
const NAME_SWAPS = [
  ["Reader of the Guild's Targets", 'Analyst Targets'],
  ['Student of the Inner Circle', 'Insider Trades'],
  ['Keeper of the Outer Rim', 'Economy'],
  ['Reader of Distant Stars', 'Related Markets'],
  ['Keeper of Expectations', 'Earnings Estimates'],
  ['Reader of the Republic', 'Congress Trades'],
  ['Reader of Probabilities', 'Options Market'],
  ['Keeper of the Ledgers', 'Financials'],
  ['Reader of Great Tides', 'Institutional Holdings'],
  ['Keeper of the Charts', 'Price Chart'],
  ['Keeper of Charters', 'SEC Filings'],
  ['Watcher of Omens', 'News'],
  ['Master of the Order', 'Summary'],
  ["The Devil's Advocate", 'The Challenger'],
  ['Cross-Market Navigator', 'Related Markets'],
  ['Structure Archivist', 'SEC Filings'],
  ['Flow Cartographer', 'Institutional Holdings'],
  ['Oracle of Options', 'Options Market'],
  ['Estimate Scribe', 'Earnings Estimates'],
  ['Insider Reader', 'Insider Trades'],
  ['Senate Watcher', 'Congress Trades'],
  ['Catalyst Seer', 'News'],
  ['Macro Sage', 'Economy'],
  ['Grand Master', 'Summary'],
  ['Risk Warden', 'Risk check'],
  ['Cost Auditor', 'Cost check'],
  ['Bull Advocate', 'Bull case'],
  ['Bear Advocate', 'Bear case'],
  ['Prosecutor', 'Challenger'],
  ['the Technician', 'Price Chart'],
  ['The Technician', 'Price Chart'],
  ['the Fundamentalist', 'Financials'],
  ['The Fundamentalist', 'Financials'],
  ['the Crypt', 'History'],
  ['the Archives', 'Seat record'],
  ['the council', 'the Council'],
  ['Tier I seats', 'seats'],
  ['Tier I seat', 'seat'],
];
// Seat ids as they appear in the AI's text: "[macro_sage]", "macro_sage".
const ID_PATTERN = new RegExp(`\\[?\\b(${SEATS.map(s => s.id).join('|')})\\b\\]?`, 'g');

function plainText(text) {
  if (text === null || text === undefined) return '';
  let out = String(text);
  for (const [from, to] of NAME_SWAPS) out = out.split(from).join(to);
  return out.replace(ID_PATTERN, (_, id) => seatName(id));
}

// ---- terms ----------------------------------------------------------------

const TERMS = ['short', 'medium', 'long'];
const TERM_LABEL = { short: 'Next week', medium: 'Next 3 months', long: 'Next year' };
const TERM_SHORT = { short: 'Week', medium: '3 mo', long: 'Year' };

// How much each seat counts on each term. Mirrored from
// council/engine/horizons.py::COMPETENCE_MATRIX.
const COMPETENCE = {
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

// ---- preferences (this browser only) ---------------------------------------

function readPref(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
}
function writePref(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch (e) { /* private browsing -- the choice just won't stick */ }
}

// Plain (the default) or Expert: how much of the numbers to show, and
// which version of the Summary to read.
function detailMode() { return readPref('council_detail', 'plain') === 'expert' ? 'expert' : 'plain'; }
function isExpert() { return detailMode() === 'expert'; }
function setDetailMode(mode) {
  writePref('council_detail', mode);
  document.dispatchEvent(new CustomEvent('detailchange'));
}

// '' follows the device; 'light' / 'dark' force one. Each page's <head>
// applies it before first paint so there's no flash.
function themePref() { return readPref('council_theme', ''); }
function setThemePref(theme) {
  writePref('council_theme', theme || null);
  if (theme) document.documentElement.setAttribute('data-theme', theme);
  else document.documentElement.removeAttribute('data-theme');
}

// The look is a cookie because the server reads it to send each page in
// the chosen look (council/api/ui_files.py).
function switchToClassicLook() {
  document.cookie = 'council_look=classic; path=/; max-age=31536000; samesite=lax';
  location.href = '/classic/settings.html#appearance';
}

// ---- leans ----------------------------------------------------------------

// Same bands as council/engine/aggregation.py::lean_label.
function leanStrength(p) {
  const d = Math.abs(Math.round((p - 0.5) * 1000) / 1000);
  if (d === 0) return 'even';
  if (d < 0.02) return 'barely';
  if (d < 0.05) return 'leaning';
  if (d < 0.10) return 'plain';
  return 'strong';
}

function dirOf(p) {
  if (p === null || p === undefined) return 'noread';
  const d = Math.round((p - 0.5) * 1000);
  return d > 0 ? 'up' : d < 0 ? 'down' : 'even';
}

// "Leaning up", "Strongly down", "Even".
function leanWords(p) {
  if (p === null || p === undefined) return 'No read';
  const strength = leanStrength(p);
  if (strength === 'even') return 'Even';
  const side = p > 0.5 ? 'up' : 'down';
  return { barely: `Barely ${side}`, leaning: `Leaning ${side}`, plain: side[0].toUpperCase() + side.slice(1), strong: `Strongly ${side}` }[strength];
}

function arrowOf(p) {
  return { up: '▲', down: '▼', even: '●', noread: '–' }[dirOf(p)];
}

function pct(p, digits = 0) {
  return `${(p * 100).toFixed(digits)}%`;
}

// "54% chance it rises" / "58% chance it falls" / "50/50".
function chanceWords(p) {
  const dir = dirOf(p);
  if (dir === 'even') return '50/50';
  if (dir === 'noread') return 'no read';
  return dir === 'up' ? `${pct(p)} chance it rises` : `${pct(1 - p)} chance it falls`;
}

// One term's lean on a chip: "Week ▲ 54%" (Expert) or "Week ▲ Up" (Plain).
function chipHtml(term, p, { expert = isExpert(), label = TERM_SHORT[term] } = {}) {
  const dir = dirOf(p);
  const value = dir === 'noread' ? 'no read' : dir === 'even' ? 'even'
    : expert ? pct(p) : (dir === 'up' ? 'up' : 'down');
  return `<span class="chip ${dir}" title="${escapeHtml(`${TERM_LABEL[term]}: ${leanWords(p)}`)}"><span class="t">${label}</span>${arrowOf(p)} ${value}</span>`;
}

// ---- the lean bar ----------------------------------------------------------

// The track spans a 25%-75% chance of rising: leans rarely go past that,
// and the narrower scale keeps seats' dots apart. Beyond it they pin to
// the ends.
const BAR_MIN = 0.25;
const BAR_MAX = 0.75;
function barPos(p) {
  return Math.max(0, Math.min(1, (p - BAR_MIN) / (BAR_MAX - BAR_MIN))) * 100;
}

// Dots that would overlap move down a row (up to three rows).
function stackDots(dots) {
  const rows = [];
  return dots
    .filter(d => d.p !== null && d.p !== undefined)
    .map(d => ({ ...d, x: barPos(d.p) }))
    .sort((a, b) => a.x - b.x)
    .map(d => {
      let row = rows.findIndex(lastX => d.x - lastX >= 2.4);
      if (row === -1) row = rows.length < 3 ? rows.length : rows.indexOf(Math.min(...rows));
      rows[row] = d.x;
      return { ...d, row };
    });
}

// p: the lean (chance of rising). opts.dots: [{p, weight, name}] for the
// seats. opts.small: a slimmer bar with no scale.
function leanBarHtml(p, opts = {}) {
  const has = p !== null && p !== undefined;
  const x = has ? barPos(p) : 50;
  const dir = has ? dirOf(p) : 'noread';
  const fillLeft = Math.min(x, 50);
  const fillWidth = Math.abs(x - 50);
  const dots = opts.dots ? stackDots(opts.dots) : [];
  const dotRows = dots.length ? Math.max(...dots.map(d => d.row)) + 1 : 0;
  const expert = opts.expert ?? isExpert();
  const scale = opts.small ? '' : expert
    ? '<div class="lean-scale num"><span>25%</span><span>50%</span><span>75%</span></div>'
    : '<div class="lean-scale"><span>◀ Down</span><span>Even</span><span>Up ▶</span></div>';
  return `
    <div class="lean${opts.small ? ' small' : ''}" role="img" aria-label="${escapeHtml(has ? `${leanWords(p)}, ${chanceWords(p)}` : 'No read')}">
      <div class="lean-track">
        <span class="lean-mid"></span>
        ${has && dir !== 'even' ? `<span class="lean-fill ${dir}" style="left:${fillLeft.toFixed(2)}%;width:${fillWidth.toFixed(2)}%"></span>` : ''}
        ${has ? `<span class="lean-knob ${dir}" style="left:${x.toFixed(2)}%"></span>` : ''}
      </div>
      ${dots.length ? `<div class="lean-dots" style="height:${dotRows * 11}px">${dots.map(d => `
        <span class="lean-dot ${dirOf(d.p)}" style="left:${d.x.toFixed(2)}%;top:${d.row * 11}px;opacity:${(0.35 + 0.65 * Math.min(1, d.weight ?? 1)).toFixed(2)}"
          title="${escapeHtml(`${d.name}: ${chanceWords(d.p)}`)}"></span>`).join('')}</div>` : ''}
      ${scale}
    </div>`;
}

// ---- nav --------------------------------------------------------------------

const NAV = [
  { href: '/index.html', label: 'Run' },
  { href: '/history.html', label: 'History' },
  { href: '/seats.html', label: 'Seat record' },
  { href: '/settings.html', label: 'Settings' },
  { href: '/guide.html', label: 'Guide' },
];

function renderNav(active) {
  const nav = document.createElement('header');
  nav.className = 'topnav';
  nav.innerHTML = `
    <div class="topnav-inner">
      <a class="brand" href="/index.html">Ticker <span>Council</span></a>
      <button class="btn btn-small btn-quiet nav-menu-btn" aria-expanded="false" aria-controls="navlinks">Menu</button>
      <nav class="navlinks" id="navlinks" aria-label="Pages">
        ${NAV.map(l => `<a href="${l.href}"${l.href === active ? ' class="active" aria-current="page"' : ''}>${l.label}</a>`).join('')}
        <span class="spacer"></span>
        <span id="nav-account"></span>
      </nav>
    </div>`;
  const btn = nav.querySelector('.nav-menu-btn');
  btn.onclick = () => {
    const open = nav.classList.toggle('open');
    btn.setAttribute('aria-expanded', String(open));
    btn.textContent = open ? 'Close' : 'Menu';
  };
  document.body.prepend(nav);
  refreshNavBadge(active);
}

// Who's signed in: admins get an Admin link (with how many sign-ups are
// waiting), and everyone gets Sign out. Nothing when sign-in is off.
let CURRENT_USER = null;
async function refreshNavBadge(active = location.pathname) {
  const slot = document.getElementById('nav-account');
  if (!slot) return;
  try {
    const me = await fetchJSON('/api/me');
    CURRENT_USER = me.user;
    document.dispatchEvent(new CustomEvent('me', { detail: me }));
    if (!me.user) { slot.replaceChildren(); return; }
    const badge = me.pending_count ? `<span class="nav-badge" title="${me.pending_count} waiting for approval">${me.pending_count}</span>` : '';
    slot.innerHTML = `
      ${me.user.is_admin ? `<a href="/admin.html"${active === '/admin.html' ? ' class="active" aria-current="page"' : ''}>Admin${badge}</a>` : ''}
      <a href="/logout" title="Signed in as ${escapeHtml(me.user.email)}">Sign out</a>`;
  } catch (e) { /* the nav still works without it */ }
}

// ---- helpers -------------------------------------------------------------------

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

// AI text, made safe and given the plain names.
function aiText(text) {
  return escapeHtml(plainText(text));
}

function firstSentence(text) {
  const clean = plainText(text).trim();
  const m = clean.match(/^.+?[.!?](?=\s|$)/);
  return m ? m[0] : clean;
}

async function fetchJSON(url, options) {
  const resp = await fetch(url, options);
  if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
  return resp.json();
}

// Reads a text/event-stream response, calling onEvent(name, data) per event.
async function consumeSSE(url, onEvent, { signal } = {}) {
  const resp = await fetch(url, { signal });
  if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
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
      let event = 'message';
      let data = '';
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7);
        else if (line.startsWith('data: ')) data = line.slice(6);
      }
      if (!data) continue;
      try {
        onEvent(event, JSON.parse(data));
      } catch (e) {
        console.error('bad event', e, data);
      }
    }
  }
}

function fmtDate(iso, withTime = false) {
  if (!iso) return '';
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`);
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric', ...(withTime ? { hour: 'numeric', minute: '2-digit' } : {}) });
}

function fmtMoney(usd) {
  if (usd === null || usd === undefined) return '';
  if (usd === 0) return '$0';
  return usd < 0.01 ? '<$0.01' : `$${usd.toFixed(2)}`;
}

function fmtMove(v) {
  if (v === null || v === undefined) return '–';
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;
}

// Settings and the Guide show one section at a time, picked from a side
// menu; the URL hash names it (#keys, #appearance, ...).
function initSections() {
  const sections = [...document.querySelectorAll('.page-section')];
  const menu = document.querySelector('.section-menu');
  if (!sections.length || !menu) return;
  menu.innerHTML = sections.map(s => `<a href="#${s.dataset.section}">${s.dataset.title}</a>`).join('');
  const show = () => {
    const wanted = decodeURIComponent(location.hash.slice(1));
    const target = sections.find(s => s.dataset.section === wanted) || sections[0];
    for (const s of sections) s.hidden = s !== target;
    for (const a of menu.querySelectorAll('a')) {
      const on = a.getAttribute('href') === `#${target.dataset.section}`;
      a.classList.toggle('active', on);
      if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    }
  };
  window.addEventListener('hashchange', show);
  show();
}
