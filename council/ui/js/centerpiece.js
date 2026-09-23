// Shared by index.html (the real Chamber) and settings.html (style
// pickers with live previews) -- the set of selectable visual designs for
// the center holocron and the seat chairs, plus the localStorage-backed
// preference each one reads/writes. Purely a per-browser cosmetic choice
// (nothing here affects deliberation behavior), so it lives in
// localStorage rather than round-tripping through the backend the way a
// setting that must be shared across devices/users would.

const CENTERPIECE_STYLE_KEY = 'councilCenterpieceStyle';
const SEAT_CHAIR_STYLE_KEY = 'councilSeatChairStyle';

function getCenterpieceStyle() {
  try {
    return localStorage.getItem(CENTERPIECE_STYLE_KEY) || 'gyroscope';
  } catch (e) {
    return 'gyroscope';
  }
}

function setCenterpieceStyle(id) {
  try { localStorage.setItem(CENTERPIECE_STYLE_KEY, id); } catch (e) { /* private browsing, quota, ... */ }
}

function getSeatChairStyle() {
  try {
    return localStorage.getItem(SEAT_CHAIR_STYLE_KEY) || 'holocard';
  } catch (e) {
    return 'holocard';
  }
}

function setSeatChairStyle(id) {
  try { localStorage.setItem(SEAT_CHAIR_STYLE_KEY, id); } catch (e) { /* private browsing, quota, ... */ }
}

function chairClassBase() {
  return `seat-chair chair-style-${getSeatChairStyle()}`;
}

