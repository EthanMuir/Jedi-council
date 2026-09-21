"""API tests. Each test overrides council.api.main's settings dependency
via monkeypatching get_settings, so the Crypt lives in a tmp_path db and
tests never share state."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from council.config import Settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = Settings(
        no_llm=True,
        use_data_fixtures=True,
        council_db_path=str(tmp_path / "council.db"),
        cache_db_path=str(tmp_path / "cache.db"),
    )
    import council.api.main as main_module

    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    return TestClient(main_module.app), settings


def test_deliberate_stream_emits_seat_results_then_done(client):
    test_client, _settings = client
    with test_client.stream(
        "GET", "/api/deliberate/stream", params={"ticker": "NVDA", "horizon": "1w"}
    ) as response:
        assert response.status_code == 200
        events = []
        for line in response.iter_lines():
            if line.startswith("event: "):
                events.append(line.removeprefix("event: "))
    assert "seat_result" in events
    assert "phase_f_synthesis" in events
    assert events[-1] == "done"


def test_deliberate_stream_emits_mode_first_and_it_is_fixture(client):
    # The `client` fixture runs with no_llm=True -- the very first event
    # must say so, so a UI (or a human watching curl) can see whether a
    # run is about to spend real money before any seat call happens. This
    # is the fix for a real deliberation billing real money despite the
    # user believing NO_LLM=true made it free: nothing in the stream said
    # otherwise until the bill did.
    test_client, _settings = client
    with test_client.stream(
        "GET", "/api/deliberate/stream", params={"ticker": "NVDA", "horizon": "1w"}
    ) as response:
        lines = list(response.iter_lines())

    first_event = next(l for l in lines if l.startswith("event: ")).removeprefix("event: ")
    assert first_event == "mode"
    first_data = next(l for l in lines if l.startswith("data: ")).removeprefix("data: ")
    payload = json.loads(first_data)
    assert payload["is_fixture"] is True
    assert "FIXTURE" in payload["message"]


def test_deliberate_stream_rejects_invalid_horizon(client):
    test_client, _settings = client
    response = test_client.get(
        "/api/deliberate/stream", params={"ticker": "NVDA", "horizon": "3w"}
    )
    assert response.status_code == 400


def test_resolve_endpoint_returns_swept_list(client):
    test_client, settings = client
    # nothing to resolve yet
    response = test_client.post("/api/resolve")
    assert response.status_code == 200
    assert response.json()["swept"] == []


def test_predictions_list_and_detail_round_trip(client):
    test_client, settings = client
    with test_client.stream(
        "GET",
        "/api/deliberate/stream",
        params={"ticker": "NVDA", "horizon": "1w", "as_of": "2026-08-01T16:00:00"},
    ) as response:
        prediction_id = None
        for line in response.iter_lines():
            if line.startswith("data: "):
                payload = json.loads(line.removeprefix("data: "))
                if "prediction_id" in payload and "ticker" in payload:
                    prediction_id = payload["prediction_id"]
    assert prediction_id is not None

    listed = test_client.get("/api/predictions", params={"ticker": "NVDA"}).json()
    assert any(p["id"] == prediction_id for p in listed["predictions"])

    detail = test_client.get(f"/api/predictions/{prediction_id}").json()
    assert detail["prediction"]["id"] == prediction_id
    assert len(detail["seat_votes"]) > 0
    assert detail["seat_votes"][0]["verdict"]["vote"] in ("BULLISH", "BEARISH", "NO_READ")


def test_prediction_detail_404_for_unknown_id(client):
    test_client, _settings = client
    response = test_client.get("/api/predictions/does-not-exist")
    assert response.status_code == 404


def test_archives_endpoint_shape(client):
    test_client, _settings = client
    response = test_client.get("/api/archives")
    assert response.status_code == 200
    body = response.json()
    assert "seats" in body and "benchmark" in body
    assert len(body["seats"]) == 12
    assert all(s["rank"] == "YOUNGLING" for s in body["seats"])  # no resolutions yet
    assert body["benchmark"] == {"n_resolutions": 0}


def test_full_lifecycle_through_api_moves_archives(client):
    """Deliberate (backdated) -> resolve -> archives should now show a
    resolution for at least one seat, end to end through HTTP."""
    test_client, _settings = client
    with test_client.stream(
        "GET",
        "/api/deliberate/stream",
        params={"ticker": "NVDA", "horizon": "1w", "as_of": "2026-08-01T16:00:00"},
    ) as response:
        for _ in response.iter_lines():
            pass

    resolve_response = test_client.post("/api/resolve")
    swept = resolve_response.json()["swept"]
    assert len(swept) == 1

    archives_response = test_client.get("/api/archives").json()
    assert archives_response["benchmark"]["n_resolutions"] == 1
    seats_with_data = [s for s in archives_response["seats"] if s["n_resolutions"] > 0]
    assert len(seats_with_data) > 0
