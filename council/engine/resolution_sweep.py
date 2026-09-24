"""The Crypt's "UPDATE" lever: sweeps every unresolved prediction whose
resolve_at has passed, fetches actual price action, and writes a
resolution row. Never modifies a prediction row -- resolutions are new
rows, keyed 1:1 to a prediction_id, immutable once written.

Also runs Addendum A6's immediate reflection for every participating seat
on every newly-resolved prediction, storing the resulting lesson in that
seat's memory -- this is the mechanism by which the council actually
improves rather than just being measured."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from council.config import Settings
from council.crypt.db import connect, get_sweepable_predictions
from council.crypt.ledger import write_resolution
from council.crypt.resolution import resolve_prediction
from council.engine.llm_client import LLMClient
from council.engine.orchestrator import build_data_service
from council.memory.reflection import generate_immediate_reflection
from council.memory.store import MemoryStore
from council.seats.base import SeatVerdict, is_abstention


@dataclass
class SweepResult:
    prediction_id: str
    ticker: str
    horizon: str
    council_vote: str
    direction_correct: bool | None
    realised_move_pct: float
    lessons_written: int


async def sweep_unresolved(settings: Settings, as_of: datetime | None = None) -> list[SweepResult]:
    as_of = as_of or datetime.utcnow()
    data_service = build_data_service(settings)
    llm_client = LLMClient(settings)
    conn = connect(settings.council_db_path)
    memory = MemoryStore(conn, cap_per_seat=settings.memory_cap_per_seat)
    try:
        rows = get_sweepable_predictions(conn, as_of.isoformat())
        results: list[SweepResult] = []
        for row in rows:
            created_at = datetime.fromisoformat(row["created_at"])
            resolve_at = datetime.fromisoformat(row["resolve_at"])
            lookback_days = (resolve_at.date() - created_at.date()).days + 5

            series = await data_service.get_ohlcv(
                row["ticker"], as_of=resolve_at, lookback_days=lookback_days
            )
            window_bars = [
                b for b in series.bars if created_at.date() <= b.trade_date <= resolve_at.date()
            ]
            if not window_bars:
                # No price data available for this window yet (e.g. a fixture
                # ticker with no coverage that far back/forward) -- leave
                # unresolved, try again on the next sweep rather than guess.
                continue

            council_vote = row["council_vote"] or "NO_CONVICTION"
            outcome = resolve_prediction(
                window_bars,
                price_at_prediction=row["price_at_prediction"],
                council_vote=council_vote,
                council_confidence=row["council_confidence"],
                entry=row["entry"],
                exit=row["exit"],
                invalidation=row["invalidation"],
            )
            write_resolution(
                conn, prediction_id=row["id"], resolved_at=resolve_at, outcome=outcome
            )

            lessons_written = await _reflect_on_resolution(
                conn=conn,
                memory=memory,
                prediction_id=row["id"],
                ticker=row["ticker"],
                horizon=row["horizon"],
                resolve_at=resolve_at,
                outcome=outcome,
                llm_client=llm_client,
                model=settings.seat_model,
            )

            results.append(
                SweepResult(
                    prediction_id=row["id"],
                    ticker=row["ticker"],
                    horizon=row["horizon"],
                    council_vote=council_vote,
                    direction_correct=outcome.direction_correct,
                    realised_move_pct=outcome.realised_move_pct,
                    lessons_written=lessons_written,
                )
            )
        return results
    finally:
        conn.close()


async def _reflect_on_resolution(
    *,
    conn,
    memory: MemoryStore,
    prediction_id: str,
    ticker: str,
    horizon: str,
    resolve_at: datetime,
    outcome,
    llm_client: LLMClient,
    model: str,
) -> int:
    """Every directional seat_vote on this prediction gets its own
    immediate reflection, written to its own memory. A seat is given only
    its own verdict and the realised outcome -- nothing about what other
    seats said, per Addendum A6."""
    seat_vote_rows = conn.execute(
        "SELECT seat_id, verdict_json FROM seat_votes WHERE prediction_id = ?", (prediction_id,)
    ).fetchall()

    written = 0
    for row in seat_vote_rows:
        verdict = SeatVerdict(**json.loads(row["verdict_json"]))
        if is_abstention(verdict.vote):
            continue  # nothing to learn from an abstention here

        lesson = await generate_immediate_reflection(
            seat_id=row["seat_id"],
            ticker=ticker,
            horizon=horizon,
            verdict=verdict,
            outcome=outcome,
            llm_client=llm_client,
            model=model,
        )
        if lesson is None:
            continue

        memory.add(
            seat_id=row["seat_id"],
            ticker=ticker,
            kind="lesson",
            as_of=resolve_at,
            horizon=horizon,
            content=lesson.model_dump(),
            importance=1.0,
        )
        written += 1
    return written
