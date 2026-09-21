"""Task #74 -- real OpenAI and Gemini structured-output calling, mirroring
the existing Anthropic retry/error-classification tests
(test_llm_client_retry.py) but for the two new provider adapters. Built
and tested against faked SDK client objects shaped like the real,
installed openai/google-genai packages (verified directly against them,
not guessed) -- unverified against a live API response either provider
actually returns, same caveat as every other provider integration built
this session without a real key to test against."""
from __future__ import annotations

import json
from types import SimpleNamespace

import google.genai.errors as genai_errors
import httpx
import openai
import pytest

import council.engine.llm_client as llm_client_module
from council.config import Settings
from council.engine.llm_client import LLMClient

_OPENAI_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _valid_openai_response():
    tool_call = SimpleNamespace(
        function=SimpleNamespace(
            arguments=json.dumps(
                {
                    "vote": "BULLISH",
                    "probability": 0.612,
                    "comparison_class": {
                        "definition": "test comparison class",
                        "n_observations": 50,
                        "base_rate": 0.5,
                        "why_this_class": "test",
                    },
                    "expected_move_pct": 3.2,
                    "thesis": "test thesis",
                    "what_would_change_my_mind": "test",
                    "data_quality": "GOOD",
                }
            )
        )
    )
    message = SimpleNamespace(tool_calls=[tool_call])
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(prompt_tokens=120, completion_tokens=60)
    return SimpleNamespace(choices=[choice], usage=usage)


def _valid_gemini_response():
    payload = {
        "vote": "BEARISH",
        "probability": 0.388,
        "comparison_class": {
            "definition": "test comparison class",
            "n_observations": 50,
            "base_rate": 0.5,
            "why_this_class": "test",
        },
        "expected_move_pct": -2.1,
        "thesis": "test thesis",
        "what_would_change_my_mind": "test",
        "data_quality": "GOOD",
    }
    usage = SimpleNamespace(prompt_token_count=200, candidates_token_count=80)
    return SimpleNamespace(text=json.dumps(payload), usage_metadata=usage)


def _fast_backoff(monkeypatch):
    monkeypatch.setattr(llm_client_module, "_BACKOFF_BASE_SECONDS", 0.001)
    monkeypatch.setattr(llm_client_module, "_BACKOFF_CAP_SECONDS", 0.001)


# --- OpenAI ------------------------------------------------------------


class _OpenAIChatCompletions:
    def __init__(self, responder):
        self._responder = responder
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        return self._responder(self.calls, kwargs)


class _FakeOpenAIClient:
    def __init__(self, responder):
        self.chat = SimpleNamespace(completions=_OpenAIChatCompletions(responder))


@pytest.mark.asyncio
async def test_openai_success_path_parses_tool_call_and_logs_cost():
    settings = Settings(no_llm=False, anthropic_api_key="sk-test", openai_api_key="sk-test-openai")
    client = LLMClient(settings)
    client._openai_client = _FakeOpenAIClient(lambda n, kwargs: _valid_openai_response())

    verdict = await client.get_verdict(
        seat_id="fundamentalist",  # routes to openai/gpt-5 per config/models.yaml
        model="claude-sonnet-5",
        system_prompt="sys",
        user_prompt="usr",
        fixture_name="fundamentalist",
    )

    assert verdict.vote == "BULLISH"
    assert verdict.probability == 0.612
    assert len(client.call_log) == 1
    record = client.call_log[0]
    assert record.provider == "openai"
    assert record.model == "gpt-5"
    assert record.input_tokens == 120
    assert record.output_tokens == 60
    assert record.cost_usd > 0
    assert record.success is True


@pytest.mark.asyncio
async def test_openai_forces_the_named_tool_choice():
    settings = Settings(no_llm=False, anthropic_api_key="sk-test", openai_api_key="sk-test-openai")
    client = LLMClient(settings)
    captured = {}

    async def responder(**kwargs):
        captured.update(kwargs)
        return _valid_openai_response()

    client._openai_client = _FakeOpenAIClient(lambda n, kwargs: None)
    client._openai_client.chat.completions.create = responder

    await client.get_verdict(
        seat_id="fundamentalist",
        model="claude-sonnet-5",
        system_prompt="sys",
        user_prompt="usr",
        fixture_name="fundamentalist",
    )
    assert captured["tool_choice"]["type"] == "function"
    assert captured["tools"][0]["function"]["name"] == captured["tool_choice"]["function"]["name"]


@pytest.mark.asyncio
async def test_openai_retryable_error_succeeds_after_backoff(monkeypatch):
    _fast_backoff(monkeypatch)
    settings = Settings(no_llm=False, anthropic_api_key="sk-test", openai_api_key="sk-test-openai")
    client = LLMClient(settings)

    def responder(n, kwargs):
        if n <= 2:
            raise openai.APIConnectionError(request=_OPENAI_REQUEST)
        return _valid_openai_response()

    client._openai_client = _FakeOpenAIClient(responder)

    verdict = await client.get_verdict(
        seat_id="fundamentalist",
        model="claude-sonnet-5",
        system_prompt="sys",
        user_prompt="usr",
        fixture_name="fundamentalist",
    )
    assert verdict.vote == "BULLISH"
    assert client._openai_client.chat.completions.calls == 3
    assert [c.success for c in client.call_log] == [False, False, True]


