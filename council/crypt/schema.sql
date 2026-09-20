-- The Crypt: append-only, hash-chained ledger. Every row in `predictions`
-- carries prev_hash/row_hash forming a chain; UPDATE and DELETE are blocked
-- by trigger on every table here. Nothing overwrites history -- resolutions
-- are inserted as new rows, weights are recomputed elsewhere, never here.

PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS predictions (
    id                          TEXT PRIMARY KEY,
    created_at                  TEXT NOT NULL,
    ticker                      TEXT NOT NULL,
    horizon                     TEXT NOT NULL,
    resolve_at                  TEXT NOT NULL,

    council_vote                TEXT,
    council_confidence          REAL,
    consensus_pct               REAL,

    entry                       REAL,
    exit                        REAL,
    invalidation                REAL,
    stop                        REAL,

    price_at_prediction         REAL NOT NULL,
    expected_move_pct           REAL,
    base_rate_move_pct          REAL,

    dissent_summary             TEXT,
    correlated_evidence_warning TEXT,
    prosecutor_verdict          TEXT,
    cost_audit_passed           INTEGER,

    model_versions_json         TEXT,
    data_snapshot_hash          TEXT NOT NULL,

    -- Addendum A8: blind vote is locked before any discussion phase runs and
    -- is never overwritten by it.
    blind_vote                  TEXT,
    blind_probability           REAL,
    blind_consensus_pct         REAL,
    discussed_vote               TEXT,
    discussed_probability        REAL,
    discussion_enabled           INTEGER NOT NULL DEFAULT 0,
    flip_count                   INTEGER,
    protected_dissenters         TEXT,

    prev_hash                    TEXT NOT NULL,
    row_hash                     TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS seat_votes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id    TEXT NOT NULL REFERENCES predictions(id),
    seat_id          TEXT NOT NULL,
    vote             TEXT NOT NULL,
    probability      REAL NOT NULL,
    entry            REAL,
    exit             REAL,
    dispersion       REAL,
    data_quality     TEXT,
    thesis           TEXT,
    weight_applied   REAL,
    model_id         TEXT,
    provider         TEXT,
    verdict_json     TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resolutions (
    prediction_id       TEXT PRIMARY KEY REFERENCES predictions(id),
    resolved_at          TEXT NOT NULL,
    price_at_resolve     REAL NOT NULL,
    high                 REAL,
    low                  REAL,
    direction_correct    INTEGER,
    entry_hit            INTEGER,
    exit_hit             INTEGER,
    invalidation_hit     INTEGER,
    mfe_pct              REAL,
    mae_pct              REAL,
    realised_move_pct    REAL,
    brier                REAL,
    notes                TEXT
);

-- Immutability: the ledger is append-only. Any UPDATE or DELETE attempt on
-- these three tables aborts the statement.
CREATE TRIGGER IF NOT EXISTS predictions_no_update
BEFORE UPDATE ON predictions
BEGIN
    SELECT RAISE(ABORT, 'predictions is append-only: UPDATE is forbidden');
END;

CREATE TRIGGER IF NOT EXISTS predictions_no_delete
BEFORE DELETE ON predictions
BEGIN
    SELECT RAISE(ABORT, 'predictions is append-only: DELETE is forbidden');
END;

CREATE TRIGGER IF NOT EXISTS seat_votes_no_update
BEFORE UPDATE ON seat_votes
BEGIN
    SELECT RAISE(ABORT, 'seat_votes is append-only: UPDATE is forbidden');
END;

CREATE TRIGGER IF NOT EXISTS seat_votes_no_delete
BEFORE DELETE ON seat_votes
BEGIN
    SELECT RAISE(ABORT, 'seat_votes is append-only: DELETE is forbidden');
END;

CREATE TRIGGER IF NOT EXISTS resolutions_no_update
BEFORE UPDATE ON resolutions
BEGIN
    SELECT RAISE(ABORT, 'resolutions is append-only: UPDATE is forbidden');
END;

CREATE TRIGGER IF NOT EXISTS resolutions_no_delete
BEFORE DELETE ON resolutions
BEGIN
    SELECT RAISE(ABORT, 'resolutions is append-only: DELETE is forbidden');
END;
