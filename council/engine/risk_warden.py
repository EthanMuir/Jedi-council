"""The Risk Warden -- "Keeper of the Shields". Sizing only, never
direction. ATR/IV-based position size for a fixed risk budget,
fractional-Kelly cap (default quarter-Kelly), and a concentration check
against other open Crypt positions.

Addendum A9 wall, enforced at the signature level, not just by convention:
this function takes no vote, probability, or direction of any kind as
input. It cannot see the directional tally before sizing because it is
never given it. Sizing is computed from the ATR/IV-implied distribution and
open positions, nothing else."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskSizing:
    position_size_pct_of_book: float
    risk_budget_pct: float
    kelly_cap_pct: float
    vol_estimate_pct: float
    concentration_warning: str | None


def size_position(
    *,
    atr_implied_range_pct: float | None,
    options_implied_move_pct: float | None,
    risk_budget_pct: float,
    kelly_cap: float,
    ticker: str,
    other_open_tickers: list[str],
) -> RiskSizing:
    candidates = [v for v in (atr_implied_range_pct, options_implied_move_pct) if v and v > 0]
    vol_estimate_pct = max(candidates) if candidates else None

    if not vol_estimate_pct:
        size_pct = 0.0
    else:
        # Crude inverse-volatility sizing: risk_budget_pct is the fraction of
        # book willing to lose if the move is exactly vol_estimate_pct against
        # the position, capped by the fractional-Kelly ceiling.
        raw_size_pct = (risk_budget_pct / vol_estimate_pct) * 100
        size_pct = min(raw_size_pct, kelly_cap * 100)

    same_ticker_exposure = [t for t in other_open_tickers if t == ticker]
    concentration_warning = (
        f"{len(same_ticker_exposure)} other open Crypt position(s) already exist for {ticker}"
        if same_ticker_exposure
        else None
    )

    return RiskSizing(
        position_size_pct_of_book=round(size_pct, 2),
        risk_budget_pct=risk_budget_pct,
        kelly_cap_pct=round(kelly_cap * 100, 2),
        vol_estimate_pct=round(vol_estimate_pct, 2) if vol_estimate_pct else 0.0,
        concentration_warning=concentration_warning,
    )
