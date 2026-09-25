"""The pages you see before you're signed in: sign in, sign up, owner
setup, waiting for approval, forgot / reset password, and the privacy
page. Self-contained on purpose (inline CSS and JS, no external asset):
they must work even though /css and /js sit behind the sign-in gate."""
from __future__ import annotations

from html import escape

_STYLE = """
  :root {
    color-scheme: light;
    --bg: #f3f5f8; --surface: #ffffff; --ink: #141820; --muted: #5a6475; --faint: #8a93a3;
    --line: #cfd5de; --accent: #2c56c9; --accent-ink: #ffffff; --accent-soft: #e6ecfb;
    --down: #c23b35; --up: #16835a; --up-soft: #e3f3ec;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      color-scheme: dark;
      --bg: #0d1015; --surface: #151a21; --ink: #e5e8ed; --muted: #9aa3b2; --faint: #6d7788;
      --line: #323b48; --accent: #7597f2; --accent-ink: #0d1015; --accent-soft: #1c2640;
      --down: #ec6b64; --up: #45c08a; --up-soft: #12291f;
    }
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; min-height: 100vh; background: var(--bg); color: var(--ink);
    font-family: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  body { display: flex; align-items: center; justify-content: center; padding: 24px 16px; }
  .panel {
    background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
    box-shadow: 0 10px 40px rgba(20, 24, 32, 0.08); padding: 30px 26px; width: 100%; max-width: 380px;
  }
  .panel.wide { max-width: 640px; }
  .brand { font-size: 15px; font-weight: 700; letter-spacing: -0.01em; margin: 0 0 18px; color: var(--ink); text-decoration: none; display: inline-block; }
  .brand span { color: var(--accent); }
  h1 { font-size: 21px; font-weight: 700; margin: 0 0 6px; letter-spacing: -0.01em; }
  h2 { font-size: 16px; margin: 20px 0 6px; }
  p { margin: 0 0 16px; color: var(--muted); font-size: 14px; line-height: 1.55; }
  .prose p, .prose li { color: var(--ink); font-size: 15px; line-height: 1.6; }
  .prose ul { padding-left: 20px; margin: 0 0 12px; }
  label { display: block; font-size: 13px; font-weight: 600; color: var(--muted); margin: 0 0 5px; }
  input {
    width: 100%; font: inherit; font-size: 15px; background: var(--surface); color: var(--ink);
    border: 1px solid var(--line); border-radius: 10px; padding: 11px 12px; margin-bottom: 12px;
  }
  input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  button, .btn {
    display: block; width: 100%; font: inherit; font-size: 15px; font-weight: 600; text-align: center;
    background: var(--accent); color: var(--accent-ink); border: 0; border-radius: 10px;
    padding: 11px; cursor: pointer; text-decoration: none;
  }
  button:hover, .btn:hover { filter: brightness(1.07); }
  button:disabled { opacity: 0.6; cursor: default; }
  .btn-google {
    background: var(--surface); color: var(--ink); border: 1px solid var(--line);
    display: flex; align-items: center; justify-content: center; gap: 10px;
  }
  .or { display: flex; align-items: center; gap: 10px; color: var(--faint); font-size: 12px; margin: 16px 0; }
  .or::before, .or::after { content: ""; flex: 1; height: 1px; background: var(--line); }
  .error { color: var(--down); font-size: 13px; min-height: 18px; margin: -4px 0 8px; }
  .ok { color: var(--up); background: var(--up-soft); border-radius: 10px; padding: 10px 12px; font-size: 14px; margin-bottom: 14px; }
  .links { display: flex; justify-content: space-between; gap: 12px; margin-top: 16px; font-size: 14px; flex-wrap: wrap; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .hint { font-size: 12px; color: var(--faint); margin: -6px 0 12px; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
"""

