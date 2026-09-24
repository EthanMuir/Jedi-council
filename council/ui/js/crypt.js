// THE CRYPT -- every run, its three terms each scored on their own window,
// the update lever, and the scoreboard (council vs buy-and-hold vs
// always-bullish).

let activeTermFilter = '';
let activeModeFilter = '';

// Runs saved before terms existed were a single horizon.
const LEGACY_HORIZONS = { '1d': 'Next day', '1w': 'Next week', '1m': 'Next month', '1y': 'Next year' };

function termName(key) {
  return TERM_NAMES[key] || `${LEGACY_HORIZONS[key] || key} (old run)`;
}

// A small label on each card for anything other than a full paid run.
function runTagsHtml(run) {
  const tags = [];
  if (run.run_mode === 'free') tags.push('<span class="run-tag run-tag-free">FREE</span>');
  if (run.run_mode === 'sample') tags.push('<span class="run-tag">SAMPLE</span>');
  if (run.run_shape === 'lite') tags.push('<span class="run-tag">LITE</span>');
  return tags.length ? `<div class="meta-line">${tags.join(' ')}</div>` : '';
}

function renderScoreboard(benchmark) {
  const el = document.getElementById('scoreboard');
  if (!benchmark || benchmark.n_resolutions === 0) {
    el.innerHTML = '<span class="dim">Nothing scored yet. Pull the lever once a term\'s window has passed -- the short term is first, a week after a run.</span>';
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
    <div class="dim" style="margin-top:8px; font-size:12px;">Every term of every run counts once here.</div>
    <div style="margin-top:10px;">Buy &amp; hold average realised return: <span class="cyan">${fmtPct(benchmark.buy_and_hold_avg_return_pct)}</span></div>
  `;
}

// A term row's chance of rising: p_raw holds it; older rows without it
// fall back to the stored vote and confidence.
function rowPBullish(row) {
  if (row.p_raw !== null && row.p_raw !== undefined) return row.p_raw;
  if (row.council_vote === 'BULLISH') return row.council_confidence ?? 0.5;
  if (row.council_vote === 'BEARISH') return 1 - (row.council_confidence ?? 0.5);
  return 0.5;
}

function outcomeHtml(row) {
  if (row.direction_correct === null || row.direction_correct === undefined) {
    const when = new Date(row.resolve_at);
    return `<span class="dim">scores ${when.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}</span>`;
  }
  const move = fmtPct(row.realised_move_pct);
  return row.direction_correct
    ? `<span class="outcome-mark amber">&#10003; right</span> <span class="dim">${move}</span>`
    : `<span class="outcome-mark crimson">&#10007; wrong</span> <span class="dim">${move}</span>`;
}

function termLineHtml(key, row) {
  const p = rowPBullish(row);
  const vote = voteFromP(p);
  const arrow = vote === 'BULLISH' ? '&#9650;' : vote === 'BEARISH' ? '&#9660;' : '&#9679;';
  const letter = TERM_LETTERS[key] || key.toUpperCase();
  return `
    <div class="term-line" title="${escapeHtml(termName(key))}">
      <span class="term-line-letter">${letter}</span>
      <span class="${voteClass(vote)}">${arrow} ${leanLabel(p)}</span>
      <span class="dim">${vote === 'NO_CONVICTION' ? '' : Math.round(Math.max(p, 1 - p) * 100) + '%'}</span>
      <span class="term-line-outcome">${outcomeHtml(row)}</span>
    </div>`;
}

function orderedTerms(run) {
  const keys = Object.keys(run.terms);
  return [...TERMS.filter(t => keys.includes(t)), ...keys.filter(k => !TERMS.includes(k))];
}

function cardOutcomeClass(run) {
  const rows = Object.values(run.terms);
  const scored = rows.filter(r => r.direction_correct !== null && r.direction_correct !== undefined);
  if (!scored.length) return 'outcome-unresolved';
  const right = scored.filter(r => r.direction_correct).length;
  if (right === scored.length) return 'outcome-correct';
  if (right === 0) return 'outcome-wrong';
  return 'outcome-mixed';
}

function renderGrid(runs) {
  const grid = document.getElementById('holocron-grid');
  if (runs.length === 0) {
    grid.innerHTML = '<p class="dim">No runs match this filter.</p>';
    return;
  }
  grid.innerHTML = '';
  for (const run of runs) {
    const card = document.createElement('div');
    card.className = `holocron-card ${cardOutcomeClass(run)}`;
    card.innerHTML = `
      <div class="ticker-line">${escapeHtml(run.ticker)}</div>
      <div class="meta-line dim">${run.created_at.slice(0, 16).replace('T', ' ')}</div>
      ${runTagsHtml(run)}
      <div class="term-lines">${orderedTerms(run).map(k => termLineHtml(k, run.terms[k])).join('')}</div>
    `;
    card.onclick = () => openRunDetail(run.run_id);
    grid.appendChild(card);
  }
}

function seatVotesTable(detail) {
  const terms = orderedTerms(detail);
  const seats = {};
  for (const t of terms) {
    for (const v of detail.terms[t].seat_votes) {
      seats[v.seat_id] = seats[v.seat_id] || { thesis: v.verdict.thesis, terms: {} };
      seats[v.seat_id].terms[t] = v.verdict;
    }
  }
  const cell = v => {
    if (!v) return '<td class="dim">--</td>';
    if (v.vote === 'NO_READ') return '<td class="status-noread">no read</td>';
    if (v.vote === 'NO_CONVICTION') return '<td class="status-noread">even</td>';
    const arrow = v.vote === 'BULLISH' ? '&#9650;' : '&#9660;';
    return `<td class="${voteClass(v.vote)}">${arrow} ${Math.round(v.probability * 100)}%</td>`;
  };
  return `
    <table class="scoreboard-table mono seat-votes-table">
      <thead><tr><th>Seat</th>${terms.map(t => `<th>${TERM_LETTERS[t] || t}</th>`).join('')}</tr></thead>
      <tbody>
        ${Object.entries(seats).map(([sid, s]) => `
          <tr title="${escapeHtml(s.thesis)}"><td>${sid}</td>${terms.map(t => cell(s.terms[t])).join('')}</tr>`).join('')}
      </tbody>
    </table>`;
}

async function openRunDetail(runId) {
  const modal = document.getElementById('holo-modal');
  modal.innerHTML = '<p class="dim">Loading...</p>';
  document.getElementById('holo-backdrop').classList.add('open');
  try {
    const detail = await fetchJSON(`/api/runs/${encodeURIComponent(runId)}`);
    const synthesis = detail.synthesis;
    const terms = orderedTerms(detail);
    const bars = terms.map(t => {
      const row = detail.terms[t].prediction;
      const res = detail.terms[t].resolution;
      const bar = synthesis?.terms?.[t] || {
        p_bullish: rowPBullish(row), seats_counted: 1, consensus_pct: row.consensus_pct,
      };
      const scored = res
        ? `<div class="${res.direction_correct ? 'amber' : 'crimson'}" style="margin-top:4px;">${res.direction_correct ? '&#10003; Right' : '&#10007; Wrong'} -- the stock moved ${fmtPct(res.realised_move_pct)}</div>`
        : `<div class="dim" style="margin-top:4px;">Scores on ${row.resolve_at.slice(0, 10)}</div>`;
      return TERMS.includes(t)
        ? termBarHtml(t, bar) + scored
        : `<div class="term-bar-row"><b>${escapeHtml(termName(t))}</b>: <span class="${voteClass(row.council_vote)}">${row.council_vote || 'PENDING'}</span> (${row.council_confidence ?? '--'})${scored}<div style="margin-top:6px;">${escapeHtml(row.dissent_summary || '')}</div></div>`;
    }).join('');
    modal.innerHTML = `
      <button class="btn close-btn" onclick="closeHoloPanel()">CLOSE</button>
      <h2>${escapeHtml(detail.ticker)}</h2>
      <div class="dim" style="margin-bottom:10px;">${detail.created_at.slice(0, 16).replace('T', ' ')}</div>
      ${synthesis ? `<div class="gm-headline">${escapeHtml(synthesis.headline)}</div>` : ''}
      ${bars}
      ${synthesis ? `
        <div style="margin-top:10px; line-height:1.55;"><b>Who disagreed:</b> ${escapeHtml(synthesis.dissent_summary)}</div>
        <div style="margin-top:6px; line-height:1.55;"><b>Reasoning:</b> ${escapeHtml(synthesis.reasoning)}</div>` : ''}
      <h3 style="margin-top:16px;">Seat Leans</h3>
      <div class="table-scroll">${seatVotesTable(detail)}</div>
    `;
  } catch (e) {
    modal.innerHTML = `<button class="btn close-btn" onclick="closeHoloPanel()">CLOSE</button><p class="crimson">ERROR: ${escapeHtml(e.message)}</p>`;
  }
}

function closeHoloPanel() {
  document.getElementById('holo-backdrop').classList.remove('open');
}

async function refresh() {
  const ticker = document.getElementById('filter-ticker').value.trim();
  const params = new URLSearchParams();
  if (ticker) params.set('ticker', ticker);
  if (activeTermFilter) params.set('term', activeTermFilter);
  if (activeModeFilter) params.set('mode', activeModeFilter);

  const [predictionsResp, archivesResp] = await Promise.all([
    fetchJSON(`/api/predictions?${params.toString()}`),
    fetchJSON(activeModeFilter ? `/api/archives?mode=${activeModeFilter}` : '/api/archives'),
  ]);
  renderGrid(predictionsResp.runs);
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
    status.textContent = `Scored ${result.swept.length} term(s).`;
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
  document.querySelectorAll('#filter-term .toggle-option').forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll('#filter-term .toggle-option').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeTermFilter = btn.dataset.term;
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
