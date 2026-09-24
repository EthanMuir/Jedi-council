"""The fixtures stop on a fixed recorded date, but live runs ask for data
"as of now". Once now drifted past the recording, short lookbacks matched
nothing and every fixture-mode deliberation died with "no price data"."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from council.data.providers.fixtures import FixtureProvider

RECORDED_THROUGH = date(2026, 9, 18)


async def test_short_lookback_past_the_recording_still_returns_bars():
    provider = FixtureProvider()
    end = RECORDED_THROUGH + timedelta(days=30)

    bars = await provider.fetch_ohlcv("NVDA", end - timedelta(days=5), end)

    assert bars, "a 5-day window a month after the recording must replay recorded bars"
    assert max(b["trade_date"] for b in bars) == RECORDED_THROUGH.isoformat()


async def test_windows_inside_the_recording_are_unchanged():
    provider = FixtureProvider()
    start, end = date(2026, 9, 1), date(2026, 9, 10)

    bars = await provider.fetch_ohlcv("NVDA", start, end)

    assert bars
    assert all(start.isoformat() <= b["trade_date"] <= end.isoformat() for b in bars)


async def test_news_window_past_the_recording_replays_recent_news():
    provider = FixtureProvider()
    end = datetime.combine(RECORDED_THROUGH + timedelta(days=30), datetime.min.time())

    items = await provider.fetch_news("NVDA", end - timedelta(days=14), end)

    assert items
