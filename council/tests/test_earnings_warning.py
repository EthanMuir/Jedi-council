from datetime import date, datetime, timedelta

import pytest

from council.config import Settings
from council.data.cache import DiskCache
from council.data.service import DataService
from council.engine import orchestrator
from council.engine.orchestrator import earnings_notice, run_deliberation


def test_notice_only_within_a_week():
    today = date(2026, 9, 28)
    assert earnings_notice(None, today) is None
    assert earnings_notice(today - timedelta(days=1), today) is None
    assert earnings_notice(today + timedelta(days=8), today) is None
    soon = earnings_notice(date(2026, 10, 1), today)
    assert soon["days"] == 3 and "in 3 days (Thu Oct 1)" in soon["message"]
    assert "tomorrow" in earnings_notice(today + timedelta(days=1), today)["message"]
    assert "today" in earnings_notice(today, today)["message"]


class _CalendarProvider:
    name = "calendar"

    def __init__(self, dates):
        self.dates = dates

    async def fetch_earnings_calendar(self, ticker):
        return {"earnings_dates": self.dates}


@pytest.mark.asyncio
async def test_next_earnings_skips_past_dates_and_backdated_runs(tmp_path):
    now = datetime.utcnow()
    upcoming = (now + timedelta(days=4)).date().isoformat()
    past = (now - timedelta(days=80)).date().isoformat()
    service = DataService([_CalendarProvider([past, upcoming])], DiskCache(str(tmp_path / "c.db")))
    assert await service.get_next_earnings("NVDA", now) == date.fromisoformat(upcoming)
    # A backdated run can't know today's calendar.
    assert await service.get_next_earnings("NVDA", now - timedelta(days=30)) is None


@pytest.mark.asyncio
async def test_earnings_soon_warns_on_next_week_only(tmp_path, monkeypatch):
    settings = Settings(
        no_llm=True,
        use_data_fixtures=True,
        council_db_path=str(tmp_path / "council.db"),
        cache_db_path=str(tmp_path / "cache.db"),
    )
    as_of = datetime(2026, 9, 18, 16, 0, 0)

    async def fake_next(self, ticker, when):
        return date(2026, 9, 22)

    monkeypatch.setattr(DataService, "get_next_earnings", fake_next)
    events = []

    async def progress(event, payload):
        events.append((event, payload))

    result = await run_deliberation("NVDA", settings, as_of=as_of, progress=progress)
    short_kinds = [w.kind for w in result.terms["short"].warnings]
    assert "earnings" in short_kinds
    assert all(w.kind != "earnings" for t in ("medium", "long") for w in result.terms[t].warnings)
    assert ("earnings_soon", orchestrator.earnings_notice(date(2026, 9, 22), as_of.date())) in events
