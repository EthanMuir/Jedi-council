"""Task #75 -- news is unioned across every provider that implements
fetch_news, not just the first one to answer without raising. Unlike every
other domain, a thin-but-successful result from the first provider (exactly
what yfinance's Ticker.news usually is -- Yahoo's current cache for that
ticker, not a real windowed query) must not block a later provider's real
feed from ever being tried. See DataService._fetch_news_merged's own
docstring for the live case that motivated this."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import council.data.service as data_service_module
from council.data.cache import DiskCache
from council.data.service import DataService


def _item(headline: str, days_ago: int, as_of: datetime, url: str | None = None) -> dict:
    return {
        "headline": headline,
        "summary": "s",
        "source": "test",
        "url": url or f"https://example.com/{headline.replace(' ', '-')}",
        "published_at": (as_of - timedelta(days=days_ago)).isoformat(),
        "sentiment_score": None,
        "sentiment_label": None,
    }


class _NewsProvider:
    def __init__(self, name: str, items: list[dict], raises: bool = False):
        self.name = name
        self._items = items
        self._raises = raises
        self.calls = 0

    async def fetch_news(self, ticker, start, end):
        self.calls += 1
        if self._raises:
            raise ConnectionError(f"{self.name} is down")
        return self._items


def _fast_backoff(monkeypatch):
    monkeypatch.setattr(data_service_module, "_PROVIDER_BACKOFF_BASE_SECONDS", 0.001)
    monkeypatch.setattr(data_service_module, "_PROVIDER_BACKOFF_CAP_SECONDS", 0.001)


@pytest.mark.asyncio
async def test_thin_but_successful_first_provider_does_not_block_the_second(tmp_path):
    # This is exactly the yfinance-blocking-Alpha-Vantage pattern: the first
    # provider succeeds (no exception) but only has one thin item, and the
    # old _fetch_with_fallback-based get_news would have stopped right there.
    as_of = datetime(2026, 9, 21)
    thin = _NewsProvider("yfinance", [_item("Old CEO news", 5, as_of)])
    richer = _NewsProvider("alpha_vantage", [_item("Fresh earnings beat", 1, as_of)])
    service = DataService(providers=[thin, richer], cache=DiskCache(str(tmp_path / "cache.db")))

    feed = await service.get_news("ENB", as_of=as_of, lookback_days=14)

    headlines = {item.headline for item in feed.items}
    assert headlines == {"Old CEO news", "Fresh earnings beat"}
    assert thin.calls == 1
    assert richer.calls == 1  # richer WAS tried, unlike the old fallback-only behaviour


@pytest.mark.asyncio
async def test_duplicate_urls_across_providers_are_deduped(tmp_path):
    as_of = datetime(2026, 9, 21)
    shared_url = "https://example.com/same-story"
    a = _NewsProvider("yfinance", [_item("Same story via yfinance", 1, as_of, url=shared_url)])
    b = _NewsProvider("fmp", [_item("Same story via fmp", 1, as_of, url=shared_url)])
    service = DataService(providers=[a, b], cache=DiskCache(str(tmp_path / "cache.db")))

    feed = await service.get_news("ENB", as_of=as_of, lookback_days=14)

    assert len(feed.items) == 1


@pytest.mark.asyncio
async def test_one_provider_failing_does_not_block_the_others(tmp_path, monkeypatch):
    _fast_backoff(monkeypatch)
    as_of = datetime(2026, 9, 21)
    dead = _NewsProvider("alpha_vantage", [], raises=True)
    working = _NewsProvider("yfinance", [_item("Still works", 1, as_of)])
    service = DataService(providers=[dead, working], cache=DiskCache(str(tmp_path / "cache.db")))

    feed = await service.get_news("ENB", as_of=as_of, lookback_days=14)

    assert [item.headline for item in feed.items] == ["Still works"]


@pytest.mark.asyncio
async def test_all_news_providers_failing_raises(tmp_path, monkeypatch):
    _fast_backoff(monkeypatch)
    as_of = datetime(2026, 9, 21)
    dead = _NewsProvider("alpha_vantage", [], raises=True)
    service = DataService(providers=[dead], cache=DiskCache(str(tmp_path / "cache.db")))

    with pytest.raises(RuntimeError):
        await service.get_news("ENB", as_of=as_of, lookback_days=14)


@pytest.mark.asyncio
async def test_items_older_than_the_lookback_window_are_dropped(tmp_path):
    # A provider that ignores its start/end args (yfinance, in practice)
    # must not be able to hand back news older than what was actually
    # requested -- get_news enforces the lower bound itself.
    as_of = datetime(2026, 9, 21)
    noncompliant = _NewsProvider(
        "yfinance",
        [_item("Within window", 5, as_of), _item("Way too old", 60, as_of)],
    )
    service = DataService(providers=[noncompliant], cache=DiskCache(str(tmp_path / "cache.db")))

    feed = await service.get_news("ENB", as_of=as_of, lookback_days=14)

    assert [item.headline for item in feed.items] == ["Within window"]


@pytest.mark.asyncio
async def test_results_sorted_newest_first(tmp_path):
    as_of = datetime(2026, 9, 21)
    provider = _NewsProvider(
        "yfinance",
        [_item("Oldest", 10, as_of), _item("Newest", 1, as_of), _item("Middle", 5, as_of)],
    )
    service = DataService(providers=[provider], cache=DiskCache(str(tmp_path / "cache.db")))

    feed = await service.get_news("ENB", as_of=as_of, lookback_days=14)

    assert [item.headline for item in feed.items] == ["Newest", "Middle", "Oldest"]


@pytest.mark.asyncio
async def test_source_field_lists_every_provider_that_implements_fetch_news(tmp_path):
    as_of = datetime(2026, 9, 21)
    a = _NewsProvider("yfinance", [_item("A", 1, as_of)])
    b = _NewsProvider("fmp", [_item("B", 1, as_of)])
    service = DataService(providers=[a, b], cache=DiskCache(str(tmp_path / "cache.db")))

    feed = await service.get_news("ENB", as_of=as_of, lookback_days=14)

    assert feed.source == "yfinance, fmp"


@pytest.mark.asyncio
async def test_merged_news_result_is_cached(tmp_path):
    as_of = datetime(2026, 9, 21)
    a = _NewsProvider("yfinance", [_item("A", 1, as_of)])
    b = _NewsProvider("fmp", [_item("B", 1, as_of)])
    service = DataService(providers=[a, b], cache=DiskCache(str(tmp_path / "cache.db")))

    await service.get_news("ENB", as_of=as_of, lookback_days=14)
    await service.get_news("ENB", as_of=as_of, lookback_days=14)

    assert a.calls == 1
    assert b.calls == 1
