"""A real bug hit in practice: a TypeError raised by the SDK call itself
(an unexpected/unsupported kwarg, e.g. an environment where
AsyncMessages.create() rejected `temperature`) was caught by the same
except clause meant for the model's own malformed output, so it got
silently retried twice (pointlessly -- a code-level TypeError doesn't fix
itself on retry) and surfaced as a confusing double-wrapped
"schema validation failed" message that hid the real cause. It must instead
fail fast, once, with a clear single-layer message."""
from __future__ import annotations

import pytest

from council.config import Settings
from council.engine.llm_client import LLMCallFailed, LLMClient


class _SDKCallRaisesTypeError:
    """Simulates an SDK/environment where messages.create() itself rejects
    a kwarg this code passes -- distinct from the model producing a
    malformed *response*, which is a different failure class entirely."""

    def __init__(self):
        self.calls = 0
        self.messages = self

    async def create(self, **kwargs):
        self.calls += 1
        raise TypeError("AsyncMessages.create() got an unexpected keyword argument 'temperature'")


@pytest.mark.asyncio
async def test_sdk_call_type_error_fails_fast_as_llm_call_failed():
    settings = Settings(no_llm=False, anthropic_api_key="sk-test-not-real")
    client = LLMClient(settings)
    client._client = _SDKCallRaisesTypeError()

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
    # must fail on the FIRST attempt -- retrying a code-level TypeError is
    # pointless, it will raise identically every time
    assert client._client.calls == 1
    assert len(client.call_log) == 1


@pytest.mark.asyncio
async def test_get_structured_raises_llm_call_failed_directly():
    from council.engine.schemas import DebateArgument

    settings = Settings(no_llm=False, anthropic_api_key="sk-test-not-real")
    client = LLMClient(settings)
    client._client = _SDKCallRaisesTypeError()

    with pytest.raises(LLMCallFailed):
        await client.get_structured(
            seat_id="bull_advocate",
            model="claude-sonnet-5",
            system_prompt="sys",
            user_prompt="usr",
            response_model=DebateArgument,
            fixture_name="bull_advocate_round1",
        )
