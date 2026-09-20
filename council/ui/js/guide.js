// THE GUIDE -- mostly static content; the one dynamic piece is the
// horizon-competence table, mirrored here from
// council/engine/horizons.py::COMPETENCE_MATRIX so it can't silently drift
// from the seat cards above it without someone noticing the duplication.

const COMPETENCE_MATRIX = {
  technician:          { '1d': 1.0, '1w': 0.9, '1m': 0.6, '1y': 0.3 },
  fundamentalist:      { '1d': 0.0, '1w': 0.2, '1m': 0.6, '1y': 1.0 },
  catalyst_seer:       { '1d': 0.9, '1w': 1.0, '1m': 0.7, '1y': 0.4 },
  insider_reader:      { '1d': 0.1, '1w': 0.4, '1m': 0.8, '1y': 0.9 },
  senate_watcher:      { '1d': 0.1, '1w': 0.3, '1m': 0.7, '1y': 0.8 },
  flow_cartographer:   { '1d': 0.2, '1w': 0.4, '1m': 0.8, '1y': 0.9 },
  oracle_options:      { '1d': 1.0, '1w': 0.9, '1m': 0.6, '1y': 0.3 },
  macro_sage:          { '1d': 0.0, '1w': 0.3, '1m': 0.8, '1y': 1.0 },
  cross_market:        { '1d': 1.0, '1w': 0.8, '1m': 0.6, '1y': 0.4 },
  estimate_scribe:     { '1d': 0.2, '1w': 0.5, '1m': 0.9, '1y': 0.9 },
  transcript_linguist: { '1d': 0.3, '1w': 0.6, '1m': 0.8, '1y': 0.7 },
  structure_archivist: { '1d': 0.4, '1w': 0.5, '1m': 0.7, '1y': 0.8 },
};

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
  transcript_linguist: 'Listener to the Council of Officers',
  structure_archivist: 'Keeper of Charters',
};

function renderCompetenceTable() {
  const tbody = document.getElementById('competence-tbody');
  tbody.innerHTML = Object.entries(COMPETENCE_MATRIX).map(([seatId, byHorizon]) => `
    <tr>
      <td>${SEAT_TITLES[seatId]}<div class="dim" style="font-size:10px;">${seatId}</div></td>
      ${['1d', '1w', '1m', '1y'].map(h => {
        const v = byHorizon[h];
        return `<td class="${v > 0 ? 'active-cell' : 'inactive-cell'}">${v.toFixed(1)}</td>`;
      }).join('')}
    </tr>
  `).join('');
}

document.addEventListener('DOMContentLoaded', () => {
  renderNav('/guide.html');
  renderCompetenceTable();
});
