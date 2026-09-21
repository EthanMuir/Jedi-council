"""yfinance fallback -- OHLCV, news (stubbed, Yahoo has no clean feed for
it), and options. Free, no key, no official rate limit, per spec: "free,
unreliable, use as backstop only". Options previously returned an empty
stub; yfinance's Ticker.options / Ticker.option_chain() genuinely works,
so it's wired up for real now -- nearest expiry only, enough for a current
IV / put-call read without pulling every future date's full chain."""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any


def _safe_float(value: Any) -> float | None:
    """None and NaN (pandas' way of saying "no bid/ask/OI for this
    contract") both mean "we don't have this number" -- normalise both to
    None rather than letting NaN silently pass Pydantic's float validation."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN never equals itself


class YFinanceProvider:
    name = "yfinance"

    async def fetch_ohlcv(self, ticker: str, start: date, end: date) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._fetch_sync, ticker, start, end)

    def _fetch_sync(self, ticker: str, start: date, end: date) -> list[dict[str, Any]]:
        import yfinance as yf

        hist = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat())
        bars = []
        for idx, row in hist.iterrows():
            bars.append(
                {
                    "trade_date": idx.date().isoformat(),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "adjusted_close": float(row["Close"]),
                    "volume": int(row["Volume"]),
                }
            )
        return bars

    async def fetch_news(self, ticker, start, end) -> list[dict[str, Any]]:
        return []

    async def fetch_option_chain(self, ticker: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_option_chain_sync, ticker)

    def _fetch_option_chain_sync(self, ticker: str) -> dict[str, Any]:
        import yfinance as yf

        t = yf.Ticker(ticker)
        expiries = t.options
        if not expiries:
            return {"underlying_price": 0.0, "contracts": [], "put_call_ratio": None}

        expiry = expiries[0]
        chain = t.option_chain(expiry)

        underlying_price = 0.0
        try:
            underlying_price = float(chain.underlying.get("regularMarketPrice", 0.0) or 0.0)
        except (AttributeError, TypeError, ValueError):
            pass

        contracts = []
        total_call_volume = 0
        total_put_volume = 0
        for option_type, df in (("call", chain.calls), ("put", chain.puts)):
            for _, row in df.iterrows():
                volume = int(_safe_float(row.get("volume")) or 0)
                if option_type == "call":
                    total_call_volume += volume
                else:
                    total_put_volume += volume
                contracts.append(
                    {
                        "strike": float(row["strike"]),
                        "expiry": expiry,
                        "option_type": option_type,
                        "bid": _safe_float(row.get("bid")),
                        "ask": _safe_float(row.get("ask")),
                        "last": _safe_float(row.get("lastPrice")),
                        "volume": volume,
                        "open_interest": int(_safe_float(row.get("openInterest")) or 0) or None,
                        "implied_volatility": _safe_float(row.get("impliedVolatility")),
                    }
                )

        put_call_ratio = (total_put_volume / total_call_volume) if total_call_volume else None
        return {
            "underlying_price": underlying_price,
            "contracts": contracts,
            "put_call_ratio": put_call_ratio,
        }