_SCRIPT = """
  function safeNext(raw) {
    if (!raw || raw.indexOf('/') !== 0 || raw.indexOf('//') === 0) return '/';
    return raw;
  }
  async function post(url, body) {
    const resp = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    let data = {};
    try { data = await resp.json(); } catch (e) {}
    if (!resp.ok) throw new Error(data.detail || 'Something went wrong. Try again.');
    return data;
  }
  function wire(formId, url, fields, onDone) {
    const form = document.getElementById(formId);
    if (!form) return;
    const error = form.querySelector('.error');
    const button = form.querySelector('button');
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      error.textContent = '';
      button.disabled = true;
      const body = {};
      for (const f of fields) body[f] = document.getElementById(f).value;
      try {
        onDone(await post(url, body));
      } catch (err) {
        error.textContent = err.message;
        button.disabled = false;
      }
    });
  }
"""

_GOOGLE_ICON = (
    '<svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">'
    '<path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.9 6.1C12.5 13.6 17.8 9.5 24 9.5z"/>'
    '<path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.7 6c4.5-4.2 6.9-10.3 6.9-17.7z"/>'
    '<path fill="#FBBC05" d="M10.6 28.6c-.5-1.4-.8-3-.8-4.6s.3-3.2.8-4.6l-7.9-6.1C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.9-6.1z"/>'
    '<path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.7-6c-2.2 1.5-5 2.3-8.2 2.3-6.2 0-11.5-4.1-13.4-9.9l-7.9 6.1C6.6 42.6 14.6 48 24 48z"/></svg>'
)


def _page(title: str, body: str, script: str = "", wide: bool = False) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{escape(title)} · Ticker Council</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600;700&display=swap" />
<style>{_STYLE}</style>
</head>
<body>
<main class="panel{' wide' if wide else ''}">
  <a class="brand" href="/welcome">Ticker <span>Council</span></a>
  {body}
</main>
<script>{_SCRIPT}{script}</script>
</body>
</html>"""


def _google_button(google_enabled: bool, label: str) -> str:
    if not google_enabled:
        return ""
    return (
        f'<a class="btn btn-google" href="/auth/google" id="google-btn">{_GOOGLE_ICON}{escape(label)}</a>'
        '<div class="or">or</div>'
    )


def login_page(google_enabled: bool, notice: str = "") -> str:
    body = f"""
  <h1>Sign in</h1>
  <p>Welcome back.</p>
  {f'<div class="ok" role="status">{escape(notice)}</div>' if notice else ''}
  {_google_button(google_enabled, "Continue with Google")}
  <form id="login-form" novalidate>
    <label for="email">Email</label>
    <input type="email" id="email" autocomplete="email" autofocus required />
    <label for="password">Password</label>
    <input type="password" id="password" autocomplete="current-password" required />
    <div class="error" id="error" role="alert"></div>
    <button type="submit">Sign in</button>
  </form>
  <div class="links"><a href="/forgot">Forgot password?</a><a href="/signup">Create an account</a></div>"""
    script = """
  const next = new URLSearchParams(location.search).get('next');
  const g = document.getElementById('google-btn');
  if (g && next) g.href = '/auth/google?next=' + encodeURIComponent(safeNext(next));
  wire('login-form', '/api/auth/login', ['email', 'password'], () => { location.href = safeNext(next); });
