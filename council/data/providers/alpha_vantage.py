"""Alpha Vantage adapter. Endpoints used by the Phase 1 seats:
TIME_SERIES_DAILY_ADJUSTED, NEWS_SENTIMENT, REALTIME_OPTIONS,
REALTIME_PUT_CALL_RATIO. Extend with the remaining endpoints in the spec's
provider table as later-tier seats are built."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx

from council.data.rate_limit import TokenBucket

_BASE_URL = "https://www.alphavantage.co/query"

# Alpha Vantage's rate limit (free tier especially) doesn't surface as an
# HTTP error -- it's a 200 OK with one of these keys instead of the
# expected payload. Left undetected, that parses as "zero results found",
# a *successful* empty response that short-circuits provider fallback
# (DataService._fetch_with_fallback returns on the first non-exception
# result) instead of correctly falling through to the next provider.
_LIMIT_OR_ERROR_KEYS = ("Note", "Information", "Error Message")


class AlphaVantageRateLimited(Exception):
    """Raised when Alpha Vantage's response body -- not its HTTP status --
    indicates a rate limit or error, so the fallback/retry machinery in
    DataService treats it as a real failure instead of an empty success."""


class AlphaVantageProvider:
    name = "alpha_vantage"

    def __init__(self, api_key: str, requests_per_minute: int = 75):
        self._api_key = api_key
        self._limiter = TokenBucket(rate_per_second=requests_per_minute / 60)
        self._client = httpx.AsyncClient(timeout=30)

    async def _get(self, params: dict[str, str]) -> dict[str, Any]:
        await self._limiter.acquire()
        params = {**params, "apikey": self._api_key}
        resp = await self._client.get(_BASE_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            for key in _LIMIT_OR_ERROR_KEYS:
                if key in data:
                    raise AlphaVantageRateLimited(f"Alpha Vantage {key}: {data[key]}")
        return data

    async def fetch_ohlcv(self, ticker: str, start: date, end: date) -> list[dict[str, Any]]:
        data = await self._get(
            {
                "function": "TIME_SERIES_DAILY_ADJUSTED",
                "symbol": ticker,
                "outputsize": "full",
            }
        )
        series = data.get("Time Series (Daily)", {})
        bars = []
        for day, row in series.items():
            if not (start.isoformat() <= day <= end.isoformat()):
                continue
            bars.append(
                {
                    "trade_date": day,
                    "open": float(row["1. open"]),
                    "high": float(row["2. high"]),
                    "low": float(row["3. low"]),
                    "close": float(row["4. close"]),
                    "adjusted_close": float(row["5. adjusted close"]),
                    "volume": int(row["6. volume"]),
                }
            )
        return sorted(bars, key=lambda b: b["trade_date"])

    async def fetch_news(
        self, ticker: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        data = await self._get(
            {
                "function": "NEWS_SENTIMENT",
                "tickers": ticker,
                "time_from": start.strftime("%Y%m%dT%H%M"),
                "time_to": end.strftime("%Y%m%dT%H%M"),
            }
        )
        items = []
        for entry in data.get("feed", []):
            published = datetime.strptime(entry["time_published"], "%Y%m%dT%H%M%S")
            ticker_sentiment = next(
                (
                    t
                    for t in entry.get("ticker_sentiment", [])
                    if t.get("ticker") == ticker
                ),
                None,
            )
            items.append(
                {
                    "headline": entry["title"],
                    "summary": entry.get("summary", ""),
                    "source": entry.get("source", "alpha_vantage"),
                    "url": entry.get("url", ""),
                    "published_at": published.isoformat(),
                    "sentiment_score": float(ticker_sentiment["ticker_sentiment_score"])
                    if ticker_sentiment
                    else None,
                    "sentiment_label": ticker_sentiment["ticker_sentiment_label"]
                    if ticker_sentiment
                    else None,
                }
            )
        return items

    async def fetch_option_chain(self, ticker: str) -> dict[str, Any]:
        chain_data, pcr_data = (
            await self._get({"function": "REALTIME_OPTIONS", "symbol": ticker}),
            await self._get({"function": "REALTIME_PUT_CALL_RATIO", "symbol": ticker}),
        )
        contracts = []
        for row in chain_data.get("data", []):
            contracts.append(
                {
                    "strike": float(row["strike"]),
                    "expiry": row["expiration"],
                    "option_type": row["type"],
                    "bid": float(row["bid"]) if row.get("bid") else None,
                    "ask": float(row["ask"]) if row.get("ask") else None,
                    "last": float(row["last"]) if row.get("last") else None,
                    "volume": int(row["volume"]) if row.get("volume") else None,
                    "open_interest": int(row["open_interest"])
                    if row.get("open_interest")
                    else None,
                    "implied_volatility": float(row["implied_volatility"])
                    if row.get("implied_volatility")
                    else None,
                }
            )
        pcr_list = pcr_data.get("data", [])
        return {
            "underlying_price": float(chain_data.get("underlying_price", 0.0) or 0.0),
            "contracts": contracts,
            "put_call_ratio": float(pcr_list[0]["put_call_volume_ratio"]) if pcr_list else None,
        }

    async def fetch_macro(self) -> dict[str, Any]:
        """Feeds the Macro Sage. `dollar_index` is a rough USD/EUR-based
        proxy, not a real DXY basket -- AV has no direct DXY endpoint."""
        ten_y, two_y, fed_funds, cpi, unemployment, wti, usd_eur = (
            await self._get({"function": "TREASURY_YIELD", "interval": "monthly", "maturity": "10year"}),
            await self._get({"function": "TREASURY_YIELD", "interval": "monthly", "maturity": "2year"}),
            await self._get({"function": "FEDERAL_FUNDS_RATE", "interval": "monthly"}),
            await self._get({"function": "CPI", "interval": "monthly"}),
            await self._get({"function": "UNEMPLOYMENT"}),
            await self._get({"function": "WTI", "interval": "monthly"}),
            await self._get({"function": "CURRENCY_EXCHANGE_RATE", "from_currency": "USD", "to_currency": "EUR"}),
        )

        def _latest(payload: dict[str, Any]) -> float:
            series = payload.get("data", [])
            return float(series[0]["value"]) if series else 0.0

        rate = usd_eur.get("Realtime Currency Exchange Rate", {})
        return {
            "treasury_10y": _latest(ten_y),
            "treasury_2y": _latest(two_y),
            "fed_funds_rate": _latest(fed_funds),
            "cpi_yoy": _latest(cpi),
            "unemployment_rate": _latest(unemployment),
            "wti_crude": _latest(wti),
            "dollar_index": float(rate.get("5. Exchange Rate", 0.0) or 0.0) * 100,
        }


def _amount_range(low: str | None, high: str | None) -> str:
    def money(v: str | None) -> str | None:
        try:
            return f"${float(v):,.0f}"
        except (TypeError, ValueError):
            return None

    lo, hi = money(low), money(high)
    if lo and hi:
        return f"{lo}-{hi}"
    if lo:
        return f"over {lo}"
    return "amount not given"


_CHAMBERS = {"HOUSE": "House", "SENATE": "Senate"}
_TRANSACTION_TYPES = {"BUY": "BUY", "PURCHASE": "BUY", "SELL": "SELL", "SALE": "SELL", "EXCHANGE": "EXCHANGE"}


class AlphaVantageCongressProvider:
    """Congressional trades only (House and Senate, from the STOCK Act
    disclosures), via CONGRESS_TRADES -- available on the free key.

    Deliberately exposes nothing else (it wraps AlphaVantageProvider rather
    than subclassing it): DataService tries providers in order for every
    data domain, and letting a failed yfinance call fall through to Alpha
    Vantage's other endpoints would spend the free key's 25 requests a day
    -- and several of those endpoints need a paid plan anyway."""

    name = "alpha_vantage"

    def __init__(self, api_key: str):
        self._av = AlphaVantageProvider(api_key)

    async def fetch_congress_data(self, ticker: str, start: date, end: date) -> dict[str, Any]:
        data = await self._av._get({"function": "CONGRESS_TRADES", "symbol": ticker})
        trades = []
        for row in data.get("trades", []) if isinstance(data, dict) else []:
            filed = row.get("filed_date") or row.get("notification_date")
            traded = row.get("transaction_date")
            chamber = _CHAMBERS.get(str(row.get("chamber", "")).upper())
            txn = _TRANSACTION_TYPES.get(str(row.get("transaction_type", "")).upper())
            if not (filed and traded and chamber and txn):
                continue  # a malformed row shouldn't sink the whole feed
            if not (start.isoformat() <= filed[:10] <= end.isoformat()):
                continue
            trades.append(
                {
                    "filed_at": datetime.fromisoformat(filed[:10]).isoformat(),
                    "transaction_date": traded[:10],
                    "member_name": row.get("politician_canonical") or row.get("politician") or "Unknown member",
                    "chamber": chamber,
                    "committees": [],
                    "transaction_type": txn,
                    "amount_range": _amount_range(row.get("amount_min"), row.get("amount_max")),
                    "party": row.get("party"),
                    "state": row.get("state"),
                    "owner": row.get("owner_code"),
                }
            )
        return {"trades": trades, "pending_legislation": []}
