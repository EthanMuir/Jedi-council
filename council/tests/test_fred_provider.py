"""FRED is the alternative to Alpha Vantage for macro data -- the one
domain nothing else in the chain covers, and the one that burns 7 of
Alpha Vantage's 25-requests/day free-tier cap in a single fetch_macro
call. api.stlouisfed.org is blocked by this sandbox's egress policy, same
as every other external host used this session, so this verifies the
parsing logic against a faked httpx transport shaped like FRED's
documented observations JSON -- it cannot verify that shape is what FRED
actually returns today."""
from __future__ import annotations

import httpx
import pytest

from council.data.providers.fred import FREDProvider


def _observations(*values: str) -> dict:
    return {"observations": [{"date": "2026-09-19", "value": v} for v in values]}


_SERIES_VALUES = {
    "DGS10": "4.25",
    "DGS2": "3.80",
    "DFF": "4.50",
    "CPIAUCSL": "2.90",  # requested with units=pc1, so this IS the YoY %
    "UNRATE": "4.00",
    "DTWEXBGS": "103.20",
    "DCOILWTICO": "71.50",
}


def _default_handler(request: httpx.Request) -> httpx.Response:
    series_id = request.url.params.get("series_id")
    value = _SERIES_VALUES.get(series_id)
    if value is None:
        return httpx.Response(404, text="unknown series")
    return httpx.Response(200, json=_observations(value))


def _make_provider(handler) -> FREDProvider:
    provider = FREDProvider(api_key="test-fred-key")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


@pytest.mark.asyncio
async def test_fetch_macro_maps_every_series_to_its_field():
    provider = _make_provider(_default_handler)
    macro = await provider.fetch_macro()

    assert macro["treasury_10y"] == 4.25
    assert macro["treasury_2y"] == 3.80
    assert macro["fed_funds_rate"] == 4.50
    assert macro["cpi_yoy"] == 2.90
    assert macro["unemployment_rate"] == 4.00
    assert macro["dollar_index"] == 103.20
    assert macro["wti_crude"] == 71.50
    # curve_10y_minus_2y is intentionally absent -- DataService.get_macro_snapshot
    # computes it centrally now (see test_macro_snapshot.py), not any one provider.
    assert "curve_10y_minus_2y" not in macro


@pytest.mark.asyncio
async def test_cpi_requested_with_pc1_units_for_year_over_year():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("series_id") == "CPIAUCSL":
            captured["units"] = request.url.params.get("units")
        return _default_handler(request)

    provider = _make_provider(handler)
    await provider.fetch_macro()
    assert captured["units"] == "pc1"


@pytest.mark.asyncio
async def test_skips_not_yet_posted_dot_values_and_uses_first_real_one():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("series_id") == "DGS10":
            return httpx.Response(200, json=_observations(".", ".", "4.25"))
        return _default_handler(request)

    provider = _make_provider(handler)
    macro = await provider.fetch_macro()
    assert macro["treasury_10y"] == 4.25


@pytest.mark.asyncio
async def test_all_dot_values_falls_back_to_zero_not_a_crash():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("series_id") == "UNRATE":
            return httpx.Response(200, json=_observations(".", "."))
        return _default_handler(request)

    provider = _make_provider(handler)
    macro = await provider.fetch_macro()
    assert macro["unemployment_rate"] == 0.0
