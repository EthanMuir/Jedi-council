"""fetch_option_chain used to be a permanent empty stub -- yfinance's real
Ticker.options / Ticker.option_chain() genuinely works, so it's wired up
for real now. Can't hit the real Yahoo endpoint from this sandbox (network
policy blocks it same as it blocks most external hosts), so this verifies
the parsing logic against a faked yfinance module shaped like the real
one's return values -- NaN handling, put/call volume ratio, field mapping."""
from __future__ import annotations

import sys
import types
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
