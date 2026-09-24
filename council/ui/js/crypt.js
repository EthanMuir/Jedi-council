// THE CRYPT -- the resolved/unresolved prediction ledger, the update lever,
// and the scoreboard (council vs buy-and-hold vs always-bullish).

let activeHorizonFilter = '';
let activeModeFilter = '';

// A small label on each card for anything other than a full paid run.
function runTagsHtml(pred) {
  const tags = [];
  if (pred.run_mode === 'free') tags.push('<span class="run-tag run-tag-free">FREE</span>');
  if (pred.run_mode === 'sample') tags.push('<span class="run-tag">SAMPLE</span>');
  if (pred.run_shape === 'lite') tags.push('<span class="run-tag">LITE</span>');
  return tags.length ? `<div class="meta-line">${tags.join(' ')}</div>` : '';
}

function renderScoreboard(benchmark) {
  const el = document.getElementById('scoreboard');
  if (!benchmark || benchmark.n_resolutions === 0) {
    el.innerHTML = '<span class="dim">No resolved predictions yet. Pull the lever after some time has passed.</span>';
    return;
  }
  const arm = (label, a) => `
    <tr>
      <td>${label}</td>
      <td>${a.n ?? '--'}</td>
      <td>${a.hit_rate !== null && a.hit_rate !== undefined ? a.hit_rate : '--'}</td>
      <td>${a.brier !== null && a.brier !== undefined ? a.brier : '--'}</td>
      <td>${a.on_right_side_of_50pct !== null && a.on_right_side_of_50pct !== undefined ? a.on_right_side_of_50pct : '--'}</td>
    </tr>`;
  el.innerHTML = `
    <table class="scoreboard-table mono">
      <thead><tr><th>Arm</th><th>N</th><th>Hit Rate</th><th>Brier</th><th>On-Right-Side-of-50%</th></tr></thead>
      <tbody>
        ${arm('Council (blind)', benchmark.council_blind)}
        ${arm('Council (final)', benchmark.council_final)}
        ${arm('Council (extremized)', benchmark.council_extremized)}
        ${arm('Always-bullish', benchmark.always_bullish)}
      </tbody>
    </table>
    <div style="margin-top:10px;">Buy &amp; hold average realised return: <span class="cyan">${fmtPct(benchmark.buy_and_hold_avg_return_pct)}</span></div>
  `;
}

function cardOutcomeClass(pred) {
  if (pred.direction_correct === null || pred.direction_correct === undefined) return 'outcome-unresolved';
  return pred.direction_correct ? 'outcome-correct' : 'outcome-wrong';
}

function renderGrid(predictions) {
  const grid = document.getElementById('holocron-grid');
  if (predictions.length === 0) {
    grid.innerHTML = '<p class="dim">No predictions match this filter.</p>';
    return;
  }
  grid.innerHTML = '';
  for (const pred of predictions) {
    const card = document.createElement('div');
    card.className = `holocron-card ${cardOutcomeClass(pred)}`;
    const voteLabel = pred.council_vote || pred.blind_vote || 'PENDING';
    card.innerHTML = `
      <div class="ticker-line ${voteClass(voteLabel)}">${pred.ticker} &middot; ${pred.horizon}</div>
      <div class="meta-line">vote: <b class="${voteClass(voteLabel)}">${voteLabel}</b></div>
      <div class="meta-line dim">created: ${pred.created_at.slice(0, 16).replace('T', ' ')}</div>
      ${runTagsHtml(pred)}
      ${pred.realised_move_pct !== null && pred.realised_move_pct !== undefined
        ? `<div class="meta-line">realised: ${fmtPct(pred.realised_move_pct)}</div>`
        : '<div class="meta-line dim">unresolved</div>'}
    `;
    card.onclick = () => openPredictionDetail(pred.id);
    grid.appendChild(card);
  }
}

