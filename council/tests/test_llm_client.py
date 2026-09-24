"""Fixture-mode loading, and the retry-twice-then-NO_READ contract for
schema violations (engineering rule #1: never parse prose into a number,
never silently accept a malformed answer)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from council.config import Settings
from council.engine.llm_client import LLMClient, _repair_seat_answer_input
from council.tests.seat_answers import seat_answer_payload

_TIER_I_FIXTURES = [
    "technician", "fundamentalist", "catalyst_seer", "insider_reader", "senate_watcher",
    "flow_cartographer", "oracle_options", "macro_sage", "cross_market", "estimate_scribe",
    "analyst_ratings", "structure_archivist",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_name", _TIER_I_FIXTURES)
async def test_fixture_mode_loads_a_valid_three_term_answer(fixture_name):
    client = LLMClient(Settings(no_llm=True))
    answer = await client.get_seat_answer(
        seat_id=fixture_name,
        model="claude-sonnet-5",
        system_prompt="unused",
        user_prompt="unused",
        fixture_name=fixture_name,
    )
    assert answer.read
    for term in ("short", "medium", "long"):
        assert answer.term(term).vote in ("BULLISH", "BEARISH", "NO_CONVICTION")
        assert answer.term(term).term_rationale
    assert len(client.call_log) == 1  # three terms, one call
    assert client.call_log[0].model == "fixture"
    assert client.call_log[0].cost_usd == 0.0


def _fake_anthropic(payload: dict):
    class _Messages:
        calls = 0

        async def create(self, **kwargs):
            _Messages.calls += 1
            tool_use = SimpleNamespace(type="tool_use", input=payload)
            usage = SimpleNamespace(input_tokens=10, output_tokens=5)
            return SimpleNamespace(content=[tool_use], usage=usage)

    return SimpleNamespace(messages=_Messages())


def _live_client(payload: dict) -> LLMClient:
    client = LLMClient(Settings(no_llm=False, anthropic_api_key="sk-test-not-real"))
    client._client = _fake_anthropic(payload)  # bypass the real network call
    return client


async def _ask(client: LLMClient, seat_id: str = "technician"):
    return await client.get_seat_answer(
        seat_id=seat_id,
        model="claude-sonnet-5",
        system_prompt="sys",
        user_prompt="usr",
        fixture_name=seat_id,
    )


@pytest.mark.asyncio
async def test_schema_violation_retries_twice_then_marks_no_read():
    client = _live_client({"status": "READ"})  # required fields missing

    answer = await _ask(client)

    assert not answer.read
    for term in ("short", "medium", "long"):
        assert answer.term(term).vote == "NO_READ"
        assert answer.term(term).abstain_reason == "schema_failure"
    # initial attempt + 2 retries = 3 logged failures, nothing succeeded
    assert len(client.call_log) == 3
    assert all(not c.success for c in client.call_log)


@pytest.mark.asyncio
async def test_a_read_missing_a_term_is_rejected():
    payload = seat_answer_payload()
    del payload["long"]
    client = _live_client(payload)
    answer = await _ask(client)
    assert not answer.read
    assert len(client.call_log) == 3


@pytest.mark.asyncio
async def test_a_directional_term_without_a_comparison_class_is_rejected():
    client = _live_client(seat_answer_payload(comparison_class=None))
    answer = await _ask(client)
    assert not answer.read


@pytest.mark.asyncio
async def test_terms_can_lean_different_ways():
    payload = seat_answer_payload()
    payload["short"] = {"vote": "BEARISH", "probability": 0.561, "expected_move_pct": 2.0,
                        "rationale": "the deal weighs on it this week"}
    payload["medium"] = {"vote": "NO_CONVICTION", "probability": 0.5, "expected_move_pct": 0.0,
                         "rationale": "integration costs and synergies cancel out"}
    answer = await _ask(_live_client(payload))

    assert answer.short.vote == "BEARISH" and answer.short.probability == 0.561
    assert answer.medium.vote == "NO_CONVICTION"
    assert answer.medium.abstain_reason == "integration costs and synergies cancel out"
    assert answer.medium.comparison_class is None
    assert answer.long.vote == "BULLISH"


# Task #70 -- the failures below kept recurring live even after the model
# was told about them via schema descriptions (Task #66/#67). Repairing
# them in code means the call succeeds on the FIRST attempt instead of
# burning two more real retries hoping the model complies this time.
class TestRepairSeatAnswerInput:
    def test_no_read_missing_abstain_reason_falls_back_to_thesis_text(self):
        raw = {"status": "NO_READ", "thesis": "No Form 4 transactions filed in the window."}
        repaired = _repair_seat_answer_input(raw)
        assert repaired["abstain_reason"] == "No Form 4 transactions filed in the window."

    def test_no_read_missing_both_gets_a_generic_placeholder(self):
        repaired = _repair_seat_answer_input({"status": "NO_READ"})
        assert isinstance(repaired["abstain_reason"], str) and repaired["abstain_reason"]

    def test_no_read_with_empty_string_abstain_reason_also_repaired(self):
        raw = {"status": "NO_READ", "thesis": "thin data", "abstain_reason": ""}
        assert _repair_seat_answer_input(raw)["abstain_reason"] == "thin data"

    def test_a_read_drops_a_stray_abstain_reason(self):
        raw = seat_answer_payload(abstain_reason="just a caveat")
        assert _repair_seat_answer_input(raw)["abstain_reason"] is None

    def test_thesis_over_120_words_truncated(self):
        raw = seat_answer_payload(thesis=" ".join(["word"] * 128))
        assert len(_repair_seat_answer_input(raw)["thesis"].split()) == 120

    def test_thesis_at_or_under_120_words_untouched(self):
        raw = seat_answer_payload(thesis=" ".join(["word"] * 120))
        assert _repair_seat_answer_input(raw)["thesis"] == raw["thesis"]

    def test_term_rationale_over_40_words_truncated(self):
        raw = seat_answer_payload()
        raw["medium"]["rationale"] = " ".join(["word"] * 55)
        assert len(_repair_seat_answer_input(raw)["medium"]["rationale"].split()) == 40

    def test_a_dead_even_term_gets_the_neutral_numbers(self):
        raw = seat_answer_payload()
        raw["short"] = {"vote": "NO_CONVICTION", "probability": 0.55, "expected_move_pct": 1.5,
                        "rationale": "even"}
        repaired = _repair_seat_answer_input(raw)
        assert repaired["short"]["probability"] == 0.5
        assert repaired["short"]["expected_move_pct"] == 0.0

    def test_missing_thesis_does_not_crash(self):
        repaired = _repair_seat_answer_input({"status": "NO_READ"})
        assert repaired["thesis"] is None


@pytest.mark.asyncio
async def test_no_read_missing_abstain_reason_succeeds_on_first_attempt():
    client = _live_client({
        "status": "NO_READ",
        "data_quality": "POOR",
        "what_would_change_my_mind": "N/A",
        "thesis": "No news items in the lookback window.",
        # abstain_reason omitted entirely -- the exact live failure
    })
    answer = await _ask(client, "catalyst_seer")

    assert answer.short.vote == "NO_READ"
    assert answer.short.abstain_reason == "No news items in the lookback window."
    assert len(client.call_log) == 1  # no wasted retries
    assert client.call_log[0].success is True


@pytest.mark.asyncio
async def test_over_length_thesis_succeeds_on_first_attempt():
    client = _live_client(seat_answer_payload(thesis=" ".join(["word"] * 128)))
    answer = await _ask(client, "fundamentalist")

    assert answer.short.vote == "BULLISH"
    assert len(answer.short.thesis.split()) == 120
    assert len(client.call_log) == 1  # no wasted retries
    assert client.call_log[0].success is True