// ---- centerpiece styles ---------------------------------------------------
// Each `build(el)` populates el (the #holocron div, or a small preview box
// styled identically) with that style's own layer elements. Verdict
// recoloring is shared across all of them: every style's CSS reads the
// same --holo-mid/--holo-bright/--holo-accent/--holo-core custom
// properties that the .verdict-* classes already override (see
// chamber.css), so a new style only has to use those variables, never
// hardcode a color, to get bullish/bearish/no-conviction recoloring for
// free.
const CENTERPIECE_STYLES = [
  {
    id: 'nebula',
    name: 'Nebula',
    blurb: 'A drifting spiral of color with real gaps between its arms, a starfield, and a pulsing core.',
    build(el) {
      el.innerHTML = `
        <div class="holocron-face f1"></div>
        <div class="holocron-face f2"></div>
        <div class="holocron-face f3"></div>
      `;
    },
  },
  {
    id: 'gyroscope',
    name: 'Armillary Gyroscope',
    blurb: 'Three tilted rings tumbling around a fixed core, like an astrolabe reading the market instead of the stars.',
    build(el) {
      el.innerHTML = `
        <div class="gyro-ring gyro-ring-a"></div>
        <div class="gyro-ring gyro-ring-b"></div>
        <div class="gyro-ring gyro-ring-c"></div>
        <div class="gyro-core"></div>
      `;
    },
  },
  {
    id: 'orb',
    name: 'Scrying Orb',
    blurb: 'A glassy sphere with real depth -- slow inner smoke and a fixed highlight, like a crystal ball the council actually consults.',
    build(el) {
      el.innerHTML = `
        <div class="orb-clip">
          <div class="orb-base"></div>
          <div class="orb-smoke orb-smoke-a"></div>
          <div class="orb-smoke orb-smoke-b"></div>
          <div class="orb-highlight"></div>
        </div>
      `;
    },
  },
  {
    id: 'equalizer',
    name: 'Equalizer Pulse Ring',
    blurb: "A ring of bars pulsing like a voiceprint -- twelve seats' worth of chatter visualized as one living waveform.",
    build(el) {
      const n = 28, radius = 58;
      let html = '';
      for (let i = 0; i < n; i++) {
        const angle = (i / n) * 360;
        const delay = (i * 0.09).toFixed(2);
        const accent = i % 4 === 0 ? ' accent' : '';
        html += `<div class="eq-bar" style="transform:translate(-50%,-50%) rotate(${angle}deg) translateY(-${radius}px)">
          <div class="eq-bar-fill${accent}" style="animation-delay:-${delay}s"></div>
        </div>`;
      }
      html += '<div class="eq-center"></div>';
      el.innerHTML = html;
    },
  },
  {
    id: 'sigil',
    name: 'Constellation Sigil',
    blurb: 'A rotating rune of circles and glowing nodes, data flowing along its spokes -- half technology, half divination.',
    build(el) {
      el.innerHTML = `
        <svg viewBox="0 0 160 160" class="sigil-svg">
          <g class="sigil-spin">
            <circle cx="80" cy="80" r="66" class="sigil-line" />
            <polygon points="80,22 130,51 130,109 80,138 30,109 30,51" class="sigil-line" />
            <line x1="80" y1="80" x2="80" y2="22" class="sigil-line thin" />
            <line x1="80" y1="80" x2="130" y2="51" class="sigil-line thin" />
            <line x1="80" y1="80" x2="130" y2="109" class="sigil-line thin" />
            <line x1="80" y1="80" x2="80" y2="138" class="sigil-line thin" />
            <line x1="80" y1="80" x2="30" y2="109" class="sigil-line thin" />
            <line x1="80" y1="80" x2="30" y2="51" class="sigil-line thin" />
            <circle cx="80" cy="22" r="4" class="sigil-node" />
            <circle cx="130" cy="51" r="4" class="sigil-node" style="animation-delay:-0.6s" />
            <circle cx="130" cy="109" r="4" class="sigil-node" style="animation-delay:-1.2s" />
            <circle cx="80" cy="138" r="4" class="sigil-node" style="animation-delay:-1.8s" />
            <circle cx="30" cy="109" r="4" class="sigil-node" style="animation-delay:-2.4s" />
            <circle cx="30" cy="51" r="4" class="sigil-node" style="animation-delay:-3s" />
          </g>
          <circle cx="80" cy="80" r="3.5" class="sigil-core" />
        </svg>
      `;
    },
  },
  {
    id: 'vortex',
    name: 'Vortex',
    blurb: 'Motes of light spiraling inward and vanishing at the core -- twelve opinions collapsing into one verdict.',
    build(el) {
      const n = 18;
      let html = '<div class="vortex-core"></div>';
      for (let i = 0; i < n; i++) {
        const accent = i % 3 === 0 ? ' accent' : '';
        const delay = (i * 0.18).toFixed(2);
        const dur = (3 + (i % 4) * 0.3).toFixed(2);
        html += `<div class="vortex-particle${accent}" style="animation-delay:-${delay}s; animation-duration:${dur}s"></div>`;
      }
      el.innerHTML = html;
    },
  },
  {
    id: 'die',
    name: 'Probability Die',
    blurb: "A wireframe cube tumbling in place -- rolling the dice is a fitting shape for what this whole tool does.",
    build(el) {
      el.innerHTML = `
        <div class="die-cube">
          <div class="die-face die-front"></div>
          <div class="die-face die-back"></div>
          <div class="die-face die-right"></div>
          <div class="die-face die-left"></div>
          <div class="die-face die-top"></div>
          <div class="die-face die-bottom"></div>
        </div>
      `;
    },
  },
  {
    id: 'radar',
    name: 'Radar Sweep',
    blurb: 'A scanning beam sweeps a grid, lighting up blips as it passes -- the council searching the noise for a signal.',
    build(el) {
      el.innerHTML = `
        <div class="radar-grid"></div>
        <div class="radar-sweep"></div>
        <div class="radar-blip" style="left:32%; top:58%; animation-delay:-1s;"></div>
        <div class="radar-blip" style="left:64%; top:30%; animation-delay:-2.4s;"></div>
        <div class="radar-blip" style="left:70%; top:68%; animation-delay:-3.6s;"></div>
        <div class="radar-core"></div>
      `;
    },
  },
];

