"""Fixture-mode loading, and the retry-twice-then-NO_READ contract for
schema violations (engineering rule #1: never parse prose into a number,
never silently accept a malformed verdict)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from council.config import Settings
from council.engine.llm_client import LLMClient, _repair_seat_verdict_input


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


# Task #70 -- both failures below kept recurring live even after the model
# was told about them via schema descriptions (Task #66/#67). Repairing
# them in code means the call succeeds on the FIRST attempt instead of
# burning two more real retries hoping the model complies this time.


class TestRepairSeatVerdictInput:
    def test_no_read_missing_abstain_reason_falls_back_to_thesis_text(self):
        raw = {"vote": "NO_READ", "thesis": "No Form 4 transactions filed in the window."}
        repaired = _repair_seat_verdict_input(raw)
        assert repaired["abstain_reason"] == "No Form 4 transactions filed in the window."

    def test_no_read_missing_both_gets_a_generic_placeholder(self):
        raw = {"vote": "NO_READ"}
        repaired = _repair_seat_verdict_input(raw)
        assert repaired["abstain_reason"]  # non-empty
        assert isinstance(repaired["abstain_reason"], str)

    def test_no_read_with_empty_string_abstain_reason_also_repaired(self):
        raw = {"vote": "NO_READ", "thesis": "thin data", "abstain_reason": ""}
        repaired = _repair_seat_verdict_input(raw)
        assert repaired["abstain_reason"] == "thin data"

    def test_directional_vote_untouched(self):
        raw = {"vote": "BULLISH", "thesis": "short thesis"}
        repaired = _repair_seat_verdict_input(raw)
        assert "abstain_reason" not in repaired

    def test_thesis_over_120_words_truncated(self):
        raw = {"vote": "BULLISH", "thesis": " ".join(["word"] * 128)}
        repaired = _repair_seat_verdict_input(raw)
        assert len(repaired["thesis"].split()) == 120

    def test_thesis_at_or_under_120_words_untouched(self):
        raw = {"vote": "BULLISH", "thesis": " ".join(["word"] * 120)}
        repaired = _repair_seat_verdict_input(raw)
        assert repaired["thesis"] == raw["thesis"]

    def test_missing_thesis_does_not_crash(self):
        raw = {"vote": "NO_READ"}
        repaired = _repair_seat_verdict_input(raw)  # no thesis key at all
        assert "thesis" not in repaired


class _NoReadMissingAbstainReasonAnthropic:
    class _Messages:
        async def create(self, **kwargs):
            tool_use = SimpleNamespace(
                type="tool_use",
                input={
                    "vote": "NO_READ",
                    "probability": 0.5,
                    "expected_move_pct": 0.0,
                    "thesis": "No news items in the lookback window.",
                    "what_would_change_my_mind": "N/A",
                    "data_quality": "POOR",
                    # abstain_reason omitted entirely -- the exact live failure
                },
            )
            usage = SimpleNamespace(input_tokens=10, output_tokens=5)
            return SimpleNamespace(content=[tool_use], usage=usage)

    def __init__(self):
        self.messages = self._Messages()


@pytest.mark.asyncio
async def test_no_read_missing_abstain_reason_succeeds_on_first_attempt():
    settings = Settings(no_llm=False, anthropic_api_key="sk-test-not-real")
    client = LLMClient(settings)
    client._client = _NoReadMissingAbstainReasonAnthropic()

    verdict = await client.get_verdict(
        seat_id="catalyst_seer",
        model="claude-sonnet-5",
        system_prompt="sys",
        user_prompt="usr",
        fixture_name="catalyst_seer",
    )

    assert verdict.vote == "NO_READ"
    assert verdict.abstain_reason == "No news items in the lookback window."
    assert len(client.call_log) == 1  # no wasted retries
    assert client.call_log[0].success is True


class _OverLengthThesisAnthropic:
    class _Messages:
        async def create(self, **kwargs):
            tool_use = SimpleNamespace(
                type="tool_use",
                input={
                    "vote": "BULLISH",
                    "probability": 0.623,
                    "comparison_class": {
                        "definition": "US large-cap semis after a >15% 1-month run",
                        "n_observations": 42,
                        "base_rate": 0.55,
                        "why_this_class": "closest analogue with enough history",
                    },
                    "entry": 100.0,
                    "exit": 110.0,
                    "invalidation": 95.0,
                    "expected_move_pct": 5.0,
                    "thesis": " ".join(["word"] * 128),  # over the 120-word cap
                    "what_would_change_my_mind": "a break below invalidation",
                    "data_quality": "GOOD",
                },
            )
            usage = SimpleNamespace(input_tokens=10, output_tokens=5)
            return SimpleNamespace(content=[tool_use], usage=usage)

    def __init__(self):
        self.messages = self._Messages()


@pytest.mark.asyncio
async def test_over_length_thesis_succeeds_on_first_attempt():
    settings = Settings(no_llm=False, anthropic_api_key="sk-test-not-real")
    client = LLMClient(settings)
    client._client = _OverLengthThesisAnthropic()

    verdict = await client.get_verdict(
        seat_id="fundamentalist",
        model="claude-sonnet-5",
        system_prompt="sys",
        user_prompt="usr",
        fixture_name="fundamentalist",
    )

    assert verdict.vote == "BULLISH"
    assert len(verdict.thesis.split()) == 120
    assert len(client.call_log) == 1  # no wasted retries
    assert client.call_log[0].success is True
