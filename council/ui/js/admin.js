// Admin (owner and admins only): approve people, see every run, usage
// and analytics, and review and publish seat weights.

let people = [];
let tier = 'paid';
let draft = null; // the release being viewed: { release, changes }

function friendlyError(err) {
  const match = /^\d+: (.*)$/s.exec(err.message);
  if (!match) return err.message;
  try { return JSON.parse(match[1]).detail || match[1]; } catch (e) { return match[1]; }
}

async function post(url, body, method = 'POST') {
  return fetchJSON(url, {
    method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined,
  });
}

function when(iso) {
  return iso ? fmtDate(iso, true) : '<span class="faint">never</span>';
}

// ---- People ----------------------------------------------------------------------------

function roleLabel(p) {
  if (p.is_owner) return '<span class="pill pill-accent">Owner</span>';
  if (p.is_admin) return '<span class="pill pill-accent">Admin</span>';
  return '';
}

function statusPill(p) {
  return {
    active: '<span class="pill pill-free">Active</span>',
    pending: '<span class="pill pill-warn">Waiting</span>',
    disabled: '<span class="pill">Turned off</span>',
  }[p.status] || escapeHtml(p.status);
}

function renderPeople() {
  const pending = people.filter(p => p.status === 'pending');
  const rest = people.filter(p => p.status !== 'pending');
  document.getElementById('pending-sub').textContent = pending.length ? `${pending.length} waiting` : '';
  document.getElementById('pending-list').innerHTML = pending.length ? pending.map(p => `
    <div class="pending-row">
      <div class="pending-who">
        <b>${escapeHtml(p.name)}</b>
        <span class="muted">${escapeHtml(p.email)}</span>
        <span class="faint">Asked ${fmtDate(p.created_at, true)} · ${p.google_linked ? 'with Google' : 'with email'}</span>
      </div>
      <div class="pending-actions">
        <button class="btn btn-small btn-quiet btn-danger" data-act="decline" data-id="${p.id}" type="button">Decline</button>
        <button class="btn btn-small btn-primary" data-act="approve" data-id="${p.id}" type="button">Approve</button>
      </div>
    </div>`).join('') : '<p class="empty">No one is waiting.</p>';

  document.getElementById('people-sub').textContent = `${rest.length} ${rest.length === 1 ? 'account' : 'accounts'}`;
  document.getElementById('people-body').innerHTML = rest.map(p => {
    const actions = p.is_owner ? '' : `
      <div class="row-actions">
        ${p.status === 'active'
          ? `<button class="btn btn-small btn-quiet" data-act="disable" data-id="${p.id}" type="button">Turn off</button>`
          : `<button class="btn btn-small btn-quiet" data-act="enable" data-id="${p.id}" type="button">Turn on</button>`}
        <button class="btn btn-small btn-quiet" data-act="${p.is_admin ? 'unadmin' : 'admin'}" data-id="${p.id}" type="button">${p.is_admin ? 'Remove admin' : 'Make admin'}</button>
        <button class="btn btn-small btn-quiet" data-act="reset" data-id="${p.id}" type="button">Reset link</button>
        <button class="btn btn-small btn-quiet btn-danger" data-act="remove" data-id="${p.id}" type="button">Remove</button>
      </div>`;
    return `
      <tr>
        <td class="wrap"><div class="seat-cell"><b>${escapeHtml(p.name)} ${roleLabel(p)}</b><span class="faint">${escapeHtml(p.email)}</span>
          <span class="faint">Last signed in ${p.last_login_at ? fmtDate(p.last_login_at, true) : 'never'}</span></div></td>
        <td>${statusPill(p)}</td>
        <td class="num">${p.usage.runs} <span class="faint">· ${fmtMoney(p.usage.cost_usd) || '$0'}</span>${p.usage.paid_runs ? `<div class="cell-sub">${p.usage.paid_runs} paid</div>` : ''}</td>
        <td>${p.usage.last_run_at ? fmtDate(p.usage.last_run_at) : '<span class="faint">never</span>'}</td>
        <td>${actions}</td>
      </tr>`;
  }).join('');
}

async function loadPeople() {
  try {
    const data = await fetchJSON('/api/admin/users');
    people = data.users;
    document.getElementById('email-notice').hidden = data.email_enabled;
    renderPeople();
    fillPersonFilter();
  } catch (e) {
    document.getElementById('pending-list').innerHTML = `<p class="empty down">Couldn't load people: ${escapeHtml(friendlyError(e))}</p>`;
  }
}

