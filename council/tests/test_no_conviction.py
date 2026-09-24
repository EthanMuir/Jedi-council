"""NO_READ means a seat couldn't form a read (API error, malformed answer,
missing data) and is left out of the council's position. NO_CONVICTION
means it read its data and found it genuinely dead even -- a real read,
counted as exactly 0.5, so it pulls the position toward the middle."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from council.engine.aggregation import council_position
from council.engine.llm_client import LLMCallFailed, LLMClient, SchemaRetryExhausted
from council.seats.base import SeatVerdict, is_abstention

_COMPARISON = {"definition": "c", "n_observations": 50, "base_rate": 0.5, "why_this_class": "w"}


def _abstain(vote: str) -> SeatVerdict:
    return SeatVerdict(
        vote=vote, probability=0.5, expected_move_pct=0.0, thesis="t",
        what_would_change_my_mind="m", data_quality="GOOD", abstain_reason="r",
    )


def _bullish(probability: float = 0.612) -> SeatVerdict:
    return SeatVerdict(
        vote="BULLISH", probability=probability, expected_move_pct=2.0, thesis="t",
        what_would_change_my_mind="m", data_quality="GOOD", comparison_class=_COMPARISON,
    )


def test_both_need_a_reason():
    for vote in ("NO_READ", "NO_CONVICTION"):
        assert is_abstention(vote)
        with pytest.raises(ValidationError):
            SeatVerdict(
                vote=vote, probability=0.5, expected_move_pct=0.0, thesis="t",
                what_would_change_my_mind="m", data_quality="GOOD",
            )
    assert not is_abstention("BULLISH")


def test_dead_even_pulls_toward_the_middle_but_no_read_is_left_out():
    with_no_read = council_position({"a": _bullish(), "b": _abstain("NO_READ")})
    with_no_conviction = council_position({"a": _bullish(), "b": _abstain("NO_CONVICTION")})
    assert with_no_read.p_bullish == 0.612
    assert with_no_conviction.p_bullish == 0.556
    assert with_no_read.seats_counted == 1
    assert with_no_conviction.seats_counted == 2


@pytest.mark.parametrize("failure", [LLMCallFailed("boom"), SchemaRetryExhausted("bad shape")])
async def test_api_and_schema_failures_are_no_read(monkeypatch, failure):
    from council.config import Settings

    client = LLMClient(Settings(no_llm=True))

    async def failing(**kwargs):
        raise failure

    monkeypatch.setattr(client, "get_structured", failing)
    answer = await client.get_seat_answer(
        seat_id="technician", model="m", system_prompt="s", user_prompt="u"
    )
    assert not answer.read
    assert {answer.term(t).vote for t in ("short", "medium", "long")} == {"NO_READ"}


def test_an_exact_tie_between_bulls_and_bears_is_dead_even():
    # From a real MCD run: one seat BULLISH 0.673, one BEARISH 0.673 -- the
    # blind vote came out "BULLISH (0.5)".
    bull = _bullish(0.673)
    bear = _bullish(0.673).model_copy(update={"vote": "BEARISH"})
    tie = council_position({"a": bull, "b": bear})
    assert (tie.vote, tie.p_bullish, tie.lean_label) == ("NO_CONVICTION", 0.5, "Dead even")

    leaning = council_position({"a": bull, "b": bear.model_copy(update={"probability": 0.612})})
    assert leaning.vote == "BULLISH"
