// THE SETTINGS -- API keys, per-seat model assignment with a live cost
// estimate, and Chamber appearance. One section shows at a time (see
// initSections in common.js).

let CATALOG_BY_ID = {};
let CURRENT_HORIZON = '1w';

// ---- API keys -------------------------------------------------------------
// Setup help for each key, written for someone who has never made an API
// key before: what it's for in plain words, what it costs, and each step
// with a link straight to the page where it happens.
const KEY_GUIDES = [
  {
    name: 'anthropic_api_key',
    label: 'Anthropic (Claude)',
    required: true,
    purpose: 'Lets the Council think. Without it, every run replays sample answers instead of analyzing anything.',
    placeholder: 'Paste key (sk-ant-...)',
    cost: 'Pay as you go -- $5 of credit is plenty to start. A Claude.ai subscription does not include this; API credit is bought separately.',
    steps: [
      { text: 'Create a free account on the Anthropic Console.', link: 'https://console.anthropic.com/', linkText: 'Open Anthropic Console' },
      { text: 'Add credit to your account under Billing.', link: 'https://console.anthropic.com/settings/billing', linkText: 'Open Billing' },
      { text: 'Go to API Keys, click "Create Key", and give it any name, like "council".', link: 'https://console.anthropic.com/settings/keys', linkText: 'Open API Keys' },
      { text: 'Copy the key (it starts with sk-ant-) and paste it above. It is only shown once, so copy it before closing that window.' },
    ],
  },
  {
    name: 'fmp_api_key',
    label: 'Financial Modeling Prep (stock data)',
    required: true,
    purpose: 'Real market data for any stock. Without it, the Council only has built-in sample data for NVDA.',
    placeholder: 'Paste key here',
    cost: 'The free plan is enough to get started.',
    steps: [
      { text: 'Sign up for a free account.', link: 'https://site.financialmodelingprep.com/register', linkText: 'Open sign-up page' },
      { text: 'Once signed in, open your Dashboard -- your API key is shown there.', link: 'https://site.financialmodelingprep.com/dashboard', linkText: 'Open Dashboard' },
      { text: 'Copy the key and paste it above.' },
    ],
  },
  {
    name: 'openai_api_key',
    label: 'OpenAI (GPT)',
    required: false,
    purpose: 'Some seats are set to use OpenAI models. Without this key those seats use Claude instead -- everything still works.',
    placeholder: 'Paste key (sk-...)',
    cost: 'Pay as you go. A ChatGPT subscription does not include this; API credit is bought separately.',
    steps: [
      { text: 'Sign in, or create an account, on the OpenAI Platform.', link: 'https://platform.openai.com/', linkText: 'Open OpenAI Platform' },
      { text: 'Add credit under Billing.', link: 'https://platform.openai.com/settings/organization/billing/overview', linkText: 'Open Billing' },
      { text: 'Go to API keys and click "Create new secret key".', link: 'https://platform.openai.com/api-keys', linkText: 'Open API keys' },
      { text: 'Copy the key (it starts with sk-) and paste it above. It is only shown once.' },
    ],
  },
  {
    name: 'google_api_key',
    label: 'Google (Gemini)',
    required: false,
    purpose: "Some seats are set to use Google's Gemini models. Without this key those seats use Claude instead.",
    placeholder: 'Paste key (AIza...)',
    cost: 'Some models can be used free within limits; beyond that it is pay as you go.',
    steps: [
      { text: 'Open Google AI Studio and sign in with any Google account.', link: 'https://aistudio.google.com/', linkText: 'Open AI Studio' },
      { text: 'Go to "Get API key" and click "Create API key".', link: 'https://aistudio.google.com/apikey', linkText: 'Open API keys' },
      { text: 'Copy the key (it starts with AIza) and paste it above.' },
    ],
  },
  {
    name: 'fred_api_key',
    label: 'FRED (economic data)',
    required: false,
    purpose: 'Interest rates, inflation and jobs data for the Keeper of the Outer Rim. Without it, that one seat sits out.',
    placeholder: 'Paste key here',
    cost: 'Free.',
    steps: [
      { text: 'Create a free FRED account (run by the St. Louis Fed) and sign in.', link: 'https://fredaccount.stlouisfed.org/apikeys', linkText: 'Open FRED API keys' },
      { text: 'Click "Request API Key", write one line about what it is for (e.g. "personal stock research"), and submit.' },
      { text: 'Copy the 32-character key and paste it above.' },
    ],
  },
];