async function onPersonAction(e) {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  const id = Number(btn.dataset.id);
  const person = people.find(p => p.id === id);
  const act = btn.dataset.act;
  try {
    if (act === 'approve' || act === 'enable') {
      btn.disabled = true;
      await post(`/api/admin/users/${id}/status`, { status: 'active' });
    } else if (act === 'disable') {
      await post(`/api/admin/users/${id}/status`, { status: 'disabled' });
    } else if (act === 'admin' || act === 'unadmin') {
      await post(`/api/admin/users/${id}/admin`, { is_admin: act === 'admin' });
    } else if (act === 'decline' || act === 'remove') {
      const q = act === 'decline'
        ? `Decline ${person.name}'s request? Their sign-up is deleted.`
        : `Remove ${person.name}? Their account, keys and settings are deleted. Their scored runs stay in the pooled record.`;
      if (!confirm(q)) return;
      await post(`/api/admin/users/${id}`, null, 'DELETE');
    } else if (act === 'reset') {
      const r = await post(`/api/admin/users/${id}/reset-link`);
      document.getElementById('reset-name').textContent = person.name;
      document.getElementById('reset-link').value = r.link;
      document.getElementById('reset-box').hidden = false;
      document.getElementById('reset-box').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      return;
    }
    await loadPeople();
    refreshNavBadge();
  } catch (err) {
    alert(friendlyError(err));
    btn.disabled = false;
  }
}

// ---- All runs (the Master Crypt) --------------------------------------------------------------

function fillPersonFilter() {
  const select = document.getElementById('run-person');
  const current = select.value;
  select.innerHTML = '<option value="">Everyone</option>' + people
    .map(p => `<option value="${p.is_owner ? 0 : p.id}">${escapeHtml(p.name)}</option>`).join('');
  select.value = current;
}

function termCell(t) {
  if (!t) return '<td class="faint">–</td>';
  const p = t.p_raw ?? (t.council_vote === 'BULLISH' ? t.council_confidence : t.council_vote === 'BEARISH' ? 1 - t.council_confidence : 0.5);
  const dir = dirOf(p);
  const outcome = t.direction_correct === null || t.direction_correct === undefined ? ''
    : t.direction_correct ? `<span class="outcome up">✓ ${fmtMove(t.realised_move_pct)}</span>` : `<span class="outcome down">✗ ${fmtMove(t.realised_move_pct)}</span>`;
  return `<td><div class="term-cell"><span class="${dir}">${arrowOf(p)} ${dir === 'even' ? '50/50' : pct(p)}</span>${outcome}</div></td>`;
}

async function loadRuns() {
  const account = document.getElementById('run-person').value;
  const body = document.getElementById('runs-body');
  try {
    const data = await fetchJSON(`/api/admin/runs?limit=300${account !== '' ? `&account=${account}` : ''}`);
    document.getElementById('runs-sub').textContent = `${data.runs.length} runs`;
    body.innerHTML = data.runs.length ? data.runs.map(r => `
      <tr class="clickable" data-run="${escapeHtml(r.run_id)}" tabindex="0">
        <td>${r.person ? escapeHtml(r.person.name) : '<span class="faint">Removed account</span>'}</td>
        <td><b>${escapeHtml(r.ticker)}</b></td>
        <td class="muted">${fmtDate(r.created_at, true)}</td>
        ${termCell(r.terms.short)}${termCell(r.terms.medium)}${termCell(r.terms.long)}
        <td><span class="pill">${r.run_shape === 'lite' ? 'Lite' : 'Full'}</span>${r.run_mode === 'free' ? ' <span class="pill pill-free">Free</span>' : r.run_mode === 'sample' ? ' <span class="pill pill-warn">Sample</span>' : ''}</td>
        <td class="num">${fmtMoney(r.total_cost_usd)}</td>
      </tr>`).join('') : '<tr><td colspan="8" class="faint">No runs yet.</td></tr>';
    body.querySelectorAll('tr.clickable').forEach(tr => {
      const open = () => { location.href = `/index.html?run=${encodeURIComponent(tr.dataset.run)}`; };
      tr.onclick = open;
      tr.onkeydown = e => { if (e.key === 'Enter') open(); };
    });
  } catch (e) {
    body.innerHTML = `<tr><td colspan="8" class="down">Couldn't load runs: ${escapeHtml(friendlyError(e))}</td></tr>`;
  }
}

