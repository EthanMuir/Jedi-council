// THE GUIDE -- mostly static content; the one dynamic piece is the
// seats-by-term table, built from common.js's shared COMPETENCE_MATRIX
// (also used by chamber.js) so there's exactly one copy of this data to
// keep in sync with council/engine/horizons.py::COMPETENCE_MATRIX.

const SEAT_TITLES = {
  technician: 'Keeper of the Charts',
  fundamentalist: 'Keeper of the Ledgers',
  catalyst_seer: 'Watcher of Omens',
  insider_reader: 'Student of the Inner Circle',
  senate_watcher: 'Reader of the Republic',
  flow_cartographer: 'Reader of Great Tides',
  oracle_options: 'Reader of Probabilities',
  macro_sage: 'Keeper of the Outer Rim',
  cross_market: 'Reader of Distant Stars',
  estimate_scribe: 'Keeper of Expectations',
  analyst_ratings: "Reader of the Guild's Targets",
  structure_archivist: 'Keeper of Charters',
};

function renderCompetenceTable() {
  const tbody = document.getElementById('competence-tbody');
  tbody.innerHTML = Object.entries(COMPETENCE_MATRIX).map(([seatId, byTerm]) => `
    <tr>
      <td>${SEAT_TITLES[seatId]}<div class="dim" style="font-size:10px;">${seatId}</div></td>
      ${TERMS.map(t => {
        const v = byTerm[t];
        return `<td class="${v >= 0.5 ? 'active-cell' : 'low-cell'}">${v.toFixed(1)}</td>`;
      }).join('')}
    </tr>
  `).join('');
}

document.addEventListener('DOMContentLoaded', () => {
  renderNav('/guide.html');
  initSections();
  renderCompetenceTable();
});