async function openPredictionDetail(id) {
  const modal = document.getElementById('holo-modal');
  modal.innerHTML = '<p class="dim">Loading...</p>';
  document.getElementById('holo-backdrop').classList.add('open');
  try {
    const detail = await fetchJSON(`/api/predictions/${id}`);
    const p = detail.prediction;
    const votesHtml = detail.seat_votes.map(v => `
      <div class="evidence-item">
        <b class="${voteClass(v.verdict.vote)}">${v.seat_id}</b>: ${v.verdict.vote} (p=${v.verdict.probability}, dispersion=${v.dispersion ?? '--'})<br/>
        <span class="dim">${v.verdict.thesis}</span>
      </div>`).join('');
    modal.innerHTML = `
      <button class="btn close-btn" onclick="closeHoloPanel()">CLOSE</button>
      <h2>${p.ticker} &middot; ${p.horizon}</h2>
      <div>blind: <span class="${voteClass(p.blind_vote)}">${p.blind_vote}</span> (${p.blind_probability})</div>
      <div>council: <span class="${voteClass(p.council_vote)}">${p.council_vote || 'PENDING'}</span> (${p.council_confidence ?? '--'})</div>
      <div>entry/exit/invalidation: ${p.entry ?? '--'} / ${p.exit ?? '--'} / ${p.invalidation ?? '--'}</div>
      <div style="margin-top:8px;">${p.dissent_summary || ''}</div>
      ${detail.resolution ? `<div style="margin-top:8px;" class="cyan">resolved: realised ${fmtPct(detail.resolution.realised_move_pct)}, direction_correct=${detail.resolution.direction_correct}</div>` : '<div class="dim" style="margin-top:8px;">not yet resolved</div>'}
      <h3 style="margin-top:16px;">Seat Votes</h3>
      ${votesHtml}
    `;
  } catch (e) {
    modal.innerHTML = `<button class="btn close-btn" onclick="closeHoloPanel()">CLOSE</button><p class="crimson">ERROR: ${e.message}</p>`;
  }
}

function closeHoloPanel() {
  document.getElementById('holo-backdrop').classList.remove('open');
}

async function refresh() {
  const ticker = document.getElementById('filter-ticker').value.trim();
  const params = new URLSearchParams();
  if (ticker) params.set('ticker', ticker);
  if (activeHorizonFilter) params.set('horizon', activeHorizonFilter);
  if (activeModeFilter) params.set('mode', activeModeFilter);

  const [predictionsResp, archivesResp] = await Promise.all([
    fetchJSON(`/api/predictions?${params.toString()}`),
    fetchJSON(activeModeFilter ? `/api/archives?mode=${activeModeFilter}` : '/api/archives'),
  ]);
  renderGrid(predictionsResp.predictions);
  renderScoreboard(archivesResp.benchmark);
}

async function updateCrypt() {
  const btn = document.getElementById('update-crypt-btn');
  const status = document.getElementById('sweep-status');
  btn.disabled = true;
  status.textContent = 'Sweeping the Crypt...';
  AudioBlips.blip(523, 0.08);
  try {
    const result = await fetchJSON('/api/resolve', { method: 'POST' });
    status.textContent = `Resolved ${result.swept.length} prediction(s).`;
    await refresh();
  } catch (e) {
    status.textContent = `ERROR: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  renderNav('/crypt.html');
  document.getElementById('update-crypt-btn').onclick = updateCrypt;
  document.getElementById('refresh-btn').onclick = refresh;
  document.getElementById('filter-ticker').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') refresh();
  });
  document.querySelectorAll('#filter-horizon .toggle-option').forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll('#filter-horizon .toggle-option').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeHorizonFilter = btn.dataset.horizon;
      refresh();
    };
  });
  document.querySelectorAll('#filter-mode .toggle-option').forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll('#filter-mode .toggle-option').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeModeFilter = btn.dataset.mode;
      refresh();
    };
  });
  document.getElementById('holo-backdrop').onclick = (e) => {
    if (e.target.id === 'holo-backdrop') closeHoloPanel();
  };
  refresh();
});
