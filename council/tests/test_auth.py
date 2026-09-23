"""Task #78 -- the shared-password gate. Unit tests for the cookie sign/
verify primitives plus end-to-end TestClient coverage of the middleware
and /login, /logout routes. With APP_PASSWORD unset, auth must be a
complete no-op -- every prior local-only workflow (including every other
test in this suite) never sets it and must keep working exactly as before."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from council.api.auth import SESSION_MAX_AGE_SECONDS, sign_session, verify_session
from council.config import Settings

# --- sign_session / verify_session ----------------------------------------


def test_valid_session_round_trips():
    token = sign_session("secret", issued_at=1_000_000)
    assert verify_session(token, "secret", now=1_000_100) is True


def test_wrong_secret_is_rejected():
    token = sign_session("secret", issued_at=1_000_000)
    assert verify_session(token, "wrong-secret", now=1_000_100) is False


def test_tampered_signature_is_rejected():
    token = sign_session("secret", issued_at=1_000_000)
    issued_at, _sig = token.split(".", 1)
    tampered = f"{issued_at}.deadbeef"
    assert verify_session(tampered, "secret", now=1_000_100) is False


def test_expired_session_is_rejected():
    token = sign_session("secret", issued_at=1_000_000)
    just_expired = 1_000_000 + SESSION_MAX_AGE_SECONDS + 1
    assert verify_session(token, "secret", now=just_expired) is False


def test_malformed_cookie_value_is_rejected():
    assert verify_session("not-a-valid-token", "secret") is False
    assert verify_session("", "secret") is False


# --- end-to-end through the app --------------------------------------------


@pytest.fixture
def app_with_settings(tmp_path, monkeypatch):
    def _make(**overrides):
        settings = Settings(
            no_llm=True,
            use_data_fixtures=True,
            council_db_path=str(tmp_path / "council.db"),
            cache_db_path=str(tmp_path / "cache.db"),
            settings_db_path=str(tmp_path / "settings.db"),
            **overrides,
        )
        import council.api.auth as auth_module
        import council.api.main as main_module

        monkeypatch.setattr(main_module, "get_settings", lambda: settings)
        monkeypatch.setattr(auth_module, "get_settings", lambda: settings)
        return TestClient(main_module.app), settings

    return _make


def test_no_password_set_means_no_auth_required(app_with_settings):
    client, _settings = app_with_settings(app_password="")
    response = client.get("/api/archives")
    assert response.status_code == 200


def test_protected_api_route_401s_without_a_session(app_with_settings):
    client, _settings = app_with_settings(app_password="hunter2")
    response = client.get("/api/archives")
    assert response.status_code == 401


def test_protected_page_redirects_to_login_without_a_session(app_with_settings):
    client, _settings = app_with_settings(app_password="hunter2")
    response = client.get("/index.html", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].startswith("/login")


def test_login_page_itself_is_always_reachable(app_with_settings):
    client, _settings = app_with_settings(app_password="hunter2")
    response = client.get("/login")
    assert response.status_code == 200
    assert "password" in response.text.lower()


def test_wrong_password_is_rejected(app_with_settings):
    client, _settings = app_with_settings(app_password="hunter2")
    response = client.post("/login", json={"password": "wrong"})
    assert response.status_code == 401
    assert "council_session" not in response.cookies


def test_correct_password_grants_a_working_session(app_with_settings):
    client, _settings = app_with_settings(app_password="hunter2")
    login_response = client.post("/login", json={"password": "hunter2"})
    assert login_response.status_code == 200
    assert "council_session" in login_response.cookies
    # a second, non-HttpOnly flag cookie the UI can read to show Logout --
    # the real session cookie is HttpOnly and deliberately invisible to JS.
    assert "council_logged_in" in login_response.cookies

    # the same client now carries the cookie -- a previously-401ing route works
    response = client.get("/api/archives")
    assert response.status_code == 200


def test_logout_clears_the_session(app_with_settings):
    client, _settings = app_with_settings(app_password="hunter2")
    client.post("/login", json={"password": "hunter2"})
    assert client.get("/api/archives").status_code == 200

    client.get("/logout", follow_redirects=False)

    assert client.get("/api/archives").status_code == 401


def test_login_post_400s_when_auth_is_not_enabled(app_with_settings):
    client, _settings = app_with_settings(app_password="")
    response = client.post("/login", json={"password": "anything"})
    assert response.status_code == 400
