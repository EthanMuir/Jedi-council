from datetime import date, timedelta

from council.data.schemas import OHLCVBar
from council.seats.technical_indicators import analyse, mechanical_direction


def _bars_from_closes(closes: list[float]) -> list[OHLCVBar]:
    start = date(2026, 1, 1)
    bars = []
    for i, close in enumerate(closes):
        bars.append(
            OHLCVBar(
                trade_date=start + timedelta(days=i),
                open=close * 0.995,
                high=close * 1.01,
                low=close * 0.99,
                close=close,
                adjusted_close=close,
                volume=1_000_000 + i * 1000,
            )
        )
    return bars


def test_steady_uptrend_is_mechanically_bullish():
    closes = [100 + i * 0.8 for i in range(60)]
    bars = _bars_from_closes(closes)
    reading = analyse(bars)
    assert reading.mechanical_vote == "BULLISH"
    assert reading.entry is not None
    assert reading.exit > reading.entry
    assert reading.invalidation < reading.entry


def test_steady_downtrend_is_mechanically_bearish():
    closes = [100 - i * 0.8 for i in range(60)]
    bars = _bars_from_closes(closes)
    reading = analyse(bars)
    assert reading.mechanical_vote == "BEARISH"
    assert reading.exit < reading.entry
    assert reading.invalidation > reading.entry


def test_insufficient_history_has_no_direction_and_no_trend_family():
    # The technician turns this into NO_READ (too little history to read)
    # rather than NO_CONVICTION, keyed off sma50 being uncomputable.
    bars = _bars_from_closes([100, 101, 99, 100.5])
    reading = analyse(bars)
    assert reading.mechanical_vote == "NO_CONVICTION"
    assert reading.sma50 is None
    assert reading.entry is None


def test_mechanical_direction_ties_to_no_conviction():
    from council.seats.technical_indicators import FamilyVotes

    votes = FamilyVotes(trend="BULLISH", breakout="BEARISH", oscillator="FLAT", volume="FLAT")
    assert mechanical_direction(votes) == "NO_CONVICTION"
