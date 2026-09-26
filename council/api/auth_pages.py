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
  .brand { font-size: 15px; font-weight: 700; letter-spacing: -0.01em; margin: 0 0 18px; color: var(--ink); text-decoration: none; display: inline-flex; align-items: center; gap: 7px; }
  .brand .logo { width: 22px; height: 22px; color: var(--accent); margin-top: -3px; }
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

_EMU = '<svg class="logo" viewBox="0 0 32 32" aria-hidden="true"><g fill="currentColor" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M2.6 20.4C2.4 14.6 7 10.6 12.6 10.6c4.4 0 7.9 2.3 8.2 5.6.2 2.6-1.8 4.2-4.8 4.4l-9.4.2c-1.6 0-3.2.5-4 1.6z"/><path fill="none" stroke-width="2.7" d="M18.2 13.4c1.5-2.6 2.7-5.3 3.1-8.3"/><circle stroke="none" cx="22" cy="4.4" r="2.3"/><path stroke="none" d="M23.6 3.5l3 1.1-3 .9z"/><path fill="none" stroke-width="1.8" d="M10.8 20.4l-1 9.2M14.6 20.4l1.6 9.2"/></g></svg>'
# Tab icon, plus what lets phones install the site like an app (Add to Home Screen).
_FAVICON = """<link rel="icon" href="/favicon.svg" type="image/svg+xml" />
<link rel="manifest" href="/manifest.webmanifest" />
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-title" content="Ticker Council" />"""

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
{_FAVICON}
<title>{escape(title)} · Ticker Council</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600;700&display=swap" />
<style>{_STYLE}</style>
</head>
<body>
<main class="panel{' wide' if wide else ''}">
  <a class="brand" href="/welcome">{_EMU}Ticker <span>Council</span></a>
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
  <p style="margin:14px 0 0;font-size:13px">By signing up you agree to the <a href="/terms">terms</a> (it's a research tool, not financial advice) and to how your data is handled (<a href="/privacy">privacy</a>).</p>
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


def notice_page(title: str, message: str) -> str:
    return _page(title, f'<h1>{escape(title)}</h1><p>{escape(message)}</p><a class="btn" href="/">Open Ticker Council</a>')


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
  <h2>Shared links</h2>
  <p>If you share a result, anyone with its link can see that one run: the call, the summary and how each seat leaned. Never your name, email or other runs. You can stop sharing at any time and the link stops working.</p>
  <h2>Visitor counts</h2>
  <p>The site counts how many people open the landing page, the sign-up form and shared results, and which sites sent them, so the owner can see if anyone's finding it. There are no cookies and no outside tracking services. Your IP address isn't stored: visits are counted with a code that changes every day, so nobody can be followed from one day to the next.</p>
  <h2>Backups</h2>
  <p>The databases are backed up nightly so nothing is lost if the server fails. Backups never include the key that unlocks saved API keys.</p>
  <h2>Emails</h2>
  <p>You'll only get emails about your account: approval and password resets.</p>
  <h2>The AI providers</h2>
  <p>Runs send stock data and prompts (never your name or email) to the AI providers whose keys you add, under their terms. On Google's free tier, Google may use what's sent to improve its products.</p>
  <h2>Deleting your account</h2>
  <p>Ask the owner and your account, keys and settings are deleted. Scored runs stay in the pooled record, which can't be edited, but no one can open them any more except the owner.</p>
  </div>
  <a class="btn" href="/" style="margin-top:18px">Back</a>"""
    return _page("Privacy", body, wide=True)


def terms_page() -> str:
    body = """
  <div class="prose">
  <h1>Terms of use</h1>
  <p>The short version: Ticker Council is a research tool. Its calls can be wrong, you make your own decisions, and you use it at your own risk.</p>
  <h2>Not financial advice</h2>
  <ul>
    <li>Nothing on Ticker Council is financial, investment, tax or legal advice, or a recommendation to buy, sell or hold anything.</li>
    <li>Every stock gets the same kind of read for everyone. It doesn't know your finances, goals or how much risk suits you.</li>
    <li>The calls come from AI models reading public data. They can be wrong, out of date or based on bad data, and past accuracy doesn't predict future results.</li>
    <li>Decide for yourself, talk to a licensed professional if you need advice, and only invest what you can afford to lose.</li>
  </ul>
  <h2>Your account</h2>
  <ul>
    <li>Accounts are approved by hand, and can be paused or removed, for example for misuse.</li>
    <li>Keep your password to yourself. You're responsible for what happens under your account.</li>
    <li>One person per account.</li>
  </ul>
  <h2>Your API keys and costs</h2>
  <p>Runs use the AI keys you add, and your AI provider bills you for them under its own terms. Ticker Council shows cost estimates, but they're estimates: check your provider's dashboard for what you're actually charged.</p>
  <h2>Fair use</h2>
  <ul>
    <li>Don't try to break, overload or get around the site's limits or sign-in.</li>
    <li>Don't scrape the site or resell its output as your own service.</li>
    <li>Sharing individual results you've made, with a link back, is fine.</li>
  </ul>
  <h2>No guarantees</h2>
  <p>The site is provided as it is. It may be down, slow, change or stop, and data may be lost. As far as the law allows, Ticker Council isn't liable for any loss, including trading losses, that comes from using it or relying on it.</p>
  <h2>Changes</h2>
  <p>These terms may change as the site grows. If they change in a way that matters, you'll hear about it by email or in the app. Your data is handled as described on the <a href="/privacy">Privacy</a> page.</p>
  </div>
  <a class="btn" href="/" style="margin-top:18px">Back</a>"""
    return _page("Terms", body, wide=True)


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


# How each seat leaned on the 3-month term in the replayed run: up, down or even.
_DEMO_VOTES = ["up", "up", "up", "up", "dn", "ev", "up", "ev", "up", "up", "dn", "up"]

# (term, where the Council landed, word shown) -- the same 25%-75% scale as the app's bars.
_DEMO_TERMS = [("Next week", 0.515, "Barely up"), ("3 months", 0.537, "Leaning up"), ("Next year", 0.564, "Up")]

_LANDING_STYLE = """
  :root {
    color-scheme: light;
    --bg: #fbfbfd; --page: none; --surface: #ffffff; --surface-2: #f5f5f7; --ink: #1d1d1f;
    --muted: #6e6e73; --faint: #86868b; --line: rgba(0,0,0,.08); --line-strong: rgba(0,0,0,.14);
    --accent: #0071e3; --grad: linear-gradient(90deg, #0071e3, #8e44ec);
    --cta: #0071e3; --cta-ink: #ffffff; --cta-glow: none;
    --up: #1f9d55; --up-soft: #e3f5e9; --down: #d93025; --down-soft: #fde8e8; --even: #a07b12; --even-soft: #f7efd9;
    --track: #ececf0; --knob: #ffffff;
    --card: #ffffff; --card-border: rgba(0,0,0,.04);
    --card-shadow: 0 30px 80px -20px rgba(0,0,0,.18), 0 0 0 1px rgba(0,0,0,.04);
    --tile-shadow: 0 0 0 1px rgba(0,0,0,.05);
    --glow-up: none; --glow-down: none; --glow-bar: none;
    --header: rgba(251,251,253,.78);
  }
  html[data-theme="dark"] {
    color-scheme: dark;
    --bg: #07080d; --page: radial-gradient(1200px 700px at 78% -8%, #1b2340 0%, rgba(7,8,13,0) 62%), radial-gradient(900px 600px at -10% 40%, #161a33 0%, rgba(7,8,13,0) 60%);
    --surface: rgba(255,255,255,.04); --surface-2: rgba(255,255,255,.06); --ink: #f2f3f7;
    --muted: #9aa0b4; --faint: #6f7690; --line: rgba(255,255,255,.08); --line-strong: rgba(255,255,255,.16);
    --accent: #9aa6ff; --grad: linear-gradient(90deg, #9aa6ff, #e0b3ff);
    --cta: linear-gradient(90deg, #7c8cff, #b18cff); --cta-ink: #0b0c12; --cta-glow: 0 0 30px rgba(140,140,255,.45);
    --up: #3ddc97; --up-soft: rgba(61,220,151,.08); --down: #ff5c7a; --down-soft: rgba(255,92,122,.08); --even: #e6c35c; --even-soft: rgba(230,195,92,.08);
    --track: rgba(255,255,255,.08); --knob: #f2f3f7;
    --card: rgba(255,255,255,.04); --card-border: rgba(255,255,255,.08);
    --card-shadow: 0 0 80px rgba(120,120,255,.15), 0 0 0 1px rgba(255,255,255,.08);
    --tile-shadow: 0 0 0 1px rgba(255,255,255,.07);
    --glow-up: 0 0 14px rgba(61,220,151,.35); --glow-down: 0 0 14px rgba(255,92,122,.35); --glow-bar: 0 0 12px;
    --header: rgba(7,8,13,.6);
  }
  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; background: var(--bg); }
  body {
    margin: 0; background: var(--page), var(--bg); background-repeat: no-repeat; color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", Inter, "Segoe UI", Roboto, sans-serif;
    line-height: 1.5; -webkit-font-smoothing: antialiased;
    transition: color .4s ease;
  }
  h1, h2, h3, .brand, .demo-tkr { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", Inter, "Segoe UI", Roboto, sans-serif; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .wrap { max-width: 1120px; margin: 0 auto; padding-inline: 20px; }

  header.top { position: sticky; top: 0; z-index: 10; background: var(--header); -webkit-backdrop-filter: saturate(180%) blur(18px); backdrop-filter: saturate(180%) blur(18px); border-bottom: 1px solid var(--line); }
  header.top .wrap { display: flex; align-items: center; gap: 12px; height: 58px; }
  .brand { font-weight: 700; font-size: 17px; color: var(--ink); letter-spacing: -.01em; display: inline-flex; align-items: center; gap: 7px; }
  .brand .logo { width: 24px; height: 24px; color: var(--accent); margin-top: -3px; }
  .brand span { color: var(--accent); }
  .brand:hover { text-decoration: none; }
  .top-links { margin-left: auto; display: flex; align-items: center; gap: 6px; }
  .theme-btn { width: 38px; height: 38px; border-radius: 50%; border: 1px solid var(--line); background: var(--surface); color: var(--ink); display: grid; place-items: center; cursor: pointer; padding: 0; }
  .theme-btn:hover { border-color: var(--line-strong); }
  .theme-btn svg { width: 18px; height: 18px; }
  .theme-btn .sun { display: none; }
  html[data-theme="dark"] .theme-btn .sun { display: block; }
  html[data-theme="dark"] .theme-btn .moon { display: none; }

  .btn { display: inline-flex; align-items: center; justify-content: center; font: inherit; font-weight: 600; font-size: 16px; border-radius: 999px; padding: 13px 26px; border: 1px solid var(--line-strong); background: transparent; color: var(--ink); white-space: nowrap; transition: transform .15s ease, filter .15s ease; }
  .btn:hover { text-decoration: none; background: var(--surface-2); }
  .btn:active { transform: scale(.98); }
  .btn-primary { background: var(--cta); border-color: transparent; color: var(--cta-ink); box-shadow: var(--cta-glow); }
  .btn-primary:hover { background: var(--cta); filter: brightness(1.08); }
  .btn-small { padding: 8px 16px; font-size: 14px; }
  .btn-quiet { border-color: transparent; color: var(--muted); }

  .hero { padding-block: 76px 64px; display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr); gap: 56px; align-items: center; }
  .eyebrow { font-size: 13px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--accent); margin: 0 0 16px; }
  h1 { font-size: clamp(40px, 6vw, 68px); line-height: 1.02; letter-spacing: -.04em; margin: 0 0 22px; font-weight: 700; text-wrap: balance; }
  .grad { background: var(--grad); -webkit-background-clip: text; background-clip: text; color: transparent; }
  .lede { font-size: 19px; color: var(--muted); margin: 0 0 30px; max-width: 32em; text-wrap: pretty; }
  .ctas { display: flex; gap: 10px; flex-wrap: wrap; }
  .fine { font-size: 13px; color: var(--faint); margin: 16px 0 0; }

  /* The replayed council run */
  .demo { background: var(--card); border-radius: 26px; box-shadow: var(--card-shadow); padding: 22px; -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px); }
  .demo-head { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
  .demo-tkr { font-size: 24px; font-weight: 700; letter-spacing: -.02em; }
  .demo-status { margin-left: auto; font-size: 13px; color: var(--muted); display: inline-flex; align-items: center; gap: 7px; }
  .pulse { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); animation: pulse 1.2s ease-in-out infinite; }
  .done .pulse { animation: none; background: var(--up); }
  @keyframes pulse { 50% { opacity: .25; } }
  .dseats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; }
  .dseat { border-radius: 12px; padding: 8px 10px; font-size: 12px; font-weight: 600; line-height: 1.25; display: flex; align-items: center; justify-content: space-between; gap: 6px; min-height: 42px; background: var(--surface-2); border: 1px solid transparent; transition: background .35s, border-color .35s, box-shadow .35s; }
  .dseat .v { font-size: 11px; opacity: 0; transform: scale(.4); transition: opacity .3s, transform .3s cubic-bezier(.3,1.6,.5,1); }
  .dseat.voted .v { opacity: 1; transform: none; }
  .dseat.voted.up { background: var(--up-soft); } .dseat.voted.up .v { color: var(--up); }
  .dseat.voted.dn { background: var(--down-soft); } .dseat.voted.dn .v { color: var(--down); }
  .dseat.voted.ev { background: var(--even-soft); } .dseat.voted.ev .v { color: var(--even); }
  html[data-theme="dark"] .dseat.voted.up { border-color: var(--up); box-shadow: var(--glow-up); }
  html[data-theme="dark"] .dseat.voted.dn { border-color: var(--down); box-shadow: var(--glow-down); }
  html[data-theme="dark"] .dseat.voted.ev { border-color: rgba(230,195,92,.5); }
  .terms { margin-top: 18px; }
  .term-row { display: grid; grid-template-columns: 82px minmax(0, 1fr) 92px; gap: 12px; align-items: center; padding: 11px 0; border-top: 1px solid var(--line); font-size: 14px; }
  .term-row b { font-weight: 600; }
  .track { position: relative; height: 8px; border-radius: 99px; background: var(--track); }
  .mid { position: absolute; left: 50%; top: -5px; bottom: -5px; width: 2px; margin-left: -1px; background: var(--line-strong); border-radius: 1px; }
  /* The lean: a fill running out from the middle line, fading in toward its rounded end. */
  .fill { position: absolute; top: 0; bottom: 0; left: 50%; width: 0; border-radius: 0 99px 99px 0; background: linear-gradient(90deg, color-mix(in srgb, var(--up) 18%, transparent), var(--up)); box-shadow: var(--glow-bar) color-mix(in srgb, var(--up) 60%, transparent); transition: width 1.1s cubic-bezier(.2,.8,.2,1); }
  .word { text-align: right; font-weight: 600; color: var(--up); opacity: 0; transition: opacity .5s .6s; }
  .demo.bars .word { opacity: 1; }
  .verdict { margin-top: 12px; border-radius: 14px; padding: 12px 14px; font-size: 14px; line-height: 1.5; background: var(--surface-2); color: var(--ink); opacity: 0; transform: translateY(6px); transition: opacity .5s, transform .5s; }
  html[data-theme="dark"] .verdict { background: rgba(124,140,255,.1); box-shadow: inset 0 0 0 1px rgba(124,140,255,.25); }
  .demo.done .verdict { opacity: 1; transform: none; }

  section.band { padding-block: 72px; }
  h2 { font-size: clamp(30px, 4.2vw, 46px); letter-spacing: -.03em; line-height: 1.08; margin: 0 0 12px; text-wrap: balance; }
  .section-lede { color: var(--muted); font-size: 18px; margin: 0 0 36px; max-width: 38em; }
  .steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; list-style: none; padding: 0; margin: 0; }
  .steps li, .seat, .faq details, .closing { background: var(--surface); box-shadow: var(--tile-shadow); }
  .steps li { border-radius: 22px; padding: 26px; }
  .steps .n { font-size: 15px; font-weight: 700; margin-bottom: 14px; }
  .steps h3 { margin: 0 0 8px; font-size: 21px; letter-spacing: -.01em; }
  .steps p { margin: 0; color: var(--muted); font-size: 16px; }
  .seats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
  .seat { border-radius: 18px; padding: 16px 18px; }
  .seat b { display: block; font-size: 16px; margin-bottom: 3px; }
  .seat span { font-size: 14px; color: var(--muted); }
  .faq { max-width: 780px; display: grid; gap: 10px; }
  .faq details { border-radius: 16px; padding: 0 20px; }
  .faq summary { cursor: pointer; list-style: none; font-weight: 600; font-size: 17px; padding: 18px 28px 18px 0; position: relative; }
  .faq summary::-webkit-details-marker { display: none; }
  .faq summary::after { content: "+"; position: absolute; right: 0; top: 50%; transform: translateY(-50%); font-size: 22px; font-weight: 400; color: var(--muted); transition: transform .25s; }
  .faq details[open] summary::after { transform: translateY(-50%) rotate(45deg); }
  .faq details p { margin: 0 0 18px; color: var(--muted); font-size: 16px; }
  .closing { text-align: center; border-radius: 28px; padding: 56px 24px; }
  .closing p { color: var(--muted); margin: 0 0 24px; font-size: 18px; }
  .closing .ctas { justify-content: center; }
  footer { padding: 34px 0 48px; color: var(--faint); font-size: 13px; border-top: 1px solid var(--line); }
  footer .wrap { display: grid; gap: 10px; }
  footer nav { display: flex; gap: 18px; flex-wrap: wrap; }
  footer p { margin: 0; max-width: 60em; }

  /* Sections ease in as you scroll (only when JS is on, and never with reduced motion). */
  html.js .reveal { opacity: 0; transform: translateY(28px); transition: opacity .8s ease, transform .8s cubic-bezier(.2,.8,.2,1); }
  html.js .reveal.in { opacity: 1; transform: none; }
  @media (prefers-reduced-motion: reduce) {
    html.js .reveal { opacity: 1; transform: none; transition: none; }
    .pulse { animation: none; }
  }

  @media (max-width: 900px) {
    .hero { grid-template-columns: 1fr; padding-block: 44px 48px; gap: 40px; }
    .seats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  /* Phones: a tighter hero so the council replay starts near the top of the screen. */
  @media (max-width: 640px) {
    header.top .wrap { height: 54px; }
    .brand { font-size: 16px; }
    .top-links .btn-quiet { display: none; }
    .top-links .btn-small { padding: 7px 13px; font-size: 13.5px; }
    .theme-btn { width: 34px; height: 34px; }
    .hero { padding-block: 26px 36px; gap: 26px; }
    .eyebrow { font-size: 12px; margin-bottom: 10px; }
    h1 { font-size: clamp(31px, 9.6vw, 40px); margin-bottom: 14px; }
    .lede { font-size: 16px; margin-bottom: 20px; }
    .ctas { display: grid; grid-template-columns: 1fr 1fr; }
    .ctas .btn { padding: 13px 12px; font-size: 15px; }
    .fine { font-size: 12px; margin-top: 12px; }
    .demo { padding: 14px; border-radius: 20px; }
    .demo-head { margin-bottom: 10px; }
    .demo-tkr { font-size: 20px; }
    .dseats { gap: 5px; }
    .dseat { font-size: 10.5px; padding: 6px 7px; min-height: 36px; border-radius: 10px; }
    .terms { margin-top: 12px; }
    .term-row { grid-template-columns: 66px minmax(0, 1fr) 76px; gap: 10px; font-size: 13px; padding: 9px 0; }
    .verdict { font-size: 13px; padding: 10px 12px; }
    section.band { padding-block: 44px; }
    .section-lede { font-size: 16px; margin-bottom: 22px; }
    .steps { grid-template-columns: 1fr; gap: 10px; }
    .steps li { padding: 18px 20px; border-radius: 18px; }
    .steps .n { margin-bottom: 6px; }
    .steps h3 { font-size: 18px; }
    .steps p { font-size: 15px; }
    .seats { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .seat { padding: 12px 13px; border-radius: 14px; }
    .seat b { font-size: 14.5px; }
    .seat span { font-size: 12.5px; }
    .faq details { padding: 0 16px; }
    .faq summary { font-size: 15.5px; padding: 15px 26px 15px 0; }
    .faq details p { font-size: 15px; margin-bottom: 15px; }
    .closing { padding: 36px 18px; border-radius: 22px; }
    .closing p { font-size: 16px; }
    footer { padding: 26px 0 36px; }
  }
  /* The smallest phones: the hero already has the Request access button. */
  @media (max-width: 360px) {
    .top-links .btn-primary { display: none; }
    .ctas { grid-template-columns: 1fr; }
    .seats { grid-template-columns: 1fr; }
  }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 6px; }
"""

# Runs before first paint so a remembered dark choice never flashes light.
_LANDING_HEAD_SCRIPT = """
  document.documentElement.classList.add('js');
  try { if (localStorage.getItem('tc_landing_theme') === 'dark') document.documentElement.dataset.theme = 'dark'; } catch (e) {}
"""

_LANDING_SCRIPT = """
(function () {
  const root = document.documentElement;
  const toggle = document.getElementById('theme-btn');
  function syncToggle() {
    const dark = root.dataset.theme === 'dark';
    toggle.setAttribute('aria-pressed', dark ? 'true' : 'false');
    toggle.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
  }
  toggle.addEventListener('click', () => {
    if (root.dataset.theme === 'dark') delete root.dataset.theme; else root.dataset.theme = 'dark';
    try { localStorage.setItem('tc_landing_theme', root.dataset.theme === 'dark' ? 'dark' : 'light'); } catch (e) {}
    syncToggle();
  });
  syncToggle();

  const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Scroll reveal
  const reveals = document.querySelectorAll('.reveal');
  if (still || !('IntersectionObserver' in window)) {
    reveals.forEach(el => el.classList.add('in'));
  } else {
    const io = new IntersectionObserver(entries => entries.forEach(e => {
      if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
    }), { threshold: 0.12 });
    reveals.forEach(el => io.observe(el));
  }

  // The replayed council run
  const demo = document.getElementById('demo');
  const seats = [...demo.querySelectorAll('.dseat')];
  const rows = [...demo.querySelectorAll('.term-row')];
  const status = demo.querySelector('.status-text');
  let timers = [];
  const later = (fn, ms) => timers.push(setTimeout(fn, ms));

  function showBars() {
    demo.classList.add('bars');
    rows.forEach(r => {
      const pos = parseFloat(r.dataset.pos);
      r.querySelector('.fill').style.width = Math.max(0, pos - 50) + '%';
    });
  }
  function finish() { demo.classList.add('done'); status.textContent = 'Verdict reached'; }
  function reset() {
    demo.classList.remove('bars', 'done');
    seats.forEach(s => s.classList.remove('voted'));
    rows.forEach(r => { r.querySelector('.fill').style.width = '0'; });
    status.textContent = 'Council in session…';
  }
  function play() {
    timers.forEach(clearTimeout); timers = [];
    reset();
    const order = seats.map((_, i) => i).sort(() => Math.random() - 0.5);
    order.forEach((i, k) => later(() => seats[i].classList.add('voted'), 700 + k * 230));
    const voted = 700 + seats.length * 230 + 250;
    later(showBars, voted);
    later(finish, voted + 1300);
    later(play, voted + 9500);
  }
  if (still) { seats.forEach(s => s.classList.add('voted')); showBars(); finish(); }
  else play();
})();
"""

_MOON = '<svg class="moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>'
_SUN = '<svg class="sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4.2"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'

_FAQ = [
    ("Is this financial advice?",
     "No. Ticker Council is a research tool. Its calls can be wrong, and the same stock gets the same kind of "
     "read for everyone. Use it as a second opinion, decide for yourself, and only invest what you can afford to lose."),
    ("Why twelve AIs instead of one?",
     "Each seat sees only its own kind of data, so no single source can drown out the rest. After they vote, a bull "
     "case and a bear case argue it out, a challenger looks for holes, and a final judge writes the verdict."),
    ("How do I know if it's any good?",
     "Every call is saved where it can't be edited, then scored against what the price actually did a week, 3 months "
     "and a year later. Seats that keep getting it right count for more over time."),
    ("What does it cost?",
     "Accounts are in early access and approved by hand. Right now you bring your own AI key, and a free Google "
     "Gemini key is enough to get started."),
    ("Which stocks can I look up?",
     "US-listed stocks, by ticker. Type something like NVDA or AAPL and the Council convenes."),
    ("What happens to my data?",
     "Your runs are private to you, and any API keys you add are stored encrypted and only used for your own runs."),
]


def landing_page(google_enabled: bool, signed_in: bool = False, base_url: str = "") -> str:
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
    marks = {"up": "▲", "dn": "▼", "ev": "●"}
    demo_seats = "".join(
        f'<div class="dseat {v}"><span>{escape(name)}</span><span class="v">{marks[v]}</span></div>'
        for (name, _), v in zip(_SEATS, _DEMO_VOTES)
    )
    demo_terms = "".join(
        f'<div class="term-row" data-pos="{max(0.0, min(1.0, (p - 0.25) / 0.5)) * 100:.1f}"><b>{term}</b>'
        f'<div class="track"><span class="mid"></span><span class="fill"></span></div>'
        f'<span class="word">{word}</span></div>'
        for term, p, word in _DEMO_TERMS
    )
    seats = "".join(f'<div class="seat"><b>{escape(n)}</b><span>{escape(r)}</span></div>' for n, r in _SEATS)
    faq = "".join(f"<details><summary>{escape(q)}</summary><p>{escape(a)}</p></details>" for q, a in _FAQ)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
{_FAVICON}
<title>Ticker Council · Twelve AIs debate every stock</title>
<meta name="description" content="Twelve AI analysts each read one kind of data about a stock, debate it, and give you one clear verdict for the next week, 3 months and year." />
<meta property="og:type" content="website" />
<meta property="og:site_name" content="Ticker Council" />
<meta property="og:title" content="Ticker Council · Twelve AIs debate every stock" />
<meta property="og:description" content="Twelve AI analysts each read one kind of data about a stock, debate it, and give you one clear verdict." />
<meta property="og:image" content="{escape(base_url)}/icons/og.png" />
<meta name="twitter:card" content="summary_large_image" />
<script>{_LANDING_HEAD_SCRIPT}</script>
<style>{_LANDING_STYLE}</style>
</head>
<body>
<header class="top"><div class="wrap">
  <a class="brand" href="/welcome">{_EMU}Ticker <span>Council</span></a>
  <nav class="top-links" aria-label="Account">
    <button type="button" class="theme-btn" id="theme-btn" aria-pressed="false" aria-label="Switch to dark mode">{_MOON}{_SUN}</button>
    {top}
  </nav>
</div></header>

<main>
  <section class="wrap hero">
    <div>
      <p class="eyebrow">AI stock research</p>
      <h1>Twelve AIs debate every stock. <span class="grad">You get the verdict.</span></h1>
      <p class="lede">Each seat reads a different kind of data, from the price chart and earnings to Congress trades and the options market. They argue it out and hand you one clear call for the next week, 3 months and year.</p>
      <div class="ctas">{ctas}</div>
      <p class="fine">Early access · Accounts approved by hand · Not financial advice</p>
    </div>
    <div class="demo" id="demo" role="img" aria-label="A replay of a council run on NVDA: twelve seats vote, then the verdict leans up over the next year, leaning up over 3 months, and barely up next week.">
      <div class="demo-head"><span class="demo-tkr">NVDA</span><span class="demo-status"><span class="pulse"></span><span class="status-text">Council in session…</span></span></div>
      <div class="dseats">{demo_seats}</div>
      <div class="terms">{demo_terms}</div>
      <div class="verdict"><b>Leaning up over the next year.</b> Rising earnings estimates and heavy call buying outweigh a stretched valuation. Next week is close to a toss-up.</div>
    </div>
  </section>

  <section class="band reveal"><div class="wrap">
    <h2>How it works</h2>
    <p class="section-lede">A few minutes from ticker to a reasoned call.</p>
    <ol class="steps">
      <li><div class="n grad">01</div><h3>Pick a stock</h3><p>Type any US ticker and the council convenes.</p></li>
      <li><div class="n grad">02</div><h3>The council debates</h3><p>Twelve AI seats each study their own data and vote. Then a bull and a bear argue it out, and a challenger looks for holes.</p></li>
      <li><div class="n grad">03</div><h3>You get the verdict</h3><p>Where the stock leans for each period, how sure the council is, and every seat's reasoning. In plain words, or with every number.</p></li>
    </ol>
  </div></section>

  <section class="band reveal"><div class="wrap">
    <h2>Meet the council</h2>
    <p class="section-lede">Twelve seats, twelve kinds of data. No single source decides the call.</p>
    <div class="seats">{seats}</div>
  </div></section>

  <section class="band reveal"><div class="wrap">
    <h2>Questions</h2>
    <p class="section-lede">The short answers.</p>
    <div class="faq">{faq}</div>
  </div></section>

  <section class="band reveal"><div class="wrap">
    <div class="closing">
      <h2>Get a second opinion <span class="grad">from twelve.</span></h2>
      <p>Request access and you'll get an email once your account is approved.</p>
      <div class="ctas">{ctas}</div>
    </div>
  </div></section>
</main>

<footer><div class="wrap">
  <nav aria-label="Footer"><a href="/terms">Terms</a><a href="/privacy">Privacy</a><a href="/login">Sign in</a><a href="/signup">Request access</a></nav>
  <p>Not financial advice. Ticker Council is a research tool: its calls can be wrong, and past accuracy doesn't guarantee future results. Decide for yourself, and only invest what you can afford to lose.</p>
</div></footer>
<script>{_LANDING_SCRIPT}</script>
</body>
</html>"""