"""
    return _page("Sign in", body, script)


def signup_page(google_enabled: bool) -> str:
    body = f"""
  <h1>Create an account</h1>
  <p>New accounts are approved by hand. You'll get an email when yours is ready.</p>
  {_google_button(google_enabled, "Sign up with Google")}
  <form id="signup-form" novalidate>
    <label for="name">Name</label>
    <input id="name" autocomplete="name" autofocus required />
    <label for="email">Email</label>
    <input type="email" id="email" autocomplete="email" required />
    <label for="password">Password</label>
    <input type="password" id="password" autocomplete="new-password" required />
    <div class="hint">At least 10 characters.</div>
    <div class="error" id="error" role="alert"></div>
    <button type="submit">Ask for an account</button>
  </form>
  <p style="margin:14px 0 0;font-size:13px">By signing up you agree to how your data is handled (<a href="/privacy">privacy</a>), and that Ticker Council is a research tool, not financial advice.</p>
  <div class="links"><a href="/login">Already have an account? Sign in</a></div>"""
    script = "wire('signup-form', '/api/auth/signup', ['name', 'email', 'password'], () => { location.href = '/pending'; });"
    return _page("Create an account", body, script)


def setup_page() -> str:
    body = """
  <h1>Set up your account</h1>
  <p>Ticker Council now has accounts. Create yours first: you'll be the owner, with the admin page, and everything already on this site (your keys, models and runs) stays yours.</p>
  <form id="setup-form" novalidate>
    <label for="name">Your name</label>
    <input id="name" autocomplete="name" autofocus required />
    <label for="email">Email</label>
    <input type="email" id="email" autocomplete="email" required />
    <label for="password">New password</label>
    <input type="password" id="password" autocomplete="new-password" required />
    <div class="hint">At least 10 characters. This replaces the shared site password.</div>
    <label for="site_password">Current site password</label>
    <input type="password" id="site_password" autocomplete="off" required />
    <div class="hint">The APP_PASSWORD you've been signing in with, to prove it's you.</div>
    <div class="error" id="error" role="alert"></div>
    <button type="submit">Create the owner account</button>
  </form>"""
    script = "wire('setup-form', '/api/auth/setup', ['name', 'email', 'password', 'site_password'], () => { location.href = '/'; });"
    return _page("Set up", body, script)


def pending_page() -> str:
    body = """
  <h1>Thanks for signing up</h1>
  <p>Your account is waiting for approval. You'll get an email as soon as it's ready, and then you can sign in.</p>
  <a class="btn" href="/login">Back to sign in</a>"""
    return _page("Waiting for approval", body)


def forgot_page(email_enabled: bool) -> str:
    if not email_enabled:
        body = """
  <h1>Forgot your password?</h1>
  <p>Ask the site's owner for a reset link. They can make one for you from the admin page.</p>
  <a class="btn" href="/login">Back to sign in</a>"""
        return _page("Forgot password", body)
    body = """
  <h1>Forgot your password?</h1>
  <p>Enter your email and we'll send you a link to choose a new one.</p>
  <form id="forgot-form" novalidate>
    <label for="email">Email</label>
    <input type="email" id="email" autocomplete="email" autofocus required />
    <div class="error" id="error" role="alert"></div>
    <button type="submit">Send the link</button>
  </form>
  <div class="links"><a href="/login">Back to sign in</a></div>"""
    script = """
  wire('forgot-form', '/api/auth/forgot', ['email'], () => {
    document.getElementById('forgot-form').outerHTML = '<div class="ok" role="status">If there\\'s an account with that email, a link is on its way. Check your inbox (and spam).</div>';
  });
"""
    return _page("Forgot password", body, script)


def reset_page(valid: bool) -> str:
    if not valid:
        body = """
  <h1>This link has expired</h1>
  <p>Reset links work once, for an hour. Ask for a new one.</p>
  <a class="btn" href="/forgot">Get a new link</a>"""
        return _page("Reset password", body)
    body = """
  <h1>Choose a new password</h1>
  <form id="reset-form" novalidate>
    <label for="password">New password</label>
    <input type="password" id="password" autocomplete="new-password" autofocus required />
    <div class="hint">At least 10 characters.</div>
    <input type="hidden" id="token" />
    <div class="error" id="error" role="alert"></div>
    <button type="submit">Save and sign in</button>
  </form>"""
    script = """
  document.getElementById('token').value = new URLSearchParams(location.search).get('token') || '';
  wire('reset-form', '/api/auth/reset', ['token', 'password'], () => { location.href = '/'; });
