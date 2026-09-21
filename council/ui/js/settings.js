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

document.addEventListener('DOMContentLoaded', () => {
  renderNav('/settings.html');

  for (const btn of document.querySelectorAll('#horizon-toggle .toggle-option')) {
    btn.onclick = () => setHorizon(btn.dataset.horizon);
  }
  setHorizon('1w');

  loadRoles();
});
