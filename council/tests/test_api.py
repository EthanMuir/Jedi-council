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
        "GET", "/api/deliberate/stream", params={"ticker": "NVDA"}
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
        "GET", "/api/deliberate/stream", params={"ticker": "NVDA"}
    ) as response:
        lines = list(response.iter_lines())

    first_event = next(l for l in lines if l.startswith("event: ")).removeprefix("event: ")
    assert first_event == "mode"
    first_data = next(l for l in lines if l.startswith("data: ")).removeprefix("data: ")
    payload = json.loads(first_data)
    assert payload["is_fixture"] is True
    assert payload["run_mode"] == "sample"
    assert "$0" in payload["message"]


def _stream_events(test_client, **params) -> list[tuple[str, dict]]:
    events, event = [], None
    with test_client.stream("GET", "/api/deliberate/stream", params=params) as response:
        for line in response.iter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                events.append((event, json.loads(line.removeprefix("data: "))))
    return events


def test_deliberate_stream_needs_only_a_ticker(client):
    test_client, _settings = client
    events = _stream_events(test_client, ticker="NVDA", as_of="2026-08-01T16:00:00")
    names = [e for e, _ in events]
    assert names[0] == "mode" and names[-1] == "done"
    done = events[-1][1]
    assert set(done["terms"]) == {"short", "medium", "long"}
    assert done["terms"]["long"]["position"]["lean_label"]


def test_resolve_endpoint_returns_swept_list(client):
    test_client, settings = client
    # nothing to resolve yet
    response = test_client.post("/api/resolve")
    assert response.status_code == 200
    assert response.json()["swept"] == []


def test_predictions_list_and_detail_round_trip(client):
    test_client, settings = client
    events = _stream_events(test_client, ticker="NVDA", as_of="2026-08-01T16:00:00")
    written = next(p for e, p in events if e == "phase_g_crypt_write")
    run_id, prediction_ids = written["run_id"], written["prediction_ids"]

    listed = test_client.get("/api/predictions", params={"ticker": "NVDA"}).json()
    assert len(listed["runs"]) == 1
    run = listed["runs"][0]
    assert run["run_id"] == run_id and not run["legacy"]
    assert {t: row["id"] for t, row in run["terms"].items()} == prediction_ids
    assert len(listed["predictions"]) == 3

    only_long = test_client.get("/api/predictions", params={"term": "long"}).json()
    assert list(only_long["runs"][0]["terms"]) == ["long"]

    detail = test_client.get(f"/api/runs/{run_id}").json()
    assert detail["synthesis"]["headline"]
    assert set(detail["synthesis"]["terms"]) == {"short", "medium", "long"}
    for term, body in detail["terms"].items():
        assert body["prediction"]["id"] == prediction_ids[term]
        assert len(body["seat_votes"]) == 12
        assert "synthesis_json" not in body["prediction"]

    # A saved run reopens in full: the Summary in plain words too, and a
    # replay of what the live page showed.
    synth = detail["synthesis"]
    assert all(synth[f"plain_{k}"] for k in ("headline", "short", "medium", "long"))
    replay = synth["replay"]
    assert len(replay["seats"]) == 12
    assert {s["seat_id"] for s in replay["seats"]} == {s["seat_id"] for s in detail["terms"]["short"]["seat_votes"]}
    assert all("key_evidence" in s for s in replay["seats"])
    assert replay["debate"] and replay["debate"][0]["round_n"] == 1
    assert set(replay["reality_anchor"]["terms"]) == {"short", "medium", "long"}
    assert "position_size_pct_of_book" in replay["risk"]

    one = test_client.get(f"/api/predictions/{prediction_ids['short']}").json()
    assert one["prediction"]["horizon"] == "short"
    assert one["seat_votes"][0]["verdict"]["vote"] in ("BULLISH", "BEARISH", "NO_CONVICTION", "NO_READ")


def test_run_detail_404_for_unknown_id(client):
    test_client, _settings = client
    assert test_client.get("/api/runs/does-not-exist").status_code == 404


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
    # Each seat's record per term, with the say it currently has there.
    for seat in body["seats"]:
        assert set(seat["terms"]) == {"short", "medium", "long"}
        for term in seat["terms"].values():
            assert term["n_resolutions"] == 0 and term["weight"] == 1.0
            assert 0 < term["competence"] <= 1


# --- Task #74: Settings API -------------------------------------------


def test_get_model_settings_shape(client):
    test_client, _settings = client
    response = test_client.get("/api/settings/models")
    assert response.status_code == 200
    body = response.json()

    assert len(body["catalog"]) == 11
    costs = [m["typical_call_cost_usd"] for m in body["catalog"]]
    assert costs == sorted(costs, reverse=True)  # most to least expensive
    assert {m["provider"] for m in body["catalog"]} == {"anthropic", "openai", "google", "groq"}
    assert [m["id"] for m in body["catalog"] if m["free"]] == ["free:gemini", "free:groq"]

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
    assert technician["chosen_model"] == "gpt-5-nano"
    assert technician["is_override"] is True
    # No OpenAI key here, so a run would really use Claude, and the page says why.
    assert technician["current_model"] == "claude-sonnet-5"
    assert technician["chosen_needs"] == {"label": "Needs an OpenAI key", "key": "openai_api_key"}

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

    baseline = test_client.get("/api/settings/cost-estimate").json()
    assert baseline["is_fixture"] is False
    assert baseline["total_cost_usd"] > 0
    assert baseline["total_calls"] > 0

    # Route the Grand Master (an expensive, single-call role) to the
    # cheapest model in the catalog and confirm the estimate actually moves.
    test_client.post("/api/settings/models", json={"role": "grand_master", "model_id": "gpt-5-nano"})
    cheaper = test_client.get("/api/settings/cost-estimate").json()
    assert cheaper["total_cost_usd"] < baseline["total_cost_usd"]

    gm_line = next(li for li in cheaper["line_items"] if li["label"] == "Grand Master synthesis")
    assert gm_line["model"] == "gpt-5-nano"


def test_cost_estimate_covers_a_whole_run(client):
    test_client, _settings = client
    response = test_client.get("/api/settings/cost-estimate").json()
    assert response["total_calls"] == 31
    assert "horizon" not in response


def test_cost_estimate_is_zero_and_flagged_in_fixture_mode(client):
    # The `client` fixture runs with no_llm=True -- same real-money-vs-free
    # confusion this whole endpoint exists to prevent (see the "mode" event
    # on /api/deliberate/stream) applies here too.
    test_client, _settings = client
    response = test_client.get("/api/settings/cost-estimate").json()
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
        params={"ticker": "NVDA", "as_of": "2026-08-01T16:00:00"},
    ) as response:
        for _ in response.iter_lines():
            pass

    resolve_response = test_client.post("/api/resolve")
    swept = resolve_response.json()["swept"]
    assert [s["horizon"] for s in swept] == ["short"]  # the other terms are still open

    archives_response = test_client.get("/api/archives").json()
    assert archives_response["benchmark"]["n_resolutions"] == 1
    seats_with_data = [s for s in archives_response["seats"] if s["n_resolutions"] > 0]
    assert len(seats_with_data) > 0
