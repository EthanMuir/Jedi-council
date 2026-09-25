// History: every saved run in a sortable table. Tapping a row reopens the
// run's full results on the Run page.

let runs = [];
let mode = '';
let sort = { key: 'date', dir: -1 };

// Runs saved before the three periods had a single horizon.
const OLD_HORIZONS = { '1d': 'Next day', '1w': 'Next week', '1m': 'Next month', '1y': 'Next year' };

// A term's chance of rising: p_raw holds it; older rows fall back to the
// stored vote and confidence.
function rowP(row) {
  if (row.p_raw !== null && row.p_raw !== undefined) return row.p_raw;
  if (row.council_vote === 'BULLISH') return row.council_confidence ?? 0.5;
  if (row.council_vote === 'BEARISH') return 1 - (row.council_confidence ?? 0.5);
  return 0.5;
}

function outcomeHtml(row) {
  if (row.direction_correct === null || row.direction_correct === undefined) {
    return `<span class="outcome faint">Scores ${fmtDate(row.resolve_at)}</span>`;
  }
  return row.direction_correct
    ? `<span class="outcome up">✓ Right <span class="num">${fmtMove(row.realised_move_pct)}</span></span>`
    : `<span class="outcome down">✗ Wrong <span class="num">${fmtMove(row.realised_move_pct)}</span></span>`;
}

// Under the direction: the price target, and once scored, whether the
// price ended inside its range.
function targetLine(row) {
  const t = row.price_target;
  if (!t) return '';
  if (row.in_range === null || row.in_range === undefined) {
    return `<span class="target-line faint">Target ${fmtPrice(t.target)} <span class="num">(${fmtPrice(t.low, false, t.target)}–${fmtPrice(t.high, false, t.target)})</span></span>`;
  }
  const where = row.in_range ? '✓ In range' : row.final_price > t.high ? 'Above range' : 'Below range';
  return `<span class="target-line ${row.in_range ? 'up' : 'faint'}">${where} <span class="num">${fmtPrice(row.final_price, true)}</span></span>`;
}

function termCell(run, term) {
  let row = run.terms[term];
  let label = '';
  if (!row && run.legacy) {
    // An old single-horizon run shows in the column closest to its horizon.
    const [key, only] = Object.entries(run.terms)[0] || [];
    const column = { '1d': 'short', '1w': 'short', '1m': 'medium', '1y': 'long' }[key];
    if (column === term) { row = only; label = `<span class="faint">${OLD_HORIZONS[key] || key}</span> `; }
  }
  if (!row) return '<td class="faint">–</td>';
  const p = rowP(row);
  const dir = dirOf(p);
  const lean = isExpert() ? `${arrowOf(p)} ${dir === 'even' ? '50/50' : pct(p)}` : `${arrowOf(p)} ${leanWords(p)}`;
  return `<td><div class="term-cell">${label}<span class="${dir}">${lean}</span>${outcomeHtml(row)}${targetLine(row)}</div></td>`;
}

function runTags(run) {
  const tags = [`<span class="pill">${run.run_shape === 'lite' ? 'Lite' : 'Full'}</span>`];
  if (run.run_mode === 'free') tags.push('<span class="pill pill-free">Free</span>');
  if (run.run_mode === 'sample') tags.push('<span class="pill pill-warn">Sample</span>');
  return tags.join(' ');
}

