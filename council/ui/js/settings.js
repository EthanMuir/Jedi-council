// THE SETTINGS -- per-seat model assignment + live cost estimate.
// Task #74.

let CATALOG_BY_ID = {};
let CURRENT_HORIZON = '1w';

function fmtUsd(v) {
  if (v === null || v === undefined) return '--';
  if (v === 0) return '$0.00';
  // Small per-call costs (a fraction of a cent) round away to $0.00 at two
  // decimals, which would make cheap models look free -- show more
  // precision the smaller the number gets.
  const digits = v < 0.01 ? 4 : 2;
  return `$${v.toFixed(digits)}`;
}

function buildModelSelect(role) {
  const select = document.createElement('select');
  select.className = 'model-select';
  select.dataset.role = role.role;

  for (const model of Object.values(CATALOG_BY_ID)) {
    const opt = document.createElement('option');
    opt.value = model.id;
    const isRecommended = model.id === role.recommended_model;
    opt.textContent = `${isRecommended ? '★ ' : ''}${model.display_name} (${model.provider}) -- ${fmtUsd(model.typical_call_cost_usd)}/call`;
    if (model.id === role.current_model) opt.selected = true;
    select.appendChild(opt);
  }

  select.onchange = () => setModel(role.role, select.value);
  return select;
}

async function setModel(role, modelId) {
  try {
    await fetchJSON('/api/settings/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role, model_id: modelId }),
    });
    AudioBlips.blip();
    await loadRoles();
    await loadCostEstimate();
  } catch (e) {
    alert(`Failed to set model: ${e.message}`);
  }
}

async function resetModel(role) {
  try {
    await fetchJSON('/api/settings/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role, model_id: null }),
    });
    AudioBlips.blip();
    await loadRoles();
    await loadCostEstimate();
  } catch (e) {
    alert(`Failed to reset model: ${e.message}`);
  }
}

function renderRolesTable(data) {
  CATALOG_BY_ID = Object.fromEntries(data.catalog.map(m => [m.id, m]));
  const tbody = document.getElementById('settings-tbody');

  tbody.innerHTML = '';
  for (const role of data.roles) {
    const model = CATALOG_BY_ID[role.current_model];
    const tr = document.createElement('tr');
    if (role.is_override) tr.classList.add('overridden');

    const seatTd = document.createElement('td');
    seatTd.innerHTML = `${role.title}<div class="dim" style="font-size:11px;">${role.role}</div>`;
    tr.appendChild(seatTd);

    const providerTd = document.createElement('td');
    providerTd.innerHTML = model
      ? `<span class="provider-badge provider-${model.provider}">${model.provider}</span>`
      : '<span class="dim">--</span>';
    tr.appendChild(providerTd);

    const modelTd = document.createElement('td');
    modelTd.appendChild(buildModelSelect(role));
    if (role.is_override) {
      const badge = document.createElement('span');
      badge.className = 'override-badge';
      badge.textContent = ' OVERRIDE';
      modelTd.appendChild(badge);
    }
    tr.appendChild(modelTd);

    const costTd = document.createElement('td');
    costTd.textContent = model ? fmtUsd(model.typical_call_cost_usd) : '--';
    tr.appendChild(costTd);

    const actionTd = document.createElement('td');
    if (role.is_override) {
      const resetBtn = document.createElement('button');
      resetBtn.className = 'reset-link';
      resetBtn.textContent = 'reset to recommended';
      resetBtn.onclick = () => resetModel(role.role);
      actionTd.appendChild(resetBtn);
    }
    tr.appendChild(actionTd);

    tbody.appendChild(tr);
  }
}

async function loadRoles() {
  try {
    const data = await fetchJSON('/api/settings/models');
    renderRolesTable(data);
  } catch (e) {
    document.getElementById('settings-tbody').innerHTML =
      `<tr><td colspan="5" class="crimson">ERROR: ${e.message}</td></tr>`;
  }
}

