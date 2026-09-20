"""The Base-Rate Keeper -- "Keeper of What Usually Happens". Not an LLM,
pure computation. For a ticker and horizon: unconditional return
distribution, hit rate of "up", realised vol, ATR-implied range, max
plausible move. Every price target from every seat is checked against this
-- surfaced downstream as the "Reality Anchor"."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Literal

from council.data.schemas import OHLCVBar
from council.engine.horizons import HORIZON_TRADING_DAYS
from council.seats.technical_indicators import _atr


@dataclass
class RealityAnchor:
    horizon: str
    horizon_trading_days: int
    n_observations: int
    hit_rate_up: float | None
    realised_vol_annualised_pct: float | None
    atr_implied_range_pct: float | None
    max_plausible_move_pct: float
    options_implied_move_pct: float | None


def compute_reality_anchor(
    bars: list[OHLCVBar], horizon: str, options_implied_move_pct: float | None = None
) -> RealityAnchor:
    n_days = HORIZON_TRADING_DAYS[horizon]
    closes = [b.close for b in bars]

    daily_returns = [
        (closes[i] / closes[i - 1]) - 1 for i in range(1, len(closes)) if closes[i - 1]
    ]
    realised_vol_annualised_pct = (
        round(statistics.pstdev(daily_returns) * math.sqrt(252) * 100, 2)
        if len(daily_returns) >= 2
        else None
    )

    rolling_returns = [
        (closes[i] / closes[i - n_days]) - 1
        for i in range(n_days, len(closes))
        if closes[i - n_days]
    ]
    n_observations = len(rolling_returns)
    if n_observations >= 5:
        hit_rate_up = round(sum(1 for r in rolling_returns if r > 0) / n_observations, 3)
        abs_returns_pct = sorted(abs(r) * 100 for r in rolling_returns)
        # 95th percentile of |N-day return| as the "max plausible move" --
        # robust to a single historical outlier in a way max() is not.
        idx = min(len(abs_returns_pct) - 1, math.ceil(0.95 * len(abs_returns_pct)) - 1)
        max_plausible_move_pct = round(abs_returns_pct[idx], 2)
    else:
        hit_rate_up = None
        max_plausible_move_pct = 0.0

    atr14 = _atr(bars, 14)
    atr_implied_range_pct = (
        round((atr14 / closes[-1]) * math.sqrt(n_days) * 100, 2) if atr14 and closes else None
    )

    # Fall back to a 3x ATR-implied range when there isn't enough rolling
    # history yet for a robust percentile (short fixture windows, new
    # listings) -- never leave max_plausible_move_pct at a hard 0, which
    # would flag every real target IMPLAUSIBLE.
    if n_observations < 5 and atr_implied_range_pct:
        max_plausible_move_pct = round(atr_implied_range_pct * 3, 2)

    return RealityAnchor(
        horizon=horizon,
        horizon_trading_days=n_days,
        n_observations=n_observations,
        hit_rate_up=hit_rate_up,
        realised_vol_annualised_pct=realised_vol_annualised_pct,
        atr_implied_range_pct=atr_implied_range_pct,
        max_plausible_move_pct=max_plausible_move_pct,
        options_implied_move_pct=options_implied_move_pct,
    )


def check_plausibility(
    expected_move_pct: float | None, anchor: RealityAnchor
) -> Literal["PLAUSIBLE", "IMPLAUSIBLE", "UNKNOWN"]:
    if expected_move_pct is None or anchor.max_plausible_move_pct <= 0:
        return "UNKNOWN"
    return "PLAUSIBLE" if abs(expected_move_pct) <= anchor.max_plausible_move_pct else "IMPLAUSIBLE"
