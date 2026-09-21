// Shared across all four screens: nav bar, audio blips (off by default),
// and a small SSE consumption helper.

const NAV_LINKS = [
  { href: '/index.html', label: 'The Chamber' },
  { href: '/crypt.html', label: 'The Crypt' },
  { href: '/archives.html', label: 'The Archives' },
  { href: '/oracle.html', label: 'The Oracle' },
  { href: '/guide.html', label: 'The Guide' },
];

// Mirrored from council/engine/horizons.py::COMPETENCE_MATRIX -- a seat at
// 0.0 for a horizon is never actually called by the backend (the orchestrator
// filters it out of eligible_seats entirely), so the UI must know this too:
// without it, a chair optimistically marked "deliberating" at convene() never
// gets an update and sits stuck forever.
const COMPETENCE_MATRIX = {
  technician:          { '1d': 1.0, '1w': 0.9, '1m': 0.6, '1y': 0.3 },
  fundamentalist:      { '1d': 0.0, '1w': 0.2, '1m': 0.6, '1y': 1.0 },
  catalyst_seer:       { '1d': 0.9, '1w': 1.0, '1m': 0.7, '1y': 0.4 },
  insider_reader:      { '1d': 0.1, '1w': 0.4, '1m': 0.8, '1y': 0.9 },
  senate_watcher:      { '1d': 0.1, '1w': 0.3, '1m': 0.7, '1y': 0.8 },
  flow_cartographer:   { '1d': 0.2, '1w': 0.4, '1m': 0.8, '1y': 0.9 },
  oracle_options:      { '1d': 1.0, '1w': 0.9, '1m': 0.6, '1y': 0.3 },
  macro_sage:          { '1d': 0.0, '1w': 0.3, '1m': 0.8, '1y': 1.0 },
  cross_market:        { '1d': 1.0, '1w': 0.8, '1m': 0.6, '1y': 0.4 },
  estimate_scribe:     { '1d': 0.2, '1w': 0.5, '1m': 0.9, '1y': 0.9 },
  transcript_linguist: { '1d': 0.3, '1w': 0.6, '1m': 0.8, '1y': 0.7 },
  structure_archivist: { '1d': 0.4, '1w': 0.5, '1m': 0.7, '1y': 0.8 },
};

function isCompetent(seatId, horizon) {
  return (COMPETENCE_MATRIX[seatId]?.[horizon] ?? 0) > 0;
}

function renderNav(activeHref) {
  const nav = document.createElement('div');
  nav.className = 'nav';
  const brand = document.createElement('span');
  brand.className = 'brand';
  brand.textContent = 'THE HIGH COUNCIL';
  nav.appendChild(brand);

  for (const link of NAV_LINKS) {
    const a = document.createElement('a');
    a.href = link.href;
    a.textContent = link.label;
    if (link.href === activeHref) a.classList.add('active');
    nav.appendChild(a);
  }

  const spacer = document.createElement('span');
  spacer.className = 'spacer';
  nav.appendChild(spacer);

  const audioBtn = document.createElement('button');
  audioBtn.className = 'btn';
  audioBtn.style.fontSize = '9px';
  audioBtn.textContent = AudioBlips.enabled ? 'SOUND: ON' : 'SOUND: OFF';
  audioBtn.onclick = () => {
    AudioBlips.enabled = !AudioBlips.enabled;
    audioBtn.textContent = AudioBlips.enabled ? 'SOUND: ON' : 'SOUND: OFF';
    if (AudioBlips.enabled) AudioBlips.blip();
  };
  nav.appendChild(audioBtn);

  document.body.prepend(nav);

  const overlay = document.createElement('div');
  overlay.className = 'crt-overlay';
  document.body.appendChild(overlay);
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