"""
    return _page("Reset password", body, script)


def message_page(title: str, message: str) -> str:
    return _page(title, f'<h1>{escape(title)}</h1><p>{escape(message)}</p><a class="btn" href="/login">Back to sign in</a>')


def privacy_page() -> str:
    body = """
  <div class="prose">
  <h1>Privacy</h1>
  <p>Plainly, what happens to your data on Ticker Council.</p>
  <h2>Your runs are private</h2>
  <p>Other users can't see your runs, your History or your settings. The site's owner runs the service and can see everything, including your runs, through the admin page.</p>
  <h2>Your API keys</h2>
  <ul>
    <li>Your keys are stored encrypted and are only ever used for your own runs.</li>
    <li>They're never shown back to anyone, including you: Settings only shows their last four characters.</li>
    <li>Runs cost whatever your AI provider charges you. The site never uses its own keys for your runs.</li>
  </ul>
  <h2>Scores are pooled anonymously</h2>
  <p>When a run's time is up it's scored against what the price actually did. Those scores, from everyone's runs, are pooled to decide how much each seat counts. The pooled numbers never show whose run was whose. Before they're used, the owner reviews them.</p>
  <h2>What's stored about you</h2>
  <ul>
    <li>Your name and email, and a scrambled (hashed) password if you set one. The actual password is never stored.</li>
    <li>If you sign in with Google: your Google account ID, name and email. Nothing else from Google.</li>
    <li>Your runs, settings and keys, and when you last signed in.</li>
  </ul>
  <h2>Emails</h2>
  <p>You'll only get emails about your account: approval and password resets.</p>
  <h2>The AI providers</h2>
  <p>Runs send stock data and prompts (never your name or email) to the AI providers whose keys you add, under their terms. On Google's free tier, Google may use what's sent to improve its products.</p>
  <h2>Deleting your account</h2>
  <p>Ask the owner and your account, keys and settings are deleted. Scored runs stay in the pooled record, which can't be edited, but no one can open them any more except the owner.</p>
  </div>
  <a class="btn" href="/" style="margin-top:18px">Back</a>"""
    return _page("Privacy", body, wide=True)


# ---- the landing page ---------------------------------------------------------------------

_SEATS = [
    ("Price Chart", "Price and trading volume"),
    ("Financials", "Revenue, profit, debt and valuation"),
    ("Analyst Targets", "Price targets and rating changes"),
    ("Earnings Estimates", "Forecasts for future earnings"),
    ("Insider Trades", "What the company's executives buy and sell"),
    ("Congress Trades", "Trades disclosed by members of Congress"),
    ("News", "Headlines and upcoming events"),
    ("SEC Filings", "Buybacks, new shares, debt, lawsuits"),
    ("Institutional Holdings", "What big funds hold, and short interest"),
    ("Options Market", "What options traders are betting on"),
    ("Economy", "Rates, inflation and the wider economy"),
    ("Related Markets", "Similar stocks, the index, overseas"),
]

_LANDING_STYLE = """
  :root {
    color-scheme: light;
    --bg: #f5f6f9; --surface: #ffffff; --surface-2: #f7f8fa; --ink: #141820; --muted: #5a6475;
    --faint: #8a93a3; --line: #e0e4ea; --line-strong: #cfd5de; --accent: #2c56c9; --accent-ink: #fff;
    --accent-soft: #e6ecfb; --up: #16835a; --up-soft: #e3f3ec; --up-mid: #7cc4a3; --down: #c23b35;
    --even: #8a6d1c; --track: #e9ecf1; --knob: #fff;
    --shadow: 0 1px 2px rgba(20,24,32,.05), 0 12px 40px rgba(20,24,32,.08);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      color-scheme: dark;
      --bg: #0d1015; --surface: #151a21; --surface-2: #1a2029; --ink: #e5e8ed; --muted: #9aa3b2;
      --faint: #6d7788; --line: #262d38; --line-strong: #323b48; --accent: #7597f2; --accent-ink: #0d1015;
      --accent-soft: #1c2640; --up: #45c08a; --up-soft: #12291f; --up-mid: #2c7d59; --down: #ec6b64;
      --even: #d4b45a; --track: #232a35; --knob: #e5e8ed; --shadow: 0 12px 40px rgba(0,0,0,.4);
    }
  }
  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; }
  body { margin: 0; background: var(--bg); color: var(--ink); font-family: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; line-height: 1.5; -webkit-font-smoothing: antialiased; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .wrap { max-width: 1080px; margin: 0 auto; padding-inline: 20px; }
  header.top { position: sticky; top: 0; z-index: 10; background: color-mix(in srgb, var(--bg) 82%, transparent); -webkit-backdrop-filter: saturate(180%) blur(16px); backdrop-filter: saturate(180%) blur(16px); border-bottom: 1px solid var(--line); }
  header.top .wrap { display: flex; align-items: center; gap: 16px; height: 58px; }
  .brand { font-weight: 700; font-size: 16px; color: var(--ink); letter-spacing: -.01em; }
  .brand span { color: var(--accent); }
  .brand:hover { text-decoration: none; }
  .top-links { margin-left: auto; display: flex; align-items: center; gap: 8px; }
  .btn { display: inline-flex; align-items: center; justify-content: center; font: inherit; font-weight: 600; font-size: 15px; border-radius: 11px; padding: 11px 20px; border: 1px solid var(--line-strong); background: var(--surface); color: var(--ink); white-space: nowrap; }
  .btn:hover { text-decoration: none; background: var(--surface-2); }
  .btn-primary { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
  .btn-primary:hover { background: var(--accent); filter: brightness(1.07); }
  .btn-small { padding: 8px 14px; font-size: 14px; border-radius: 9px; }
  .btn-quiet { border-color: transparent; background: transparent; color: var(--muted); }

  .hero { padding-block: 72px 40px; display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, .95fr); gap: 48px; align-items: center; }
  .eyebrow { font-size: 13px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--accent); margin: 0 0 14px; }
  h1 { font-size: clamp(34px, 5.2vw, 54px); line-height: 1.06; letter-spacing: -.025em; margin: 0 0 18px; text-wrap: balance; font-weight: 700; }
  .lede { font-size: 18px; color: var(--muted); margin: 0 0 26px; max-width: 34em; text-wrap: pretty; }
  .ctas { display: flex; gap: 10px; flex-wrap: wrap; }
  .fine { font-size: 13px; color: var(--faint); margin: 14px 0 0; }

  .demo { background: var(--surface); border: 1px solid var(--line); border-radius: 18px; box-shadow: var(--shadow); padding: 22px 22px 10px; }
  .demo-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 4px; }
  .demo-tkr { font-size: 26px; font-weight: 700; letter-spacing: -.02em; }
  .demo-meta { font-size: 12px; color: var(--faint); margin-left: auto; }
  .demo-line { font-size: 14px; color: var(--muted); margin: 0 0 8px; }
  .row { display: grid; grid-template-columns: 96px minmax(0, 1fr) 104px; gap: 14px; align-items: center; padding: 14px 0; border-top: 1px solid var(--line); }
  .row:first-of-type { border-top: 0; }
  .term { font-size: 14px; font-weight: 600; }
  .val { text-align: right; font-weight: 600; font-size: 15px; }
  .val.up { color: var(--up); }
  .val.even { color: var(--even); }
  .track { position: relative; height: 8px; border-radius: 99px; background: var(--track); }
  .mid { position: absolute; left: 50%; top: -4px; bottom: -4px; width: 2px; margin-left: -1px; background: var(--line-strong); border-radius: 1px; }
  .fill { position: absolute; top: 0; bottom: 0; left: 50%; border-radius: 99px; background: linear-gradient(90deg, var(--up-mid), var(--up)); }
  .knob { position: absolute; top: 50%; width: 16px; height: 16px; margin: -8px 0 0 -8px; border-radius: 50%; background: var(--knob); border: 3px solid var(--up); box-shadow: 0 1px 4px rgba(20,24,32,.25); }
  .knob.even { border-color: var(--even); }
  .dots { position: relative; height: 8px; margin-top: 7px; }
  .dot { position: absolute; width: 6px; height: 6px; margin-left: -3px; border-radius: 50%; background: var(--up); opacity: .7; }
  .dot.d { background: var(--down); }
  .dot.e { background: var(--even); }

  section.band { padding-block: 56px; }
  h2 { font-size: clamp(26px, 3.4vw, 34px); letter-spacing: -.02em; line-height: 1.15; margin: 0 0 10px; text-wrap: balance; }
  .section-lede { color: var(--muted); font-size: 17px; margin: 0 0 30px; max-width: 40em; }
  .steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; list-style: none; padding: 0; margin: 0; counter-reset: step; }
  .steps li { background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 20px; counter-increment: step; }
  .steps li::before { content: counter(step); display: inline-grid; place-items: center; width: 28px; height: 28px; border-radius: 50%; background: var(--accent-soft); color: var(--accent); font-weight: 700; font-size: 14px; margin-bottom: 12px; }
  .steps h3, .honest h3 { margin: 0 0 6px; font-size: 17px; }
  .steps p, .honest p { margin: 0; color: var(--muted); font-size: 15px; }
  .seats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
  .seat { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; }
  .seat b { display: block; font-size: 15px; margin-bottom: 2px; }
  .seat span { font-size: 13.5px; color: var(--muted); }
  .honest { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 28px 40px; }
  .honest div { border-top: 2px solid var(--line); padding-top: 14px; }
  .closing { text-align: center; background: var(--surface); border: 1px solid var(--line); border-radius: 20px; padding: 44px 24px; }
  .closing p { color: var(--muted); margin: 0 0 22px; font-size: 17px; }
  .closing .ctas { justify-content: center; }
  footer { padding: 34px 0 48px; color: var(--faint); font-size: 13px; }
  footer .wrap { display: grid; gap: 10px; }
  footer nav { display: flex; gap: 18px; flex-wrap: wrap; }
  footer p { margin: 0; max-width: 60em; }

  @media (max-width: 900px) {
    .hero { grid-template-columns: 1fr; padding-block-start: 44px; gap: 36px; }
    .seats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  @media (max-width: 640px) {
    .steps, .honest { grid-template-columns: 1fr; }
    .seats { grid-template-columns: 1fr; }
    .row { grid-template-columns: 78px minmax(0, 1fr) 88px; gap: 10px; }
    .top-links .btn-quiet { display: none; }
    .lede { font-size: 17px; }
  }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 6px; }
