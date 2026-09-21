"""FRED (Federal Reserve Bank of St. Louis) adapter -- the alternative for
macro data, the one domain nothing else in the provider chain covers
(macro_sage is the only seat that ever calls fetch_macro). Needs a free
API key (https://fred.stlouisfed.org/docs/api/api_key.html), but FRED's
own rate limit (120 requests/minute per key, no documented hard daily cap
for ordinary single-user use) is nowhere near as constraining as Alpha
Vantage's free-tier 25-requests-per-day cap -- a single fetch_macro call
burns 7 Alpha Vantage requests by itself, so macro_sage alone can exhaust
an entire day's AV quota.

Bonus: FRED's DTWEXBGS series is a real broad trade-weighted dollar index,
not the rough USD/EUR-based proxy Alpha Vantage's own fetch_macro used for
the same field (see alpha_vantage.py's docstring on that method).

Built and tested against a faked httpx transport -- api.stlouisfed.org is
blocked by this sandbox's egress policy, same as every other external host
used this session, so none of this has been exercised against a live FRED
response. The observations endpoint is a small, stable, officially
documented JSON shape (unlike yfinance's scraped surface), which is why
this was still worth building blind -- but treat the first live run like
any new integration, not a known-good one."""
from __future__ import annotations

from typing import Any

import httpx

from council.data.rate_limit import TokenBucket

_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"

# field name -> (FRED series id, `units` transform or None for the raw level).
# units="pc1" asks FRED itself to compute percent-change-from-a-year-ago,
# which is exactly what "cpi_yoy" means -- no manual YoY math needed here.
_SERIES = {
    "treasury_10y": ("DGS10", None),
    "treasury_2y": ("DGS2", None),
    "fed_funds_rate": ("DFF", None),
    "cpi_yoy": ("CPIAUCSL", "pc1"),
    "unemployment_rate": ("UNRATE", None),
    "dollar_index": ("DTWEXBGS", None),
    "wti_crude": ("DCOILWTICO", None),
}


class FREDProvider:
    name = "fred"

    def __init__(self, api_key: str, requests_per_minute: float = 100.0):
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=30)
        self._limiter = TokenBucket(rate_per_second=requests_per_minute / 60)

    async def _latest_value(self, series_id: str, units: str | None) -> float:
        await self._limiter.acquire()
        params = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
            "sort_order": "desc",
            # A handful, not just the newest one -- FRED reports "." for a
            # period that hasn't posted yet (common on daily series around
            # holidays/weekends), so scan back for the first real value.
            "limit": 10,
        }
        if units:
            params["units"] = units
        resp = await self._client.get(_OBSERVATIONS_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        for obs in data.get("observations", []):
            value = obs.get("value")
            if value not in (None, "."):
                try:
                    return float(value)
                except ValueError:
                    continue
        return 0.0

    async def fetch_macro(self) -> dict[str, Any]:
        return {
            field: await self._latest_value(series_id, units)
            for field, (series_id, units) in _SERIES.items()
        }
