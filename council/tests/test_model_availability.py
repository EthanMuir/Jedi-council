"""Seats only use models the person's keys can run: a paid Gemini model
needs the Google key marked as having billing, Google's real model name is
looked up from the key's model list, and an answer Claude wrapped one
level down is still read."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from council.config import Settings
from council.engine import free_models, model_settings
from council.engine.llm_client import LLMClient, _unwrap_answer
from council.engine.routing import model_block, resolve_route
from council.seats.base import SeatAnswer
from council.tests.seat_answers import seat_answer_payload


@pytest.fixture
def settings(tmp_path):
    return Settings(
        settings_db_path=str(tmp_path / "settings.db"),
        anthropic_api_key="sk-ant-test", google_api_key="AQ.test",
    )


def _billing(settings, on):
    conn = model_settings.connect(settings.settings_db_path)
    model_settings.set_google_billing(conn, on)
    conn.close()


def test_paid_gemini_needs_billing_marked(settings):
    assert model_block("gemini-3-pro", settings) == "billing:google"
    assert model_block("free:gemini", settings) is None
    assert model_block("gpt-5", settings) == "key:openai"
    assert model_block("claude-sonnet-5", settings) is None
    _billing(settings, True)
    assert model_block("gemini-3-pro", settings) is None


def test_gemini_seats_use_claude_until_billing_is_marked(settings):
    route = resolve_route("macro_sage", settings, default_model="claude-sonnet-5")
    assert (route.provider, route.model, route.routed_as_intended) == ("anthropic", "claude-sonnet-5", False)
    _billing(settings, True)
    route = resolve_route("macro_sage", settings, default_model="claude-sonnet-5")
    assert (route.provider, route.model) == ("google", "gemini-3-pro")


def test_a_chosen_gpt_model_without_an_openai_key_uses_claude(settings):
    conn = model_settings.connect(settings.settings_db_path)
    model_settings.set_override(conn, "technician", "gpt-5")
    conn.close()
    route = resolve_route("technician", settings, default_model="claude-sonnet-5")
    assert (route.provider, route.model) == ("anthropic", "claude-sonnet-5")


def test_google_model_names_are_looked_up():
    names = ["models/gemini-3-pro-preview", "models/gemini-3-pro-preview-tts", "models/gemini-3-flash",
             "models/gemini-2.5-flash-lite"]
    assert free_models.pick_google_model(names, "gemini-3-pro") == "gemini-3-pro-preview"
    assert free_models.pick_google_model(names, "gemini-3-flash") == "gemini-3-flash"
    assert free_models.pick_google_model(names + ["models/gemini-3-pro"], "gemini-3-pro") == "gemini-3-pro"
    assert free_models.pick_google_model(names, "gemini-9-ultra") is None


def test_wrapped_answers_are_unwrapped():
    schema = SeatAnswer.model_json_schema()
    payload = seat_answer_payload()
    assert _unwrap_answer({"answer": payload}, schema) == payload
    assert _unwrap_answer({"answer": json.dumps(payload), "thesis": None}, schema) == payload
    assert _unwrap_answer(payload, schema) is payload
    odd = {"answer": "not json"}
    assert _unwrap_answer(odd, schema) is odd


@pytest.mark.asyncio
async def test_a_wrapped_claude_answer_is_read_first_time():
    payload = seat_answer_payload()

    class _Messages:
        calls = 0

        async def create(self, **kwargs):
            _Messages.calls += 1
            tool_use = SimpleNamespace(type="tool_use", input={"answer": json.dumps(payload)})
            return SimpleNamespace(content=[tool_use], usage=SimpleNamespace(input_tokens=10, output_tokens=5))

    client = LLMClient(Settings(no_llm=False, anthropic_api_key="sk-test-not-real"))
    client._client = SimpleNamespace(messages=_Messages())
    answer = await client.get_seat_answer(
        seat_id="technician", model="claude-sonnet-5", system_prompt="s", user_prompt="u", fixture_name="technician"
    )
    assert answer.read and _Messages.calls == 1


@pytest.mark.asyncio
async def test_failed_answers_still_count_toward_cost():
    class _Messages:
        async def create(self, **kwargs):
            tool_use = SimpleNamespace(type="tool_use", input={"status": "READ"})
            return SimpleNamespace(content=[tool_use], usage=SimpleNamespace(input_tokens=1000, output_tokens=500))

    client = LLMClient(Settings(no_llm=False, anthropic_api_key="sk-test-not-real"))
    client._client = SimpleNamespace(messages=_Messages())
    await client.get_seat_answer(
        seat_id="technician", model="claude-sonnet-5", system_prompt="s", user_prompt="u", fixture_name="technician"
    )
    assert len(client.call_log) == 3
    assert all(not c.success and c.cost_usd > 0 and c.input_tokens == 1000 for c in client.call_log)


def test_the_models_page_shows_what_will_really_run(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import council.api.main as main_module

    settings = Settings(
        no_llm=True, use_data_fixtures=True, anthropic_api_key="sk-ant-test", google_api_key="AQ.test",
        council_db_path=str(tmp_path / "council.db"), cache_db_path=str(tmp_path / "cache.db"),
        settings_db_path=str(tmp_path / "settings.db"),
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    client = TestClient(main_module.app)
    body = client.get("/api/settings/models").json()
    roles = {r["role"]: r for r in body["roles"]}
    assert roles["macro_sage"]["chosen_model"] == "gemini-3-pro"
    assert roles["macro_sage"]["current_model"] == "claude-sonnet-5"
    assert roles["macro_sage"]["chosen_needs"]["label"] == "Needs billing on your Google key"
    assert roles["fundamentalist"]["current_model"] == "claude-sonnet-5"
    catalog = {m["id"]: m for m in body["catalog"]}
    assert catalog["gpt-5"]["available"] is False and catalog["gpt-5"]["needs"]["key"] == "openai_api_key"
    assert catalog["free:gemini"]["available"] is True and catalog["claude-opus-5"]["available"] is True

    assert client.post("/api/settings/google-billing", json={"enabled": True}).json() == {"google_billing": True}
    roles = {r["role"]: r for r in client.get("/api/settings/models").json()["roles"]}
    assert roles["macro_sage"]["current_model"] == "gemini-3-pro" and roles["macro_sage"]["chosen_needs"] is None
