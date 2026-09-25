// THE ARCHIVES -- per-seat leaderboard, calibration curves, and ranks.

const RANK_ORDER = ['GRAND_MASTER', 'MASTER', 'KNIGHT', 'PADAWAN', 'YOUNGLING'];

// Free-mode runs use weaker models, so they're scored apart from paid runs
// by default -- one kind never drags down (or props up) the other's record.
const MODE_HINTS = {
  paid: 'Scoring runs made with paid models only.',
  free: 'Scoring Free Mode runs only -- free models, kept apart from the paid council.',
  '': 'Scoring every run together, paid and free.',
};
let activeMode = 'paid';

function calibBarHtml(curve) {
  if (!curve || curve.length === 0) return '<span class="dim">no data</span>';
  return `<div class="calib-bar">${curve.map(bin => {
    const height = Math.round((bin.actual_rate || 0) * 100);
    return `<div class="bin" title="${bin.range_label}: predicted ${bin.mean_predicted}, actual ${bin.actual_rate} (n=${bin.n})">
      <div class="fill" style="height:${height}%"></div>
    </div>`;
  }).join('')}</div>`;
}

async function load() {
  const tbody = document.getElementById('archives-tbody');
  try {
    const data = await fetchJSON(activeMode ? `/api/archives?mode=${activeMode}` : '/api/archives');
    const sorted = [...data.seats].sort((a, b) => {
      const rankDiff = RANK_ORDER.indexOf(a.rank) - RANK_ORDER.indexOf(b.rank);
      if (rankDiff !== 0) return rankDiff;
      return (b.hit_rate || 0) - (a.hit_rate || 0);
    });

    tbody.innerHTML = sorted.map(s => `
      <tr>
        <td><span class="rank-badge rank-${s.rank}">${s.rank.replace('_', ' ')}</span></td>
        <td>${s.title}<div class="dim" style="font-size:11px;">${s.seat_id}</div></td>
        <td>${s.n_resolutions}</td>
        <td>${s.hit_rate ?? '--'}</td>
        <td>${s.brier_score ?? '--'}</td>
        <td>${s.log_loss ?? '--'}</td>
        <td>${calibBarHtml(s.calibration_curve)}</td>
      </tr>
    `).join('');
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="7" class="crimson">ERROR: ${e.message}</td></tr>`;
  }
}

function setMode(mode) {
  activeMode = mode;
  for (const btn of document.querySelectorAll('#mode-toggle .toggle-option')) {
    btn.classList.toggle('active', btn.dataset.mode === mode);
  }
  document.getElementById('mode-hint').textContent = MODE_HINTS[mode];
  load();
}

document.addEventListener('DOMContentLoaded', async () => {
  renderNav('/classic/archives.html');
  for (const btn of document.querySelectorAll('#mode-toggle .toggle-option')) {
    btn.onclick = () => setMode(btn.dataset.mode);
  }
  let mode = 'paid';
  try {
    if ((await fetchJSON('/api/settings/free-mode')).enabled) mode = 'free';
  } catch (e) {
    /* fall back to paid */
  }
  setMode(mode);
});
