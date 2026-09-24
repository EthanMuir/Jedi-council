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


# Task #73 -- chain.underlying.get("regularMarketPrice") alone silently
# fell back to 0.0 on any parsing hiccup, and oracle_options picks its
# "ATM" contract as whichever strike is closest to that number -- a price
# of 0.0 makes that pick the lowest-strike contract in the whole chain
# instead of the real ATM one, very likely the cause of "ATM IV wildly
# inconsistent with the chain" showing up on every ticker tested live so
# far. These test the fallback chain that replaced the single lookup.


@pytest.mark.asyncio
async def test_underlying_price_falls_back_to_fast_info(monkeypatch):
    calls_df = pd.DataFrame(
        [{"strike": 100.0, "bid": 2.1, "ask": 2.3, "lastPrice": 2.2, "volume": 50, "openInterest": 200, "impliedVolatility": 0.32}]
    )
    fake_chain = MagicMock()
    fake_chain.calls = calls_df
    fake_chain.puts = pd.DataFrame(columns=calls_df.columns)
    fake_chain.underlying = {}  # regularMarketPrice missing -- the live failure shape

    fake_ticker = MagicMock()
    fake_ticker.options = ("2026-10-16",)
    fake_ticker.option_chain.return_value = fake_chain
    fake_ticker.fast_info = {"lastPrice": 87.3}
    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = MagicMock(return_value=fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)

    provider = YFinanceProvider()
    result = await provider.fetch_option_chain("NFLX")
    assert result["underlying_price"] == 87.3


@pytest.mark.asyncio
async def test_underlying_price_falls_back_to_info_when_fast_info_also_empty(monkeypatch):
    calls_df = pd.DataFrame(
        [{"strike": 100.0, "bid": 2.1, "ask": 2.3, "lastPrice": 2.2, "volume": 50, "openInterest": 200, "impliedVolatility": 0.32}]
    )
    fake_chain = MagicMock()
    fake_chain.calls = calls_df
    fake_chain.puts = pd.DataFrame(columns=calls_df.columns)
    fake_chain.underlying = None  # some yfinance versions/tickers -- not even a dict

    fake_ticker = MagicMock()
    fake_ticker.options = ("2026-10-16",)
    fake_ticker.option_chain.return_value = fake_chain
    fake_ticker.fast_info = {}
    fake_ticker.info = {"currentPrice": 42.1}
    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = MagicMock(return_value=fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)

    provider = YFinanceProvider()
    result = await provider.fetch_option_chain("MP")
    assert result["underlying_price"] == 42.1


@pytest.mark.asyncio
async def test_underlying_price_zero_when_every_source_is_empty(monkeypatch):
    calls_df = pd.DataFrame(
        [{"strike": 100.0, "bid": 2.1, "ask": 2.3, "lastPrice": 2.2, "volume": 50, "openInterest": 200, "impliedVolatility": 0.32}]
    )
    fake_chain = MagicMock()
    fake_chain.calls = calls_df
    fake_chain.puts = pd.DataFrame(columns=calls_df.columns)
    fake_chain.underlying = {}

    fake_ticker = MagicMock()
    fake_ticker.options = ("2026-10-16",)
    fake_ticker.option_chain.return_value = fake_chain
    fake_ticker.fast_info = {}
    fake_ticker.info = {}
    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = MagicMock(return_value=fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)

    provider = YFinanceProvider()
    result = await provider.fetch_option_chain("OBSCURETICKER")
    assert result["underlying_price"] == 0.0


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


@pytest.mark.asyncio
async def test_analyst_ratings_maps_targets_split_and_changes(monkeypatch):
    fake_ticker = MagicMock()
    fake_ticker.analyst_price_targets = {
        "current": 80.0, "high": 120.0, "low": 60.0, "mean": 100.0, "median": 98.0,
    }
    fake_ticker.info = {"numberOfAnalystOpinions": 31}
    fake_ticker.recommendations_summary = pd.DataFrame([
        {"period": "0m", "strongBuy": 8, "buy": 15, "hold": 6, "sell": 1, "strongSell": 0},
        {"period": "-1m", "strongBuy": 8, "buy": 14, "hold": 7, "sell": 1, "strongSell": 0},
        {"period": "-3m", "strongBuy": 6, "buy": 12, "hold": 10, "sell": 2, "strongSell": 1},
    ])
    fake_ticker.upgrades_downgrades = pd.DataFrame(
        {
            "Firm": ["Older Co", "Newer Co"],
            "ToGrade": ["Hold", "Buy"],
            "FromGrade": ["Buy", "Hold"],
            "Action": ["down", "up"],
            "currentPriceTarget": [85.0, 110.0],
            "priorPriceTarget": [95.0, 90.0],
        },
        index=pd.DatetimeIndex([pd.Timestamp("2026-06-01"), pd.Timestamp("2026-09-10")], name="GradeDate"),
    )
    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = MagicMock(return_value=fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)

    result = await YFinanceProvider().fetch_analyst_ratings("MRVL")

    assert (result["current_price"], result["target_mean"], result["target_median"]) == (80.0, 100.0, 98.0)
    assert result["analyst_count"] == 31
    assert (result["strong_buy"], result["buy"], result["hold"]) == (8, 15, 6)
    assert (result["prior_strong_buy"], result["prior_hold"], result["prior_strong_sell"]) == (6, 10, 1)
    assert [c["firm"] for c in result["recent_changes"]] == ["Newer Co", "Older Co"]  # newest first
    newest = result["recent_changes"][0]
    assert (newest["action"], newest["from_grade"], newest["to_grade"]) == ("up", "Hold", "Buy")
    assert (newest["price_target"], newest["prior_price_target"]) == (110.0, 90.0)
    assert newest["changed_at"].startswith("2026-09-10")


