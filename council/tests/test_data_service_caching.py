"""A real bug hit in practice: a provider's empty OHLCV result (caused by
the now-fixed Alpha Vantage rate-limit issue) got cached for the full
1-hour TTL, so every retry within that hour kept getting served the same
stale empty answer even after the underlying provider bug was fixed.
get_ohlcv() must not cache an empty result; other domains (where an empty
result is often a legitimate answer, e.g. "no news this week") keep caching
it as before."""
from __future__ import annotations

from datetime import datetime

import pytest

from council.data.cache import DiskCache
from council.data.service import DataService


class _EmptyThenRealProvider:
    """First call returns nothing (simulating the AV-rate-limit-adjacent
    failure mode); a later call, after whatever broke it is fixed, returns
    real data. If the empty result got cached, the second call would never
    even reach the provider."""

    name = "empty_then_real"

    def __init__(self):
        self.calls = 0

    async def fetch_ohlcv(self, ticker, start, end):
        self.calls += 1
        if self.calls == 1:
            return []
        return [
            {
                "trade_date": end.isoformat(),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "adjusted_close": 100.5,
                "volume": 1_000_000,
            }
        ]


@pytest.mark.asyncio
async def test_empty_ohlcv_result_is_not_cached(tmp_path):
    provider = _EmptyThenRealProvider()
    service = DataService(providers=[provider], cache=DiskCache(str(tmp_path / "cache.db")))
    as_of = datetime(2026, 9, 21)

    first = await service.get_ohlcv("MRVL", as_of=as_of, lookback_days=5)
    assert first.bars == []
    assert provider.calls == 1

    # A second call for the exact same cache key must reach the provider
    # again, not be served the stale empty result from the cache.
    second = await service.get_ohlcv("MRVL", as_of=as_of, lookback_days=5)
    assert len(second.bars) == 1
    assert provider.calls == 2


class _EmptyNewsProvider:
    name = "empty_news"

    def __init__(self):
        self.calls = 0

    async def fetch_news(self, ticker, start, end):
        self.calls += 1
        return []


@pytest.mark.asyncio
async def test_empty_news_result_is_still_cached_as_a_legitimate_answer(tmp_path):
    # Unlike OHLCV, "no news this week" is a genuine, cacheable answer --
    # cache_empty defaults to True for every domain except get_ohlcv's own
    # explicit opt-out.
    provider = _EmptyNewsProvider()
    service = DataService(providers=[provider], cache=DiskCache(str(tmp_path / "cache.db")))
    as_of = datetime(2026, 9, 21)

    first = await service.get_news("MRVL", as_of=as_of, lookback_days=14)
    assert first.items == []
    assert provider.calls == 1

    second = await service.get_news("MRVL", as_of=as_of, lookback_days=14)
    assert second.items == []
    assert provider.calls == 1  # served from cache, provider not called again
