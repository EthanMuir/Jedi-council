"""Resolution sequencing: "be precise here, most systems get this wrong."
A stop broken before the target is a loss even if price later reaches the
target -- these tests exist specifically to catch that class of bug."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from council.crypt.resolution import resolve_prediction
from council.data.schemas import OHLCVBar


def _bar(day_offset, o, h, l, c, start=date(2026, 1, 1)):
    return OHLCVBar(
        trade_date=start + timedelta(days=day_offset),
        open=o, high=h, low=l, close=c, adjusted_close=c, volume=1_000_000,
    )


def test_bullish_exit_hit_before_invalidation():
    bars = [
        _bar(0, 100, 101, 99, 100),   # entry ~100 touched
        _bar(1, 100, 102, 100, 101),
        _bar(2, 101, 110, 101, 109),  # exit (108) reached here
        _bar(3, 109, 109, 90, 91),    # invalidation (95) breached AFTER exit already hit
    ]
    outcome = resolve_prediction(
        bars, price_at_prediction=100.0, council_vote="BULLISH", council_confidence=0.6,
        entry=100.0, exit=108.0, invalidation=95.0,
    )
    assert outcome.entry_hit is True
    assert outcome.exit_hit is True
    assert outcome.invalidation_hit is False  # sequence matters -- exit already won


def test_bullish_invalidation_hit_before_exit_is_a_loss_even_if_target_reached_later():
    """The exact scenario the spec calls out: stop breaks first, target is
    reached later -- must resolve as a loss, not a win."""
    bars = [
        _bar(0, 100, 101, 99, 100),    # entry touched
        _bar(1, 100, 100, 90, 91),     # invalidation (95) breached FIRST
        _bar(2, 91, 115, 91, 114),     # exit (108) technically reached later -- must not count
    ]
    outcome = resolve_prediction(
        bars, price_at_prediction=100.0, council_vote="BULLISH", council_confidence=0.6,
        entry=100.0, exit=108.0, invalidation=95.0,
    )
    assert outcome.entry_hit is True
    assert outcome.invalidation_hit is True
    assert outcome.exit_hit is False


def test_bearish_mirror_sequence():
    bars = [
        _bar(0, 100, 101, 99, 100),   # entry touched
        _bar(1, 100, 101, 92, 93),    # exit (92) reached (price falling)
        _bar(2, 93, 112, 93, 111),    # invalidation (110) breached after
    ]
    outcome = resolve_prediction(
        bars, price_at_prediction=100.0, council_vote="BEARISH", council_confidence=0.6,
        entry=100.0, exit=92.0, invalidation=110.0,
    )
    assert outcome.exit_hit is True
    assert outcome.invalidation_hit is False


def test_entry_never_triggered_means_nothing_can_hit():
    bars = [_bar(0, 100, 101, 99, 100), _bar(1, 100, 130, 100, 129)]  # never touches entry=50
    outcome = resolve_prediction(
        bars, price_at_prediction=100.0, council_vote="BULLISH", council_confidence=0.6,
        entry=50.0, exit=200.0, invalidation=40.0,
    )
    assert outcome.entry_hit is False
    assert outcome.exit_hit is False
    assert outcome.invalidation_hit is False


@pytest.mark.parametrize(
    "vote,resolve_close,expected",
    [("BULLISH", 105.0, True), ("BULLISH", 95.0, False), ("BEARISH", 95.0, True), ("BEARISH", 105.0, False)],
)
def test_direction_correct(vote, resolve_close, expected):
    bars = [_bar(0, 100, 106, 94, resolve_close)]
    outcome = resolve_prediction(
        bars, price_at_prediction=100.0, council_vote=vote, council_confidence=0.7,
        entry=None, exit=None, invalidation=None,
    )
    assert outcome.direction_correct is expected


def test_no_conviction_has_no_direction_correct_or_brier():
    bars = [_bar(0, 100, 106, 94, 103)]
    outcome = resolve_prediction(
        bars, price_at_prediction=100.0, council_vote="NO_CONVICTION", council_confidence=None,
        entry=None, exit=None, invalidation=None,
    )
    assert outcome.direction_correct is None
    assert outcome.brier is None


def test_brier_score_computed_correctly():
    bars = [_bar(0, 100, 106, 94, 105)]  # bullish call, price went up -- correct
    outcome = resolve_prediction(
        bars, price_at_prediction=100.0, council_vote="BULLISH", council_confidence=0.7,
        entry=None, exit=None, invalidation=None,
    )
    assert outcome.direction_correct is True
    assert outcome.brier == pytest.approx((0.7 - 1.0) ** 2, abs=1e-6)


def test_mfe_mae_bullish_convention():
    bars = [_bar(0, 100, 112, 90, 105)]  # entry anchor 100, high 112, low 90
    outcome = resolve_prediction(
        bars, price_at_prediction=100.0, council_vote="BULLISH", council_confidence=0.6,
        entry=100.0, exit=200.0, invalidation=1.0,
    )
    assert outcome.mfe_pct == pytest.approx(12.0, abs=0.01)
    assert outcome.mae_pct == pytest.approx(-10.0, abs=0.01)


def test_mfe_mae_bearish_convention_favourable_is_price_falling():
    bars = [_bar(0, 100, 112, 90, 95)]  # entry anchor 100, high 112, low 90
    outcome = resolve_prediction(
        bars, price_at_prediction=100.0, council_vote="BEARISH", council_confidence=0.6,
        entry=100.0, exit=1.0, invalidation=200.0,
    )
    # favourable = price falling to 90 -> +11.1%; adverse = price rising to 112 -> -10.7%
    assert outcome.mfe_pct > 0
    assert outcome.mae_pct < 0


def test_raises_on_empty_bars():
    with pytest.raises(ValueError):
        resolve_prediction(
            [], price_at_prediction=100.0, council_vote="BULLISH", council_confidence=0.6,
            entry=None, exit=None, invalidation=None,
        )
