"""With only free AI keys, Free Mode is on by default and stays on."""
from __future__ import annotations

from council.tests.test_auth import _signup_and_approve, make_app, site  # noqa: F401

GOOGLE = "AIza" + "x" * 35


def test_saving_only_a_free_key_turns_free_mode_on(site):
    owner_client, settings = site
    friend, _ = _signup_and_approve(owner_client, settings)
    assert friend.get("/api/settings/free-mode").json()["enabled"] is False
    friend.post("/api/settings/keys", json={"name": "google_api_key", "value": GOOGLE})
    state = friend.get("/api/settings/free-mode").json()
    assert state["enabled"] is True and state["locked"] is True
    assert friend.get("/api/onboarding").json()["free_mode"] is True


def test_it_cant_be_turned_off_with_only_free_keys(site):
    owner_client, settings = site
    friend, _ = _signup_and_approve(owner_client, settings)
    friend.post("/api/settings/keys", json={"name": "google_api_key", "value": GOOGLE})
    resp = friend.post("/api/settings/free-mode", json={"enabled": False})
    assert resp.status_code == 400 and "free ones" in resp.json()["detail"]


def test_with_a_paid_key_too_it_can_be_turned_off(site):
    owner_client, settings = site
    friend, _ = _signup_and_approve(owner_client, settings)
    friend.post("/api/settings/keys", json={"name": "google_api_key", "value": GOOGLE})
    friend.post("/api/settings/keys", json={"name": "anthropic_api_key", "value": "sk-ant-" + "x" * 40})
    assert friend.get("/api/settings/free-mode").json()["locked"] is False
    off = friend.post("/api/settings/free-mode", json={"enabled": False}).json()
    assert off["enabled"] is False
    # Removing the paid key leaves only the free one: back on.
    friend.delete("/api/settings/keys/anthropic_api_key")
    assert friend.get("/api/settings/free-mode").json()["enabled"] is True


def test_an_account_from_before_gets_it_on_its_next_run(site):
    owner_client, settings = site
    friend, user = _signup_and_approve(owner_client, settings)
    account = user["id"]
    from council import key_store

    # Saved straight into the store, the way an older version left it: key, Free Mode off.
    key_store.save_key(settings.settings_db_path, "google_api_key", GOOGLE, account, settings.secret_key or None)
    from council.engine import model_settings

    conn = model_settings.connect(settings.settings_db_path)
    assert model_settings.free_mode_enabled(conn, account) is False
    conn.close()
    with friend.stream("GET", "/api/deliberate/stream", params={"ticker": "NVDA"}) as r:
        for _ in r.iter_lines():
            pass
    conn = model_settings.connect(settings.settings_db_path)
    assert model_settings.free_mode_enabled(conn, account) is True
    conn.close()


def test_no_keys_at_all_leaves_it_off(site):
    owner_client, settings = site
    friend, _ = _signup_and_approve(owner_client, settings)
    friend.get("/api/onboarding")
    assert friend.get("/api/settings/free-mode").json()["enabled"] is False
