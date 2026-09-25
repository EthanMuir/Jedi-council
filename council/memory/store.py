"""Per-seat memory store: summarised observations, past verdicts, and
reflections. Retrieval score = relevance x recency_weight x importance
(Addendum A5). Relevance is a documented simplification -- exact ticker
match scores 1.0, a lesson that says it generalises beyond its own ticker
scores 0.3 on any other ticker, everything else scores near zero. Real
semantic relevance would need embeddings; this system doesn't have them
yet.

Point-in-time guard: retrieve() only ever returns events whose `as_of` is
STRICTLY earlier than the `as_of` of the prediction being made. A memory
event from the future is exactly the kind of lookahead the rest of this
system works hard to prevent -- see council/data/service.py's
filter_point_in_time for the same discipline applied to market data.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from council.memory.decay import half_life_for_seat, recency_weight

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_SAME_TICKER_RELEVANCE = 1.0
_GENERALISABLE_RELEVANCE = 0.3
_OTHER_RELEVANCE = 0.005
_MIN_RETRIEVAL_SCORE = 0.01


@dataclass
class MemoryEvent:
    id: int
    seat_id: str
    ticker: str
    kind: str
    as_of: datetime
    horizon: str | None
    content: dict
    importance: float
    created_at: datetime


def to_seat_memory_lesson(event: "MemoryEvent"):
    """Bridges a retrieved MemoryEvent (rich ReflectionLesson-shaped
    content) to the compact MemoryLesson schema a seat's own prompt/output
    actually uses (Addendum A1's memory_applied field)."""
    from council.seats.base import MemoryLesson

    return MemoryLesson(
        lesson_id=str(event.id),
        lesson=event.content.get("lesson", ""),
        from_date=event.as_of.date().isoformat(),
    )


def _row_to_event(row: sqlite3.Row) -> MemoryEvent:
    return MemoryEvent(
        id=row["id"],
        seat_id=row["seat_id"],
        ticker=row["ticker"],
        kind=row["kind"],
        as_of=datetime.fromisoformat(row["as_of"]),
        horizon=row["horizon"],
        content=json.loads(row["content_json"]),
        importance=row["importance"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class MemoryStore:
    """One person's seat memories: each account's seats learn only from
    that account's own runs (0 = the owner, whose rows are NULL or 0)."""

    def __init__(self, conn: sqlite3.Connection, cap_per_seat: int = 200, account: int = 0):
        self._conn = conn
        self._cap = cap_per_seat
        self._account = account
        self._conn.executescript(_SCHEMA_PATH.read_text())
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(memory_events)")}
        if "user_id" not in columns:
            self._conn.execute("ALTER TABLE memory_events ADD COLUMN user_id INTEGER")
        self._conn.commit()

    def _mine(self) -> tuple[str, list]:
        if self._account == 0:
            return "(user_id IS NULL OR user_id = 0)", []
        return "user_id = ?", [self._account]

    def add(
        self,
        *,
        seat_id: str,
        ticker: str,
        kind: str,
        as_of: datetime,
        horizon: str | None,
        content: dict,
        importance: float = 1.0,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO memory_events "
            "(seat_id, ticker, kind, as_of, horizon, content_json, importance, created_at, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                seat_id,
                ticker,
                kind,
                as_of.isoformat(),
                horizon,
                json.dumps(content),
                importance,
                datetime.utcnow().isoformat(),
                self._account or None,
            ),
        )
        self._conn.commit()
        event_id = cur.lastrowid
        self._evict_if_over_cap(seat_id)
        return event_id

    def _evict_if_over_cap(self, seat_id: str) -> None:
        mine, params = self._mine()
        rows = self._conn.execute(
            f"SELECT id, as_of, importance FROM memory_events WHERE seat_id = ? AND {mine}",
            (seat_id, *params),
        ).fetchall()
        if len(rows) <= self._cap:
            return
        half_life = half_life_for_seat(seat_id)
        now = datetime.utcnow()
        scored = []
        for row in rows:
            age_days = (now - datetime.fromisoformat(row["as_of"])).total_seconds() / 86400
            score = recency_weight(age_days, half_life) * row["importance"]
            scored.append((score, row["id"]))
        scored.sort(key=lambda x: x[0])  # lowest score first
        excess = len(rows) - self._cap
        evict_ids = [event_id for _score, event_id in scored[:excess]]
        self._conn.executemany(
            "DELETE FROM memory_events WHERE id = ?", [(i,) for i in evict_ids]
        )
        self._conn.commit()

    def retrieve(
        self, *, seat_id: str, ticker: str, as_of: datetime, limit: int = 5
    ) -> list[MemoryEvent]:
        mine, params = self._mine()
        rows = self._conn.execute(
            f"SELECT * FROM memory_events WHERE seat_id = ? AND as_of < ? AND {mine}",
            (seat_id, as_of.isoformat(), *params),
        ).fetchall()
        half_life = half_life_for_seat(seat_id)
        scored = []
        for row in rows:
            event_as_of = datetime.fromisoformat(row["as_of"])
            age_days = (as_of - event_as_of).total_seconds() / 86400
            content = json.loads(row["content_json"])
            if row["ticker"] == ticker:
                relevance = _SAME_TICKER_RELEVANCE
            elif content.get("generalises_beyond_this_ticker"):
                relevance = _GENERALISABLE_RELEVANCE
            else:
                relevance = _OTHER_RELEVANCE
            score = relevance * recency_weight(age_days, half_life) * row["importance"]
            scored.append((score, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [_row_to_event(row) for score, row in scored[:limit] if score >= _MIN_RETRIEVAL_SCORE]
