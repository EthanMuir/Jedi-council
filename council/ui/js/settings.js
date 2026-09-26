// Settings (clean look): API keys, Free Mode, cost, each seat's model, and
// Appearance. One section shows at a time (initSections in app.js).

const ROLE_NAMES = {
  bull_advocate: 'Bull case',
  bear_advocate: 'Bear case',
  prosecutor: 'Challenger',
  grand_master: 'Summary',
};
function roleName(role) {
  return ROLE_NAMES[role] || seatName(role);
}

// ---- API keys ---------------------------------------------------------------
// KEY_GUIDES (the setup steps for each key) is in keys.js, shared with the
// setup wizard on the Run page.

let KEY_STATUS = {};
let FREE_MODE = { enabled: false, gemini_key: false, groq_key: false };
let CATALOG_BY_ID = {};
let GOOGLE_BILLING = false;

// fetchJSON errors read '400: {"detail":"..."}' -- show just the detail.
function friendlyError(err) {
  const match = /^\d+: (.*)$/s.exec(err.message);
  if (!match) return err.message;
  try { return JSON.parse(match[1]).detail || match[1]; } catch (e) { return match[1]; }
}

function renderKeysSummary() {
  const has = name => KEY_STATUS[name]?.is_set;
  const hasPaid = has('anthropic_api_key') || has('openai_api_key');
  const hasFree = has('google_api_key') || has('groq_api_key');
  const el = document.getElementById('keys-summary');
  if (hasPaid) {
    el.className = 'keys-summary ok';
    el.textContent = 'You\'re all set. The Council reads any stock with real AI and real market data.';
  } else if (hasFree) {
    el.className = 'keys-summary ok';
    el.textContent = 'You\'re all set for free runs. Free Mode is on.';
  } else {
    el.className = 'keys-summary todo';
    el.textContent = 'Right now the Council only replays sample answers about NVDA. Add either key below to read any stock for real. The Gemini one is free.';
  }
}

function buildKeyCard(guide) {
  const card = document.createElement('div');
  card.className = 'card key-card';
  card.dataset.key = guide.name;
  card.innerHTML = `
    <div class="key-head">
      <div class="stack" style="gap:3px">
        <div class="row" style="gap:8px"><h3>${guide.label}</h3>${guide.tag ? `<span class="pill ${guide.tag.startsWith('Free') ? 'pill-free' : 'pill-accent'}">${guide.tag}</span>` : ''}</div>
        <p class="muted">${guide.purpose}</p>
      </div>
      <span class="pill key-badge"></span>
    </div>
    <form class="key-form">
      <label class="visually-hidden" for="key-input-${guide.name}">${guide.label} API key</label>
      <input class="input" type="password" id="key-input-${guide.name}" autocomplete="off" spellcheck="false" placeholder="${guide.placeholder}" />
      <button class="btn btn-primary" type="submit">Save</button>
      <button class="btn btn-quiet key-remove" type="button" hidden>Remove</button>
    </form>
    <p class="key-msg" role="status"></p>
    ${guide.name === 'google_api_key' ? `
    <label class="key-billing" for="google-billing" hidden>
      <input type="checkbox" id="google-billing" />
      <span><b>My Google project has billing turned on</b><br /><span class="muted">Lets seats use paid Gemini models such as Gemini 3 Pro. Leave it off for a free key: those seats then use another model you have a key for.</span></span>
    </label>` : ''}
    <details class="key-howto">
      <summary>How do I get this key?</summary>
      <ol>${guide.steps.map(s => `<li>${s.text}${s.link ? `<br /><a href="${s.link}" target="_blank" rel="noopener noreferrer">${s.linkText} ↗</a>` : ''}</li>`).join('')}</ol>
      <p class="muted"><b>Cost:</b> ${guide.cost}</p>
    </details>`;

  const input = card.querySelector('input');
  const save = card.querySelector('button[type=submit]');
  const msg = card.querySelector('.key-msg');
  const say = (text, bad = false) => { msg.textContent = text; msg.className = `key-msg ${bad ? 'down' : 'up'}`; };

  card.querySelector('form').onsubmit = async e => {
    e.preventDefault();
    const value = input.value.trim();
    if (!value) { say('Paste a key into the box first.', true); return; }
    save.disabled = true;
    try {
      msg.textContent = 'Checking the key…'; msg.className = 'key-msg';
      const data = await fetchJSON('/api/settings/keys', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: guide.name, value, check: true }),
      });
      input.value = '';
      applyKeyStatus(data.keys);
      const check = data.check || { status: 'ok' };
      if (check.status === 'ok') say('Key works and is saved. The Council uses it from the next run on.');
      else { say(check.message); msg.className = 'key-msg warn'; }
      loadFreeMode();
      loadRoles();
      loadCostEstimate();
    } catch (err) {
      say(`That key couldn't be saved: ${friendlyError(err)}`, true);
    } finally {
      save.disabled = false;
    }
  };

  const billing = card.querySelector('#google-billing');
  if (billing) {
    billing.onchange = async () => {
      try {
        await fetchJSON('/api/settings/google-billing', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: billing.checked }),
        });
        say(billing.checked ? 'Saved. Seats set to paid Gemini models now use them.' : 'Saved. Paid Gemini models are off; those seats use another model.');
        loadRoles();
        loadCostEstimate();
      } catch (err) {
        billing.checked = !billing.checked;
        say(`Couldn't save that: ${friendlyError(err)}`, true);
      }
    };
  }

  card.querySelector('.key-remove').onclick = async () => {
    try {
      const data = await fetchJSON(`/api/settings/keys/${guide.name}`, { method: 'DELETE' });
      applyKeyStatus(data.keys);
      say(KEY_STATUS[guide.name]?.is_set ? 'Removed. Using the key from the server\'s .env file again.' : 'Removed.');
      loadFreeMode();
      loadRoles();
      loadCostEstimate();
    } catch (err) {
      say(`Couldn't remove it: ${friendlyError(err)}`, true);
    }
  };
  return card;
}

