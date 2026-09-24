"""API keys saved from the Settings screen: stored, layered over .env by
get_settings(), removable, and never readable back in full via the API."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from council import key_store
from council.config import Settings
from council.data.cache import DiskCache


def _settings(tmp_path, **overrides) -> Settings:
    return Settings(
        no_llm=True,
        use_data_fixtures=True,
        council_db_path=str(tmp_path / "council.db"),
        cache_db_path=str(tmp_path / "cache.db"),
        settings_db_path=str(tmp_path / "settings.db"),
        **overrides,
    )


def test_saved_key_overrides_env_and_removal_falls_back(tmp_path):
    settings = _settings(tmp_path, anthropic_api_key="sk-ant-from-env")
    db = settings.settings_db_path

    key_store.save_key(db, "anthropic_api_key", "  sk-ant-from-app\n")
    assert key_store.apply_saved_keys(settings).anthropic_api_key == "sk-ant-from-app"

    key_store.delete_key(db, "anthropic_api_key")
    assert key_store.apply_saved_keys(settings).anthropic_api_key == "sk-ant-from-env"


def test_a_saved_key_turns_off_sample_mode(tmp_path):
    settings = Settings(
        settings_db_path=str(tmp_path / "settings.db"),
        anthropic_api_key="", google_api_key="", groq_api_key="", openai_api_key="",
    )
    assert settings.resolved_no_llm and settings.resolved_use_data_fixtures

    key_store.save_key(settings.settings_db_path, "google_api_key", "AIza-free-key")
    effective = key_store.apply_saved_keys(settings)

    assert not effective.resolved_no_llm
    assert not effective.resolved_use_data_fixtures


def test_reading_keys_never_creates_the_settings_file(tmp_path):
    db = tmp_path / "nothing-here" / "settings.db"
    assert key_store.saved_keys(str(db)) == {}
    assert not db.exists()


@pytest.mark.parametrize("bad", ["", "   ", "sk-ant key with spaces", "x" * 501])
def test_bad_key_values_are_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        key_store.save_key(str(tmp_path / "settings.db"), "anthropic_api_key", bad)


def test_unknown_key_names_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        key_store.save_key(str(tmp_path / "settings.db"), "app_password", "hunter22")


def test_sample_and_live_cache_entries_never_mix(tmp_path):
    path = str(tmp_path / "cache.db")
    DiskCache(path, namespace="sample").set("ohlcv:NVDA:x:y", ["sample bar"], 3600)
    assert DiskCache(path).get("ohlcv:NVDA:x:y") is None
    assert DiskCache(path, namespace="sample").get("ohlcv:NVDA:x:y") == ["sample bar"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = _settings(tmp_path, fred_api_key="fred-from-env-9876")
    import council.api.main as main_module

    # Mirror config.get_settings(): the .env-style settings with saved keys layered on.
    monkeypatch.setattr(main_module, "get_settings", lambda: key_store.apply_saved_keys(settings))
    return TestClient(main_module.app)


def _by_name(response):
    return {k["name"]: k for k in response.json()["keys"]}


def test_keys_endpoint_reports_status_without_revealing_keys(client):
    secret = "sk-ant-api03-SUPERSECRETVALUE-wxyz"
    saved = client.post("/api/settings/keys", json={"name": "anthropic_api_key", "value": secret})
    assert saved.status_code == 200

    listed = client.get("/api/settings/keys")
    for response in (saved, listed):
        assert secret not in response.text
        keys = _by_name(response)
        assert keys["anthropic_api_key"] == {
            "name": "anthropic_api_key", "is_set": True, "source": "app", "last4": "wxyz",
        }
        assert keys["fred_api_key"]["source"] == "env"
        assert keys["openai_api_key"] == {
            "name": "openai_api_key", "is_set": False, "source": None, "last4": None,
        }


def test_removing_a_saved_key_reverts_to_env_or_unset(client):
    client.post("/api/settings/keys", json={"name": "fred_api_key", "value": "fred-from-app-1111"})
    assert _by_name(client.get("/api/settings/keys"))["fred_api_key"]["last4"] == "1111"

    removed = _by_name(client.delete("/api/settings/keys/fred_api_key"))
    assert removed["fred_api_key"] == {
        "name": "fred_api_key", "is_set": True, "source": "env", "last4": "9876",
    }


def test_keys_endpoint_rejects_bad_input(client):
    assert client.post("/api/settings/keys", json={"name": "app_password", "value": "x1234567"}).status_code == 400
    assert client.post("/api/settings/keys", json={"name": "fmp_api_key", "value": "gone1234"}).status_code == 400
    assert client.post("/api/settings/keys", json={"name": "groq_api_key", "value": "has a space"}).status_code == 400
    assert client.delete("/api/settings/keys/app_password").status_code == 400
