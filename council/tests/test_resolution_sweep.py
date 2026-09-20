"""Integration test: deliberate with a backdated as_of, then sweep -- the
resolution should be written against real subsequent price action from the
fixture, and never re-swept once resolved."""
from __future__ import annotations

from datetime import datetime

import pytest

from council.config import Settings
from council.crypt.db import connect
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
async def test_backdated_prediction_gets_resolved(settings):
    as_of = datetime(2026, 8, 1, 16, 0, 0)
    result = await run_deliberation("NVDA", "1w", settings, as_of=as_of)

    swept = await sweep_unresolved(settings, as_of=datetime(2026, 9, 18))
    assert len(swept) == 1
    assert swept[0].prediction_id == result.prediction_id
    assert swept[0].direction_correct is not None

    conn = connect(settings.council_db_path)
    row = conn.execute(
        "SELECT * FROM resolutions WHERE prediction_id = ?", (result.prediction_id,)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["price_at_resolve"] > 0


@pytest.mark.asyncio
async def test_future_prediction_is_not_swept(settings):
    result = await run_deliberation("NVDA", "1w", settings)  # as_of defaults to now
    swept = await sweep_unresolved(settings, as_of=datetime.utcnow())
    assert result.prediction_id not in [s.prediction_id for s in swept]


@pytest.mark.asyncio
async def test_sweep_is_idempotent(settings):
    as_of = datetime(2026, 8, 1, 16, 0, 0)
    result = await run_deliberation("NVDA", "1w", settings, as_of=as_of)

    first = await sweep_unresolved(settings, as_of=datetime(2026, 9, 18))
    second = await sweep_unresolved(settings, as_of=datetime(2026, 9, 18))
    assert len(first) == 1
    assert len(second) == 0  # already resolved, not re-swept


@pytest.mark.asyncio
async def test_sweep_writes_immediate_reflections_to_memory(settings):
    from council.memory.store import MemoryStore

    as_of = datetime(2026, 8, 1, 16, 0, 0)
    result = await run_deliberation("NVDA", "1w", settings, as_of=as_of)
    directional_seats = {sr.seat_id for sr in result.seat_results if sr.verdict.vote != "NO_READ"}

    swept = await sweep_unresolved(settings, as_of=datetime(2026, 9, 18))
    assert swept[0].lessons_written == len(directional_seats)

    conn = connect(settings.council_db_path)
    memory = MemoryStore(conn, cap_per_seat=settings.memory_cap_per_seat)
    # Query shortly after resolution, not weeks later -- shallow-layer seats
    # (5-day half-life) legitimately decay below the retrieval threshold
    # over a long gap, which is correct behaviour, not a write failure.
    # This test is about the write path, not decay math (see test_memory_store.py).
    query_as_of = datetime(2026, 8, 9)
    for seat_id in directional_seats:
        lessons = memory.retrieve(seat_id=seat_id, ticker="NVDA", as_of=query_as_of)
        assert len(lessons) >= 1
    conn.close()


@pytest.mark.asyncio
async def test_resolutions_table_immutable_after_sweep(settings):
    import sqlite3

    as_of = datetime(2026, 8, 1, 16, 0, 0)
    result = await run_deliberation("NVDA", "1w", settings, as_of=as_of)
    await sweep_unresolved(settings, as_of=datetime(2026, 9, 18))

    conn = connect(settings.council_db_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE resolutions SET price_at_resolve = 0 WHERE prediction_id = ?",
            (result.prediction_id,),
        )
    conn.close()