function renderTable() {
  const filter = document.getElementById('ticker-filter').value.trim().toUpperCase();
  const rows = runs
    .filter(r => !filter || r.ticker.includes(filter))
    .sort((a, b) => {
      const v = {
        ticker: () => a.ticker.localeCompare(b.ticker),
        date: () => a.created_at.localeCompare(b.created_at),
        cost: () => (a.total_cost_usd || 0) - (b.total_cost_usd || 0),
      }[sort.key]();
      return v * sort.dir;
    });
  document.querySelectorAll('#runs-table th.sortable').forEach(th => {
    th.querySelector('.sort-arrow').textContent = th.dataset.sort === sort.key ? (sort.dir > 0 ? '↑' : '↓') : '';
    th.setAttribute('aria-sort', th.dataset.sort === sort.key ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none');
  });
  const body = document.getElementById('runs-body');
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="7" class="faint">${runs.length ? 'No runs match this filter.' : 'No runs yet. Start one on the Run page.'}</td></tr>`;
    return;
  }
  body.innerHTML = rows.map(run => `
    <tr class="clickable" data-run="${escapeHtml(run.run_id)}" tabindex="0">
      <td><b>${escapeHtml(run.ticker)}</b></td>
      <td class="muted">${fmtDate(run.created_at, true)}</td>
      ${TERMS.map(t => termCell(run, t)).join('')}
      <td>${runTags(run)}</td>
      <td class="num">${fmtMoney(run.total_cost_usd)}</td>
    </tr>`).join('');
  body.querySelectorAll('tr.clickable').forEach(tr => {
    const open = () => { location.href = `/index.html?run=${encodeURIComponent(tr.dataset.run)}`; };
    tr.onclick = open;
    tr.onkeydown = e => { if (e.key === 'Enter') open(); };
  });
}

function renderScoreboard(bench) {
  const box = document.getElementById('scoreboard');
  const final = bench?.council_final;
  if (!bench || !bench.n_resolutions || !final?.n) {
    box.innerHTML = '<div class="score-empty"><b>Nothing scored yet.</b> <span class="muted">A run\'s next-week call is scored 7 days after it; press "Score finished runs" then.</span></div>';
    return;
  }
  const rate = v => (v === null || v === undefined ? '–' : `${Math.round(v * 100)}%`);
  const always = bench.always_bullish;
  const stat = (big, label, sub = '') => `<div class="stat"><div class="stat-big num">${big}</div><div class="stat-label">${label}</div>${sub ? `<div class="stat-sub">${sub}</div>` : ''}</div>`;
  const stats = [
    stat(rate(final.hit_rate), 'Council right', `${final.n} scored calls`),
    stat(rate(always?.hit_rate), 'Always saying "up"', 'the simplest rival'),
  ];
  // How often the price ended inside its range, over the runs listed here.
  const ranged = runs.flatMap(r => Object.values(r.terms)).filter(x => x.in_range !== null && x.in_range !== undefined);
  if (ranged.length) {
    const held = ranged.filter(x => x.in_range).length;
    stats.push(stat(rate(held / ranged.length), 'Ended in the price range', `${held} of ${ranged.length} scored ranges`));
  }
  if (bench.buy_and_hold_avg_return_pct !== null && bench.buy_and_hold_avg_return_pct !== undefined) {
    stats.push(stat(fmtMove(bench.buy_and_hold_avg_return_pct), 'Average move', 'buying and holding'));
  }
  if (isExpert()) {
    stats.push(stat(final.brier ?? '–', 'Brier score', 'lower is better; 0.25 is a coin flip'));
    if (bench.council_blind?.hit_rate !== undefined) stats.push(stat(rate(bench.council_blind.hit_rate), 'Before the debate', 'the seats\' first answers'));
  }
  box.innerHTML = `<div class="stats">${stats.join('')}</div>`;
}

async function load() {
  const q = mode ? `&mode=${mode}` : '';
  try {
    const data = await fetchJSON(`/api/predictions?limit=200${q}`);
    runs = data.runs;
    renderTable();
  } catch (e) {
    document.getElementById('runs-body').innerHTML = `<tr><td colspan="7" class="down">Couldn't load runs: ${escapeHtml(e.message)}</td></tr>`;
  }
  const benchMode = mode === 'paid' || mode === 'free' ? `?mode=${mode}` : '';
  fetchJSON(`/api/archives${benchMode}`).then(a => renderScoreboard(a.benchmark)).catch(() => renderScoreboard(null));
}

async function scoreRuns() {
  const btn = document.getElementById('score-btn');
  const status = document.getElementById('score-status');
  btn.disabled = true;
  status.textContent = 'Checking prices for runs whose time is up…';
  try {
    const r = await fetchJSON('/api/resolve', { method: 'POST' });
    const n = (r.swept || []).length;
    status.textContent = n ? `Scored ${n} ${n === 1 ? 'call' : 'calls'}.` : 'Nothing new to score yet.';
    await load();
  } catch (e) {
    status.textContent = `Couldn't score: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

function syncDetailSeg() {
  document.querySelectorAll('#detail-seg button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.detail === detailMode())));
}

document.addEventListener('DOMContentLoaded', () => {
  renderNav('/history.html');
  document.querySelectorAll('#mode-seg button').forEach(b => {
    b.onclick = () => {
      mode = b.dataset.mode;
      document.querySelectorAll('#mode-seg button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
      load();
    };
  });
  document.querySelectorAll('#runs-table th.sortable').forEach(th => {
    th.onclick = () => {
      sort = sort.key === th.dataset.sort ? { key: sort.key, dir: -sort.dir } : { key: th.dataset.sort, dir: th.dataset.sort === 'ticker' ? 1 : -1 };
      renderTable();
    };
  });
  document.getElementById('ticker-filter').addEventListener('input', renderTable);
  document.getElementById('score-btn').onclick = scoreRuns;
  document.querySelectorAll('#detail-seg button').forEach(b => { b.onclick = () => setDetailMode(b.dataset.detail); });
  document.addEventListener('detailchange', () => { syncDetailSeg(); load(); });
  syncDetailSeg();
  load();
});