function applyKeyStatus(keys) {
  KEY_STATUS = Object.fromEntries(keys.map(k => [k.name, k]));
  for (const card of document.querySelectorAll('.key-card')) {
    const status = KEY_STATUS[card.dataset.key];
    const badge = card.querySelector('.key-badge');
    badge.textContent = !status?.is_set ? 'Not added'
      : status.source === 'env' ? `On server ••••${status.last4}` : `Added ••••${status.last4}`;
    badge.className = `pill key-badge ${status?.is_set ? 'pill-free' : ''}`;
    badge.title = status?.source === 'env' ? 'Set in the server\'s .env file. Saving a key here replaces it.' : '';
    card.querySelector('.key-remove').hidden = status?.source !== 'app';
    card.classList.toggle('is-set', Boolean(status?.is_set));
  }
  syncGoogleBilling();
  renderKeysSummary();
}

function syncGoogleBilling() {
  const box = document.getElementById('google-billing');
  if (!box) return;
  box.checked = GOOGLE_BILLING;
  box.closest('.key-billing').hidden = !KEY_STATUS.google_api_key?.is_set;
}

async function loadKeys() {
  const required = document.getElementById('keys-required');
  const optional = document.getElementById('keys-optional');
  for (const guide of KEY_GUIDES) (guide.group === 'ai' ? required : optional).appendChild(buildKeyCard(guide));
  try {
    applyKeyStatus((await fetchJSON('/api/settings/keys')).keys);
  } catch (err) {
    const el = document.getElementById('keys-summary');
    el.className = 'keys-summary bad';
    el.textContent = `Couldn't load your keys: ${friendlyError(err)}`;
  }
}

// ---- Free Mode ----------------------------------------------------------------

function renderFreeMode() {
  const btn = document.getElementById('free-mode-toggle');
  const status = document.getElementById('free-mode-status');
  const pill = document.getElementById('free-mode-pill');
  const hasFreeKey = FREE_MODE.gemini_key || FREE_MODE.groq_key;
  pill.textContent = FREE_MODE.enabled ? 'On' : 'Off';
  pill.className = `pill ${FREE_MODE.enabled ? 'pill-free' : ''}`;
  if (FREE_MODE.enabled) {
    btn.textContent = 'Turn off Free Mode';
    btn.disabled = !!FREE_MODE.locked;
    const using = FREE_MODE.gemini_key ? (FREE_MODE.groq_key ? 'Gemini, with Groq as backup' : 'Gemini') : 'Groq';
    status.textContent = FREE_MODE.locked
      ? `Every seat uses free models (${using}). It stays on while your only AI keys are free ones; add an Anthropic key to use paid models.`
      : `Every seat uses free models (${using}). Turning it off puts back the models you had before.`;
  } else {
    btn.textContent = 'Turn on Free Mode';
    btn.disabled = !hasFreeKey;
    status.innerHTML = hasFreeKey ? 'Seats use the models listed below.' : 'Needs a free Gemini or Groq key first: <a href="#keys">add one under API keys</a>.';
  }
  renderKeysSummary();
}