@pytest.mark.asyncio
async def test_openai_non_retryable_error_fails_fast():
    settings = Settings(no_llm=False, anthropic_api_key="sk-test", openai_api_key="sk-test-openai")
    client = LLMClient(settings)

    def responder(n, kwargs):
        response = httpx.Response(401, request=_OPENAI_REQUEST)
        raise openai.AuthenticationError("bad key", response=response, body=None)

    client._openai_client = _FakeOpenAIClient(responder)

    verdict = await client.get_verdict(
        seat_id="fundamentalist",
        model="claude-sonnet-5",
        system_prompt="sys",
        user_prompt="usr",
        fixture_name="fundamentalist",
        max_retries=2,
    )
    assert verdict.vote == "NO_READ"
    assert verdict.abstain_reason == "llm_call_failed"
    assert client._openai_client.chat.completions.calls == 1


# --- Gemini --------------------------------------------------------------


class _GeminiModels:
    def __init__(self, responder):
        self._responder = responder
        self.calls = 0

    async def generate_content(self, **kwargs):
        self.calls += 1
        return self._responder(self.calls, kwargs)


class _FakeGeminiClient:
    def __init__(self, responder):
        self.aio = SimpleNamespace(models=_GeminiModels(responder))


@pytest.mark.asyncio
async def test_gemini_success_path_parses_json_and_logs_cost():
    settings = Settings(no_llm=False, anthropic_api_key="sk-test", google_api_key="test-google-key")
    client = LLMClient(settings)
    client._gemini_client = _FakeGeminiClient(lambda n, kwargs: _valid_gemini_response())

    verdict = await client.get_verdict(
        seat_id="macro_sage",  # routes to google/gemini-3-pro per config/models.yaml
        model="claude-sonnet-5",
        system_prompt="sys",
        user_prompt="usr",
        fixture_name="macro_sage",
    )

    assert verdict.vote == "BEARISH"
    assert verdict.probability == 0.388
    record = client.call_log[0]
    assert record.provider == "google"
    assert record.model == "gemini-3-pro"
    assert record.input_tokens == 200
    assert record.output_tokens == 80
    assert record.cost_usd > 0
    assert record.success is True


@pytest.mark.asyncio
async def test_gemini_passes_response_schema_and_json_mime_type():
    settings = Settings(no_llm=False, anthropic_api_key="sk-test", google_api_key="test-google-key")
    client = LLMClient(settings)
    captured = {}

    async def responder(**kwargs):
        captured.update(kwargs)
        return _valid_gemini_response()

    client._gemini_client = _FakeGeminiClient(lambda n, kwargs: None)
    client._gemini_client.aio.models.generate_content = responder

    await client.get_verdict(
        seat_id="macro_sage",
        model="claude-sonnet-5",
        system_prompt="sys",
        user_prompt="usr",
        fixture_name="macro_sage",
    )
    assert captured["config"].response_mime_type == "application/json"
    assert captured["config"].response_schema is not None


@pytest.mark.asyncio
async def test_gemini_retryable_server_error_succeeds_after_backoff(monkeypatch):
    _fast_backoff(monkeypatch)
    settings = Settings(no_llm=False, anthropic_api_key="sk-test", google_api_key="test-google-key")
    client = LLMClient(settings)

    def responder(n, kwargs):
        if n <= 2:
            raise genai_errors.ServerError(503, {"error": "server error"})
        return _valid_gemini_response()

    client._gemini_client = _FakeGeminiClient(responder)

    verdict = await client.get_verdict(
        seat_id="macro_sage",
        model="claude-sonnet-5",
        system_prompt="sys",
        user_prompt="usr",
        fixture_name="macro_sage",
    )
    assert verdict.vote == "BEARISH"
    assert client._gemini_client.aio.models.calls == 3
    assert [c.success for c in client.call_log] == [False, False, True]


@pytest.mark.asyncio
async def test_gemini_non_retryable_client_error_fails_fast():
    settings = Settings(no_llm=False, anthropic_api_key="sk-test", google_api_key="test-google-key")
    client = LLMClient(settings)

    def responder(n, kwargs):
        raise genai_errors.ClientError(400, {"error": "bad request"})

    client._gemini_client = _FakeGeminiClient(responder)

    verdict = await client.get_verdict(
        seat_id="macro_sage",
        model="claude-sonnet-5",
        system_prompt="sys",
        user_prompt="usr",
        fixture_name="macro_sage",
        max_retries=2,
    )
    assert verdict.vote == "NO_READ"
    assert verdict.abstain_reason == "llm_call_failed"
    assert client._gemini_client.aio.models.calls == 1
