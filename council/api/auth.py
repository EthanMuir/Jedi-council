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
<title>The High Council -- Sign In</title>
<style>
  :root {
    --navy: #0a0e1a; --navy-deep: #05070d; --slate: #1a2030;
    --bevel-hi: rgba(255,255,255,0.10); --bevel-lo: rgba(0,0,0,0.55);
    --cyan: #4fd6e8; --cyan-dim: #2a6b75; --bone: #e8e4d8; --bone-dim: #8b8779;
    --crimson: #d6453f;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; min-height: 100vh; background: var(--navy);
    color: var(--bone); font-family: ui-monospace, 'SF Mono', Consolas, monospace;
    display: flex; align-items: center; justify-content: center;
  }
  .panel {
    background: var(--slate); border: 2px solid #2a3348; border-radius: 2px;
    box-shadow: inset 2px 2px 0 var(--bevel-hi), inset -2px -2px 0 var(--bevel-lo),
      4px 4px 0 rgba(0,0,0,0.35);
    padding: 32px; width: 320px;
  }
  h1 {
    font-family: 'Courier New', monospace; font-size: 15px; letter-spacing: 0.04em;
    text-transform: uppercase; color: var(--bone); margin: 0 0 20px; text-align: center;
  }
  input[type="password"] {
    width: 100%; font-family: inherit; font-size: 14px; background: var(--navy-deep);
    border: 2px solid #131722; box-shadow: inset 2px 2px 0 rgba(0,0,0,0.6);
    color: var(--bone); padding: 10px 12px; margin-bottom: 14px;
  }
  input[type="password"]:focus { outline: none; border-color: var(--cyan-dim); }
  button {
    width: 100%; font-family: inherit; font-size: 13px; text-transform: uppercase;
    background: #242c42; color: var(--bone); border: 2px solid #3a4560;
    box-shadow: inset 1px 1px 0 var(--bevel-hi), inset -1px -1px 0 var(--bevel-lo);
    padding: 10px; cursor: pointer;
  }
  button:hover { color: var(--cyan); border-color: var(--cyan-dim); }
  .error { color: var(--crimson); font-size: 12px; min-height: 16px; margin-bottom: 10px; }
</style>
</head>
<body>
<div class="panel">
  <h1>The High Council</h1>
  <form id="login-form">
    <input type="password" id="password" placeholder="Password" autofocus autocomplete="current-password" />
    <div class="error" id="error"></div>
    <button type="submit">Enter</button>
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