"""


def _demo_row(term: str, p: float, label: str, dots: list[float], even: bool = False) -> str:
    # The same 25%-75% scale as the app's own bars.
    def x(v: float) -> float:
        return max(0.0, min(1.0, (v - 0.25) / 0.5)) * 100

    pos = x(p)
    fill = "" if even else f'<span class="fill" style="width:{pos - 50:.1f}%"></span>'
    dot_html = "".join(
        f'<span class="dot{" d" if d < 0.5 else " e" if d == 0.5 else ""}" style="left:{x(d):.1f}%"></span>'
        for d in dots
    )
    return f"""
      <div class="row">
        <div class="term">{term}</div>
        <div><div class="track"><span class="mid"></span>{fill}<span class="knob{' even' if even else ''}" style="left:{pos:.1f}%"></span></div>
          <div class="dots">{dot_html}</div></div>
        <div class="val {'even' if even else 'up'}">{label}</div>
      </div>"""


def landing_page(google_enabled: bool, signed_in: bool = False) -> str:
    ctas = (
        '<a class="btn btn-primary" href="/index.html">Open the app</a>'
        if signed_in else
        '<a class="btn btn-primary" href="/signup">Request access</a><a class="btn" href="/login">Sign in</a>'
    )
    top = (
        '<a class="btn btn-small btn-primary" href="/index.html">Open the app</a>'
        if signed_in else
        '<a class="btn btn-small btn-quiet" href="/login">Sign in</a><a class="btn btn-small btn-primary" href="/signup">Request access</a>'
    )
    seats = "".join(f'<div class="seat"><b>{escape(n)}</b><span>{escape(r)}</span></div>' for n, r in _SEATS)
    demo = (
        _demo_row("Next week", 0.515, "Barely up", [0.5, 0.5, 0.53, 0.54, 0.45, 0.52, 0.47, 0.55, 0.5], even=False)
        + _demo_row("3 months", 0.537, "Leaning up", [0.55, 0.56, 0.57, 0.47, 0.54, 0.53, 0.58, 0.46, 0.55, 0.51])
        + _demo_row("Next year", 0.564, "Up", [0.58, 0.61, 0.62, 0.59, 0.53, 0.55, 0.54, 0.48, 0.6, 0.57, 0.52])
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<title>Ticker Council · Twelve AI analysts, one honest read on any stock</title>
<meta name="description" content="Twelve AI analysts each read one kind of data about a stock, debate it, and tell you which way it leans over the next week, 3 months and year, and how sure they are." />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" />
<style>{_LANDING_STYLE}</style>
</head>
<body>
<header class="top"><div class="wrap">
  <a class="brand" href="/welcome">Ticker <span>Council</span></a>
  <nav class="top-links" aria-label="Account">{top}</nav>
</div></header>

<main>
  <section class="wrap hero">
    <div>
      <p class="eyebrow">AI stock research</p>
      <h1>Twelve AI analysts. One honest read on any stock.</h1>
      <p class="lede">Each analyst reads one kind of data: the price chart, earnings, insider and Congress trades, the options market and more. They debate, and the Council tells you which way the stock leans over the next week, the next 3 months and the next year, and how sure it is.</p>
      <div class="ctas">{ctas}</div>
      <p class="fine">Accounts are approved by hand. Bring your own AI key; a free Google Gemini key works.</p>
    </div>
    <div class="demo" role="img" aria-label="Example result for NVDA: next week close to even, next 3 months leaning up, next year up.">
      <div class="demo-head"><span class="demo-tkr">NVDA</span><span class="demo-meta">Example result</span></div>
      <p class="demo-line">Leaning up over the next year on rising estimates and analyst targets. Next week is close to a toss-up.</p>
      {demo}
    </div>
  </section>

  <section class="band"><div class="wrap">
    <h2>How it works</h2>
    <p class="section-lede">A few minutes from ticker to a reasoned call.</p>
    <ol class="steps">
      <li><h3>Pick a stock</h3><p>Type a ticker. A Full run is the most thorough; a Lite run costs about a third as much.</p></li>
      <li><h3>Twelve seats read and debate</h3><p>Each seat sees only its own data, so their views stay independent. Then a bull case and a bear case argue it out, and a challenger looks for holes.</p></li>
      <li><h3>Read the call</h3><p>Where the Council leans for each period, why, and who disagreed. In plain words, or with every number.</p></li>
    </ol>
  </div></section>

  <section class="band"><div class="wrap">
    <h2>Twelve seats, twelve kinds of data</h2>
    <p class="section-lede">No single source decides the call. Each seat counts most on the period its data actually says something about.</p>
    <div class="seats">{seats}</div>
  </div></section>

  <section class="band"><div class="wrap">
    <h2>Built to be honest</h2>
    <p class="section-lede">A stock tool is only useful if you know when not to trust it.</p>
    <div class="honest">
      <div><h3>Weak calls look weak</h3><p>A lean barely off the middle is shown as close to a coin flip, not dressed up as a prediction.</p></div>
      <div><h3>Every call gets checked</h3><p>Each run is saved where it can't be edited, then scored against what the price actually did a week, 3 months and a year later.</p></div>
      <div><h3>Seats earn their say</h3><p>Analysts that keep getting it right count for more over time, and the ones that don't count for less.</p></div>
      <div><h3>Private by default</h3><p>Your runs are yours alone, your API keys are stored encrypted, and they're only ever used for your own runs. <a href="/privacy">Privacy</a></p></div>
    </div>
  </div></section>

  <section class="band"><div class="wrap">
    <div class="closing">
      <h2>Get a second opinion from twelve.</h2>
      <p>Request an account and you'll get an email once it's approved.</p>
      <div class="ctas">{ctas}</div>
    </div>
  </div></section>
</main>

<footer><div class="wrap">
  <nav aria-label="Footer"><a href="/privacy">Privacy</a><a href="/login">Sign in</a><a href="/signup">Request access</a></nav>
  <p>Not financial advice. Ticker Council is a research tool: its calls can be wrong, and past accuracy doesn't guarantee future results. Decide for yourself, and only invest what you can afford to lose.</p>
</div></footer>
</body>
</html>"""
