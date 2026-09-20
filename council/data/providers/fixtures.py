"""Recorded-fixture provider. Used whenever USE_DATA_FIXTURES resolves true
(no Alpha Vantage / FMP key configured) so DataService, the point-in-time
guard, and the seats can all be exercised end to end offline."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "market_data"


class FixtureProvider:
    name = "fixtures"

    def __init__(self, fixture_dir: Path = _FIXTURE_DIR):
        self._dir = fixture_dir

    def _load(self, ticker: str, kind: str) -> Any:
        path = self._dir / f"{ticker.upper()}_{kind}.json"
        if not path.exists():
            path = self._dir / f"DEFAULT_{kind}.json"
        with open(path) as f:
            return json.load(f)

    async def fetch_ohlcv(self, ticker: str, start: date, end: date) -> list[dict[str, Any]]:
        bars = self._load(ticker, "ohlcv")
        return [b for b in bars if start.isoformat() <= b["trade_date"] <= end.isoformat()]

    async def fetch_news(
        self, ticker: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        items = self._load(ticker, "news")
        return [
            n
            for n in items
            if start.isoformat() <= n["published_at"] <= end.isoformat()
        ]

    async def fetch_option_chain(self, ticker: str) -> dict[str, Any]:
        return self._load(ticker, "options")

    async def fetch_fundamentals(self, ticker: str) -> dict[str, Any]:
        return self._load(ticker, "fundamentals")

    async def fetch_insider_transactions(
        self, ticker: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        txns = self._load(ticker, "insider")
        return [
            t
            for t in txns
            if start.isoformat() <= t["filed_at"][:10] <= end.isoformat()
        ]

    async def fetch_congress_data(self, ticker: str, start: date, end: date) -> dict[str, Any]:
        data = self._load(ticker, "congress")
        trades = [
            t
            for t in data["trades"]
            if start.isoformat() <= t["filed_at"][:10] <= end.isoformat()
        ]
        return {"trades": trades, "pending_legislation": data.get("pending_legislation", [])}

    async def fetch_institutional_holdings(self, ticker: str) -> dict[str, Any]:
        return self._load(ticker, "institutional")

    async def fetch_macro(self) -> dict[str, Any]:
        return self._load("DEFAULT", "macro")

    async def fetch_analyst_estimates(self, ticker: str) -> dict[str, Any]:
        return self._load(ticker, "estimates")

    async def fetch_earnings_transcripts(self, ticker: str, limit: int = 4) -> list[dict[str, Any]]:
        calls = self._load(ticker, "transcripts")
        return calls[:limit]

    async def fetch_sec_filings(self, ticker: str, start: date, end: date) -> list[dict[str, Any]]:
        filings = self._load(ticker, "filings")
        return [
            f
            for f in filings
            if start.isoformat() <= f["filed_at"][:10] <= end.isoformat()
        ]
