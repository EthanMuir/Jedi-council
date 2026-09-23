"""Settings must survive a .env with NO_LLM= and USE_DATA_FIXTURES= present
but blank -- exactly what .env.example ships, on purpose, to mean
"auto-detect". A real user hit this as a ValidationError on first setup:
pydantic-settings hands an env var that's present-but-empty to the field as
the literal string "", which fails bool parsing outright instead of
falling back to the field's None default."""
from __future__ import annotations

import pytest

from council.config import Settings


@pytest.mark.parametrize("field", ["no_llm", "use_data_fixtures", "cookie_secure"])
def test_blank_env_value_resolves_to_none(monkeypatch, field):
    monkeypatch.setenv(field.upper(), "")
    settings = Settings(_env_file=None)
    assert getattr(settings, field) is None


def test_resolved_no_llm_auto_detects_with_blank_env(monkeypatch):
    monkeypatch.setenv("NO_LLM", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    settings = Settings(_env_file=None)
    assert settings.resolved_no_llm is True


def test_explicit_bool_env_value_still_respected(monkeypatch):
    monkeypatch.setenv("NO_LLM", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    settings = Settings(_env_file=None)
    assert settings.no_llm is False
    assert settings.resolved_no_llm is False


# --- Task #77: alpha_vantage_api_key no longer affects fixture auto-detect ---


def test_alpha_vantage_key_alone_does_not_escape_fixture_auto_detect():
    # AV was pulled from the live provider chain entirely -- its key's
    # presence no longer means a live run is possible, so it must not
    # affect this auto-detect the way it used to.
    settings = Settings(alpha_vantage_api_key="test-av-key", fmp_api_key="")
    assert settings.resolved_use_data_fixtures is True


def test_fmp_key_alone_escapes_fixture_auto_detect():
    settings = Settings(alpha_vantage_api_key="", fmp_api_key="test-fmp-key")
    assert settings.resolved_use_data_fixtures is False


def test_no_keys_at_all_defaults_to_fixture_mode():
    settings = Settings(alpha_vantage_api_key="", fmp_api_key="")
    assert settings.resolved_use_data_fixtures is True


# --- Task #78: auth settings ------------------------------------------------


def test_auth_disabled_by_default():
    settings = Settings(app_password="")
    assert settings.resolved_auth_enabled is False


def test_auth_enabled_when_password_set():
    settings = Settings(app_password="hunter2")
    assert settings.resolved_auth_enabled is True


def test_cookie_secure_defaults_false_when_unset(monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "")
    settings = Settings(_env_file=None)
    assert settings.resolved_cookie_secure is False


def test_cookie_secure_explicit_true_respected():
    settings = Settings(cookie_secure=True)
    assert settings.resolved_cookie_secure is True


def test_session_secret_is_stable_and_password_dependent():
    a = Settings(app_password="hunter2")
    b = Settings(app_password="hunter2")
    c = Settings(app_password="different")
    assert a.resolved_session_secret == b.resolved_session_secret
    assert a.resolved_session_secret != c.resolved_session_secret
