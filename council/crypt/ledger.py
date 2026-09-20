"""Append-only writers for the Crypt. No function here ever issues an UPDATE
or DELETE -- the schema's triggers would reject it anyway, but the intent is
enforced here too so a bug fails loudly in Python, not just in SQLite.

`write_prediction` writes exactly once, after the FULL pipeline (Phases
A-F) has run entirely in memory -- it is impossible to write the blind
columns now and the council/synthesis columns later, because the
immutability trigger blocks any UPDATE. Addendum A8's "written and
hash-chained before any discussion runs" requirement is satisfied by
computation order (Phase A completes before Phase C's debate begins), not
by a second database write."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime

from council.crypt.db import compute_row_hash, get_last_hash
from council.seats.base import SeatVerdict


class ResolutionWindowError(Exception):
    """Raised when a prediction's resolve_at is not strictly in the future
    at write time -- the point at which the outcome must still be
    unknowable, per the spec's forward-only evaluation guard."""


def write_prediction(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    horizon: str,
    resolve_at: datetime,
    price_at_prediction: float,
    data_snapshot_hash: str,
    model_versions: dict[str, str],
    blind_vote: str,
    blind_probability: float,
    blind_consensus_pct: float,
    council_vote: str | None = None,
    council_confidence: float | None = None,
    consensus_pct: float | None = None,
    entry: float | None = None,
    exit: float | None = None,
    invalidation: float | None = None,
    stop: float | None = None,
    expected_move_pct: float | None = None,
    base_rate_move_pct: float | None = None,
    dissent_summary: str | None = None,
    correlated_evidence_warning: str | None = None,
    prosecutor_verdict: str | None = None,
    cost_audit_passed: bool | None = None,
    created_at: datetime | None = None,
) -> str:
    created_at = created_at or datetime.utcnow()
    if resolve_at <= created_at:
        raise ResolutionWindowError(
            f"resolve_at ({resolve_at.isoformat()}) must be strictly after "
            f"created_at ({created_at.isoformat()}); a prediction may never "
            "be written after its own resolution window has opened."
        )

    prediction_id = str(uuid.uuid4())
    prev_hash = get_last_hash(conn)
    fields = {
        "id": prediction_id,
        "created_at": created_at.isoformat(),
        "ticker": ticker,
        "horizon": horizon,
        "resolve_at": resolve_at.isoformat(),
        "price_at_prediction": price_at_prediction,
        "data_snapshot_hash": data_snapshot_hash,
        "model_versions_json": json.dumps(model_versions, sort_keys=True),
        "blind_vote": blind_vote,
        "blind_probability": blind_probability,
        "blind_consensus_pct": blind_consensus_pct,
        "council_vote": council_vote,
        "council_confidence": council_confidence,
        "consensus_pct": consensus_pct,
        "entry": entry,
        "exit": exit,
        "invalidation": invalidation,
        "stop": stop,
        "expected_move_pct": expected_move_pct,
        "base_rate_move_pct": base_rate_move_pct,
        "dissent_summary": dissent_summary,
        "correlated_evidence_warning": correlated_evidence_warning,
        "prosecutor_verdict": prosecutor_verdict,
        "cost_audit_passed": int(cost_audit_passed) if cost_audit_passed is not None else None,
        "discussion_enabled": 0,
    }
    row_hash = compute_row_hash(fields, prev_hash)

    columns = [k for k in fields] + ["prev_hash", "row_hash"]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO predictions ({', '.join(columns)}) VALUES ({placeholders})",
        [*fields.values(), prev_hash, row_hash],
    )
    conn.commit()
    return prediction_id


def write_seat_vote(
    conn: sqlite3.Connection,
    *,
    prediction_id: str,
    seat_id: str,
    verdict: SeatVerdict,
    dispersion: float | None,
    weight_applied: float,
    model_id: str,
    provider: str,
) -> None:
    conn.execute(
        """
        INSERT INTO seat_votes (
            prediction_id, seat_id, vote, probability, entry, exit,
            dispersion, data_quality, thesis, weight_applied, model_id,
            provider, verdict_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            prediction_id,
            seat_id,
            verdict.vote,
            verdict.probability,
            verdict.entry,
            verdict.exit,
            dispersion,
            verdict.data_quality,
            verdict.thesis,
            weight_applied,
            model_id,
            provider,
            verdict.model_dump_json(),
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
