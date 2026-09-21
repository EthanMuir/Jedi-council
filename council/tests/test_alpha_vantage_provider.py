"""A real bug hit in practice: Alpha Vantage's rate limit doesn't surface as
an HTTP error -- it's a 200 OK with a "Note"/"Information"/"Error Message"
key instead of the expected payload. Left undetected, that parsed as "zero
results found" (a successful empty response) and short-circuited
DataService's provider fallback instead of correctly falling through to the
next provider."""
from __future__ import annotations

import httpx
import pytest

from council.data.providers.alpha_vantage import AlphaVantageProvider, AlphaVantageRateLimited


def _provider_with_response(payload: dict) -> AlphaVantageProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    provider = AlphaVantageProvider(api_key="test-key")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


@pytest.mark.asyncio
async def test_rate_limit_information_key_raises_instead_of_empty_success():
    provider = _provider_with_response(
        {"Information": "Thank you for using Alpha Vantage! Our standard API rate limit is 25 requests per day."}
    )
    with pytest.raises(AlphaVantageRateLimited):
        await provider._get({"function": "TIME_SERIES_DAILY_ADJUSTED", "symbol": "MRVL"})


@pytest.mark.asyncio
async def test_note_key_raises():
    provider = _provider_with_response({"Note": "Thank you for using Alpha Vantage! Call frequency limited."})
    with pytest.raises(AlphaVantageRateLimited):
        await provider._get({"function": "TIME_SERIES_DAILY_ADJUSTED", "symbol": "MRVL"})


@pytest.mark.asyncio
async def test_error_message_key_raises():
    provider = _provider_with_response({"Error Message": "Invalid API call."})
    with pytest.raises(AlphaVantageRateLimited):
        await provider._get({"function": "TIME_SERIES_DAILY_ADJUSTED", "symbol": "BOGUS"})


@pytest.mark.asyncio
async def test_normal_payload_passes_through_unchanged():
    payload = {"Time Series (Daily)": {"2026-09-18": {"1. open": "100.0"}}}
    provider = _provider_with_response(payload)
    result = await provider._get({"function": "TIME_SERIES_DAILY_ADJUSTED", "symbol": "MRVL"})
    assert result == payload
