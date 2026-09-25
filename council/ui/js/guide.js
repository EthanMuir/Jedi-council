// Guide: a sample bar, and the seats table built from the same lists the
// rest of the app uses so it can't drift.

// "Report a problem": the run page's error alerts link here with the run
// and ticker in the address, so the report says which run went wrong.
function wireReportForm() {
  const form = document.getElementById('report-form');
  if (!form) return;
  const params = new URLSearchParams(location.search);
  const runId = params.get('run') || '';
  if (params.get('ticker')) document.getElementById('report-ticker').value = params.get('ticker');
  if (params.get('kind')) document.getElementById('report-kind').value = params.get('kind');
  if (runId) {
    const note = document.getElementById('report-run');
    note.hidden = false;
    note.textContent = 'This report will include the run you came from, so the owner can open it.';
  }
  const msg = document.getElementById('report-msg');
  const send = document.getElementById('report-send');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = document.getElementById('report-message').value.trim();
    if (message.length < 5) {
      msg.className = 'report-msg error';
      msg.textContent = 'Tell us a little about what happened first.';
      document.getElementById('report-message').focus();
      return;
    }
    send.disabled = true;
    msg.className = 'report-msg';
    msg.textContent = 'Sending…';
    try {
      const resp = await fetch('/api/reports', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: document.getElementById('report-kind').value,
          message,
          ticker: document.getElementById('report-ticker').value.trim(),
          run_id: runId,
          page: params.get('from') || document.referrer.replace(location.origin, '') || '',
        }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || 'Couldn\'t send it. Try again in a moment.');
      form.reset();
      msg.className = 'report-msg ok';
      msg.textContent = 'Thanks, it\'s been sent. The owner will take a look.';
    } catch (err) {
      msg.className = 'report-msg error';
      msg.textContent = err.message;
    } finally {
      send.disabled = false;
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  renderNav('/guide.html');
  initSections();

  document.getElementById('demo-bar').innerHTML = leanBarHtml(0.556, {
    dots: [0.61, 0.58, 0.57, 0.55, 0.5, 0.5, 0.47, 0.6, 0.54, 0.53, 0.45, 0.56]
      .map((p, i) => ({ p, weight: 0.4 + (i % 3) * 0.3, name: SEATS[i].name })),
  });

  wireReportForm();

  const cell = w => `<td class="num"><span class="weight" style="--w:${w}">${w.toFixed(1)}</span></td>`;
  document.getElementById('seats-table').innerHTML = `
    <thead><tr><th>Seat</th><th>Reads</th><th class="num">Week</th><th class="num">3 mo</th><th class="num">Year</th></tr></thead>
    <tbody>${SEATS.map(s => `
      <tr><td><b>${s.name}</b></td><td class="wrap muted">${s.reads}</td>${TERMS.map(t => cell(COMPETENCE[s.id][t])).join('')}</tr>`).join('')}
    </tbody>`;
});