async function loadCostEstimate() {
  const summary = document.getElementById('cost-summary');
  const lineitems = document.getElementById('cost-lineitems');
  try {
    const est = await fetchJSON(`/api/settings/cost-estimate?horizon=${CURRENT_HORIZON}`);
    summary.innerHTML = `
      <span class="big-number ${est.is_fixture ? 'fixture' : ''}">${fmtUsd(est.total_cost_usd)}</span>
      <span class="dim">${est.total_calls} LLM calls -- ${CURRENT_HORIZON} horizon${est.is_fixture ? ' -- FIXTURE MODE, $0 actual' : ''}</span>
    `;
    lineitems.innerHTML = est.line_items.map(li => `
      <tr>
        <td>${li.label}<span class="dim"> (${li.model})</span></td>
        <td>${fmtUsd(li.est_cost_usd)}</td>
      </tr>
    `).join('');
  } catch (e) {
    summary.innerHTML = `<span class="crimson">ERROR: ${e.message}</span>`;
  }
}

function setHorizon(horizon) {
  CURRENT_HORIZON = horizon;
  for (const btn of document.querySelectorAll('#horizon-toggle .toggle-option')) {
    btn.classList.toggle('active', btn.dataset.horizon === horizon);
  }
  loadCostEstimate();
}

function renderCenterpieceGrid() {
  const grid = document.getElementById('centerpiece-grid');
  const current = getCenterpieceStyle();
  grid.innerHTML = '';
  for (const style of CENTERPIECE_STYLES) {
    const swatch = document.createElement('div');
    swatch.className = 'appearance-swatch' + (style.id === current ? ' active' : '');
    swatch.title = style.blurb;

    const visual = document.createElement('div');
    visual.className = 'appearance-swatch-visual';
    const holo = document.createElement('div');
    holo.className = 'holocron preview';
    visual.appendChild(holo);
    buildHolocronInto(holo, style.id);
    // buildHolocronInto only manages the "style-*" token -- "preview" has
    // to survive that swap, since it's what turns off the real Chamber's
    // absolute positioning for use in this small inline swatch instead.
    holo.classList.add('preview');

    const name = document.createElement('div');
    name.className = 'appearance-swatch-name';
    name.textContent = style.name;

    swatch.appendChild(visual);
    swatch.appendChild(name);
    swatch.onclick = () => {
      setCenterpieceStyle(style.id);
      AudioBlips.blip();
      renderCenterpieceGrid();
    };
    grid.appendChild(swatch);
  }
}

function renderChairGrid() {
  const grid = document.getElementById('chair-grid');
  const current = getSeatChairStyle();
  grid.innerHTML = '';
  for (const style of SEAT_CHAIR_STYLES) {
    const swatch = document.createElement('div');
    swatch.className = 'appearance-swatch' + (style.id === current ? ' active' : '');
    swatch.title = style.blurb;

    const visual = document.createElement('div');
    visual.className = 'appearance-swatch-visual';
    if (style.id === 'wizard') {
      visual.innerHTML = `
        <div class="chair-preview-wrap" style="height:108px;">
          <div class="seat-chair chair-style-wizard state-bullish">
            <div class="wiz-chair-stage">
              <div class="wiz-chair-glow"></div>
              <canvas class="wiz-chair-canvas" width="36" height="46"></canvas>
            </div>
            <div class="seat-title">Keeper of the Charts</div>
            <div class="seat-vote status-bullish">BULLISH 0.612</div>
          </div>
        </div>
      `;
      drawWizardBust(visual.querySelector('canvas'), 'bullish');
    } else {
      visual.innerHTML = `
        <div class="chair-preview-wrap">
          <div class="seat-chair chair-style-${style.id} state-bullish">
            <div class="seat-bust"><div class="seat-bust-fill vote-bullish" style="width:100%;"></div></div>
            <div class="seat-title">Keeper of the Charts</div>
            <div class="seat-vote status-bullish">BULLISH 0.612</div>
          </div>
        </div>
      `;
    }

    const name = document.createElement('div');
    name.className = 'appearance-swatch-name';
    name.textContent = style.name;

    swatch.appendChild(visual);
    swatch.appendChild(name);
    swatch.onclick = () => {
      setSeatChairStyle(style.id);
      AudioBlips.blip();
      renderChairGrid();
    };
    grid.appendChild(swatch);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  renderNav('/settings.html');

  for (const btn of document.querySelectorAll('#horizon-toggle .toggle-option')) {
    btn.onclick = () => setHorizon(btn.dataset.horizon);
  }
  setHorizon('1w');

  renderCenterpieceGrid();
  renderChairGrid();
  loadRoles();
});
