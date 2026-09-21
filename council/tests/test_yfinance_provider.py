"""fetch_option_chain used to be a permanent empty stub -- yfinance's real
Ticker.options / Ticker.option_chain() genuinely works, so it's wired up
for real now. Can't hit the real Yahoo endpoint from this sandbox (network
policy blocks it same as it blocks most external hosts), so this verifies
the parsing logic against a faked yfinance module shaped like the real
one's return values -- NaN handling, put/call volume ratio, field mapping.

Same caveat, more so, for fetch_fundamentals / fetch_institutional_holdings
/ fetch_analyst_estimates below: these lean on yfinance's Ticker.info dict
and several purpose-built DataFrames that aren't a documented, stable API
-- field availability varies by ticker and yfinance version. These tests
verify the extraction/mapping logic is correct given a plausible real
shape; they cannot verify that shape is what Yahoo actually returns today."""
from __future__ import annotations

import sys
import types
from datetime import datetime
from unittest.mock import MagicMock

import pandas as pd
import pytest

from council.data.providers.yfinance_provider import YFinanceProvider, _safe_float


def test_safe_float_normalises_none_and_nan():
    assert _safe_float(None) is None
    assert _safe_float(float("nan")) is None
    assert _safe_float("3.5") == 3.5
    assert _safe_float(0) == 0.0


@pytest.fixture
def fake_yfinance(monkeypatch):
    calls_df = pd.DataFrame(
        [
            {"strike": 100.0, "bid": 2.1, "ask": 2.3, "lastPrice": 2.2, "volume": 50, "openInterest": 200, "impliedVolatility": 0.32},
            {"strike": 105.0, "bid": float("nan"), "ask": float("nan"), "lastPrice": float("nan"), "volume": float("nan"), "openInterest": float("nan"), "impliedVolatility": float("nan")},
        ]
    )
    puts_df = pd.DataFrame(
        [{"strike": 95.0, "bid": 1.5, "ask": 1.7, "lastPrice": 1.6, "volume": 20, "openInterest": 80, "impliedVolatility": 0.29}]
    )

    fake_chain = MagicMock()
    fake_chain.calls = calls_df
    fake_chain.puts = puts_df
    fake_chain.underlying = {"regularMarketPrice": 101.5}

    fake_ticker = MagicMock()
    fake_ticker.options = ("2026-10-16", "2026-11-20")
    fake_ticker.option_chain.return_value = fake_chain

    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = MagicMock(return_value=fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)
    return fake_ticker


@pytest.mark.asyncio
async def test_option_chain_parses_real_and_nan_rows(fake_yfinance):
    provider = YFinanceProvider()
    result = await provider.fetch_option_chain("MRVL")

    assert result["underlying_price"] == 101.5
    assert len(result["contracts"]) == 3

    call_100 = next(c for c in result["contracts"] if c["strike"] == 100.0)
    assert call_100["option_type"] == "call"
    assert call_100["bid"] == 2.1
    assert call_100["expiry"] == "2026-10-16"  # nearest expiry only

    nan_row = next(c for c in result["contracts"] if c["strike"] == 105.0)
    assert nan_row["bid"] is None
    assert nan_row["open_interest"] is None
    assert nan_row["volume"] == 0

    # put_call_ratio = total put volume / total call volume = 20 / 50
    assert result["put_call_ratio"] == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_option_chain_with_no_expiries_returns_empty_not_an_error(monkeypatch):
    fake_ticker = MagicMock()
    fake_ticker.options = ()
    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = MagicMock(return_value=fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)

    provider = YFinanceProvider()
    result = await provider.fetch_option_chain("OBSCURETICKER")
    assert result == {"underlying_price": 0.0, "contracts": [], "put_call_ratio": None}


@pytest.mark.asyncio
async def test_fundamentals_maps_info_dict(monkeypatch):
    info = {
        "totalRevenue": 5_500_000_000,
        "revenueGrowth": 0.34,
        "grossMargins": 0.62,
        "operatingMargins": 0.28,
        "profitMargins": 0.19,
        "totalDebt": 1_200_000_000,
        "totalCash": 900_000_000,
        "freeCashflow": 700_000_000,
        "trailingPE": 42.5,
        "enterpriseToEbitda": 25.1,
        "priceToSalesTrailing12Months": 9.8,
    }
    fake_income_stmt = pd.DataFrame(
        {pd.Timestamp("2026-06-30"): [1.0], pd.Timestamp("2026-03-31"): [1.0]}
    )

    fake_ticker = MagicMock()
    fake_ticker.info = info
    fake_ticker.quarterly_income_stmt = fake_income_stmt
    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = MagicMock(return_value=fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)

    provider = YFinanceProvider()
    result = await provider.fetch_fundamentals("MRVL")

    assert result["revenue"] == 5_500_000_000
    assert result["gross_margin"] == 0.62
    assert result["pe_ratio"] == 42.5
    assert result["sector_median_pe"] is None
    assert "2026-06-30" in result["fiscal_period"]


