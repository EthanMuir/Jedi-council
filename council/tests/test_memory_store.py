"""Layered memory: decay-weighted retrieval, the N=200 eviction cap, and
the point-in-time guard -- a memory event from the future must never reach
a deliberation, exactly like a future-dated market record."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from council.crypt.db import connect
from council.memory.decay import half_life_for_seat, recency_weight
from council.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    conn = connect(str(tmp_path / "crypt.db"))
    yield MemoryStore(conn, cap_per_seat=5)
    conn.close()


def test_point_in_time_guard_excludes_future_memory(store):
    as_of = datetime(2026, 9, 18)
    store.add(
        seat_id="technician", ticker="NVDA", kind="lesson",
        as_of=as_of - timedelta(days=1), horizon="1w",
        content={"lesson": "past lesson, should be visible"},
    )
    store.add(
        seat_id="technician", ticker="NVDA", kind="lesson",
        as_of=as_of + timedelta(days=3), horizon="1w",
        content={"lesson": "FUTURE LEAK -- should never be retrievable"},
    )

    retrieved = store.retrieve(seat_id="technician", ticker="NVDA", as_of=as_of)
    lessons = [e.content["lesson"] for e in retrieved]
    assert "past lesson, should be visible" in lessons
    assert not any("FUTURE LEAK" in l for l in lessons)


def test_point_in_time_guard_excludes_memory_from_the_exact_same_instant(store):
    as_of = datetime(2026, 9, 18, 12, 0, 0)
    store.add(
        seat_id="technician", ticker="NVDA", kind="lesson", as_of=as_of, horizon="1w",
        content={"lesson": "exactly-now, must not count as strictly earlier"},
    )
    retrieved = store.retrieve(seat_id="technician", ticker="NVDA", as_of=as_of)
    assert retrieved == []


def test_same_ticker_outranks_cross_ticker_at_equal_age(store):
    as_of = datetime(2026, 9, 18)
    store.add(
        seat_id="fundamentalist", ticker="AAPL", kind="lesson",
        as_of=as_of - timedelta(days=10), horizon="1m",
        content={"lesson": "AAPL lesson", "generalises_beyond_this_ticker": True},
    )
    store.add(
        seat_id="fundamentalist", ticker="NVDA", kind="lesson",
        as_of=as_of - timedelta(days=10), horizon="1m",
        content={"lesson": "NVDA lesson", "generalises_beyond_this_ticker": True},
    )
    retrieved = store.retrieve(seat_id="fundamentalist", ticker="NVDA", as_of=as_of)
    assert retrieved[0].content["lesson"] == "NVDA lesson"  # same-ticker ranked first


def test_non_generalising_cross_ticker_lesson_is_not_retrieved(store):
    as_of = datetime(2026, 9, 18)
    store.add(
        seat_id="fundamentalist", ticker="AAPL", kind="lesson",
        as_of=as_of - timedelta(days=5), horizon="1m",
        content={"lesson": "AAPL-specific, does not generalise", "generalises_beyond_this_ticker": False},
    )
    retrieved = store.retrieve(seat_id="fundamentalist", ticker="NVDA", as_of=as_of)
    assert retrieved == []


def test_recency_weight_decays_toward_zero_with_age():
    half_life = 5.0
    assert recency_weight(0, half_life) == 1.0
    assert recency_weight(5, half_life) == pytest.approx(0.5, abs=1e-6)
    assert recency_weight(50, half_life) < 0.01


def test_shallow_seat_has_much_shorter_half_life_than_deep_seat():
    assert half_life_for_seat("catalyst_seer") < half_life_for_seat("fundamentalist")


def test_eviction_caps_at_configured_limit(store):
    as_of = datetime(2026, 9, 18)
    for i in range(8):  # cap_per_seat=5 in the fixture
        store.add(
            seat_id="technician", ticker="NVDA", kind="lesson",
            as_of=as_of - timedelta(days=100 - i), horizon="1w",
            content={"lesson": f"lesson {i}"},
        )
    remaining = store._conn.execute(
        "SELECT COUNT(*) FROM memory_events WHERE seat_id = 'technician'"
    ).fetchone()[0]
    assert remaining == 5


def test_eviction_keeps_more_recent_over_older_at_equal_importance(store):
    as_of = datetime(2026, 9, 18)
    for i in range(8):
        store.add(
            seat_id="technician", ticker="NVDA", kind="lesson",
            as_of=as_of - timedelta(days=200 - i * 10), horizon="1w",
            content={"lesson": f"lesson {i}"},
        )
    remaining = store._conn.execute(
        "SELECT content_json FROM memory_events WHERE seat_id = 'technician'"
    ).fetchall()
    remaining_lessons = {row[0] for row in remaining}
    # the oldest (lesson 0, 200 days back) should have been evicted first
    assert not any('"lesson 0"' in r for r in remaining_lessons)