const SEAT_CHAIR_STYLES = [
  { id: 'classic', name: 'Classic', blurb: 'The original bevelled plate.' },
  { id: 'hex', name: 'Hex Plate', blurb: 'A hexagonal panel silhouette.' },
  { id: 'holocard', name: 'Holo-Card', blurb: 'Rounded, with a glowing top edge and a thin underline progress bar.' },
  { id: 'terminal', name: 'Terminal Readout', blurb: 'Black background, green monospace, a segmented ASCII-style bar.' },
  { id: 'wizard', name: 'Seat Wizards', blurb: 'Each seat as a small pixel-art spellcaster -- a thinking stance while deliberating, a distinct reaction per verdict.' },
];

// ---- Seat Wizards (chair-style-wizard) ------------------------------------
// A canvas-drawn bust (hat/face/beard/shoulders only -- the seat chair's
// 92px height has no room for a full standing figure) whose pose and
// palette both follow that seat's current state. Skin and beard are fixed
// neutral tones on purpose -- only the robe/hat/eye-glow recolor per
// verdict, the same way a person's face doesn't change color with their
// mood. Deliberately a *bearded, brimmed-hat* wizard with a visible face,
// not a plain pointed hood over a featureless robe -- that silhouette
// read as something else entirely and was the whole reason this exists.
const WIZARD_SKIN = '#d9c0a0';
const WIZARD_BEARD = '#e8e4d8';
const WIZARD_DARK = '#241a14';

const WIZARD_PALETTES = {
  idle:     { base: '#4f3785', mid: '#7952c4', accent: '#4fd6e8', eye: '#4fd6e8' },
  thinking: { base: '#4f3785', mid: '#7952c4', accent: '#4fd6e8', eye: '#4fd6e8' },
  bullish:  { base: '#2f6e39', mid: '#48a35a', accent: '#4fd6e8', eye: '#c3f7cc' },
  bearish:  { base: '#6e2724', mid: '#a3392f', accent: '#e8b04f', eye: '#f7c4c1' },
  noread:   { base: '#746e2c', mid: '#a89e3f', accent: '#e8b04f', eye: '#f7f0b8' },
};

// hunch: how far the hood drops from resting (bigger = more compressed/defeated)
// hatTilt: -1..1, which way the hat's flopped tip leans
const WIZARD_POSES = {
  idle:     { hunch: 0, hatTilt: 0.15 },
  thinking: { hunch: 1, hatTilt: 0.5 },
  bullish:  { hunch: -1, hatTilt: -0.5 },
  bearish:  { hunch: 3, hatTilt: 0.6 },
  noread:   { hunch: 1, hatTilt: -0.3 },
};

