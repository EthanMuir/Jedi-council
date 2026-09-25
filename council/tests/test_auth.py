"""Accounts (#114): owner setup, sign-up and approval, sign-in, sessions,
password resets, Google sign-in, the admin-only gate -- and above all that
one person can never see or use another's runs or keys. With APP_PASSWORD
unset, none of it runs: the app stays single-user exactly as before."""
from __future__ import annotations

import base64
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from council import accounts, key_store
from council.config import Settings

OWNER = {"name": "Ethan", "email": "owner@example.com", "password": "owner-password-1"}
SITE_PASSWORD = "hunter2-site"


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    def _make(**overrides):
        settings = Settings(
            no_llm=True,
            use_data_fixtures=True,
            council_db_path=str(tmp_path / "council.db"),
            cache_db_path=str(tmp_path / "cache.db"),
            settings_db_path=str(tmp_path / "settings.db"),
            **overrides,
        )
        import council.api.admin as admin_module
        import council.api.auth as auth_module
        import council.api.main as main_module
        import council.api.reports as reports_module

        for module in (main_module, auth_module, admin_module, reports_module):
            monkeypatch.setattr(module, "get_settings", lambda: settings)
        auth_module._FAILS.clear()
        return TestClient(main_module.app), settings

    return _make


@pytest.fixture
def site(make_app):
    """Sign-in on, the owner's account set up, the owner signed in."""
    client, settings = make_app(app_password=SITE_PASSWORD, anthropic_api_key="sk-ant-owners-env-key")
    resp = client.post("/api/auth/setup", json={**OWNER, "site_password": SITE_PASSWORD})
    assert resp.status_code == 200, resp.text
    return client, settings


def _new_client(settings) -> TestClient:
    import council.api.main as main_module

    return TestClient(main_module.app)


def _signup_and_approve(owner_client, settings, email="friend@example.com", password="friend-password-1"):
    other = _new_client(settings)
    assert other.post("/api/auth/signup", json={"name": "Friend", "email": email, "password": password}).status_code == 200
    users = owner_client.get("/api/admin/users").json()["users"]
    friend = next(u for u in users if u["email"] == email)
    assert owner_client.post(f"/api/admin/users/{friend['id']}/status", json={"status": "active"}).status_code == 200
    assert other.post("/api/auth/login", json={"email": email, "password": password}).status_code == 200
    return other, friend


def _stream(client, **params):
    events, event = [], None
    with client.stream("GET", "/api/deliberate/stream", params=params) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                events.append((event, json.loads(line.removeprefix("data: "))))
    return events


# ---- passwords and tokens ------------------------------------------------------------


def test_passwords_are_hashed_and_checked():
    stored = accounts.hash_password("correct horse battery")
    assert "correct horse" not in stored
    assert accounts.verify_password("correct horse battery", stored)
    assert not accounts.verify_password("wrong", stored)
    assert not accounts.verify_password("anything", None)
    assert not accounts.verify_password("anything", "garbage")


def test_short_passwords_and_bad_emails_are_refused(tmp_path):
    conn = accounts.connect(str(tmp_path / "s.db"))
    with pytest.raises(accounts.AccountError):
        accounts.create_user(conn, email="a@b.co", name="A", password="short")
    with pytest.raises(accounts.AccountError):
        accounts.create_user(conn, email="not-an-email", name="A", password="long-enough-pw")


def test_sessions_are_stored_only_as_hashes(tmp_path):
    db = str(tmp_path / "s.db")
    conn = accounts.connect(db)
    user = accounts.create_user(conn, email="a@b.co", name="A", password="long-enough-pw", status="active")
    token = accounts.create_session(conn, user.id)
    assert accounts.session_user(conn, token).id == user.id
    assert token not in open(db, "rb").read().decode("latin-1")
    accounts.set_status(conn, user.id, "disabled")
    assert accounts.session_user(conn, token) is None


# ---- sign-in off: single-user, as before ---------------------------------------------------


def test_no_password_set_means_no_auth_required(make_app):
    client, _ = make_app(app_password="")
    assert client.get("/api/archives").status_code == 200
    assert client.get("/api/me").json() == {"auth": False, "user": None, "pending_count": 0, "open_reports": 0, "email_enabled": False}


