"""Tier III quality control: Base-Rate Keeper, Cost Auditor, Risk Warden --
all pure computation, no LLM involved."""
from __future__ import annotations

from datetime import date, timedelta

from council.data.schemas import OHLCVBar
from council.engine.base_rate import check_plausibility, compute_reality_anchor
from council.engine.cost_auditor import audit
from council.engine.risk_warden import size_position


def _bars(closes: list[float]) -> list[OHLCVBar]:
    start = date(2026, 1, 1)
    return [
        OHLCVBar(
            trade_date=start + timedelta(days=i),
            open=c * 0.995,
            high=c * 1.01,
            low=c * 0.99,
            close=c,
            adjusted_close=c,
            volume=1_000_000,
        )
        for i, c in enumerate(closes)
    ]


def test_reality_anchor_flags_implausible_target():
    # Tight, low-vol random walk around 100
    import random

    random.seed(1)
    closes = [100.0]
    for _ in range(200):
        closes.append(round(closes[-1] * (1 + random.uniform(-0.005, 0.005)), 2))

    anchor = compute_reality_anchor(_bars(closes), "1w", options_implied_move_pct=1.5)
    assert anchor.n_observations > 0
    assert anchor.max_plausible_move_pct < 10  # tight random walk, no wild swings

    assert check_plausibility(1.0, anchor) == "PLAUSIBLE"
    assert check_plausibility(50.0, anchor) == "IMPLAUSIBLE"
    assert check_plausibility(None, anchor) == "UNKNOWN"


def test_reality_anchor_degrades_gracefully_with_short_history():
    anchor = compute_reality_anchor(_bars([100, 101, 99, 102]), "1w", None)
    assert anchor.n_observations == 0
    # still produces SOME plausible-move ceiling rather than a hard 0 that
    # would flag every real target implausible
    assert anchor.max_plausible_move_pct >= 0


def test_cost_auditor_passes_when_edge_exceeds_spread():
    result = audit(expected_move_pct=3.0, assumed_spread_bps=5.0)
    assert result.passed
    assert result.edge_pct > 0
    assert result.round_trip_cost_pct == 0.1


def test_cost_auditor_fails_when_edge_smaller_than_spread():
    result = audit(expected_move_pct=0.05, assumed_spread_bps=5.0)
    assert not result.passed
    assert result.edge_pct < 0


def test_risk_warden_signature_accepts_no_directional_input():
    """Addendum A9: the function must not even be callable with a vote or
    probability -- assert its only inputs are vol/sizing/concentration."""
    import inspect

    params = set(inspect.signature(size_position).parameters)
    assert "vote" not in params
    assert "probability" not in params
    assert "direction" not in params


def test_risk_warden_caps_at_kelly_ceiling():
    sizing = size_position(
        atr_implied_range_pct=0.5,  # very low vol -> uncapped size would be huge
        options_implied_move_pct=None,
        risk_budget_pct=1.0,
        kelly_cap=0.25,
        ticker="NVDA",
        other_open_tickers=[],
    )
    assert sizing.position_size_pct_of_book == 25.0  # capped, not 200%
    assert sizing.concentration_warning is None


def test_risk_warden_flags_existing_open_position_same_ticker():
    sizing = size_position(
        atr_implied_range_pct=3.0,
        options_implied_move_pct=4.0,
        risk_budget_pct=1.0,
        kelly_cap=0.25,
        ticker="NVDA",
        other_open_tickers=["NVDA", "AAPL"],
    )
    assert sizing.concentration_warning is not None
    assert "NVDA" in sizing.concentration_warning
