"""Task #78 -- a single shared-password gate for exposing the app beyond
your own machine, ahead of real per-user accounts (a separate, larger
piece of work the README's hosting section flags as still needed).
Deliberately a thin layer: a session is "does this browser hold a cookie
signed with a secret derived from the one shared password", not a user
record anywhere -- swapping the credential check in login_submit for a
real per-user lookup later, and adding a users table for authorization
instead of this single on/off gate, doesn't require touching the session
cookie mechanism itself."""
from __future__ import annotations

import hmac
import time
import urllib.parse
from hashlib import sha256

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from council.config import get_settings

COOKIE_NAME = "council_session"
# A second, deliberately non-HttpOnly cookie carrying no secret -- just a
# flag the UI can read via document.cookie to decide whether to show a
# Logout link. The real session cookie above is HttpOnly (so an injected
# script can never read or exfiltrate it), which also means client-side JS
# can never see it either -- this exists purely so the nav doesn't have to
# guess.
LOGGED_IN_FLAG_COOKIE = "council_logged_in"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days
_PUBLIC_PATHS = {"/login", "/logout"}


def sign_session(secret: str, issued_at: int | None = None) -> str:
    issued_at = int(time.time()) if issued_at is None else issued_at
    signature = hmac.new(secret.encode(), str(issued_at).encode(), sha256).hexdigest()
    return f"{issued_at}.{signature}"


def verify_session(cookie_value: str, secret: str, now: int | None = None) -> bool:
    now = int(time.time()) if now is None else now
    try:
        issued_at_str, signature = cookie_value.split(".", 1)
        issued_at = int(issued_at_str)
    except ValueError:
        return False
    expected = hmac.new(secret.encode(), issued_at_str.encode(), sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    return 0 <= now - issued_at <= SESSION_MAX_AGE_SECONDS


def _safe_next_path(raw: str | None) -> str:
    """Only ever redirect somewhere on this same site -- `next` comes from
    a query param an attacker could craft into a shared link, so a bare
    "//evil.com" (protocol-relative) or "https://evil.com" must not pass
    through even though the user would still have to enter the real
    password first for it to matter."""
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return "/"
    return raw


router = APIRouter()


class _LoginRequest(BaseModel):
    password: str


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return _LOGIN_PAGE_HTML


@router.post("/login")
async def login_submit(body: _LoginRequest):
    settings = get_settings()
    if not settings.resolved_auth_enabled:
        raise HTTPException(400, "authentication is not enabled (APP_PASSWORD not set)")
    if not hmac.compare_digest(body.password, settings.app_password):
        raise HTTPException(401, "incorrect password")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        COOKIE_NAME,
        sign_session(settings.resolved_session_secret),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.resolved_cookie_secure,
    )
    response.set_cookie(
        LOGGED_IN_FLAG_COOKIE,
        "1",
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=False,
        samesite="lax",
        secure=settings.resolved_cookie_secure,
    )
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse("/login")
    response.delete_cookie(COOKIE_NAME)
    response.delete_cookie(LOGGED_IN_FLAG_COOKIE)
    return response


async def _auth_gate(request: Request, call_next):
    settings = get_settings()
    if not settings.resolved_auth_enabled or request.url.path in _PUBLIC_PATHS:
        return await call_next(request)

    cookie = request.cookies.get(COOKIE_NAME)
    if cookie and verify_session(cookie, settings.resolved_session_secret):
        return await call_next(request)

    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    next_path = urllib.parse.quote(request.url.path, safe="")
    return RedirectResponse(f"/login?next={next_path}", status_code=307)


def install_auth(app) -> None:
    app.include_router(router)
    app.add_middleware(BaseHTTPMiddleware, dispatch=_auth_gate)


# Self-contained on purpose (inline CSS/JS, no external asset) -- it must
# render and work even though every static asset under /css and /js is
# itself behind this same gate.
_LOGIN_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Sign in · High Council</title>
<style>
  :root {
    color-scheme: light;
    --bg: #f3f5f8; --surface: #ffffff; --ink: #141820; --muted: #5a6475; --line: #cfd5de;
    --accent: #2c56c9; --accent-ink: #ffffff; --accent-soft: #e6ecfb; --down: #c23b35;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      color-scheme: dark;
      --bg: #0d1015; --surface: #151a21; --ink: #e5e8ed; --muted: #9aa3b2; --line: #323b48;
      --accent: #7597f2; --accent-ink: #0d1015; --accent-soft: #1c2640; --down: #ec6b64;
    }
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; min-height: 100vh; background: var(--bg); color: var(--ink);
    font-family: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    display: flex; align-items: center; justify-content: center; padding: 16px;
  }
  .panel {
    background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
    box-shadow: 0 10px 40px rgba(20, 24, 32, 0.08); padding: 32px 28px; width: 100%; max-width: 340px;
  }
  h1 { font-size: 20px; font-weight: 700; margin: 0 0 6px; letter-spacing: -0.01em; }
  h1 span { color: var(--accent); }
  p { margin: 0 0 20px; color: var(--muted); font-size: 14px; }
  input[type="password"] {
    width: 100%; font: inherit; font-size: 15px; background: var(--surface); color: var(--ink);
    border: 1px solid var(--line); border-radius: 10px; padding: 11px 12px; margin-bottom: 10px;
  }
  input[type="password"]:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  button {
    width: 100%; font: inherit; font-size: 15px; font-weight: 600; background: var(--accent); color: var(--accent-ink);
    border: 0; border-radius: 10px; padding: 11px; cursor: pointer;
  }
  button:hover { filter: brightness(1.07); }
  .error { color: var(--down); font-size: 13px; min-height: 18px; margin-bottom: 8px; }
</style>
</head>
<body>
<div class="panel">
  <h1>High <span>Council</span></h1>
  <p>Enter the password to continue.</p>
  <form id="login-form">
    <input type="password" id="password" placeholder="Password" aria-label="Password" autofocus autocomplete="current-password" />
    <div class="error" id="error" role="alert"></div>
    <button type="submit">Sign in</button>
  </form>
</div>
<script>
  function safeNext(raw) {
    if (!raw || raw.indexOf('/') !== 0 || raw.indexOf('//') === 0) return '/';
    return raw;
  }
  document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const password = document.getElementById('password').value;
    const errorEl = document.getElementById('error');
    errorEl.textContent = '';
    try {
      const resp = await fetch('/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      if (!resp.ok) {
        errorEl.textContent = resp.status === 401 ? 'Incorrect password.' : 'Login failed.';
        return;
      }
      const params = new URLSearchParams(window.location.search);
      window.location.href = safeNext(params.get('next'));
    } catch (err) {
      errorEl.textContent = 'Network error -- try again.';
    }
  });
</script>
</body>
</html>"""