async function loadFreeMode() {
  try {
    FREE_MODE = await fetchJSON('/api/settings/free-mode');
    renderFreeMode();
  } catch (e) { /* older server: leave it off */ }
}

async function toggleFreeMode() {
  const btn = document.getElementById('free-mode-toggle');
  btn.disabled = true;
  try {
    FREE_MODE = await fetchJSON('/api/settings/free-mode', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: !FREE_MODE.enabled }),
    });
    renderFreeMode();
    await loadRoles();
    await loadCostEstimate();
  } catch (e) {
    document.getElementById('free-mode-status').textContent = `Couldn't change Free Mode: ${friendlyError(e)}`;
    btn.disabled = false;
  }
}

// ---- cost and models ----------------------------------------------------------

function fmtUsd(v) {
  if (v === null || v === undefined) return '–';
  if (v === 0) return '$0.00';
  // A fraction of a cent would round to $0.00 and look free.
  return `$${v.toFixed(v < 0.01 ? 4 : 2)}`;
}

const RUN_MODE_NOTE = {
  sample: 'No AI key yet, so runs replay sample answers and cost nothing.',
  free: 'Everything is on free tiers, so runs cost nothing.',
  paid: '',
};

async function loadCostEstimate() {
  const summary = document.getElementById('cost-summary');
  try {
    const [est, lite] = await Promise.all([
      fetchJSON('/api/settings/cost-estimate'),
      fetchJSON('/api/settings/cost-estimate?lite=true'),
    ]);
    summary.innerHTML = `
      <div class="cost-row"><span class="cost-big num">${fmtUsd(est.total_cost_usd)}</span><span class="muted">Full run · ${est.total_calls} AI calls</span></div>
      <div class="cost-row"><span class="cost-big num">${fmtUsd(lite.total_cost_usd)}</span><span class="muted">Lite run · ${lite.total_calls} AI calls</span></div>
      ${RUN_MODE_NOTE[est.run_mode] ? `<p class="faint">${RUN_MODE_NOTE[est.run_mode]}</p>` : ''}`;
    document.getElementById('cost-lineitems').innerHTML = est.line_items.map(li => `
      <tr><td>${escapeHtml(plainText(li.label))} <span class="faint">${escapeHtml(li.model)}</span></td><td class="num">${fmtUsd(li.est_cost_usd)}</td></tr>`).join('');
  } catch (e) {
    summary.innerHTML = `<span class="down">Couldn't estimate: ${escapeHtml(e.message)}</span>`;
  }
}

function setModelStatus(text, bad = false) {
  const el = document.getElementById('model-status');
  el.textContent = text;
  el.className = `pane-note ${bad ? 'down' : 'up'}`;
}

async function saveModelChoice(role, modelId, text) {
  try {
    await fetchJSON('/api/settings/models', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role, model_id: modelId }),
    });
    setModelStatus(text);
    await loadRoles();
    await loadCostEstimate();
  } catch (e) {
    setModelStatus(`Couldn't save that change: ${friendlyError(e)}`, true);
  }
}

const PROVIDER_NAMES = { anthropic: 'Anthropic', openai: 'OpenAI', google: 'Google', groq: 'Groq' };

// Jump to a key's card under API keys (from a greyed-out model's "Add key").
function goToKey(keyName) {
  location.hash = 'keys';
  requestAnimationFrame(() => {
    const card = document.querySelector(`.key-card[data-key="${keyName}"]`);
    if (!card) return;
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const input = card.querySelector(keyName === 'google_api_key' && card.classList.contains('is-set') ? '.key-billing input' : 'input');
    if (input) input.focus({ preventScroll: true });
  });
}

function modelLine(m) {
  return `${escapeHtml(m.display_name)} <span class="faint">· ${PROVIDER_NAMES[m.provider] || escapeHtml(m.provider)}</span>`;
}

function closeModelMenus(except) {
  for (const pick of document.querySelectorAll('.mpick.open')) {
    if (pick === except) continue;
    pick.classList.remove('open');
    pick.querySelector('.mpick-btn').setAttribute('aria-expanded', 'false');
    pick.querySelector('.mpick-menu').hidden = true;
  }
}

