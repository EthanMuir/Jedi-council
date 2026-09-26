"""Accounts and sign-in (#114). With APP_PASSWORD unset the app is
single-user and none of this runs -- every local workflow and most tests
never know it exists. With it set:

- The very first visit goes to /setup, which creates the owner's account
  (proving ownership with APP_PASSWORD, once).
- After that, people sign in with their own email and password, or Google.
  Anyone can ask for an account at /signup; it waits for an admin's
  approval on the admin page.
- Every request carries the signed-in person on request.state.user, which
  the API uses to show and change only their own keys, models and runs.

Sessions are random tokens in an HttpOnly cookie, stored only as a hash.
Writes (POST/PUT/PATCH/DELETE) from another site are refused by checking
Origin, on top of SameSite=Lax cookies."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import urllib.parse
from collections import defaultdict

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from council import accounts, emailer, key_store, notify, reports, secrets_box, site_stats
from council.api import auth_pages
from council.config import get_settings
from council.engine import model_settings

COOKIE_NAME = "council_session"
# A second, deliberately non-HttpOnly cookie carrying no secret -- just a
# flag the UI can read via document.cookie to show "Log out".
LOGGED_IN_FLAG_COOKIE = "council_logged_in"
OAUTH_COOKIE = "council_oauth"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * accounts.SESSION_DAYS

_PUBLIC_PAGES = {"/welcome", "/login", "/signup", "/setup", "/pending", "/forgot", "/reset", "/privacy",
                 "/terms", "/logout", "/auth/google", "/auth/google/callback", "/favicon.svg",
                 "/manifest.webmanifest", "/health", "/unsubscribe"}
# App icons (for Add to Home Screen) and shared verdicts (/s/<token>) are
# open to anyone -- a share link is its own permission.
_PUBLIC_PREFIXES = ("/icons/", "/s/")
# Still reachable before the owner's account exists.
_BEFORE_SETUP = {"/setup", "/api/auth/setup", "/privacy", "/terms", "/favicon.svg", "/manifest.webmanifest",
                 "/health"}
# Signed-out visitors to the front door see the landing page, not sign-in.
_FRONT_DOOR = {"/", "/index.html"}
_PUBLIC_API = {"/api/auth/login", "/api/auth/signup", "/api/auth/setup", "/api/auth/forgot",
               "/api/auth/reset", "/api/me"}
_ADMIN_PREFIXES = ("/api/admin/", "/admin.html")
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_DUMMY_HASH = accounts.hash_password(secrets.token_urlsafe(16))
_GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"


def _safe_next_path(raw: str | None) -> str:
    """Only ever redirect somewhere on this same site -- `next` comes from
    a query param an attacker could craft into a shared link, so a bare
    "//evil.com" (protocol-relative) or "https://evil.com" must not pass."""
    if not raw or not raw.startswith("/") or raw.startswith("//") or raw.startswith("/\\"):
        return "/"
    return raw


def _conn():
    return accounts.connect(get_settings().settings_db_path)


def _server_secret(settings) -> str:
    return secrets_box.load_secret(settings.settings_db_path, settings.secret_key)


def site_url(request: Request) -> str:
    settings = get_settings()
    return (settings.public_url or str(request.base_url)).rstrip("/")


def google_enabled() -> bool:
    settings = get_settings()
    return bool(settings.google_client_id and settings.google_client_secret)


# ---- throttling sign-in attempts -------------------------------------------------------
# In memory: a restart forgets it, which is fine -- it's here to slow down
# password guessing, not to be a permanent record.
_FAILS: dict[str, list[float]] = defaultdict(list)
_FAIL_WINDOW = 15 * 60
_FAIL_LIMIT = 8


def _throttle_key(request: Request, email: str) -> str:
    client = request.client.host if request.client else "?"
    return f"{client}|{email.strip().lower()}"


def _check_throttle(key: str) -> None:
    now = time.time()
    _FAILS[key] = [t for t in _FAILS[key] if now - t < _FAIL_WINDOW]
    if len(_FAILS[key]) >= _FAIL_LIMIT:
        raise HTTPException(429, "Too many attempts. Wait 15 minutes and try again.")


def _record_fail(key: str) -> None:
    _FAILS[key].append(time.time())


# ---- sessions -------------------------------------------------------------------------


def _signed_in_response(response, conn, user: accounts.User):
    settings = get_settings()
    token = accounts.create_session(conn, user.id)
    for name, value, http_only in ((COOKIE_NAME, token, True), (LOGGED_IN_FLAG_COOKIE, "1", False)):
        response.set_cookie(
            name, value, max_age=SESSION_MAX_AGE_SECONDS, httponly=http_only,
            samesite="lax", secure=settings.resolved_cookie_secure,
        )
    return response


def _status_error(user: accounts.User) -> HTTPException | None:
    if user.status == "pending":
        return HTTPException(403, "Your account is still waiting for approval. You'll get an email when it's ready.")
    if user.status == "disabled":
        return HTTPException(403, "This account has been turned off. Contact the site's owner.")
    return None


async def _notify_owner_of_signup(request: Request, conn, user: accounts.User) -> None:
    owner = accounts.owner(conn)
    if owner:
        message = emailer.signup_alert(user.name, user.email, f"{site_url(request)}/admin.html")
        await emailer.send_message(get_settings(), owner.email, message)


# ---- routes -----------------------------------------------------------------------------

router = APIRouter()
log = logging.getLogger("council.auth")


class _LoginRequest(BaseModel):
    email: str = ""
    password: str


class _SignupRequest(BaseModel):
    name: str
    email: str
    password: str


class _SetupRequest(BaseModel):
    name: str
    email: str
    password: str
    site_password: str


class _ForgotRequest(BaseModel):
    email: str


class _ResetRequest(BaseModel):
    token: str
    password: str


def _auth_off() -> bool:
    return not get_settings().resolved_auth_enabled


def count_visit(request: Request, event: str) -> None:
    """The admin page's visitor counter (council/site_stats.py). Never lets
    a counting problem break the page."""
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() or (request.client.host if request.client else "")
    try:
        site_stats.record(
            get_settings().settings_db_path, event, ip=ip,
            user_agent=request.headers.get("user-agent", ""),
            referer=request.headers.get("referer", ""),
            own_host=request.url.hostname or "",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("couldn't count a visit: %s", exc)


@router.get("/welcome", response_class=HTMLResponse)
async def welcome_page(request: Request):
    if getattr(request.state, "user", None) is None:
        count_visit(request, "landing")
    base = get_settings().public_url.rstrip("/") or str(request.base_url).rstrip("/")
    return auth_pages.landing_page(
        google_enabled(), signed_in=getattr(request.state, "user", None) is not None, base_url=base
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(notice: str | None = None):
    notices = {"approved": "Your account is ready. Sign in below.", "reset": "Password changed. Sign in with your new one."}
    return auth_pages.login_page(google_enabled(), notices.get(notice or "", ""))


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    count_visit(request, "signup_form")
    return auth_pages.signup_page(google_enabled())


@router.get("/setup", response_class=HTMLResponse)
async def setup_page():
    conn = _conn()
    try:
        if accounts.count_users(conn):
            return RedirectResponse("/login")
    finally:
        conn.close()
    return auth_pages.setup_page()


@router.get("/pending", response_class=HTMLResponse)
async def pending_page():
    return auth_pages.pending_page()


@router.get("/forgot", response_class=HTMLResponse)
async def forgot_page():
    return auth_pages.forgot_page(emailer.email_configured(get_settings()))


@router.get("/reset", response_class=HTMLResponse)
async def reset_page(token: str = ""):
    conn = _conn()
    try:
        valid = accounts.reset_token_user(conn, token) is not None
    finally:
        conn.close()
    return auth_pages.reset_page(valid)


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_page():
    return auth_pages.privacy_page()


@router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(u: int = -1, t: str = ""):
    """The link at the bottom of every 'your call was scored' email."""
    settings = get_settings()
    if u < 0 or not notify.check_unsubscribe_token(settings, u, t):
        return HTMLResponse(auth_pages.notice_page(
            "Link not recognised", "This unsubscribe link isn't valid. You can turn these emails off "
            "in Settings -> Account instead."), status_code=400)
    notify.set_scored_emails(settings.settings_db_path, u, False)
    return auth_pages.notice_page(
        "Emails turned off", "You won't get 'your call was scored' emails any more. "
        "You can turn them back on in Settings -> Account.")


@router.get("/terms", response_class=HTMLResponse)
async def terms_page():
    return auth_pages.terms_page()


@router.post("/api/auth/login")
async def login_submit(body: _LoginRequest, request: Request):
    if _auth_off():
        raise HTTPException(400, "Sign-in isn't turned on (APP_PASSWORD not set).")
    key = _throttle_key(request, body.email)
    _check_throttle(key)
    conn = _conn()
    try:
        user = accounts.get_user_by_email(conn, body.email or "")
        # Checked against a throwaway hash when there's no such person, so
        # a wrong email takes as long as a wrong password.
        stored = accounts.password_hash(conn, user.id) if user else _DUMMY_HASH
        if not accounts.verify_password(body.password, stored) or user is None:
            _record_fail(key)
            raise HTTPException(401, "That email and password don't match.")
        if (err := _status_error(user)) is not None:
            raise err
        return _signed_in_response(JSONResponse({"ok": True}), conn, user)
    finally:
        conn.close()


@router.post("/api/auth/signup")
async def signup_submit(body: _SignupRequest, request: Request):
    if _auth_off():
        raise HTTPException(400, "Sign-in isn't turned on (APP_PASSWORD not set).")
    conn = _conn()
    try:
        if accounts.count_users(conn) == 0:
            raise HTTPException(409, "The site's owner hasn't set up their account yet.")
        try:
            user = accounts.create_user(conn, email=body.email, name=body.name, password=body.password)
        except accounts.AccountError as exc:
            raise HTTPException(400, str(exc)) from exc
        await _notify_owner_of_signup(request, conn, user)
        return {"ok": True, "status": "pending"}
    finally:
        conn.close()


@router.post("/api/auth/setup")
async def setup_submit(body: _SetupRequest, request: Request):
    settings = get_settings()
    if _auth_off():
        raise HTTPException(400, "Sign-in isn't turned on (APP_PASSWORD not set).")
    key = _throttle_key(request, "setup")
    _check_throttle(key)
    conn = _conn()
    try:
        if accounts.count_users(conn):
            raise HTTPException(409, "The owner's account already exists. Sign in instead.")
        if not hmac.compare_digest(body.site_password.encode(), settings.app_password.encode()):
            _record_fail(key)
            raise HTTPException(401, "That isn't the current site password.")
        try:
            user = accounts.create_user(
                conn, email=body.email, name=body.name, password=body.password,
                status="active", is_admin=True, is_owner=True,
            )
        except accounts.AccountError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _signed_in_response(JSONResponse({"ok": True}), conn, user)
    finally:
        conn.close()


@router.post("/api/auth/forgot")
async def forgot_submit(body: _ForgotRequest, request: Request):
    """Always the same answer, so it can't be used to find out who has an
    account."""
    settings = get_settings()
    key = _throttle_key(request, "forgot")
    _check_throttle(key)
    _record_fail(key)  # counts every request: a handful per 15 minutes is plenty
    conn = _conn()
    try:
        user = accounts.get_user_by_email(conn, body.email or "")
        if user and user.status == "active" and emailer.email_configured(settings):
            token = accounts.create_reset_token(conn, user.id)
            message = emailer.reset_message(
                user.name, f"{site_url(request)}/reset?token={token}", accounts.RESET_HOURS
            )
            await emailer.send_message(settings, user.email, message)
    finally:
        conn.close()
    return {"ok": True}


@router.post("/api/auth/reset")
async def reset_submit(body: _ResetRequest):
    conn = _conn()
    try:
        user = accounts.reset_token_user(conn, body.token)
        if user is None:
            raise HTTPException(400, "This link has expired. Ask for a new one.")
        try:
            accounts.set_password(conn, user.id, body.password)
        except accounts.AccountError as exc:
            raise HTTPException(400, str(exc)) from exc
        if (err := _status_error(user)) is not None:
            raise err
        return _signed_in_response(JSONResponse({"ok": True}), conn, user)
    finally:
        conn.close()


@router.get("/api/me")
async def me(request: Request):
    user = getattr(request.state, "user", None)
    pending = 0
    open_reports = 0
    if user is None and _auth_off():
        open_reports = reports.open_count(get_settings().settings_db_path)
    if user and user.is_admin:
        conn = _conn()
        try:
            pending = sum(1 for u in accounts.list_users(conn) if u.status == "pending")
        finally:
            conn.close()
        open_reports = reports.open_count(get_settings().settings_db_path)
    return {
        "auth": not _auth_off(),
        "user": user.public() if user else None,
        "pending_count": pending,
        "open_reports": open_reports,
        "email_enabled": emailer.email_configured(get_settings()),
    }


class _PasswordRequest(BaseModel):
    current_password: str = ""
    new_password: str


@router.post("/api/auth/password")
async def change_password(body: _PasswordRequest, request: Request):
    """Changing your own password. Someone who only ever used Google can
    add one without a current password."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "authentication required")
    conn = _conn()
    try:
        if user.has_password and not accounts.verify_password(body.current_password, accounts.password_hash(conn, user.id)):
            raise HTTPException(401, "Your current password isn't right.")
        try:
            accounts.set_password(conn, user.id, body.new_password)
        except accounts.AccountError as exc:
            raise HTTPException(400, str(exc)) from exc
        # set_password signed out every session, this one too: sign back in.
        return _signed_in_response(JSONResponse({"ok": True}), conn, user)
    finally:
        conn.close()


