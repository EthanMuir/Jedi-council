"""Network-level retry/backoff (Phase 6): transient API errors (connection,
rate limit, 5xx) retry with backoff and can still succeed; non-retryable
errors (auth, bad request) fail fast with no wasted retries. Both map to
NO_READ via get_seat_answer's LLMCallFailed handler -- the network-error
analogue of test_llm_client.py's schema-violation contract."""
from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx
import pytest

import council.engine.llm_client as llm_client_module
from council.config import Settings
from council.engine.llm_client import LLMClient
from council.tests.seat_answers import seat_answer_payload

_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _valid_tool_use_response():
    tool_use = SimpleNamespace(
        type="tool_use",
        input=seat_answer_payload(),
    )
    usage = SimpleNamespace(input_tokens=100, output_tokens=50)
    return SimpleNamespace(content=[tool_use], usage=usage)


class _FlakyThenOkAnthropic:
    """Fails with a retryable connection error `fail_times` times, then
    returns a valid response."""

    def __init__(self, fail_times: int):
        self._fail_times = fail_times
        self.calls = 0
        self.messages = self

    async def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise anthropic.APIConnectionError(request=_REQUEST)
        return _valid_tool_use_response()


class _AlwaysRateLimitedAnthropic:
    def __init__(self):
        self.calls = 0
        self.messages = self

    async def create(self, **kwargs):
        self.calls += 1
        response = httpx.Response(429, request=_REQUEST)
        raise anthropic.RateLimitError("rate limited", response=response, body=None)


class _AuthFailsAnthropic:
    def __init__(self):
        self.calls = 0
        self.messages = self

    async def create(self, **kwargs):
        self.calls += 1
        response = httpx.Response(401, request=_REQUEST)
        raise anthropic.AuthenticationError("bad key", response=response, body=None)


def _fast_backoff(monkeypatch):
    # Real backoff (1s/2s/4s...) would make these tests slow for no reason --
    # the thing under test is retry COUNT and error CLASSIFICATION, not
    # wall-clock timing.
    monkeypatch.setattr(llm_client_module, "_BACKOFF_BASE_SECONDS", 0.001)
    monkeypatch.setattr(llm_client_module, "_BACKOFF_CAP_SECONDS", 0.001)


@pytest.mark.asyncio
async def test_retryable_error_succeeds_after_backoff(monkeypatch):
    _fast_backoff(monkeypatch)
    settings = Settings(no_llm=False, anthropic_api_key="sk-test-not-real")
    client = LLMClient(settings)
    client._client = _FlakyThenOkAnthropic(fail_times=2)

    verdict = await client.get_seat_answer(
        seat_id="technician",
        model="claude-sonnet-5",
        system_prompt="sys",
        user_prompt="usr",
        fixture_name="technician",
    )
    assert verdict.short.vote == "BULLISH"
    assert client._client.calls == 3
    assert len(client.call_log) == 3
    assert [c.success for c in client.call_log] == [False, False, True]


@pytest.mark.asyncio
async def test_retryable_error_exhausts_to_no_read(monkeypatch):
    _fast_backoff(monkeypatch)
    settings = Settings(no_llm=False, anthropic_api_key="sk-test-not-real")
    client = LLMClient(settings)
    client._client = _AlwaysRateLimitedAnthropic()

    verdict = await client.get_seat_answer(
        seat_id="technician",
        model="claude-sonnet-5",
        system_prompt="sys",
        user_prompt="usr",
        fixture_name="technician",
        max_retries=2,
    )
    assert verdict.short.vote == "NO_READ"
    assert verdict.short.abstain_reason == "llm_call_failed"
    # initial attempt + 2 retries = 3 attempts, all failed
    assert client._client.calls == 3
    assert len(client.call_log) == 3
    assert all(not c.success for c in client.call_log)


@pytest.mark.asyncio
async def test_non_retryable_error_fails_fast_with_no_retries():
    settings = Settings(no_llm=False, anthropic_api_key="sk-test-not-real")
    client = LLMClient(settings)
    client._client = _AuthFailsAnthropic()

    verdict = await client.get_seat_answer(
        seat_id="technician",
        model="claude-sonnet-5",
        system_prompt="sys",
        user_prompt="usr",
        fixture_name="technician",
        max_retries=2,
    )
    assert verdict.short.vote == "NO_READ"
    assert verdict.short.abstain_reason == "llm_call_failed"
    # a 401 is never going to succeed on retry -- must fail on the first attempt
    assert client._client.calls == 1
    assert len(client.call_log) == 1
