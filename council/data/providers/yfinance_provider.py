"""yfinance fallback for OHLCV only, per spec: 'free, unreliable, use as
backstop only'. No news or options coverage."""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any


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
        return {"underlying_price": 0.0, "contracts": [], "put_call_ratio": None}
