"""End-to-end: a seat with no history deliberates with empty memory_applied,
gets resolved, and a later deliberation on the same ticker genuinely
retrieves and applies the resulting lesson -- this is the compounding
improvement loop, not just wiring that exists on paper."""
from __future__ import annotations

from datetime import datetime

import pytest

from council.config import Settings
from council.engine.orchestrator import run_deliberation
from council.engine.resolution_sweep import sweep_unresolved


@pytest.fixture
def settings(tmp_path):
    return Settings(
        no_llm=True,
        use_data_fixtures=True,
        council_db_path=str(tmp_path / "council.db"),
        cache_db_path=str(tmp_path / "cache.db"),
    )


@pytest.mark.asyncio
async def test_first_deliberation_has_no_memory_applied(settings):
    result = await run_deliberation("NVDA", settings, as_of=datetime(2026, 8, 1, 16, 0, 0))
    for sr in result.seat_results:
        for term in ("short", "medium", "long"):
            assert sr.verdicts.term(term).memory_applied == []


@pytest.mark.asyncio
async def test_second_deliberation_applies_a_lesson_from_the_first(settings):
    as_of1 = datetime(2026, 8, 1, 16, 0, 0)
    await run_deliberation("NVDA", settings, as_of=as_of1)

    swept = await sweep_unresolved(settings, as_of=datetime(2026, 8, 20))
    assert [s.horizon for s in swept] == ["short"]  # only the week-long term has resolved
    assert swept[0].lessons_written > 0

    as_of2 = datetime(2026, 8, 22, 16, 0, 0)
    result2 = await run_deliberation("NVDA", settings, as_of=as_of2)

    applied_anywhere = [
        m for sr in result2.seat_results for m in sr.verdicts.short.memory_applied
    ]
    assert len(applied_anywhere) > 0


@pytest.mark.asyncio
async def test_memory_never_leaks_across_tickers_without_generalisation(settings):
    """A ticker-specific, non-generalising lesson from one ticker must not
    be retrievable under a different ticker's deliberation."""
    from council.crypt.db import connect
    from council.memory.store import MemoryStore

    as_of1 = datetime(2026, 8, 1, 16, 0, 0)
    await run_deliberation("NVDA", settings, as_of=as_of1)
    await sweep_unresolved(settings, as_of=datetime(2026, 8, 20))

    conn = connect(settings.council_db_path)
    memory = MemoryStore(conn, cap_per_seat=settings.memory_cap_per_seat)
    # force a non-generalising lesson to prove it's excluded under AAPL
    memory.add(
        seat_id="technician", ticker="NVDA", kind="lesson",
        as_of=datetime(2026, 8, 10), horizon="1w",
        content={"lesson": "NVDA-specific quirk", "generalises_beyond_this_ticker": False},
    )
    retrieved_under_aapl = memory.retrieve(
        seat_id="technician", ticker="AAPL", as_of=datetime(2026, 8, 22)
    )
    assert not any(e.content.get("lesson") == "NVDA-specific quirk" for e in retrieved_under_aapl)
    conn.close()
