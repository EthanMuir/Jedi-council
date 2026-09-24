"""NO_READ means a seat couldn't form a read (API error, malformed answer,
missing data); NO_CONVICTION means it read its data and genuinely landed
in the middle. Both are abstentions and count identically in the vote."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from council.engine.aggregation import weighted_vote
from council.engine.llm_client import LLMCallFailed, LLMClient, SchemaRetryExhausted
from council.seats.base import SeatVerdict, is_abstention

_COMPARISON = {"definition": "c", "n_observations": 50, "base_rate": 0.5, "why_this_class": "w"}


def _abstain(vote: str) -> SeatVerdict:
    return SeatVerdict(
        vote=vote, probability=0.5, expected_move_pct=0.0, thesis="t",
        what_would_change_my_mind="m", data_quality="GOOD", abstain_reason="r",
    )


def _bullish() -> SeatVerdict:
    return SeatVerdict(
        vote="BULLISH", probability=0.612, expected_move_pct=2.0, thesis="t",
        what_would_change_my_mind="m", data_quality="GOOD", comparison_class=_COMPARISON,
    )


def test_both_abstentions_need_a_reason():
    for vote in ("NO_READ", "NO_CONVICTION"):
        assert is_abstention(vote)
        with pytest.raises(ValidationError):
            SeatVerdict(
                vote=vote, probability=0.5, expected_move_pct=0.0, thesis="t",
                what_would_change_my_mind="m", data_quality="GOOD",
            )
    assert not is_abstention("BULLISH")


def test_no_conviction_counts_exactly_like_no_read_in_the_vote():
    with_no_read = weighted_vote({"a": _bullish(), "b": _abstain("NO_READ")})
    with_no_conviction = weighted_vote({"a": _bullish(), "b": _abstain("NO_CONVICTION")})
    assert with_no_read == with_no_conviction


@pytest.mark.parametrize("failure", [LLMCallFailed("boom"), SchemaRetryExhausted("bad shape")])
async def test_api_and_schema_failures_are_no_read(monkeypatch, failure):
    from council.config import Settings

    client = LLMClient(Settings(no_llm=True))

    async def failing(**kwargs):
        raise failure

    monkeypatch.setattr(client, "get_structured", failing)
    verdict = await client.get_verdict(
        seat_id="technician", model="m", system_prompt="s", user_prompt="u"
    )
    assert verdict.vote == "NO_READ"


def test_an_exact_tie_between_bulls_and_bears_is_no_conviction():
    # From a real MCD run: one seat BULLISH 0.673, one BEARISH 0.673 -- the
    # blind vote came out "BULLISH (0.5)".
    bull = _bullish().model_copy(update={"probability": 0.673})
    bear = _bullish().model_copy(update={"vote": "BEARISH", "probability": 0.673})
    assert weighted_vote({"a": bull, "b": bear}) == ("NO_CONVICTION", 0.5, 0.0)

    leaning = weighted_vote({"a": bull, "b": bear.model_copy(update={"probability": 0.612})})
    assert leaning[0] == "BULLISH"