// One seat's model picker: shows the model a run will really use, and opens
// a list where models this person's keys can't use are greyed out with a
// way to add the missing key.
function modelPicker(role) {
  const runs = CATALOG_BY_ID[role.current_model];
  const chosen = CATALOG_BY_ID[role.chosen_model];
  const name = roleName(role.role);
  const pick = document.createElement('div');
  pick.className = 'mpick';
  const options = Object.values(CATALOG_BY_ID).map(m => {
    const star = m.id === role.recommended_model ? '<span class="mpick-star" title="Recommended">★</span>' : '<span class="mpick-star"></span>';
    const price = `<span class="mpick-price num">${fmtUsd(m.typical_call_cost_usd)}</span>`;
    if (!m.available) {
      return `<li class="mpick-opt is-off" role="option" aria-disabled="true" aria-selected="false">
        ${star}<span class="mpick-name">${modelLine(m)}<small>${escapeHtml(m.needs ? m.needs.label : 'Not available')}</small></span>
        ${m.needs ? `<button class="btn btn-small" type="button" data-add-key="${m.needs.key}">${m.needs.label.includes('billing') ? 'Set up' : 'Add key'}</button>` : ''}
        ${price}</li>`;
    }
    const on = m.id === role.current_model;
    return `<li class="mpick-opt${on ? ' is-on' : ''}" role="option" aria-selected="${on}" data-model="${escapeHtml(m.id)}" tabindex="-1">
      ${star}<span class="mpick-name">${modelLine(m)}</span>${price}</li>`;
  }).join('');
  const note = role.chosen_needs && chosen
    ? `<p class="mpick-note">${role.is_override ? 'Set to' : 'Recommended:'} ${escapeHtml(chosen.display_name)}. ${escapeHtml(role.chosen_needs.label)}, so it uses ${escapeHtml(runs ? runs.display_name : role.current_model)} for now.
        <button class="link-btn" type="button" data-add-key="${role.chosen_needs.key}">${role.chosen_needs.label.includes('billing') ? 'Set up billing' : 'Add the key'}</button></p>`
    : '';
  pick.innerHTML = `
    <button class="mpick-btn" type="button" aria-haspopup="listbox" aria-expanded="false" aria-label="Model for ${escapeHtml(name)}: ${escapeHtml(runs ? runs.display_name : role.current_model)}">
      <span>${runs && runs.id === role.recommended_model ? '<span class="mpick-star">★</span>' : ''}${runs ? modelLine(runs) : escapeHtml(role.current_model)}</span>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>
    </button>
    ${note}
    <ul class="mpick-menu" role="listbox" aria-label="Models for ${escapeHtml(name)}" hidden>${options}</ul>`;

  const btn = pick.querySelector('.mpick-btn');
  const menu = pick.querySelector('.mpick-menu');
  const choices = () => [...menu.querySelectorAll('.mpick-opt[data-model]')];
  const setOpen = open => {
    closeModelMenus(pick);
    pick.classList.toggle('open', open);
    btn.setAttribute('aria-expanded', String(open));
    menu.hidden = !open;
    if (open) (menu.querySelector('.is-on') || choices()[0])?.focus();
  };
  btn.onclick = () => setOpen(!pick.classList.contains('open'));
  pick.addEventListener('click', e => {
    const add = e.target.closest('[data-add-key]');
    if (add) { setOpen(false); goToKey(add.dataset.addKey); return; }
    const opt = e.target.closest('.mpick-opt[data-model]');
    if (!opt) return;
    setOpen(false);
    btn.focus();
    if (opt.dataset.model === role.current_model && !role.chosen_needs) return;
    const m = CATALOG_BY_ID[opt.dataset.model];
    saveModelChoice(role.role, opt.dataset.model, `Saved. ${name} now uses ${m ? m.display_name : opt.dataset.model}.`);
  });
  menu.addEventListener('keydown', e => {
    const list = choices();
    const i = list.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      list[(i + (e.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length]?.focus();
    } else if (e.key === 'Enter' || e.key === ' ') {
      if (document.activeElement.matches('.mpick-opt[data-model]')) { e.preventDefault(); document.activeElement.click(); }
    } else if (e.key === 'Escape') {
      setOpen(false);
      btn.focus();
    }
  });
  return pick;
}

function renderRoles(data) {
  CATALOG_BY_ID = Object.fromEntries(data.catalog.map(m => [m.id, m]));
  GOOGLE_BILLING = data.google_billing;
  syncGoogleBilling();
  const body = document.getElementById('models-body');
  body.innerHTML = '';
  for (const role of data.roles) {
    const model = CATALOG_BY_ID[role.current_model];
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><b>${escapeHtml(roleName(role.role))}</b>${role.is_override ? ' <span class="pill pill-accent">Changed</span>' : ''}</td>
      <td class="mpick-cell"></td>
      <td class="num">${model ? fmtUsd(model.typical_call_cost_usd) : '–'}</td>
      <td>${role.is_override ? '<button class="btn btn-small btn-quiet" type="button">Use recommended</button>' : ''}</td>`;
    tr.querySelector('.mpick-cell').appendChild(modelPicker(role));
    const reset = tr.querySelector('td:last-child button');
    if (reset) reset.onclick = () => saveModelChoice(role.role, null, `Saved. ${roleName(role.role)} is back on its recommended model.`);
    body.appendChild(tr);
  }
}

document.addEventListener('click', e => { if (!e.target.closest('.mpick')) closeModelMenus(); });

async function loadRoles() {
  try {
    renderRoles(await fetchJSON('/api/settings/models'));
  } catch (e) {
    document.getElementById('models-body').innerHTML = `<tr><td colspan="4" class="down">Couldn't load models: ${escapeHtml(e.message)}</td></tr>`;
  }
}

// ---- appearance -------------------------------------------------------------------

function syncAppearance() {
  document.querySelectorAll('#detail-seg button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.detail === detailMode())));
  document.querySelectorAll('#theme-seg button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.themeChoice === themePref())));
}

// ---- account ---------------------------------------------------------------------

function renderAccount(me) {
  const info = document.getElementById('account-info');
  const pwCard = document.getElementById('password-card');
  if (!me.user) {
    info.textContent = 'Sign-in is turned off on this server, so there are no accounts: everything here is yours.';
    pwCard.hidden = true;
    return;
  }
  const u = me.user;
  const how = [u.has_password && 'email and password', u.google_linked && 'Google'].filter(Boolean).join(' or ');
  const role = u.is_owner ? 'Owner' : u.is_admin ? 'Admin' : 'Member';
  info.innerHTML = `
    <b style="color:var(--ink)">${escapeHtml(u.name)}</b> · ${escapeHtml(u.email)} <span class="pill">${role}</span><br />
    You sign in with ${escapeHtml(how || 'email')}. Member since ${fmtDate(u.created_at)}.<br />
    <a href="/logout">Sign out</a> · <a href="/guide.html#report">Report a problem</a> · <a href="/terms">Terms</a> · <a href="/privacy">Privacy</a>`;
  pwCard.hidden = false;
  document.getElementById('current-field').hidden = !u.has_password;
  document.getElementById('password-title').textContent = u.has_password ? 'Change password' : 'Add a password';
}

async function loadNotifications() {
  const card = document.getElementById('emails-card');
  try {
    const n = await fetchJSON('/api/settings/notifications');
    if (!n.email_enabled) { card.hidden = true; return; }
    card.hidden = false;
    const box = document.getElementById('scored-toggle');
    box.checked = n.scored_calls;
    box.onchange = async () => {
      const msg = document.getElementById('emails-msg');
      try {
        await fetchJSON('/api/settings/notifications', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scored_calls: box.checked }),
        });
        msg.textContent = box.checked ? 'On.' : 'Off. You won\'t get these emails.';
        msg.className = 'key-msg up';
      } catch (err) {
        box.checked = !box.checked;
        msg.textContent = friendlyError(err);
        msg.className = 'key-msg down';
      }
    };
  } catch (e) { card.hidden = true; }
}

