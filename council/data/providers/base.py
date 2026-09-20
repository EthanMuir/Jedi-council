"""Provider protocol. Raw, un-normalised records only -- normalisation and
point-in-time filtering happen once, in DataService, not per-provider."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Protocol


class MarketDataProvider(Protocol):
    name: str

    async def fetch_ohlcv(
        self, ticker: str, start: date, end: date
    ) -> list[dict[str, Any]]: ...

    async def fetch_news(
        self, ticker: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]: ...

    async def fetch_option_chain(self, ticker: str) -> dict[str, Any]: ...