@pytest.mark.asyncio
async def test_analyst_ratings_with_no_coverage_raises_so_the_seat_reads_no_data(monkeypatch):
    fake_ticker = MagicMock()
    fake_ticker.analyst_price_targets = {}
    fake_ticker.info = {}
    fake_ticker.recommendations_summary = pd.DataFrame()
    fake_ticker.upgrades_downgrades = pd.DataFrame()
    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = MagicMock(return_value=fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)

    with pytest.raises(ValueError, match="No analyst coverage"):
        await YFinanceProvider().fetch_analyst_ratings("TINY")


# fetch_news used to be a permanent `return []` stub -- because
# YFinanceProvider is tried first in the provider chain and any
# non-exception result (even an empty list) short-circuits
# DataService._fetch_with_fallback, that stub silently blocked Alpha
# Vantage's real NEWS_SENTIMENT feed from ever being tried. Confirmed live:
# a seat reporting "no news" for a ticker with real recent news. Both of
# yfinance's known news shapes (old flat, new nested-under-"content") are
# tested here since which one a given yfinance version returns isn't
# something this sandbox can check against live Yahoo data.


@pytest.mark.asyncio
async def test_news_parses_new_nested_content_shape(monkeypatch):
    raw_items = [
        {
            "content": {
                "title": "Company announces new contract",
                "summary": "Details of the contract.",
                "pubDate": "2026-09-19T14:30:00Z",
                "provider": {"displayName": "Reuters"},
                "canonicalUrl": {"url": "https://example.com/story"},
            }
        }
    ]
    fake_ticker = MagicMock()
    fake_ticker.news = raw_items
    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = MagicMock(return_value=fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)

    provider = YFinanceProvider()
    items = await provider.fetch_news("MP", datetime(2026, 9, 1), datetime(2026, 9, 21))

    assert len(items) == 1
    assert items[0]["headline"] == "Company announces new contract"
    assert items[0]["source"] == "Reuters"
    assert items[0]["url"] == "https://example.com/story"
    assert items[0]["published_at"] == "2026-09-19T14:30:00"


@pytest.mark.asyncio
async def test_news_parses_legacy_flat_shape(monkeypatch):
    raw_items = [
        {
            "title": "Older-style headline",
            "summary": "Older-style summary.",
            "publisher": "AP",
            "link": "https://example.com/older-story",
            "providerPublishTime": 1758290400,  # unix timestamp
        }
    ]
    fake_ticker = MagicMock()
    fake_ticker.news = raw_items
    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = MagicMock(return_value=fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)

    provider = YFinanceProvider()
    items = await provider.fetch_news("MP", datetime(2026, 9, 1), datetime(2026, 9, 21))

    assert len(items) == 1
    assert items[0]["headline"] == "Older-style headline"
    assert items[0]["source"] == "AP"
    assert items[0]["url"] == "https://example.com/older-story"


@pytest.mark.asyncio
async def test_news_skips_items_missing_required_fields_instead_of_crashing(monkeypatch):
    raw_items = [
        {"content": {"title": None, "summary": "no title, skip me"}},
        {"title": "Has everything", "link": "https://example.com/ok", "providerPublishTime": 1758290400},
        {"title": "No link at all", "providerPublishTime": 1758290400},
    ]
    fake_ticker = MagicMock()
    fake_ticker.news = raw_items
    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = MagicMock(return_value=fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)

    provider = YFinanceProvider()
    items = await provider.fetch_news("MP", datetime(2026, 9, 1), datetime(2026, 9, 21))

    assert len(items) == 1
    assert items[0]["headline"] == "Has everything"


@pytest.mark.asyncio
async def test_news_survives_no_news_attribute_or_empty_list(monkeypatch):
    fake_ticker = MagicMock()
    fake_ticker.news = []
    fake_module = types.ModuleType("yfinance")
    fake_module.Ticker = MagicMock(return_value=fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)

    provider = YFinanceProvider()
    items = await provider.fetch_news("OBSCURETICKER", datetime(2026, 9, 1), datetime(2026, 9, 21))
    assert items == []
