-- Layered memory (Addendum A5). Unlike the Crypt, this is deliberately
-- mutable and prunable -- capped at N events per seat, lowest-score
-- evicted first. No immutability triggers here.

CREATE TABLE IF NOT EXISTS memory_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    seat_id       TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    kind          TEXT NOT NULL,   -- 'lesson' | 'extended_reflection'
    as_of         TEXT NOT NULL,   -- point-in-time guard key: when this became known
    horizon       TEXT,
    content_json  TEXT NOT NULL,
    importance    REAL NOT NULL DEFAULT 1.0,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_events_seat ON memory_events(seat_id);