let KEY_STATUS = {};

function keyBadgeText(status) {
  if (!status || !status.is_set) return 'Not added';
  const tail = `••••${status.last4}`;
  return status.source === 'env' ? `Set on server ${tail}` : `Added ${tail}`;
}

function renderKeysSummary() {
  const hasAi = KEY_STATUS.anthropic_api_key?.is_set;
  const hasData = KEY_STATUS.fmp_api_key?.is_set;
  const el = document.getElementById('keys-summary');
  if (hasAi && hasData) {
    el.className = 'keys-summary mono ready';
    el.textContent = "You're all set -- the Council uses real AI and real market data.";
  } else if (hasAi) {
    el.className = 'keys-summary mono partial';
    el.textContent = 'Real AI is on, but stock data is still sample data (NVDA only). Add a Financial Modeling Prep key below.';
  } else if (hasData) {
    el.className = 'keys-summary mono partial';
    el.textContent = 'Real stock data is on, but answers are still samples. Add an Anthropic key below.';
  } else {
    el.className = 'keys-summary mono partial';
    el.textContent = 'Right now the Council runs on sample data and sample answers. Add the two keys below to analyze any stock for real.';
  }
}

function buildKeyCard(guide) {
  const card = document.createElement('div');
  card.className = 'panel key-card';
  card.dataset.key = guide.name;

  const head = document.createElement('div');
  head.className = 'key-head';
  const titles = document.createElement('div');
  const label = document.createElement('div');
  label.className = 'key-label';
  label.textContent = guide.label;
  const purpose = document.createElement('div');
  purpose.className = 'key-purpose';
  purpose.textContent = guide.purpose;
  titles.append(label, purpose);
  const badge = document.createElement('span');
  badge.className = 'key-badge';
  head.append(titles, badge);

  const form = document.createElement('form');
  form.className = 'key-form';
  const inputId = `key-input-${guide.name}`;
  const inputLabel = document.createElement('label');
  inputLabel.className = 'visually-hidden';
  inputLabel.htmlFor = inputId;
  inputLabel.textContent = `${guide.label} API key`;
  const input = document.createElement('input');
  input.type = 'password';
  input.id = inputId;
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.placeholder = guide.placeholder;
  const save = document.createElement('button');
  save.type = 'submit';
  save.className = 'btn';
  save.textContent = 'Save';
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'reset-link key-remove';
  remove.textContent = 'Remove';
  form.append(inputLabel, input, save, remove);

  const msg = document.createElement('div');
  msg.className = 'key-msg mono';
  msg.setAttribute('role', 'status');

  const howto = document.createElement('details');
  howto.className = 'key-howto';
  const summary = document.createElement('summary');
  summary.textContent = 'How do I get this key?';
  const steps = document.createElement('ol');
  steps.className = 'key-steps';
  for (const step of guide.steps) {
    const li = document.createElement('li');
    li.append(document.createTextNode(step.text));
    if (step.link) {
      const a = document.createElement('a');
      a.className = 'key-link';
      a.href = step.link;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = `${step.linkText} ↗`;
      li.append(document.createElement('br'), a);
    }
    steps.appendChild(li);
  }
  const cost = document.createElement('p');
  cost.className = 'key-cost';
  const costLabel = document.createElement('b');
  costLabel.textContent = 'Cost: ';
  cost.append(costLabel, document.createTextNode(guide.cost));
  howto.append(summary, steps, cost);

  form.onsubmit = async (e) => {
    e.preventDefault();
    const value = input.value.trim();
    if (!value) {
      showKeyMessage(msg, 'Paste a key into the box first.', true);
      return;
    }
    save.disabled = true;
    try {
      const data = await fetchJSON('/api/settings/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: guide.name, value }),
      });
      input.value = '';
      applyKeyStatus(data.keys);
      showKeyMessage(msg, 'Saved. The Council will use this key from the next run on.');
      AudioBlips.blip();
      loadCostEstimate();
    } catch (err) {
      showKeyMessage(msg, `That key couldn't be saved: ${friendlyError(err)}`, true);
    } finally {
      save.disabled = false;
    }
  };

  remove.onclick = async () => {
    try {
      const data = await fetchJSON(`/api/settings/keys/${guide.name}`, { method: 'DELETE' });
      applyKeyStatus(data.keys);
      const stillSet = KEY_STATUS[guide.name]?.is_set;
      showKeyMessage(msg, stillSet ? "Removed. Using the key from the server's .env file again." : 'Removed.');
      loadCostEstimate();
    } catch (err) {
      showKeyMessage(msg, `Couldn't remove it: ${friendlyError(err)}`, true);
    }
  };

  card.append(head, form, msg, howto);
  return card;
}

