"""Sharing a run by public link, and "What changed" since the same
ticker's previous run."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from council.api.run_views import compare_runs
from council.config import Settings
from council.tests.test_auth import _new_client, _signup_and_approve, make_app, site  # noqa: F401


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = Settings(
        no_llm=True,
        use_data_fixtures=True,
        council_db_path=str(tmp_path / "council.db"),
        cache_db_path=str(tmp_path / "cache.db"),
        settings_db_path=str(tmp_path / "settings.db"),
    )
    import council.api.main as main_module

    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    return TestClient(main_module.app), settings


def _run(test_client, ticker="NVDA", as_of="2026-08-01T16:00:00") -> str:
    run_id = None
    with test_client.stream("GET", "/api/deliberate/stream", params={"ticker": ticker, "as_of": as_of}) as resp:
        event = None
        for line in resp.iter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: ") and event == "phase_g_crypt_write":
                run_id = json.loads(line.removeprefix("data: "))["run_id"]
    assert run_id
    return run_id


def test_share_link_round_trip(client):
    test_client, _ = client
    run_id = _run(test_client)
    assert test_client.get(f"/api/runs/{run_id}/share").json() == {"shared": False, "url": None}

    made = test_client.post(f"/api/runs/{run_id}/share").json()
    assert made["shared"] and "/s/" in made["url"]
    # Sharing again keeps the same link.
    assert test_client.post(f"/api/runs/{run_id}/share").json()["url"] == made["url"]

    path = made["url"].split("://", 1)[1].split("/", 1)[1]
    page = test_client.get(f"/{path}")
    assert page.status_code == 200
    assert "NVDA" in page.text and "Which way it leans" in page.text
    assert 'property="og:image"' in page.text and "Not financial advice" in page.text

    assert test_client.delete(f"/api/runs/{run_id}/share").json()["shared"] is False
    assert test_client.get(f"/{path}").status_code == 404
    # A new share after stopping is a new link, so the old one stays dead.
    again = test_client.post(f"/api/runs/{run_id}/share").json()["url"]
    assert again != made["url"]


def test_unknown_share_link_is_404(client):
    test_client, _ = client
    assert test_client.get("/s/not-a-real-token").status_code == 404


def test_changes_compares_with_the_previous_run_of_the_ticker(client):
    test_client, _ = client
    first = _run(test_client, as_of="2026-08-01T16:00:00")
    assert test_client.get(f"/api/runs/{first}/changes").json()["previous"] is None

    _run(test_client, ticker="AAPL", as_of="2026-08-02T16:00:00")  # another ticker doesn't count
    second = _run(test_client, as_of="2026-08-03T16:00:00")
    changes = test_client.get(f"/api/runs/{second}/changes").json()
    assert changes["previous"]["run_id"] == first
    assert set(changes["terms"]) == {"short", "medium", "long"}
    # The sample council answers the same way each time: nothing flipped.
    assert changes["flips"] == [] and not any(t["flipped"] for t in changes["terms"].values())


def _fake_run(run_id, term_p, seat_votes):
    return {
        "run_id": run_id,
        "created_at": "2026-08-01T16:00:00",
        "synthesis": {"terms": {t: {"p_bullish": p, "seats_counted": 12} for t, p in term_p.items()}},
        "terms": {
            t: {"prediction": {}, "seat_votes": [{"seat_id": s, "verdict": v} for s, v in seat_votes.items()]}
            for t in term_p
        },
    }


def test_compare_runs_spots_flipped_terms_and_seats():
    before = _fake_run(
        "a", {"short": 0.53}, {"technician": {"vote": "BULLISH", "probability": 0.6},
                               "macro_sage": {"vote": "BEARISH", "probability": 0.55}},
    )
    after = _fake_run(
        "b", {"short": 0.47}, {"technician": {"vote": "BEARISH", "probability": 0.58},
                               "macro_sage": {"vote": "BEARISH", "probability": 0.6}},
    )
    diff = compare_runs(before, after)
    assert diff["terms"]["short"]["flipped"] and diff["terms"]["short"]["change"] == pytest.approx(-0.06)
    assert [f["seat_id"] for f in diff["flips"]] == ["technician"]


def test_shared_page_is_public_but_sharing_is_owner_only(site):
    owner_client, settings = site
    run_id = _run(owner_client)
    url = owner_client.post(f"/api/runs/{run_id}/share").json()["url"]
    path = "/" + url.split("://", 1)[1].split("/", 1)[1]

    stranger = _new_client(settings)
    page = stranger.get(path, follow_redirects=False)
    assert page.status_code == 200 and "NVDA" in page.text
    assert "Owner" not in page.text and "owner@example.com" not in page.text

    friend, _ = _signup_and_approve(owner_client, settings)
    assert friend.post(f"/api/runs/{run_id}/share").status_code == 404
    assert friend.delete(f"/api/runs/{run_id}/share").status_code == 404
    assert friend.get(f"/api/runs/{run_id}/changes").status_code == 404


def test_app_icons_and_manifest_load_before_sign_in(site):
    _, settings = site
    visitor = _new_client(settings)
    manifest = visitor.get("/manifest.webmanifest", follow_redirects=False)
    assert manifest.status_code == 200 and manifest.json()["display"] == "standalone"
    assert visitor.get("/icons/apple-touch-icon.png", follow_redirects=False).status_code == 200
    assert visitor.get("/terms", follow_redirects=False).status_code == 200
    assert 'rel="manifest"' in visitor.get("/welcome").text
