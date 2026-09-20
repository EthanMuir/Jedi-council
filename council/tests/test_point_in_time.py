"""The single most common way these systems silently cheat: a record dated
after `as_of` leaking into a seat's context. These tests fabricate
future-dated records and assert the guard drops them."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from council.data.cache import DiskCache
from council.data.providers.fixtures import FixtureProvider
from council.data.service import DataService, filter_point_in_time


def test_filter_point_in_time_drops_future_records():
    as_of = datetime(2026, 9, 18, 12, 0, 0)
    records = [
        {"published_at": (as_of - timedelta(days=1)).isoformat(), "headline": "past"},
        {"published_at": as_of.isoformat(), "headline": "exactly now"},
        {"published_at": (as_of + timedelta(days=1)).isoformat(), "headline": "FUTURE LEAK"},
        {"published_at": (as_of + timedelta(seconds=1)).isoformat(), "headline": "barely future"},
    ]

    filtered = filter_point_in_time(records, as_of, "published_at")

    headlines = {r["headline"] for r in filtered}
    assert headlines == {"past", "exactly now"}
    assert "FUTURE LEAK" not in headlines
    assert "barely future" not in headlines


def test_filter_point_in_time_handles_tz_aware_timestamps():
    from datetime import timezone

    as_of = datetime(2026, 9, 18, 12, 0, 0)
    records = [
        {"published_at": datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc).isoformat()},
    ]
    assert filter_point_in_time(records, as_of, "published_at") == []


class _LeakyProvider:
    """A fake provider that always injects one future-dated news item,
    simulating a provider (or a bug) that returns lookahead data."""

    name = "leaky"

    def __init__(self, as_of: datetime):
        self._as_of = as_of

    async def fetch_news(self, ticker, start, end):
        return [
            {
                "headline": "real past news",
                "summary": "s",
                "source": "test",
                "url": "https://example.com",
                "published_at": (self._as_of - timedelta(days=1)).isoformat(),
                "sentiment_score": 0.1,
                "sentiment_label": "Neutral",
            },
            {
                "headline": "FUTURE LEAK: earnings beat reported",
                "summary": "s",
                "source": "test",
                "url": "https://example.com",
                "published_at": (self._as_of + timedelta(days=3)).isoformat(),
                "sentiment_score": 0.9,
                "sentiment_label": "Bullish",
            },
        ]

    async def fetch_ohlcv(self, ticker, start, end):
        return []

    async def fetch_option_chain(self, ticker):
        return {"underlying_price": 0.0, "contracts": [], "put_call_ratio": None}


@pytest.mark.asyncio
async def test_data_service_get_news_never_returns_future_dated_record(tmp_path):
    as_of = datetime(2026, 9, 18, 12, 0, 0)
    cache = DiskCache(str(tmp_path / "cache.db"))
    service = DataService(providers=[_LeakyProvider(as_of)], cache=cache)

    feed = await service.get_news("NVDA", as_of=as_of, lookback_days=30)

    headlines = {item.headline for item in feed.items}
    assert headlines == {"real past news"}
    assert not any("FUTURE LEAK" in h for h in headlines)


@pytest.mark.asyncio
async def test_data_service_ohlcv_from_fixtures_respects_as_of(tmp_path):
    cache = DiskCache(str(tmp_path / "cache.db"))
    service = DataService(providers=[FixtureProvider()], cache=cache)
    as_of = datetime(2026, 9, 10, 16, 0, 0)

    series = await service.get_ohlcv("NVDA", as_of=as_of, lookback_days=180)

    assert series.bars
    assert all(bar.trade_date.isoformat() <= as_of.date().isoformat() for bar in series.bars)
    assert series.staleness_seconds is not None