// fetchJSON errors read "400: {"detail":"..."}" -- show just the detail.
function friendlyError(err) {
  const match = /^\d+: (.*)$/s.exec(err.message);
  if (!match) return err.message;
  try {
    return JSON.parse(match[1]).detail || match[1];
  } catch (e) {
    return match[1];
  }
}

function showKeyMessage(el, text, isError = false) {
  el.textContent = text;
  el.className = `key-msg mono ${isError ? 'crimson' : 'green'}`;
}

function applyKeyStatus(keys) {
  KEY_STATUS = Object.fromEntries(keys.map(k => [k.name, k]));
  for (const card of document.querySelectorAll('.key-card')) {
    const status = KEY_STATUS[card.dataset.key];
    const badge = card.querySelector('.key-badge');
    badge.textContent = keyBadgeText(status);
    badge.className = `key-badge ${status?.is_set ? 'is-set' : 'is-unset'}`;
    badge.title = status?.source === 'env'
      ? "Set in the server's .env file. Saving a key here replaces it."
      : '';
    card.querySelector('.key-remove').hidden = status?.source !== 'app';
  }
  renderKeysSummary();
}

async function loadKeys() {
  const required = document.getElementById('keys-required');
  const optional = document.getElementById('keys-optional');
  for (const guide of KEY_GUIDES) {
    (guide.required ? required : optional).appendChild(buildKeyCard(guide));
  }
  try {
    const data = await fetchJSON('/api/settings/keys');
    applyKeyStatus(data.keys);
  } catch (err) {
    const el = document.getElementById('keys-summary');
    el.className = 'keys-summary mono crimson';
    el.textContent = String(err.message).startsWith('404')
      ? "Couldn't load your keys: the server is still running an older version. Restart it (sudo systemctl restart jedi-council) and reload this page."
      : `Couldn't load your keys: ${friendlyError(err)}`;
  }
}

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

function setModelStatus(text, isError = false) {
  const el = document.getElementById('model-status');
  el.textContent = text;
  el.className = `mono model-status ${isError ? 'crimson' : 'green'}`;
}

async function saveModelChoice(role, modelId, successText) {
  try {
    await fetchJSON('/api/settings/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role, model_id: modelId }),
    });
    AudioBlips.blip();
    setModelStatus(successText);
    await loadRoles();
    await loadCostEstimate();
  } catch (e) {
    setModelStatus(`Couldn't save that change: ${friendlyError(e)}`, true);
  }
}

function setModel(role, modelId) {
  const name = CATALOG_BY_ID[modelId]?.display_name || modelId;
  return saveModelChoice(role, modelId, `Saved -- ${role} now uses ${name}.`);
}

function resetModel(role) {
  return saveModelChoice(role, null, `Saved -- ${role} is back on its recommended model.`);
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
    costTd.dataset.label = 'Est. per call';
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
      <span class="dim">${est.total_calls} model calls at the ${CURRENT_HORIZON} horizon${est.is_fixture ? ' -- no Anthropic key yet, so runs use sample answers and cost $0' : ''}</span>
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
  initSections();

  for (const btn of document.querySelectorAll('#horizon-toggle .toggle-option')) {
    btn.onclick = () => setHorizon(btn.dataset.horizon);
  }
  setHorizon('1w');

  loadKeys();
  renderCenterpieceGrid();
  renderChairGrid();
  loadRoles();
});
