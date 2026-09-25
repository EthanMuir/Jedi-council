// Seat record: each seat's hit rate per period, and the say it has earned.

let seats = [];
let mode = 'paid';
let sort = { key: 'name', dir: 1 };

function rate(v) {
  return v === null || v === undefined ? null : Math.round(v * 100);
}

function termCell(t) {
  if (!t || !t.n_resolutions) return '<td class="num faint">–</td>';
  const r = rate(t.hit_rate);
  const weight = t.weight === 0 ? '<span class="pill pill-warn">left out</span>'
    : t.weight !== 1 ? `<span class="pill ${t.weight > 1 ? 'pill-free' : 'pill-warn'}">${t.weight.toFixed(2)}×</span>` : '';
  const expert = isExpert()
    ? `<div class="cell-sub">${t.brier_score !== null && t.brier_score !== undefined ? `Brier ${t.brier_score.toFixed(3)} · ` : ''}counts ${t.competence.toFixed(1)}</div>` : '';
  return `<td class="num"><div class="rate-cell"><span><b>${r}%</b> <span class="faint">of ${t.n_resolutions}</span></span>${weight}</div>${expert}</td>`;
}

function sortValue(seat, key) {
  if (key === 'name') return seatName(seat.seat_id);
  if (key === 'overall') return seat.hit_rate ?? -1;
  return seat.terms?.[key]?.n_resolutions ? seat.terms[key].hit_rate ?? -1 : -1;
}

function render() {
  document.querySelectorAll('#seats-table th.sortable').forEach(th => {
    th.querySelector('.sort-arrow').textContent = th.dataset.sort === sort.key ? (sort.dir > 0 ? '↑' : '↓') : '';
    th.setAttribute('aria-sort', th.dataset.sort === sort.key ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none');
  });
  const rows = [...seats].sort((a, b) => {
    const va = sortValue(a, sort.key);
    const vb = sortValue(b, sort.key);
    return (typeof va === 'string' ? va.localeCompare(vb) : va - vb) * sort.dir;
  });
  document.getElementById('seats-body').innerHTML = rows.map(s => {
    const seat = SEAT_BY_ID[s.seat_id] || { name: s.seat_id, reads: '' };
    const overall = s.n_resolutions
      ? `<b>${rate(s.hit_rate)}%</b> <span class="faint">of ${s.n_resolutions}</span>` : '<span class="faint">–</span>';
    return `
      <tr>
        <td class="wrap"><div class="seat-cell"><b>${seat.name}</b><span class="faint">${seat.reads}</span></div></td>
        ${TERMS.map(t => termCell(s.terms?.[t])).join('')}
        <td class="num">${overall}</td>
      </tr>`;
  }).join('');
  const scored = seats.some(s => s.n_resolutions);
  document.getElementById('table-foot').textContent = scored
    ? 'Right means the price moved the way the seat leaned by the end of the period. "Even" calls aren\'t counted.'
    : 'No calls scored yet. The first ones come 7 days after a run, once you press "Score finished runs" on the History page.';
}

async function load() {
  try {
    const data = await fetchJSON(`/api/archives?mode=${mode}`);
    seats = data.seats;
    const r = data.release;
    document.getElementById('release-line').textContent = r
      ? `From everyone's ${mode} runs: weights published ${fmtDate(r.published_at)}, based on ${r.n_calls} scored calls by ${r.n_people} ${r.n_people === 1 ? 'person' : 'people'}.`
      : `No weights published for ${mode} runs yet, so every seat counts the same. The record below is your own runs.`;
    render();
  } catch (e) {
    document.getElementById('seats-body').innerHTML = `<tr><td colspan="5" class="down">Couldn't load: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function syncDetailSeg() {
  document.querySelectorAll('#detail-seg button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.detail === detailMode())));
}

document.addEventListener('DOMContentLoaded', () => {
  renderNav('/seats.html');
  document.querySelectorAll('#mode-seg button').forEach(b => {
    b.onclick = () => {
      mode = b.dataset.mode;
      document.querySelectorAll('#mode-seg button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
      load();
    };
  });
  document.querySelectorAll('#seats-table th.sortable').forEach(th => {
    th.onclick = () => {
      sort = sort.key === th.dataset.sort ? { key: sort.key, dir: -sort.dir } : { key: th.dataset.sort, dir: th.dataset.sort === 'name' ? 1 : -1 };
      render();
    };
  });
  document.querySelectorAll('#detail-seg button').forEach(b => { b.onclick = () => setDetailMode(b.dataset.detail); });
  document.addEventListener('detailchange', () => { syncDetailSeg(); render(); });
  syncDetailSeg();
  load();
});