// ---- Analytics ---------------------------------------------------------------------------

function rate(v) {
  return v === null || v === undefined ? '–' : `${Math.round(v * 100)}%`;
}

async function loadVisitors() {
  try {
    const v = await fetchJSON('/api/admin/site-stats?days=30');
    const t = v.totals;
    const pct = (a, b) => (b ? `${Math.round((a / b) * 100)}%` : '–');
    const stat = (big, label, sub = '') => `<div class="stat"><div class="stat-big num">${big}</div><div class="stat-label">${label}</div>${sub ? `<div class="stat-sub">${sub}</div>` : ''}</div>`;
    document.getElementById('visitor-tiles').innerHTML = [
      stat(t.landing.visitors, 'Saw the landing page', `${t.landing.views} page views`),
      stat(t.signup_form.visitors, 'Opened the sign-up form', `${pct(t.signup_form.visitors, t.landing.visitors)} of visitors`),
      stat(v.signups.total, 'Signed up', `${pct(v.signups.total, t.landing.visitors)} of visitors · ${v.signups.approved} approved`),
      stat(t.share.views, 'Shared-result views', 'people opening shared links'),
    ].join('');
    const max = Math.max(1, ...v.daily.map(d => d.landing_visitors));
    document.getElementById('visitor-chart').innerHTML = `
      <div class="day-bars">${v.daily.map(d => `<span class="day-bar" style="height:${Math.max(d.landing_visitors ? 4 : 1, (d.landing_visitors / max) * 100)}%" title="${d.day}: ${d.landing_visitors} visitors, ${d.signups} sign-ups"></span>`).join('')}</div>
      <div class="day-axis"><span>${fmtDate(v.daily[0].day)}</span><span>peak ${max} visitors a day</span><span>today</span></div>`;
    const refMax = Math.max(1, ...v.referrers.map(r => r.visitors));
    document.getElementById('referrers').innerHTML = v.referrers.length
      ? `<h3 class="sub-head">Where visitors came from</h3>` + v.referrers.map(r => `
        <div class="ticker-bar"><b>${escapeHtml(r.domain)}</b><span class="ticker-track"><span style="width:${(r.visitors / refMax) * 100}%"></span></span><span class="num">${r.visitors}</span></div>`).join('')
      : '<p class="faint" style="font-size:13px">No visits from other sites yet. Links shared on X, Reddit and so on show up here.</p>';
  } catch (e) {
    document.getElementById('visitor-tiles').innerHTML = `<p class="down">Couldn't load visitors: ${escapeHtml(friendlyError(e))}</p>`;
  }
}

