"""First sign-in: the welcome tour and Getting started checklist (#125)."""
from __future__ import annotations

from council.tests.test_auth import _new_client, _signup_and_approve, make_app, site  # noqa: F401


def test_a_new_account_starts_with_nothing_done(site):
    owner_client, settings = site
    friend, _ = _signup_and_approve(owner_client, settings)
    state = friend.get("/api/onboarding").json()
    assert state["tour_done"] is False and state["checklist_hidden"] is False
    assert state["steps"] == {"key": False, "run": False, "seat": False, "home": False}


def test_progress_is_saved_per_account(site):
    owner_client, settings = site
    friend, _ = _signup_and_approve(owner_client, settings)
    after = friend.post("/api/onboarding", json={"tour_done": True, "seat_opened": True}).json()
    assert after["tour_done"] and after["steps"]["seat"] and not after["steps"]["home"]
    # Someone else's state is untouched.
    assert owner_client.get("/api/onboarding").json()["tour_done"] is False
    friend.post("/api/onboarding", json={"home_screen": True, "checklist_hidden": True})
    again = friend.get("/api/onboarding").json()
    assert again["steps"]["home"] and again["checklist_hidden"]


def test_key_and_run_steps_come_from_real_state(site):
    owner_client, settings = site
    # The owner has an AI key in .env (see the site fixture), so that's done.
    assert owner_client.get("/api/onboarding").json()["steps"]["key"] is True
    assert owner_client.get("/api/onboarding").json()["steps"]["run"] is False
    with owner_client.stream("GET", "/api/deliberate/stream", params={"ticker": "NVDA"}) as r:
        for _ in r.iter_lines():
            pass
    assert owner_client.get("/api/onboarding").json()["steps"]["run"] is True


def test_onboarding_needs_sign_in(site):
    _, settings = site
    assert _new_client(settings).get("/api/onboarding").status_code == 401


def test_setup_shows_for_new_accounts_and_for_anyone_without_a_key(site):
    owner_client, settings = site
    friend, _ = _signup_and_approve(owner_client, settings)
    assert friend.get("/api/onboarding").json()["show_setup"] is True
    # Went through the old tour but never added a key: still shown, once.
    assert friend.post("/api/onboarding", json={"tour_done": True}).json()["show_setup"] is True
    done = friend.post("/api/onboarding", json={"setup_done": True}).json()
    assert done["setup_done"] is True and done["show_setup"] is False
    # The owner has an AI key and, once past the tour, isn't asked again.
    assert owner_client.post("/api/onboarding", json={"tour_done": True}).json()["show_setup"] is False


def test_onboarding_says_which_keys_are_set(site, monkeypatch):
    owner_client, settings = site
    friend, _ = _signup_and_approve(owner_client, settings)
    keys = friend.get("/api/onboarding").json()["keys"]
    assert keys["fred_api_key"] is False and keys["google_api_key"] is False
    friend.post("/api/settings/keys", json={"name": "fred_api_key", "value": "a" * 32})
    assert friend.get("/api/onboarding").json()["keys"]["fred_api_key"] is True


def test_an_older_onboarding_table_gets_the_setup_column(tmp_path):
    import sqlite3

    from council import onboarding

    db = str(tmp_path / "settings.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE onboarding (account INTEGER PRIMARY KEY, tour_done INTEGER NOT NULL DEFAULT 0, "
        "checklist_hidden INTEGER NOT NULL DEFAULT 0, seat_opened INTEGER NOT NULL DEFAULT 0, "
        "home_screen INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute("INSERT INTO onboarding (account, tour_done) VALUES (5, 1)")
    conn.commit()
    conn.close()
    assert onboarding.get(db, 5) == {
        "tour_done": True, "setup_done": False, "checklist_hidden": False, "seat_opened": False, "home_screen": False,
    }
    assert onboarding.update(db, 5, {"setup_done": True})["setup_done"] is True
