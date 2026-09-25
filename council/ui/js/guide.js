// Guide: a sample bar, and the seats table built from the same lists the
// rest of the app uses so it can't drift.

document.addEventListener('DOMContentLoaded', () => {
  renderNav('/guide.html');
  initSections();

  document.getElementById('demo-bar').innerHTML = leanBarHtml(0.556, {
    dots: [0.61, 0.58, 0.57, 0.55, 0.5, 0.5, 0.47, 0.6, 0.54, 0.53, 0.45, 0.56]
      .map((p, i) => ({ p, weight: 0.4 + (i % 3) * 0.3, name: SEATS[i].name })),
  });

  const cell = w => `<td class="num"><span class="weight" style="--w:${w}">${w.toFixed(1)}</span></td>`;
  document.getElementById('seats-table').innerHTML = `
    <thead><tr><th>Seat</th><th>Reads</th><th class="num">Week</th><th class="num">3 mo</th><th class="num">Year</th></tr></thead>
    <tbody>${SEATS.map(s => `
      <tr><td><b>${s.name}</b></td><td class="wrap muted">${s.reads}</td>${TERMS.map(t => cell(COMPETENCE[s.id][t])).join('')}</tr>`).join('')}
    </tbody>`;
});