@router.get("/logout")
async def logout(request: Request):
    if not _auth_off():
        conn = _conn()
        try:
            accounts.delete_session(conn, request.cookies.get(COOKIE_NAME))
        finally:
            conn.close()
    response = RedirectResponse("/login")
    response.delete_cookie(COOKIE_NAME)
    response.delete_cookie(LOGGED_IN_FLAG_COOKIE)
    return response


# ---- Google sign-in ---------------------------------------------------------------------


def _sign(settings, value: str) -> str:
    mac = hmac.new(_server_secret(settings).encode(), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{mac}"


def _unsign(settings, signed: str | None) -> str | None:
    if not signed or "." not in signed:
        return None
    value, mac = signed.rsplit(".", 1)
    expected = hmac.new(_server_secret(settings).encode(), value.encode(), hashlib.sha256).hexdigest()
    return value if hmac.compare_digest(mac, expected) else None


def _redirect_uri(request: Request) -> str:
    return f"{site_url(request)}/auth/google/callback"


@router.get("/auth/google")
async def google_start(request: Request, next: str = "/"):
    settings = get_settings()
    if _auth_off() or not google_enabled():
        raise HTTPException(404, "Google sign-in isn't set up on this site.")
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": _redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    response = RedirectResponse(f"{_GOOGLE_AUTH}?{urllib.parse.urlencode(params)}")
    payload = base64.urlsafe_b64encode(json.dumps(
        {"state": state, "verifier": verifier, "next": _safe_next_path(next)}
    ).encode()).decode()
    response.set_cookie(
        OAUTH_COOKIE, _sign(settings, payload), max_age=600, httponly=True,
        samesite="lax", secure=settings.resolved_cookie_secure,
    )
    return response


def _id_token_claims(id_token: str) -> dict:
    """The claims of an ID token we just received straight from Google's
    token endpoint over HTTPS, in exchange for our own client secret --
    Google's docs allow skipping the signature check in exactly this case."""
    payload = id_token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


@router.get("/auth/google/callback")
async def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    settings = get_settings()
    if _auth_off() or not google_enabled():
        raise HTTPException(404, "Google sign-in isn't set up on this site.")
    raw = _unsign(settings, request.cookies.get(OAUTH_COOKIE))
    if error or not raw:
        return HTMLResponse(auth_pages.message_page("Sign-in cancelled", "Google sign-in didn't finish. Try again."), 400)
    saved = json.loads(base64.urlsafe_b64decode(raw))
    if not hmac.compare_digest(saved["state"], state) or not code:
        return HTMLResponse(auth_pages.message_page("Sign-in expired", "That sign-in link expired. Try again."), 400)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(_GOOGLE_TOKEN, data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": _redirect_uri(request),
                "grant_type": "authorization_code",
                "code_verifier": saved["verifier"],
            })
        resp.raise_for_status()
        claims = _id_token_claims(resp.json()["id_token"])
    except (httpx.HTTPError, KeyError, ValueError):
        return HTMLResponse(auth_pages.message_page("Google sign-in failed", "Google didn't confirm the sign-in. Try again."), 400)

    if (
        claims.get("aud") != settings.google_client_id
        or claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com")
        or claims.get("exp", 0) < time.time()
        or not claims.get("email_verified")
        or not claims.get("sub")
    ):
        return HTMLResponse(auth_pages.message_page("Google sign-in failed", "Google couldn't confirm that email address."), 400)

    conn = _conn()
    try:
        if accounts.count_users(conn) == 0:
            return RedirectResponse("/setup")
        user = accounts.get_user_by_google(conn, claims["sub"])
        if user is None:
            user = accounts.get_user_by_email(conn, claims["email"])
            if user is not None:
                accounts.link_google(conn, user.id, claims["sub"])
            else:
                try:
                    user = accounts.create_user(
                        conn, email=claims["email"], name=claims.get("name") or claims["email"].split("@")[0],
                        google_sub=claims["sub"],
                    )
                except accounts.AccountError:
                    return HTMLResponse(auth_pages.message_page("Couldn't sign up", "Something about that Google account didn't work here."), 400)
                await _notify_owner_of_signup(request, conn, user)
        if user.status == "pending":
            response = RedirectResponse("/pending")
        elif user.status == "disabled":
            response = HTMLResponse(auth_pages.message_page("Account turned off", "This account has been turned off. Contact the site's owner."), 403)
        else:
            response = _signed_in_response(RedirectResponse(saved["next"]), conn, user)
        response.delete_cookie(OAUTH_COOKIE)
        return response
    finally:
        conn.close()