async function loadAnalytics() {
  try {
    const a = await fetchJSON('/api/admin/analytics');
    const last30 = a.runs_per_day.reduce((n, d) => n + d.runs, 0);
    const stat = (big, label, sub = '') => `<div class="stat"><div class="stat-big num">${big}</div><div class="stat-label">${label}</div>${sub ? `<div class="stat-sub">${sub}</div>` : ''}</div>`;
    document.getElementById('stat-tiles').innerHTML = [
      stat(a.people.active, 'Active accounts', a.people.pending ? `${a.people.pending} waiting` : 'none waiting'),
      stat(a.runs_total, 'Runs', `${last30} in the last 30 days`),
      stat(fmtMoney(a.cost_total_usd) || '$0', 'Spent on AI', 'everyone\'s own keys'),
      stat(a.scored_calls, 'Scored calls', 'periods whose time is up'),
    ].join('');

    // Runs per day: the last 30 days, gaps filled with zero.
    const byDay = Object.fromEntries(a.runs_per_day.map(d => [d.day, d.runs]));
    const days = [];
    for (let i = 29; i >= 0; i--) {
      const d = new Date(Date.now() - i * 86400000).toISOString().slice(0, 10);
      days.push({ day: d, runs: byDay[d] || 0 });
    }
    const max = Math.max(1, ...days.map(d => d.runs));
    document.getElementById('day-chart').innerHTML = `
      <div class="day-bars">${days.map(d => `<span class="day-bar" style="height:${Math.max(d.runs ? 4 : 1, (d.runs / max) * 100)}%" title="${d.day}: ${d.runs} ${d.runs === 1 ? 'run' : 'runs'}"></span>`).join('')}</div>
      <div class="day-axis"><span>${fmtDate(days[0].day)}</span><span>peak ${max} a day</span><span>today</span></div>`;

    const benchRow = (label, b) => {
      const f = b?.council_final;
      return `<tr><td>${label}</td><td class="num">${f?.n ?? 0}</td><td class="num">${rate(f?.hit_rate)}</td><td class="num">${rate(b?.always_bullish?.hit_rate)}</td><td class="num">${f?.brier ?? '–'}</td></tr>`;
    };
    document.getElementById('bench-table').innerHTML = `
      <thead><tr><th>Runs</th><th class="num">Scored calls</th><th class="num">Council right</th><th class="num">Always "up"</th><th class="num">Brier</th></tr></thead>
      <tbody>${benchRow('Paid', a.benchmark.paid)}${benchRow('Free', a.benchmark.free)}</tbody>`;

    const topMax = Math.max(1, ...a.top_tickers.map(t => t.runs));
    document.getElementById('top-tickers').innerHTML = a.top_tickers.length ? a.top_tickers.map(t => `
      <div class="ticker-bar"><b>${escapeHtml(t.ticker)}</b><span class="ticker-track"><span style="width:${(t.runs / topMax) * 100}%"></span></span><span class="num">${t.runs}</span></div>`).join('')
      : '<p class="faint">No runs yet.</p>';
  } catch (e) {
    document.getElementById('stat-tiles').innerHTML = `<p class="down">Couldn't load analytics: ${escapeHtml(friendlyError(e))}</p>`;
  }
}

// ---- Seat weights ---------------------------------------------------------------------------

function renderLive(live) {
  const card = document.getElementById('live-card');
  const r = live[tier];
  card.innerHTML = r ? `
    <div class="row" style="justify-content:space-between"><h2>Live now</h2><span class="pill pill-free">Set #${r.id}</span></div>
    <p class="muted" style="font-size:14px">Published ${fmtDate(r.published_at, true)}, from ${r.n_calls} scored calls by ${r.n_people} ${r.n_people === 1 ? 'person' : 'people'}.${r.note ? ` "${escapeHtml(r.note)}"` : ''}</p>`
    : `<h2>Live now</h2><p class="muted" style="font-size:14px">Nothing published for ${tier} runs yet, so every seat counts 1.0 on every period.</p>`;
}

function renderReleases(list) {
  const body = document.getElementById('releases-body');
  body.innerHTML = list.length ? list.map(r => `
    <tr class="clickable" data-release="${r.id}" tabindex="0">
      <td>#${r.id}</td><td>${r.tier === 'paid' ? 'Paid' : 'Free'}</td>
      <td>${{ published: '<span class="pill pill-free">Live</span>', draft: '<span class="pill pill-warn">Draft</span>', retired: '<span class="pill">Replaced</span>' }[r.status]}</td>
      <td>${fmtDate(r.created_at, true)}</td><td>${r.published_at ? fmtDate(r.published_at, true) : '–'}</td>
      <td class="num">${r.n_calls}</td><td class="num">${r.n_people}</td><td class="wrap muted">${escapeHtml(r.note || '')}</td>
    </tr>`).join('') : '<tr><td colspan="8" class="faint">No sets yet.</td></tr>';
  body.querySelectorAll('tr.clickable').forEach(tr => {
    const open = async () => showDraft(await fetchJSON(`/api/admin/weights/${tr.dataset.release}`));
    tr.onclick = open;
    tr.onkeydown = e => { if (e.key === 'Enter') open(); };
  });
}

async function loadWeights() {
  try {
    const data = await fetchJSON('/api/admin/weights');
    document.getElementById('min-calls').textContent = data.min_calls;
    renderLive(data.live);
    renderReleases(data.releases);
  } catch (e) {
    document.getElementById('live-card').innerHTML = `<p class="down">Couldn't load weights: ${escapeHtml(friendlyError(e))}</p>`;
  }
}