def test_login_400s_when_auth_is_not_enabled(make_app):
    client, _ = make_app(app_password="")
    assert client.post("/api/auth/login", json={"email": "a@b.co", "password": "x"}).status_code == 400


# ---- owner setup ---------------------------------------------------------------------------


def test_first_visit_goes_to_setup(make_app):
    client, _ = make_app(app_password=SITE_PASSWORD)
    resp = client.get("/index.html", follow_redirects=False)
    assert resp.status_code == 307 and resp.headers["location"] == "/setup"
    assert client.get("/api/archives").status_code == 401
    assert "Set up your account" in client.get("/setup").text


def test_setup_needs_the_site_password_and_only_happens_once(make_app):
    client, _ = make_app(app_password=SITE_PASSWORD)
    assert client.post("/api/auth/setup", json={**OWNER, "site_password": "wrong"}).status_code == 401
    assert client.post("/api/auth/setup", json={**OWNER, "site_password": SITE_PASSWORD}).status_code == 200
    me = client.get("/api/me").json()["user"]
    assert me["is_owner"] and me["is_admin"] and me["status"] == "active"
    again = _new_client(None).post("/api/auth/setup", json={**OWNER, "email": "x@y.co", "site_password": SITE_PASSWORD})
    assert again.status_code == 409


# ---- sign-up, approval, sign-in ------------------------------------------------------------


def test_signups_wait_for_approval(site):
    owner_client, settings = site
    other = _new_client(settings)
    resp = other.post("/api/auth/signup", json={"name": "Friend", "email": "friend@example.com", "password": "friend-password-1"})
    assert resp.json()["status"] == "pending"
    login = other.post("/api/auth/login", json={"email": "friend@example.com", "password": "friend-password-1"})
    assert login.status_code == 403 and "waiting for approval" in login.json()["detail"]

    friend = next(u for u in owner_client.get("/api/admin/users").json()["users"] if u["email"] == "friend@example.com")
    owner_client.post(f"/api/admin/users/{friend['id']}/status", json={"status": "active"})
    assert other.post("/api/auth/login", json={"email": "friend@example.com", "password": "friend-password-1"}).status_code == 200
    assert other.get("/api/me").json()["user"]["is_admin"] is False