// Draws the hat/face/beard relative to chinY (the bottom of the beard /
// top of the shoulders) -- a proper brimmed, curve-tipped hat with a star
// at the flop, not a straight cone, and a visible bearded face instead of
// glowing eye-slits in an empty hood.
function drawWizardHead(ctx, cx, chinY, palette, pose) {
  const faceTop = chinY - 9;
  ctx.fillStyle = WIZARD_SKIN;
  for (let y = faceTop; y < chinY - 3; y++) ctx.fillRect(Math.round(cx - 3.5), y, 7, 1);

  ctx.fillStyle = WIZARD_DARK;
  ctx.fillRect(Math.round(cx - 2.5), faceTop + 2, 1.5, 1.5);
  ctx.fillRect(Math.round(cx + 1), faceTop + 2, 1.5, 1.5);
  ctx.fillStyle = palette.eye;
  ctx.fillRect(Math.round(cx - 2.2), faceTop + 2.2, 0.8, 0.8);
  ctx.fillRect(Math.round(cx + 1.3), faceTop + 2.2, 0.8, 0.8);

  const beardTop = chinY - 4, beardBottom = chinY + 3;
  for (let y = beardTop; y < beardBottom; y++) {
    const t = (y - beardTop) / (beardBottom - beardTop);
    const halfW = 4 - t * 3.3;
    ctx.fillStyle = WIZARD_BEARD;
    ctx.fillRect(Math.round(cx - halfW), y, Math.round(halfW * 2), 1);
  }

  const brimY = faceTop - 2;
  ctx.fillStyle = palette.base;
  ctx.fillRect(Math.round(cx - 9), brimY, 18, 2);
  ctx.fillStyle = palette.mid;
  ctx.fillRect(Math.round(cx - 9), brimY, 18, 1);

  const hatBottom = brimY, hatTop = hatBottom - 13;
  const dir = pose.hatTilt >= 0 ? 1 : -1;
  for (let y = hatTop; y < hatBottom; y++) {
    const t = (y - hatTop) / (hatBottom - hatTop); // 0 at tip, 1 at brim
    const halfW = 1 + t * 4.5;
    const bend = (1 - t) * (1 - t) * 7 * dir; // tip curves/flops over, doesn't run straight up
    const xOff = pose.hatTilt * (1 - t) * 4 + bend;
    ctx.fillStyle = palette.base;
    ctx.fillRect(Math.round(cx - halfW + xOff), y, Math.round(halfW * 2), 1);
  }
  const tipBend = 0.9025 * 7 * dir; // (1-0.05)^2 at t=0.05, just below the very tip
  const tipX = cx + pose.hatTilt * 0.95 * 4 + tipBend;
  ctx.fillStyle = palette.accent;
  ctx.fillRect(Math.round(tipX - 1), hatTop - 1, 2, 2);
}

// The only mode actually used in the app: a compact bust that fits the
// seat chair's existing space. width=30 height=40 native resolution,
// displayed scaled up with image-rendering:pixelated for the blocky look.
function drawWizardBust(canvas, state) {
  const ctx = canvas.getContext('2d');
  const palette = WIZARD_PALETTES[state] || WIZARD_PALETTES.idle;
  const pose = WIZARD_POSES[state] || WIZARD_POSES.idle;
  const W = canvas.width, H = canvas.height, cx = W / 2;
  ctx.clearRect(0, 0, W, H);

  const chinY = H - 12;
  drawWizardHead(ctx, cx, chinY, palette, pose);

  const shoulderTop = chinY + 3, shoulderBottom = H - 1;
  for (let y = shoulderTop; y < shoulderBottom; y++) {
    const t = (y - shoulderTop) / (shoulderBottom - shoulderTop);
    const halfW = 5 + t * 3;
    ctx.fillStyle = palette.base;
    ctx.fillRect(Math.round(cx - halfW), y, Math.round(halfW * 2), 1);
  }
}

// Maps a seat chair's state-* class to the wizard state key (only naming
// differs -- "deliberating" reads better as a chair state, "thinking" as
// a pose).
function wizardStateFor(chairState) {
  if (chairState === 'deliberating') return 'thinking';
  if (chairState === 'bullish' || chairState === 'bearish') return chairState;
  if (chairState === 'noread') return 'noread';
  return 'idle';
}

// Rebuilds el's contents for the given centerpiece style id, preserving
// every class el already has except swapping whichever "style-*" token
// was there for the new one -- callers (chamber.js manages verdict-*,
// settings.js manages nothing extra) never have to know this function
// touches classNames at all.
function buildHolocronInto(el, styleId, opts = {}) {
  const style = CENTERPIECE_STYLES.find(s => s.id === styleId) || CENTERPIECE_STYLES[0];
  style.build(el);
  if (opts.labelHtml !== undefined) {
    const label = document.createElement('div');
    label.className = 'holocron-label';
    label.id = 'holocron-label';
    label.innerHTML = opts.labelHtml;
    el.appendChild(label);
  }
  const kept = el.className.split(' ').filter(c => c && !c.startsWith('style-'));
  kept.push(`style-${style.id}`);
  el.className = kept.join(' ');
}
