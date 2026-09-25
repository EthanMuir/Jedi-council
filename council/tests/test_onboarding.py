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