@pytest.mark.asyncio
async def test_fundamentals_survives_empty_info(monkeypatch):
    fake_ticker = MagicMock()
    fake_ticker.info = {}
    fake_ticker.quarterly_income_stmt = pd.DataFrame()
    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = MagicMock(return_value=fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)

    provider = YFinanceProvider()
    result = await provider.fetch_fundamentals("OBSCURETICKER")

    assert result["revenue"] == 0.0
    assert result["pe_ratio"] is None
    assert result["fiscal_period"] == "unknown"


@pytest.mark.asyncio
async def test_institutional_holdings_maps_holders_and_shorts(monkeypatch):
    holders_df = pd.DataFrame(
        {
            "Holder": ["Vanguard", "BlackRock"],
            "Shares": [10_000_000, 8_000_000],
            "Date Reported": [pd.Timestamp("2026-06-30"), pd.Timestamp("2026-06-30")],
        }
    )
    major_df = pd.DataFrame({"Value": [0.75, 0.05]}, index=["institutionsPercentHeld", "insidersPercentHeld"])

    fake_ticker = MagicMock()
    fake_ticker.info = {"sharesShort": 2_000_000, "shortRatio": 1.8}
    fake_ticker.institutional_holders = holders_df
    fake_ticker.major_holders = major_df
    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = MagicMock(return_value=fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)

    provider = YFinanceProvider()
    result = await provider.fetch_institutional_holdings("MRVL")

    assert result["total_institutional_shares"] == 18_000_000
    assert result["pct_of_float_held"] == 0.75
    assert result["short_interest_shares"] == 2_000_000
    assert result["days_to_cover"] == 1.8
    assert result["quarter_end"] == "2026-06-30"


@pytest.mark.asyncio
async def test_institutional_holdings_survives_missing_tables(monkeypatch):
    fake_ticker = MagicMock()
    fake_ticker.info = {}
    fake_ticker.institutional_holders = None
    fake_ticker.major_holders = None
    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = MagicMock(return_value=fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)

    provider = YFinanceProvider()
    result = await provider.fetch_institutional_holdings("OBSCURETICKER")

    assert result["total_institutional_shares"] == 0
    assert result["pct_of_float_held"] == 0.0
    assert result["short_interest_shares"] == 0


@pytest.mark.asyncio
async def test_analyst_estimates_maps_all_sources(monkeypatch):
    earnings_est = pd.DataFrame({"avg": [1.25]}, index=["0q"])
    revenue_est = pd.DataFrame({"avg": [1_800_000_000]}, index=["0q"])
    eps_trend = pd.DataFrame({"current": [1.25], "30daysAgo": [1.10]}, index=["0q"])
    earnings_history = pd.DataFrame(
        {"surprisePercent": [0.05, -0.02, 0.08, 0.01]},
        index=[
            pd.Timestamp("2025-12-15"),
            pd.Timestamp("2026-03-15"),
            pd.Timestamp("2026-06-15"),
            pd.Timestamp("2026-09-15"),
        ],
    )

    fake_ticker = MagicMock()
    fake_ticker.earnings_estimate = earnings_est
    fake_ticker.revenue_estimate = revenue_est
    fake_ticker.eps_trend = eps_trend
    fake_ticker.analyst_price_targets = {"mean": 100.0, "high": 130.0, "low": 70.0}
    fake_ticker.earnings_history = earnings_history
    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = MagicMock(return_value=fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)

    provider = YFinanceProvider()
    result = await provider.fetch_analyst_estimates("MRVL")

    assert result["consensus_eps_next_q"] == 1.25
    assert result["consensus_revenue_next_q"] == 1_800_000_000
    assert result["eps_revision_pct_30d"] == pytest.approx(13.64, abs=0.01)
    assert result["price_target_mean"] == 100.0
    assert result["price_target_dispersion"] == pytest.approx(0.6)
    assert len(result["historical_surprises"]) == 4
    assert result["historical_surprises"][-1]["surprise_pct"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_analyst_estimates_survives_missing_tables(monkeypatch):
    fake_ticker = MagicMock()
    fake_ticker.earnings_estimate = pd.DataFrame()
    fake_ticker.revenue_estimate = pd.DataFrame()
    fake_ticker.eps_trend = pd.DataFrame()
    fake_ticker.analyst_price_targets = {}
    fake_ticker.earnings_history = pd.DataFrame()
    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = MagicMock(return_value=fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)

    provider = YFinanceProvider()
    result = await provider.fetch_analyst_estimates("OBSCURETICKER")

    assert result["consensus_eps_next_q"] == 0.0
    assert result["price_target_dispersion"] == 0.0
    assert result["historical_surprises"] == []