# ---- the gate ---------------------------------------------------------------------------


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True  # same-origin fetches from older browsers, curl, tests
    return urllib.parse.urlparse(origin).netloc == request.headers.get("host")


async def _auth_gate(request: Request, call_next):
    request.state.user = None
    path = request.url.path
    if _auth_off():
        return await call_next(request)
    if request.method in _UNSAFE_METHODS and not _same_origin(request):
        return JSONResponse({"detail": "cross-site request refused"}, status_code=403)

    conn = _conn()
    try:
        if (accounts.count_users(conn) == 0 and path not in _BEFORE_SETUP
                and not path.startswith("/icons/")):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "the owner's account needs setting up first"}, status_code=401)
            return RedirectResponse("/setup", status_code=307)
        user = accounts.session_user(conn, request.cookies.get(COOKIE_NAME))
    finally:
        conn.close()
    request.state.user = user

    if path in _PUBLIC_PAGES or path in _PUBLIC_API or path.startswith(_PUBLIC_PREFIXES):
        return await call_next(request)
    if user is None:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        if path in _FRONT_DOOR:
            return RedirectResponse("/welcome", status_code=307)
        next_path = urllib.parse.quote(path, safe="")
        return RedirectResponse(f"/login?next={next_path}", status_code=307)
    if path.startswith(_ADMIN_PREFIXES) and not user.is_admin:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "admins only"}, status_code=403)
        return RedirectResponse("/", status_code=307)
    return await call_next(request)


def install_auth(app) -> None:
    app.include_router(router)
    app.add_middleware(BaseHTTPMiddleware, dispatch=_auth_gate)


# ---- used by the admin API ------------------------------------------------------------


def delete_account_data(settings_db_path: str, user: accounts.User) -> None:
    """Everything personal besides the Crypt rows: keys, models, Free Mode."""
    key_store.delete_account_keys(settings_db_path, user.account)
    conn = model_settings.connect(settings_db_path)
    try:
        model_settings.delete_account(conn, user.account)
    finally:
        conn.close()