def test_wrong_password_and_throttling(site):
    _, settings = site
    client = _new_client(settings)
    for _ in range(8):
        assert client.post("/api/auth/login", json={"email": OWNER["email"], "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]}).status_code == 429


def test_logout_ends_the_session(site):
    client, _ = site
    assert client.get("/api/archives").status_code == 200
    client.get("/logout", follow_redirects=False)
    assert client.get("/api/archives").status_code == 401


def test_disabling_someone_signs_them_out(site):
    owner_client, settings = site
    other, friend = _signup_and_approve(owner_client, settings)
    assert other.get("/api/archives").status_code == 200
    owner_client.post(f"/api/admin/users/{friend['id']}/status", json={"status": "disabled"})
    assert other.get("/api/archives").status_code == 401


def test_only_admins_reach_the_admin_page(site):
    owner_client, settings = site
    other, _ = _signup_and_approve(owner_client, settings)
    assert other.get("/api/admin/users").status_code == 403
    assert other.get("/admin.html", follow_redirects=False).status_code == 307
    assert owner_client.get("/api/admin/users").status_code == 200


def test_the_owner_cannot_be_changed_or_removed(site):
    owner_client, _ = site
    owner = owner_client.get("/api/me").json()["user"]
    assert owner_client.post(f"/api/admin/users/{owner['id']}/status", json={"status": "disabled"}).status_code == 400
    assert owner_client.delete(f"/api/admin/users/{owner['id']}").status_code == 400


def test_cross_site_writes_are_refused(site):
    client, _ = site
    resp = client.post("/api/settings/free-mode", json={"enabled": False}, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_password_reset_link_from_the_admin_page(site):
    owner_client, settings = site
    other, friend = _signup_and_approve(owner_client, settings)
    link = owner_client.post(f"/api/admin/users/{friend['id']}/reset-link").json()["link"]
    token = link.split("token=")[1]
    fresh = _new_client(settings)
    assert fresh.post("/api/auth/reset", json={"token": token, "password": "brand-new-password"}).status_code == 200
    # the link works once, and old sessions and the old password are gone
    assert fresh.post("/api/auth/reset", json={"token": token, "password": "another-password-9"}).status_code == 400
    assert other.get("/api/archives").status_code == 401
    assert fresh.post("/api/auth/login", json={"email": "friend@example.com", "password": "friend-password-1"}).status_code == 401


def test_forgot_password_never_reveals_who_has_an_account(site):
    _, settings = site
    client = _new_client(settings)
    a = client.post("/api/auth/forgot", json={"email": OWNER["email"]})
    b = client.post("/api/auth/forgot", json={"email": "nobody@example.com"})
    assert a.status_code == b.status_code == 200 and a.json() == b.json()


# ---- privacy between people ----------------------------------------------------------------


def test_runs_are_private_to_their_owner(site):
    owner_client, settings = site
    other, _ = _signup_and_approve(owner_client, settings)

    written = next(p for e, p in _stream(other, ticker="NVDA") if e == "phase_g_crypt_write")
    run_id = written["run_id"]
    assert [r["run_id"] for r in other.get("/api/predictions").json()["runs"]] == [run_id]
    assert other.get(f"/api/runs/{run_id}").status_code == 200

    # The owner's own History doesn't include it...
    assert owner_client.get("/api/predictions").json()["runs"] == []
    # ...but the admin can open it, and it's in the Master Crypt.
    assert owner_client.get(f"/api/runs/{run_id}").status_code == 200
    master = owner_client.get("/api/admin/runs").json()["runs"]
    assert master[0]["run_id"] == run_id and master[0]["person"]["email"] == "friend@example.com"

    # A third person can't open it even with the id.
    third, _ = _signup_and_approve(owner_client, settings, email="third@example.com")
    assert third.get(f"/api/runs/{run_id}").status_code == 404
    pid = written["prediction_ids"]["short"]
    assert third.get(f"/api/predictions/{pid}").status_code == 404


def test_keys_are_per_person_encrypted_and_never_shared(site):
    owner_client, settings = site
    other, friend = _signup_and_approve(owner_client, settings)

    # The owner sees the .env key; nobody else does.
    owner_keys = {k["name"]: k for k in owner_client.get("/api/settings/keys").json()["keys"]}
    other_keys = {k["name"]: k for k in other.get("/api/settings/keys").json()["keys"]}
    assert owner_keys["anthropic_api_key"]["is_set"] and owner_keys["anthropic_api_key"]["source"] == "env"
    assert not other_keys["anthropic_api_key"]["is_set"]

    other.post("/api/settings/keys", json={"name": "google_api_key", "value": "AIza-friends-own-key"})
    other_keys = {k["name"]: k for k in other.get("/api/settings/keys").json()["keys"]}
    owner_keys = {k["name"]: k for k in owner_client.get("/api/settings/keys").json()["keys"]}
    assert other_keys["google_api_key"]["is_set"] and not owner_keys["google_api_key"]["is_set"]

    # Stored encrypted, not in plain text.
    raw = sqlite3.connect(settings.settings_db_path).execute("SELECT value_enc FROM account_api_keys").fetchall()
    assert raw and all("AIza-friends-own-key" not in r[0] for r in raw)
    assert key_store.saved_keys(settings.settings_db_path, friend["id"]) == {"google_api_key": "AIza-friends-own-key"}


def test_free_mode_and_models_are_per_person(site):
    owner_client, settings = site
    other, _ = _signup_and_approve(owner_client, settings)
    other.post("/api/settings/keys", json={"name": "google_api_key", "value": "AIza-friends-own-key"})
    assert other.post("/api/settings/free-mode", json={"enabled": True}).json()["enabled"] is True
    assert owner_client.get("/api/settings/free-mode").json()["enabled"] is False


def test_new_people_get_sample_answers_until_they_add_a_key(site):
    owner_client, settings = site
    other, _ = _signup_and_approve(owner_client, settings)
    settings.no_llm = None  # as on a real server: decided by which keys exist
    mode = next(p for e, p in _stream(other, ticker="NVDA") if e == "mode")
    assert mode["run_mode"] == "sample"


# ---- Google sign-in -------------------------------------------------------------------------


def test_google_button_hidden_until_configured(site):
    _, settings = site
    client = _new_client(settings)
    assert "Continue with Google" not in client.get("/login").text
    assert client.get("/auth/google", follow_redirects=False).status_code == 404


def _fake_id_token(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


def test_google_sign_in_creates_a_pending_account(make_app, monkeypatch):
    client, settings = make_app(app_password=SITE_PASSWORD, google_client_id="cid", google_client_secret="csecret")
    client.post("/api/auth/setup", json={**OWNER, "site_password": SITE_PASSWORD})
    import council.api.auth as auth_module

    claims = {"aud": "cid", "iss": "https://accounts.google.com", "exp": time.time() + 600,
              "email": "gpal@example.com", "email_verified": True, "sub": "google-123", "name": "G Pal"}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id_token": _fake_id_token(claims)}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, **k):
            assert data["code_verifier"] and data["client_secret"] == "csecret"
            return _Resp()

    monkeypatch.setattr(auth_module.httpx, "AsyncClient", _Client)
    visitor = _new_client(settings)
    start = visitor.get("/auth/google", follow_redirects=False)
    assert start.status_code == 307 and "accounts.google.com" in start.headers["location"]
    state = dict(p.split("=", 1) for p in start.headers["location"].split("?", 1)[1].split("&"))["state"]

    # A forged state is refused.
    assert visitor.get("/auth/google/callback", params={"code": "c", "state": "forged"}).status_code == 400
    done = visitor.get("/auth/google/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert done.headers["location"] == "/pending"
    users = client.get("/api/admin/users").json()["users"]
    pal = next(u for u in users if u["email"] == "gpal@example.com")
    assert pal["status"] == "pending" and pal["google_linked"]


# ---- landing page, own password, weights from the admin page ---------------------------------


def test_signed_out_visitors_see_the_landing_page(site):
    _, settings = site
    visitor = _new_client(settings)
    resp = visitor.get("/", follow_redirects=False)
    assert resp.status_code == 307 and resp.headers["location"] == "/welcome"
    page = visitor.get("/welcome").text
    assert "Request access" in page and "Not financial advice" in page
    # Other pages still ask you to sign in.
    assert visitor.get("/history.html", follow_redirects=False).headers["location"].startswith("/login")


def test_changing_your_own_password(site):
    client, _ = site
    wrong = client.post("/api/auth/password", json={"current_password": "nope", "new_password": "a-new-password-1"})
    assert wrong.status_code == 401
    ok = client.post("/api/auth/password", json={"current_password": OWNER["password"], "new_password": "a-new-password-1"})
    assert ok.status_code == 200
    assert client.get("/api/archives").status_code == 200  # still signed in here


def test_admin_sees_pending_count(site):
    owner_client, settings = site
    _new_client(settings).post("/api/auth/signup", json={"name": "P", "email": "p@example.com", "password": "p-password-123"})
    assert owner_client.get("/api/me").json()["pending_count"] == 1


def test_weights_draft_then_publish_from_the_admin_page(site):
    owner_client, settings = site
    assert owner_client.get("/api/admin/weights").json()["live"] == {"paid": None, "free": None}
    draft = owner_client.post("/api/admin/weights/candidate", json={"tier": "paid", "note": "first"}).json()
    release_id = draft["release"]["id"]
    assert draft["release"]["status"] == "draft"
    assert draft["changes"]["short"]["technician"] == {"live": 1.0, "new": 1.0, "change": 0.0}
    assert owner_client.get("/api/admin/weights").json()["live"]["paid"] is None  # drafts aren't live
    assert owner_client.post(f"/api/admin/weights/{release_id}/publish").status_code == 200
    overview = owner_client.get("/api/admin/weights").json()
    assert overview["live"]["paid"]["id"] == release_id and overview["live"]["free"] is None
    assert owner_client.get("/api/archives?mode=paid").json()["release"]["id"] == release_id


def test_the_tab_icon_loads_before_signing_in(site):
    _, settings = site
    resp = _new_client(settings).get("/favicon.svg")
    assert resp.status_code == 200 and "<svg" in resp.text
    assert 'rel="icon"' in _new_client(settings).get("/welcome").text
