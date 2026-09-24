"""Run-to-run dispersion capture: majority vote, dispersion as the fraction
disagreeing, and confidence discounted toward 0.5 by that dispersion."""
from __future__ import annotations

import pytest

from council.engine.sampling import aggregate_samples
from council.seats.base import SeatVerdict

_CC = {
    "definition": "test comparison class",
    "n_observations": 10,
    "base_rate": 0.5,
    "why_this_class": "test",
}


def _v(vote, probability, **overrides):
    payload = dict(
        vote=vote,
        probability=probability,
        expected_move_pct=1.0,
        thesis="t",
        what_would_change_my_mind="w",
        data_quality="GOOD",
        abstain_reason=None if vote != "NO_READ" else "test",
        comparison_class=_CC if vote != "NO_READ" else None,
    )
    payload.update(overrides)
    return SeatVerdict(**payload)


def test_unanimous_samples_have_zero_dispersion():
    samples = [_v("BULLISH", 0.61), _v("BULLISH", 0.63), _v("BULLISH", 0.59)]
    result = aggregate_samples("technician", samples)
    assert result.consensus_vote == "BULLISH"
    assert result.dispersion == 0.0
    assert result.representative.probability == 0.61  # median of the three


def test_disagreeing_sample_raises_dispersion_and_discounts_confidence():
    samples = [_v("BULLISH", 0.713), _v("BULLISH", 0.713), _v("BEARISH", 0.713)]
    result = aggregate_samples("technician", samples)
    assert result.consensus_vote == "BULLISH"
    assert result.dispersion == pytest.approx(1 / 3, abs=1e-3)
    # discounted toward 0.5, so strictly less confident than the raw 0.7
    assert 0.5 < result.representative.probability < 0.713


def test_three_way_tie_yields_no_conviction_with_high_dispersion():
    # Real answers that disagree are a genuine "in between", not a failure
    # to read -- NO_CONVICTION, not NO_READ.
    samples = [_v("BULLISH", 0.612), _v("BEARISH", 0.612), _v("NO_READ", 0.5)]
    result = aggregate_samples("technician", samples)
    assert result.consensus_vote == "NO_CONVICTION"
    assert result.representative.vote == "NO_CONVICTION"
    # No sample matches the synthesized consensus: full disagreement, the
    # same reading the two-sample split below gets.
    assert result.dispersion == pytest.approx(1.0, abs=1e-3)


def test_majority_no_read_is_no_read():
    samples = [_v("NO_READ", 0.5), _v("NO_READ", 0.5), _v("BULLISH", 0.612)]
    result = aggregate_samples("technician", samples)
    assert result.consensus_vote == "NO_READ"
    assert result.dispersion == pytest.approx(1 / 3, abs=1e-3)


def test_full_disagreement_falls_back_to_synthesized_no_conviction():
    # two-sample edge case with no natural abstention among samples but a tie
    samples = [_v("BULLISH", 0.612), _v("BEARISH", 0.612)]
    result = aggregate_samples("technician", samples)
    assert result.consensus_vote == "NO_CONVICTION"
    assert result.representative.vote == "NO_CONVICTION"
    assert result.representative.abstain_reason == "run_to_run_disagreement"
