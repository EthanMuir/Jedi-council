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
let seatDetails = {}; // seat_id -> full seat_result payload, for the holo-panel

function layoutRing() {
  const ring = document.getElementById('chamber-ring');
  const radius = ring.clientWidth < 500 ? ring.clientWidth * 0.38 : 260;
  const n = TIER_I_SEATS.length;

  TIER_I_SEATS.forEach((seat, i) => {
    const angle = (i / n) * 2 * Math.PI - Math.PI / 2;
    const x = Math.cos(angle) * radius;
    const y = Math.sin(angle) * radius;

    const chair = document.createElement('div');
    chair.className = 'seat-chair state-idle';
    chair.id = `chair-${seat.id}`;
    chair.style.left = `calc(50% + ${x}px)`;
    chair.style.top = `calc(50% + ${y}px)`;
    chair.innerHTML = `
      <div class="seat-bust"></div>
      <div class="seat-title">${seat.title}</div>
      <div class="seat-vote dim">idle</div>
    `;
    chair.onclick = () => openHoloPanel(seat.id, seat.title);
    ring.appendChild(chair);
  });
}

function resetRing() {
  seatDetails = {};
  for (const seat of TIER_I_SEATS) {
    const chair = document.getElementById(`chair-${seat.id}`);
    if (!isCompetent(seat.id, selectedHorizon)) {
      // This seat has 0 competence at the selected horizon -- the backend
      // never calls it at all (see council/engine/horizons.py), so it will
      // never emit a seat_result event. Mark it up front instead of leaving
      // it stuck on "deliberating..." forever.
      chair.className = 'seat-chair state-idle';
      chair.querySelector('.seat-vote').textContent = `not called @ ${selectedHorizon}`;
      chair.querySelector('.seat-vote').className = 'seat-vote dim';
      continue;
    }
    chair.className = 'seat-chair state-deliberating';
    chair.querySelector('.seat-vote').textContent = 'deliberating...';
    chair.querySelector('.seat-vote').className = 'seat-vote cyan';
  }
  const holocron = document.getElementById('holocron');
  holocron.className = 'holocron';
  document.getElementById('holocron-label').innerHTML = 'DELIBERATING';
  document.getElementById('reality-anchor').innerHTML = '<span class="dim">Awaiting Phase B...</span>';
  document.getElementById('dissent-map').innerHTML = '<span class="dim">Awaiting verdicts...</span>';
  document.getElementById('debate-transcript').innerHTML = '<span class="dim">Awaiting Phase C...</span>';
  document.getElementById('audit-panel').innerHTML = '<span class="dim">Awaiting Phase E...</span>';
  document.getElementById('grand-master-verdict').innerHTML = '<span class="dim">The council is deliberating...</span>';
}

function updateSeatChair(payload) {
  seatDetails[payload.seat_id] = payload;
  const chair = document.getElementById(`chair-${payload.seat_id}`);
  if (!chair) return;
  const voteEl = chair.querySelector('.seat-vote');

  if (payload.vote === 'BULLISH') {
    chair.className = 'seat-chair state-bullish';
    voteEl.textContent = `BULLISH ${payload.probability}`;
    voteEl.className = 'seat-vote status-bullish';
    AudioBlips.blip(1046, 0.05);
  } else if (payload.vote === 'BEARISH') {
    chair.className = 'seat-chair state-bearish';
    voteEl.textContent = `BEARISH ${payload.probability}`;
    voteEl.className = 'seat-vote status-bearish';
    AudioBlips.blip(392, 0.05);
  } else {
    chair.className = 'seat-chair state-noread';
    voteEl.textContent = 'NO_READ';
    voteEl.className = 'seat-vote status-noread';
    AudioBlips.blip(220, 0.04);
  }
}

