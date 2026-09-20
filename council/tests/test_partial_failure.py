"""Phase 6: a single Tier I seat's total failure (data-gather blowing up
after all provider retries) must degrade that one seat to NO_READ, not take
down the other eleven or the whole deliberation."""
from __future__ import annotations

from datetime import datetime

import pytest

from council.config import Settings
from council.engine import orchestrator as orchestrator_module
from council.engine.orchestrator import TIER_I_SEATS, run_deliberation

AS_OF = datetime(2026, 9, 18, 16, 0, 0)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        no_llm=True,
        use_data_fixtures=True,
        council_db_path=str(tmp_path / "council.db"),
        cache_db_path=str(tmp_path / "cache.db"),
    )


@pytest.mark.asyncio
async def test_one_seat_data_gather_failure_degrades_to_no_read(settings, monkeypatch):
    broken_seat = next(s for s in TIER_I_SEATS if s.id == "technician")

    async def _broken_gather(*args, **kwargs):
        raise RuntimeError("simulated total data outage for this seat")

    monkeypatch.setattr(broken_seat, "gather", _broken_gather)

    result = await run_deliberation("NVDA", "1w", settings, as_of=AS_OF)

    broken_result = next(sr for sr in result.seat_results if sr.seat_id == "technician")
    assert broken_result.verdict.vote == "NO_READ"
    assert broken_result.verdict.abstain_reason == "seat_infrastructure_failure"
    # the deliberation as a whole must still complete -- 11 other seats
    # unaffected, a final verdict still reached
    assert len(result.seat_results) == len(TIER_I_SEATS)
    assert result.grand_master_verdict.vote in ("BULLISH", "BEARISH", "NO_CONVICTION")


@pytest.mark.asyncio
async def test_broken_seat_still_emits_seat_result_progress_event(settings, monkeypatch):
    broken_seat = next(s for s in TIER_I_SEATS if s.id == "fundamentalist")

    async def _broken_gather(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(broken_seat, "gather", _broken_gather)

    events = []

    async def progress(event, payload):
        if event == "seat_result":
            events.append(payload)

    await run_deliberation("NVDA", "1w", settings, as_of=AS_OF, progress=progress)

    broken_payload = next(p for p in events if p["seat_id"] == "fundamentalist")
    assert broken_payload["vote"] == "NO_READ"
    assert len(events) == len(TIER_I_SEATS)