async function savePassword(e) {
  e.preventDefault();
  const msg = document.getElementById('password-msg');
  try {
    await fetchJSON('/api/auth/password', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        current_password: document.getElementById('current-password').value,
        new_password: document.getElementById('new-password').value,
      }),
    });
    msg.textContent = 'Saved.';
    msg.className = 'key-msg up';
    document.getElementById('current-password').value = '';
    document.getElementById('new-password').value = '';
  } catch (err) {
    msg.textContent = friendlyError(err);
    msg.className = 'key-msg down';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.addEventListener('me', e => renderAccount(e.detail));
  document.getElementById('password-form').addEventListener('submit', savePassword);
  renderNav('/settings.html');
  initSections();
  document.getElementById('free-mode-toggle').onclick = toggleFreeMode;
  document.querySelectorAll('#detail-seg button').forEach(b => { b.onclick = () => { setDetailMode(b.dataset.detail); syncAppearance(); }; });
  document.querySelectorAll('#theme-seg button').forEach(b => { b.onclick = () => { setThemePref(b.dataset.themeChoice); syncAppearance(); }; });
  document.getElementById('look-classic').onclick = switchToClassicLook;
  syncAppearance();
  loadKeys();
  loadFreeMode();
  loadCostEstimate();
  loadRoles();
  loadNotifications();
});
