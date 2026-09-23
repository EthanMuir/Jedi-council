"""Task #75 -- FMP's stock_news endpoint has no server-side date filter,
only `limit`, so fetch_news over-fetches and filters client-side (same
pattern as fetch_insider_transactions). Mocked against the transport layer
(no live FMP_API_KEY in this sandbox), mirroring test_alpha_vantage_provider.py."""
from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from council.data.providers.fmp import FMPProvider


def _provider_with_response(payload) -> FMPProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    provider = FMPProvider(api_key="test-key")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


@pytest.mark.asyncio
async def test_fetch_news_parses_and_filters_by_window():
    payload = [
        {
            "title": "In window",
            "text": "summary text",
            "site": "Reuters",
            "url": "https://example.com/in-window",
            "publishedDate": "2026-09-10 14:30:00",
        },
        {
            "title": "Too old",
            "text": "s",
            "site": "AP",
            "url": "https://example.com/too-old",
            "publishedDate": "2026-08-01 09:00:00",
        },
    ]
    provider = _provider_with_response(payload)

    items = await provider.fetch_news("ENB", datetime(2026, 9, 1), datetime(2026, 9, 21))

    assert len(items) == 1
    assert items[0]["headline"] == "In window"
    assert items[0]["source"] == "Reuters"
    assert items[0]["url"] == "https://example.com/in-window"
    assert items[0]["published_at"] == "2026-09-10T14:30:00"
    assert items[0]["sentiment_score"] is None


@pytest.mark.asyncio
async def test_fetch_news_skips_items_missing_required_fields():
    payload = [
        {"title": None, "url": "https://example.com/no-title", "publishedDate": "2026-09-10 00:00:00"},
        {"title": "No url", "url": "", "publishedDate": "2026-09-10 00:00:00"},
        {"title": "No date", "url": "https://example.com/no-date"},
        {"title": "Fine", "url": "https://example.com/fine", "publishedDate": "2026-09-10 00:00:00"},
    ]
    provider = _provider_with_response(payload)

    items = await provider.fetch_news("ENB", datetime(2026, 9, 1), datetime(2026, 9, 21))

    assert len(items) == 1
    assert items[0]["headline"] == "Fine"


@pytest.mark.asyncio
async def test_fetch_news_handles_empty_response():
    provider = _provider_with_response([])
    items = await provider.fetch_news("ENB", datetime(2026, 9, 1), datetime(2026, 9, 21))
    assert items == []