function showDraft(data) {
  draft = data;
  const r = data.release;
  const card = document.getElementById('draft-card');
  card.hidden = false;
  document.getElementById('draft-title').textContent = `Set #${r.id} · ${r.tier === 'paid' ? 'paid' : 'free'} runs`;
  document.getElementById('draft-sub').textContent = `${r.n_calls} scored calls · ${r.n_people} ${r.n_people === 1 ? 'person' : 'people'}${r.note ? ` · "${r.note}"` : ''}`;
  const cell = (term, seatId) => {
    const c = data.changes[term][seatId];
    const s = r.stats?.[term]?.[seatId] || {};
    const delta = c.change === 0 ? '' : `<span class="${c.change > 0 ? 'up' : 'down'}">${c.change > 0 ? '+' : ''}${c.change.toFixed(2)}</span>`;
    return `<td class="num"><div class="w-cell"><span><span class="faint">${c.live.toFixed(2)} →</span> <b>${c.new.toFixed(2)}</b> ${delta}</span>
      <span class="cell-sub">${s.n ? `${rate(s.hit_rate)} of ${s.n}` : 'no calls'}</span></div></td>`;
  };
  document.getElementById('draft-table').innerHTML = `
    <thead><tr><th>Seat</th>${TERMS.map(t => `<th class="num">${TERM_LABEL[t]}</th>`).join('')}</tr></thead>
    <tbody>${SEATS.map(s => `<tr><td><b>${s.name}</b></td>${TERMS.map(t => cell(t, s.id)).join('')}</tr>`).join('')}</tbody>`;
  const btn = document.getElementById('publish-btn');
  btn.hidden = r.status === 'published';
  btn.disabled = false;
  btn.textContent = r.status === 'retired' ? 'Publish this set again' : 'Publish these weights';
  document.getElementById('draft-hint').textContent = r.status === 'published'
    ? 'This set is live.'
    : 'Nothing changes for live runs until you publish. Compare the live weight with the new one for each seat and period.';
  card.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function computeDraft() {
  const btn = document.getElementById('compute-btn');
  btn.disabled = true;
  btn.textContent = 'Computing…';
  try {
    const note = document.getElementById('draft-note').value;
    showDraft(await post('/api/admin/weights/candidate', { tier, note }));
    document.getElementById('draft-note').value = '';
    loadWeights();
  } catch (e) {
    alert(friendlyError(e));
  } finally {
    btn.disabled = false;
    btn.textContent = 'Compute new weights';
  }
}

async function publishDraft() {
  if (!draft) return;
  const r = draft.release;
  if (!confirm(`Publish set #${r.id}? From the next run on, every ${r.tier} run uses these weights.`)) return;
  const btn = document.getElementById('publish-btn');
  btn.disabled = true;
  try {
    await post(`/api/admin/weights/${r.id}/publish`);
    showDraft(await fetchJSON(`/api/admin/weights/${r.id}`));
    loadWeights();
  } catch (e) {
    alert(friendlyError(e));
    btn.disabled = false;
  }
}

// ---- wiring ---------------------------------------------------------------------------------

// ---- Problem reports ---------------------------------------------------------------------

let reportStatus = 'open';

async function loadReports() {
  const list = document.getElementById('report-list');
  try {
    const data = await fetchJSON(`/api/admin/reports?status=${reportStatus}`);
    document.getElementById('reports-sub').textContent = data.open_count
      ? `${data.open_count} open` : 'Nothing open';
    list.innerHTML = data.reports.length ? data.reports.map(r => {
      const who = r.email
        ? `<b>${escapeHtml(r.name)}</b> <a href="mailto:${escapeHtml(r.email)}">${escapeHtml(r.email)}</a>`
        : `<b>${escapeHtml(r.name || 'Owner')}</b>`;
      const about = [
        r.ticker ? `<span class="pill">${escapeHtml(r.ticker)}</span>` : '',
        r.run_id ? `<a class="pill pill-accent" href="/index.html?run=${encodeURIComponent(r.run_id)}">Open the run</a>` : '',
        r.page ? `<span class="faint">from ${escapeHtml(r.page)}</span>` : '',
      ].filter(Boolean).join(' ');
      const resolved = r.status === 'resolved';
      return `
        <article class="card report${resolved ? ' is-resolved' : ''}">
          <div class="report-head">
            <span class="pill ${r.kind === 'broken' ? 'pill-warn' : r.kind === 'idea' ? 'pill-free' : ''}">${escapeHtml(r.kind_label)}</span>
            <span class="faint">${fmtDate(r.created_at, true)}</span>
            ${resolved ? `<span class="pill">Resolved ${fmtDate(r.resolved_at)}</span>` : ''}
          </div>
          <p class="report-message">${escapeHtml(r.message)}</p>
          <div class="report-meta">${who}${about ? ` · ${about}` : ''}</div>
          ${r.user_agent ? `<div class="report-device faint">${escapeHtml(r.user_agent)}</div>` : ''}
          <div class="report-actions">
            <button class="btn btn-small ${resolved ? '' : 'btn-primary'}" type="button" data-report="${r.id}" data-to="${resolved ? 'open' : 'resolved'}">
              ${resolved ? 'Reopen' : 'Mark resolved'}</button>
          </div>
        </article>`;
    }).join('') : `<p class="empty">${reportStatus === 'open' ? 'No open reports.' : 'No reports here yet.'}</p>`;
  } catch (err) {
    list.innerHTML = `<p class="empty">Couldn't load reports: ${escapeHtml(friendlyError(err))}</p>`;
  }
}

async function onReportAction(e) {
  const btn = e.target.closest('button[data-report]');
  if (!btn) return;
  btn.disabled = true;
  try {
    await post(`/api/admin/reports/${btn.dataset.report}`, { status: btn.dataset.to });
    await loadReports();
    refreshNavBadge('/admin.html');
  } catch (err) {
    btn.disabled = false;
    alert(friendlyError(err));
  }
}

// ---- Emails (#149) ---------------------------------------------------------------------

async function loadEmails() {
  const list = document.getElementById('email-list');
  try {
    const data = await fetchJSON('/api/admin/emails');
    document.getElementById('emails-off').hidden = data.email_enabled;
    list.innerHTML = data.emails.map(e => `
      <div class="email-row">
        <div><b>${escapeHtml(e.name)}</b><div class="muted">To: ${escapeHtml(e.to)}</div></div>
        <div class="email-actions">
          <a class="btn btn-small" href="/api/admin/emails/${e.kind}/preview" target="_blank" rel="noopener">Preview</a>
          <button class="btn btn-small" type="button" data-test="${e.kind}" ${data.email_enabled ? '' : 'disabled'}>Send me a test</button>
          <span class="email-status muted" role="status"></span>
        </div>
      </div>`).join('');
  } catch (err) {
    list.innerHTML = `<p class="empty">Couldn't load the emails: ${escapeHtml(friendlyError(err))}</p>`;
  }
}

async function onEmailTest(e) {
  const btn = e.target.closest('button[data-test]');
  if (!btn) return;
  const status = btn.parentElement.querySelector('.email-status');
  btn.disabled = true;
  status.textContent = 'Sending…';
  try {
    const r = await post(`/api/admin/emails/${btn.dataset.test}/test`);
    status.textContent = `Sent to ${r.to}`;
  } catch (err) {
    status.textContent = friendlyError(err);
  } finally {
    btn.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  renderNav('/admin.html');
  initSections();
  document.getElementById('pending-list').addEventListener('click', onPersonAction);
  document.getElementById('people-body').addEventListener('click', onPersonAction);
  document.getElementById('reset-copy').onclick = async () => {
    const input = document.getElementById('reset-link');
    try { await navigator.clipboard.writeText(input.value); document.getElementById('reset-copy').textContent = 'Copied'; }
    catch (e) { input.select(); }
  };
  document.getElementById('run-person').onchange = loadRuns;
  document.querySelectorAll('#tier-seg button').forEach(b => {
    b.onclick = () => {
      tier = b.dataset.tier;
      document.querySelectorAll('#tier-seg button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
      document.getElementById('draft-card').hidden = true;
      loadWeights();
    };
  });
  document.getElementById('compute-btn').onclick = computeDraft;
  document.getElementById('publish-btn').onclick = publishDraft;
  document.querySelectorAll('#report-seg button').forEach(b => {
    b.onclick = () => {
      reportStatus = b.dataset.status;
      document.querySelectorAll('#report-seg button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
      loadReports();
    };
  });
  document.getElementById('report-list').addEventListener('click', onReportAction);
  document.getElementById('email-list').addEventListener('click', onEmailTest);
  loadEmails();
  loadReports();
  loadPeople().then(loadRuns);
  loadVisitors();
  loadAnalytics();
  loadWeights();
});
