"""FastAPI routes for The High Council. Static UI is mounted at "/" from
council/ui/; everything else lives under /api. Run with:
    uvicorn council.api.main:app --reload
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from council.api.serialize import to_jsonable
from council.calibration.benchmark import compute_benchmark
from council.calibration.officer import compute_seat_calibration, rank_for_seat
from council.config import get_settings
from council.crypt.db import connect
from council.engine.orchestrator import TIER_I_SEATS, run_deliberation
from council.engine.resolution_sweep import sweep_unresolved

app = FastAPI(title="The High Council")

_UI_DIR = Path(__file__).resolve().parents[1] / "ui"
_SEAT_TITLES = {seat.id: seat.title for seat in TIER_I_SEATS}


@app.get("/api/deliberate/stream")
async def deliberate_stream(
    ticker: str, horizon: str, as_of: str | None = None, context: str | None = None
):
    if horizon not in ("1d", "1w", "1m", "1y"):
        raise HTTPException(400, f"invalid horizon '{horizon}'")
    settings = get_settings()
    settings.ensure_dirs()
    as_of_dt = datetime.fromisoformat(as_of) if as_of else None

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()

        async def progress(event: str, payload: dict) -> None:
            await queue.put((event, to_jsonable(payload)))

        # Emitted before anything else so a client can show whether this
        # run is about to spend real money -- a real deliberation billed
        # real money once despite the user believing NO_LLM=true made it
        # free, and nothing in the stream said otherwise until the bill did.
        await queue.put(
            (
                "mode",
                {
                    "is_fixture": settings.resolved_no_llm,
                    "message": (
                        "FIXTURE -- no Anthropic API calls, $0 cost"
                        if settings.resolved_no_llm
                        else "LIVE -- real Anthropic API calls will be made and billed"
                    ),
                },
            )
        )

        async def run() -> None:
            try:
                result = await run_deliberation(
                    ticker.upper(),
                    horizon,
                    settings,
                    as_of=as_of_dt,
                    progress=progress,
                    context=context,
                )
                await queue.put(("done", to_jsonable(result)))
            except Exception as exc:  # noqa: BLE001 -- surface any failure to the client, don't hang it
                await queue.put(("error", {"message": str(exc)}))
            finally:
                await queue.put((None, None))

        task = asyncio.create_task(run())
        try:
            while True:
                event, payload = await queue.get()
                if event is None:
                    break
                yield f"event: {event}\ndata: {json.dumps(payload)}\n\n"
        finally:
            await task

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/resolve")
async def resolve():
    settings = get_settings()
    settings.ensure_dirs()
    swept = await sweep_unresolved(settings)
    return {"swept": to_jsonable(swept)}


@app.get("/api/predictions")
async def list_predictions(ticker: str | None = None, horizon: str | None = None, limit: int = 50):
    settings = get_settings()
    settings.ensure_dirs()
    conn = connect(settings.council_db_path)
    try:
        query = (
            "SELECT p.id, p.created_at, p.ticker, p.horizon, p.resolve_at, p.council_vote, "
            "p.council_confidence, p.blind_vote, p.entry, p.exit, p.invalidation, "
            "p.total_cost_usd, p.total_input_tokens, p.total_output_tokens, "
            "r.direction_correct, r.realised_move_pct, r.resolved_at "
            "FROM predictions p LEFT JOIN resolutions r ON r.prediction_id = p.id WHERE 1=1"
        )
        params: list = []
        if ticker:
            query += " AND p.ticker = ?"
            params.append(ticker.upper())
        if horizon:
            query += " AND p.horizon = ?"
            params.append(horizon)
        query += " ORDER BY p.created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return {"predictions": [dict(row) for row in rows]}
    finally:
        conn.close()


@app.get("/api/predictions/{prediction_id}")
async def get_prediction(prediction_id: str):
    settings = get_settings()
    settings.ensure_dirs()
    conn = connect(settings.council_db_path)
    try:
        pred = conn.execute("SELECT * FROM predictions WHERE id = ?", (prediction_id,)).fetchone()
        if not pred:
            raise HTTPException(404, "prediction not found")
        votes = conn.execute(
            "SELECT * FROM seat_votes WHERE prediction_id = ?", (prediction_id,)
        ).fetchall()
        resolution = conn.execute(
            "SELECT * FROM resolutions WHERE prediction_id = ?", (prediction_id,)
        ).fetchone()
        return {
            "prediction": dict(pred),
            "seat_votes": [
                {**dict(v), "verdict": json.loads(v["verdict_json"])} for v in votes
            ],
            "resolution": dict(resolution) if resolution else None,
        }
    finally:
        conn.close()


@app.get("/api/archives")
async def archives():
    settings = get_settings()
    settings.ensure_dirs()
    conn = connect(settings.council_db_path)
    try:
        seats_summary = []
        for seat in TIER_I_SEATS:
            calib = compute_seat_calibration(conn, seat.id)
            seats_summary.append(
                {
                    "seat_id": seat.id,
                    "title": seat.title,
                    "n_resolutions": calib.n_resolutions,
                    "hit_rate": calib.hit_rate,
                    "brier_score": calib.brier_score,
                    "log_loss": calib.log_loss,
                    "rank": rank_for_seat(calib, settings.calibration_min_resolutions),
                    "calibration_curve": [
                        dataclasses.asdict(b) for b in calib.calibration_curve
                    ],
                }
            )
        benchmark = compute_benchmark(conn)
        return {"seats": seats_summary, "benchmark": benchmark}
    finally:
        conn.close()


if _UI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")
