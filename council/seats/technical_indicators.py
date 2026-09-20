"""Deterministic core for the Technician seat. Direction is mechanically
computed here, one vote per family (trend, breakout, oscillator, volume);
the LLM's job downstream is only to narrate the configuration and place
entry/exit/invalidation from ATR and recent structure, never to judge
direction itself."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from council.data.schemas import OHLCVBar

FamilyVote = Literal["BULLISH", "BEARISH", "FLAT"]


@dataclass
class FamilyVotes:
    trend: FamilyVote
    breakout: FamilyVote
    oscillator: FamilyVote
    volume: FamilyVote


@dataclass
class TechnicalReading:
    family_votes: FamilyVotes
    mechanical_vote: Literal["BULLISH", "BEARISH", "NO_READ"]
    sma20: float | None
    sma50: float | None
    rsi14: float | None
    atr14: float | None
    last_close: float
    twenty_day_high: float | None
    twenty_day_low: float | None
    entry: float | None
    exit: float | None
    invalidation: float | None
    expected_move_pct: float | None


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for prev, curr in zip(closes[-(period + 1):-1], closes[-period:]):
        delta = curr - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _atr(bars: list[OHLCVBar], period: int = 14) -> float | None:
    if len(bars) < period + 1:
        return None
    true_ranges = []
    for prev, curr in zip(bars[-(period + 1):-1], bars[-period:]):
        true_ranges.append(
            max(
                curr.high - curr.low,
                abs(curr.high - prev.close),
                abs(curr.low - prev.close),
            )
        )
    return sum(true_ranges) / period


def compute_family_votes(bars: list[OHLCVBar]) -> FamilyVotes:
    closes = [b.close for b in bars]
    sma20, sma50 = _sma(closes, 20), _sma(closes, 50)

    if sma20 is None or sma50 is None:
        trend: FamilyVote = "FLAT"
    else:
        trend = "BULLISH" if sma20 > sma50 else "BEARISH" if sma20 < sma50 else "FLAT"

    if len(closes) >= 21:
        prior_high, prior_low = max(closes[-21:-1]), min(closes[-21:-1])
        if closes[-1] >= prior_high:
            breakout: FamilyVote = "BULLISH"
        elif closes[-1] <= prior_low:
            breakout = "BEARISH"
        else:
            breakout = "FLAT"
    else:
        breakout = "FLAT"

    rsi14 = _rsi(closes, 14)
    if rsi14 is None:
        oscillator: FamilyVote = "FLAT"
    else:
        oscillator = "BULLISH" if rsi14 > 55 else "BEARISH" if rsi14 < 45 else "FLAT"

    if len(bars) >= 21:
        avg_volume = sum(b.volume for b in bars[-21:-1]) / 20
        last = bars[-1]
        if last.volume > avg_volume * 1.1 and last.close > last.open:
            volume: FamilyVote = "BULLISH"
        elif last.volume > avg_volume * 1.1 and last.close < last.open:
            volume = "BEARISH"
        else:
            volume = "FLAT"
    else:
        volume = "FLAT"

    return FamilyVotes(trend=trend, breakout=breakout, oscillator=oscillator, volume=volume)


def mechanical_direction(votes: FamilyVotes) -> Literal["BULLISH", "BEARISH", "NO_READ"]:
    tally = [votes.trend, votes.breakout, votes.oscillator, votes.volume]
    bulls = tally.count("BULLISH")
    bears = tally.count("BEARISH")
    if bulls > bears:
        return "BULLISH"
    if bears > bulls:
        return "BEARISH"
    return "NO_READ"


def analyse(bars: list[OHLCVBar]) -> TechnicalReading:
    if not bars:
        raise ValueError("cannot analyse an empty OHLCV series")

    votes = compute_family_votes(bars)
    direction = mechanical_direction(votes)
    closes = [b.close for b in bars]
    sma20, sma50 = _sma(closes, 20), _sma(closes, 50)
    rsi14 = _rsi(closes, 14)
    atr14 = _atr(bars, 14)
    last_close = closes[-1]
    twenty_day_high = max(closes[-20:]) if len(closes) >= 20 else None
    twenty_day_low = min(closes[-20:]) if len(closes) >= 20 else None

    entry = exit_ = invalidation = expected_move_pct = None
    if direction != "NO_READ" and atr14 is not None:
        entry = round(last_close, 2)
        if direction == "BULLISH":
            exit_ = round(last_close + 2 * atr14, 2)
            invalidation = round(last_close - 1 * atr14, 2)
        else:
            exit_ = round(last_close - 2 * atr14, 2)
            invalidation = round(last_close + 1 * atr14, 2)
        expected_move_pct = round((2 * atr14 / last_close) * 100, 2)

    return TechnicalReading(
        family_votes=votes,
        mechanical_vote=direction,
        sma20=sma20,
        sma50=sma50,
        rsi14=rsi14,
        atr14=atr14,
        last_close=last_close,
        twenty_day_high=twenty_day_high,
        twenty_day_low=twenty_day_low,
        entry=entry,
        exit=exit_,
        invalidation=invalidation,
        expected_move_pct=expected_move_pct,
    )
