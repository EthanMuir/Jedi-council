"""Append-only writers for the Crypt. No function here ever issues an UPDATE
or DELETE -- the schema's triggers would reject it anyway, but the intent is
enforced here too so a bug fails loudly in Python, not just in SQLite."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from council.crypt.db import compute_row_hash, get_last_hash
from council.seats.base import SeatVerdict


class ResolutionWindowError(Exception):
    """Raised when a prediction's resolve_at is not strictly in the future
    at write time -- the point at which the outcome must still be
    unknowable, per the spec's forward-only evaluation guard."""


def write_blind_prediction(
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
    created_at: datetime | None = None,
) -> str:
    """Writes the Phase A (blind round) result only. `council_vote` and the
    other Tier IV synthesis fields stay NULL until the Grand Master exists
    (Phase 3) -- this function must never be used to fake a synthesised
    verdict."""
    created_at = created_at or datetime.utcnow()
    if resolve_at <= created_at:
        raise ResolutionWindowError(
            f"resolve_at ({resolve_at.isoformat()}) must be strictly after "
            f"created_at ({created_at.isoformat()}); a prediction may never "
            "be written after its own resolution window has opened."
        )

    import json

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
        "discussion_enabled": 0,
    }
    row_hash = compute_row_hash(fields, prev_hash)

    conn.execute(
        """
        INSERT INTO predictions (
            id, created_at, ticker, horizon, resolve_at,
            price_at_prediction, data_snapshot_hash, model_versions_json,
            blind_vote, blind_probability, blind_consensus_pct,
            discussion_enabled, prev_hash, row_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fields["id"],
            fields["created_at"],
            fields["ticker"],
            fields["horizon"],
            fields["resolve_at"],
            fields["price_at_prediction"],
            fields["data_snapshot_hash"],
            fields["model_versions_json"],
            fields["blind_vote"],
            fields["blind_probability"],
            fields["blind_consensus_pct"],
            fields["discussion_enabled"],
            prev_hash,
            row_hash,
        ),
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
