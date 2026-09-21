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
        settings_db_path=str(tmp_path / "settings.db"),
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


# --- Task #74: Settings API -------------------------------------------


def test_get_model_settings_shape(client):
    test_client, _settings = client
    response = test_client.get("/api/settings/models")
    assert response.status_code == 200
    body = response.json()

    assert len(body["catalog"]) == 9
    costs = [m["typical_call_cost_usd"] for m in body["catalog"]]
    assert costs == sorted(costs, reverse=True)  # most to least expensive
    assert {m["provider"] for m in body["catalog"]} == {"anthropic", "openai", "google"}

    assert len(body["roles"]) == 16
    technician = next(r for r in body["roles"] if r["role"] == "technician")
    assert technician["title"] == "Keeper of the Charts"
    assert technician["recommended_model"] == "claude-sonnet-5"
    assert technician["current_model"] == "claude-sonnet-5"
    assert technician["is_override"] is False


def test_post_model_settings_sets_and_clears_override(client):
    test_client, _settings = client

    response = test_client.post("/api/settings/models", json={"role": "technician", "model_id": "gpt-5-nano"})
    assert response.status_code == 200
    assert response.json() == {"role": "technician", "current_model": "gpt-5-nano", "is_override": True}

    roles = test_client.get("/api/settings/models").json()["roles"]
    technician = next(r for r in roles if r["role"] == "technician")
    assert technician["current_model"] == "gpt-5-nano"
    assert technician["is_override"] is True

    # model_id omitted (None) clears the override back to the recommendation
    clear_response = test_client.post("/api/settings/models", json={"role": "technician"})
    assert clear_response.json()["is_override"] is False
    assert clear_response.json()["current_model"] == "claude-sonnet-5"


def test_post_model_settings_rejects_unknown_role_or_model(client):
    test_client, _settings = client
    assert test_client.post(
        "/api/settings/models", json={"role": "not_a_real_seat", "model_id": "gpt-5"}
    ).status_code == 400
    assert test_client.post(
        "/api/settings/models", json={"role": "technician", "model_id": "not-a-real-model"}
    ).status_code == 400


def test_cost_estimate_reflects_current_overrides(tmp_path, monkeypatch):
    # no_llm=False here -- unlike every other test in this file, the whole
    # point is pricing a real (not fixture, $0-by-design) run.
    settings = Settings(
        no_llm=False,
        anthropic_api_key="sk-test",
        openai_api_key="sk-test-openai",
        use_data_fixtures=True,
        council_db_path=str(tmp_path / "council.db"),
        cache_db_path=str(tmp_path / "cache.db"),
        settings_db_path=str(tmp_path / "settings.db"),
    )
    import council.api.main as main_module

    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    test_client = TestClient(main_module.app)

    baseline = test_client.get("/api/settings/cost-estimate", params={"horizon": "1w"}).json()
    assert baseline["is_fixture"] is False
    assert baseline["total_cost_usd"] > 0
    assert baseline["total_calls"] > 0

    # Route the Grand Master (an expensive, single-call role) to the
    # cheapest model in the catalog and confirm the estimate actually moves.
    test_client.post("/api/settings/models", json={"role": "grand_master", "model_id": "gpt-5-nano"})
    cheaper = test_client.get("/api/settings/cost-estimate", params={"horizon": "1w"}).json()
    assert cheaper["total_cost_usd"] < baseline["total_cost_usd"]

    gm_line = next(li for li in cheaper["line_items"] if li["label"] == "Grand Master synthesis")
    assert gm_line["model"] == "gpt-5-nano"


def test_cost_estimate_rejects_invalid_horizon(client):
    test_client, _settings = client
    response = test_client.get("/api/settings/cost-estimate", params={"horizon": "3w"})
    assert response.status_code == 400


def test_cost_estimate_is_zero_and_flagged_in_fixture_mode(client):
    # The `client` fixture runs with no_llm=True -- same real-money-vs-free
    # confusion this whole endpoint exists to prevent (see the "mode" event
    # on /api/deliberate/stream) applies here too.
    test_client, _settings = client
    response = test_client.get("/api/settings/cost-estimate", params={"horizon": "1w"}).json()
    assert response["is_fixture"] is True
    assert response["total_cost_usd"] == 0.0
    assert response["total_calls"] > 0


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