function renderRealityAnchor(payload) {
  document.getElementById('reality-anchor').innerHTML = `
    max plausible move: <span class="amber">${fmtPct(payload.max_plausible_move_pct)}</span><br/>
    hit-rate-up: <span class="cyan">${fmtNum(payload.hit_rate_up)}</span><br/>
    options-implied move: <span class="cyan">${fmtPct(payload.options_implied_move_pct)}</span><br/>
    ${Object.entries(payload.plausibility_flags).filter(([, f]) => f === 'IMPLAUSIBLE').map(([sid]) =>
      `<div class="crimson">&#9650; ${sid} target flagged IMPLAUSIBLE</div>`).join('') || '<span class="dim">no implausible targets</span>'}
  `;
}

function appendDebateRound(payload) {
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

function renderGrandMaster(payload) {
  const holocron = document.getElementById('holocron');
  const label = document.getElementById('holocron-label');
  if (payload.vote === 'BULLISH') {
    holocron.className = 'holocron verdict-bullish';
    label.innerHTML = `BULLISH<br/>${fmtNum(payload.confidence)}`;
  } else if (payload.vote === 'BEARISH') {
    holocron.className = 'holocron verdict-bearish';
    label.innerHTML = `BEARISH<br/>${fmtNum(payload.confidence)}`;
  } else {
    holocron.className = 'holocron verdict-noconviction';
    label.innerHTML = `NO<br/>CONVICTION`;
  }

  document.getElementById('grand-master-verdict').innerHTML = `
    <div><b class="${voteClass(payload.vote)}">${payload.vote}</b> confidence=${fmtNum(payload.confidence)}</div>
    <div style="margin-top:8px;"><b>Dissent summary:</b> ${payload.dissent_summary}</div>
    ${payload.correlated_evidence_warning ? `<div class="crimson" style="margin-top:8px;"><b>Correlated evidence:</b> ${payload.correlated_evidence_warning}</div>` : ''}
    <div style="margin-top:8px;"><b>Reasoning:</b> ${payload.reasoning}</div>
  `;

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

function closeHoloPanel() {
  document.getElementById('holo-backdrop').classList.remove('open');
}

async function convene() {
  const ticker = document.getElementById('ticker-input').value.trim().toUpperCase();
  if (!ticker) return;
  const btn = document.getElementById('convene-btn');
  const status = document.getElementById('status-line');
  btn.disabled = true;
  resetRing();

  const contextEl = document.getElementById('context-input');
  const context = contextEl ? contextEl.value.trim() : '';
  status.textContent = context
    ? `Convening the council for ${ticker} @ ${selectedHorizon} with your question...`
    : `Convening the council for ${ticker} @ ${selectedHorizon}...`;

  let url = `/api/deliberate/stream?ticker=${encodeURIComponent(ticker)}&horizon=${selectedHorizon}`;
  if (context) url += `&context=${encodeURIComponent(context)}`;

  try {
    await consumeSSE(url, (event, payload) => {
      if (event === 'seat_result') updateSeatChair(payload);
      else if (event === 'phase_b_reality_anchor') renderRealityAnchor(payload);
      else if (event === 'debate_round') appendDebateRound(payload);
      else if (event === 'phase_e_gates') renderAuditPanel(payload, null);
      else if (event === 'risk_warden') renderAuditPanel(null, payload);
      else if (event === 'phase_f_synthesis') renderGrandMaster(payload);
      else if (event === 'phase_g_crypt_write') status.textContent = `Written to the Crypt: ${payload.prediction_id}`;
      else if (event === 'error') status.textContent = `ERROR: ${payload.message}`;
      else if (event === 'done') status.textContent = `Deliberation complete -- ${ticker} @ ${selectedHorizon}`;
    });
  } catch (e) {
    status.textContent = `ERROR: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const path = window.location.pathname === '/' ? '/index.html' : window.location.pathname;
  renderNav(path);
  layoutRing();

  document.querySelectorAll('#horizon-toggle .toggle-option').forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll('#horizon-toggle .toggle-option').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      selectedHorizon = btn.dataset.horizon;
      AudioBlips.blip(660, 0.03);
    };
  });

  document.getElementById('convene-btn').onclick = convene;
  document.getElementById('holo-backdrop').onclick = (e) => {
    if (e.target.id === 'holo-backdrop') closeHoloPanel();
  };
});
