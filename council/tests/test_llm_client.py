"""Fixture-mode loading, and the retry-twice-then-NO_READ contract for
schema violations (engineering rule #1: never parse prose into a number,
never silently accept a malformed verdict)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from council.config import Settings
from council.engine.llm_client import LLMClient


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_name", ["technician", "catalyst_seer", "oracle_options"])
async def test_fixture_mode_loads_valid_verdict(fixture_name):
    client = LLMClient(Settings(no_llm=True))
    verdict = await client.get_verdict(
        seat_id=fixture_name,
        model="claude-sonnet-5",
        system_prompt="unused",
        user_prompt="unused",
        fixture_name=fixture_name,
    )
    assert verdict.vote in ("BULLISH", "BEARISH", "NO_READ")
    assert len(client.call_log) == 1
    assert client.call_log[0].model == "fixture"
    assert client.call_log[0].cost_usd == 0.0


class _AlwaysMalformedAnthropic:
    """Simulates a live client whose tool_use content never contains a valid
    SeatVerdict payload -- e.g. a required field omitted."""

    class _Messages:
        async def create(self, **kwargs):
            tool_use = SimpleNamespace(type="tool_use", input={"vote": "BULLISH"})  # missing required fields
            usage = SimpleNamespace(input_tokens=10, output_tokens=5)
            return SimpleNamespace(content=[tool_use], usage=usage)

    def __init__(self):
        self.messages = self._Messages()


@pytest.mark.asyncio
async def test_schema_violation_retries_twice_then_marks_no_read():
    settings = Settings(no_llm=False, anthropic_api_key="sk-test-not-real")
    client = LLMClient(settings)
    client._client = _AlwaysMalformedAnthropic()  # bypass real network call

    verdict = await client.get_verdict(
        seat_id="technician",
        model="claude-sonnet-5",
        system_prompt="sys",
        user_prompt="usr",
        fixture_name="technician",
    )

    assert verdict.vote == "NO_READ"
    assert verdict.abstain_reason == "schema_failure"
    # initial attempt + 2 retries = 3 logged failures, nothing succeeded
    assert len(client.call_log) == 3
    assert all(not c.success for c in client.call_log)
