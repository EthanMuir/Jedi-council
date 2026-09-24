"""FastAPI routes for The High Council. Static UI is mounted at "/" from
council/ui/; everything else lives under /api. Run with:
    uvicorn council.api.main:app --reload
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from council import key_store
from council.api.auth import install_auth
from council.api.serialize import to_jsonable
from council.api.ui_files import UIFiles
from council.calibration.benchmark import compute_benchmark
from council.calibration.officer import compute_seat_calibration, rank_for_seat
from council.config import as_lite, get_settings
from council.crypt.db import connect, effective_run_mode
from council.engine import model_settings
from council.engine.cost_estimate import estimate_deliberation_cost
from council.engine.llm_client import RunStopped
from council.engine.model_catalog import ALL_ROLES, FREE_MODELS, RECOMMENDED, models_sorted_by_cost
from council.engine.orchestrator import TIER_I_SEATS, TickerNotFound, run_deliberation
from council.engine.resolution_sweep import sweep_unresolved
from council.engine.routing import planned_run_mode
from council.seats.advocates import BearAdvocateSeat, BullAdvocateSeat
from council.seats.grand_master import GrandMasterSeat
from council.seats.prosecutor import ProsecutorSeat

# Rate limits, used-up daily limits, timeouts and provider switches are
# logged under "council.*" -- sent to stderr so they land in the server log
# (journalctl -u jedi-council) with a timestamp.
_council_log = logging.getLogger("council")
if not _council_log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _council_log.addHandler(_handler)
    _council_log.setLevel(logging.INFO)
    _council_log.propagate = False

app = FastAPI(title="The High Council")
install_auth(app)

_UI_DIR = Path(__file__).resolve().parents[1] / "ui"
_SEAT_TITLES = {seat.id: seat.title for seat in TIER_I_SEATS}
# Task #74 -- every role model_catalog.ALL_ROLES covers has a display
# title somewhere: Tier I seats carry theirs on TIER_I_SEATS already
# (above), Tier II-IV roles carry theirs on their own seat classes, which
# aren't collected into a single roster the way Tier I is.
_ROLE_TITLES = {
    **_SEAT_TITLES,
    BullAdvocateSeat.id: BullAdvocateSeat.title,
    BearAdvocateSeat.id: BearAdvocateSeat.title,
    ProsecutorSeat.id: ProsecutorSeat.title,
    GrandMasterSeat.id: GrandMasterSeat.title,
}


@app.get("/api/deliberate/stream")
async def deliberate_stream(
    ticker: str,
    as_of: str | None = None,
    context: str | None = None,
    lite: bool = False,
):
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
        run_mode = planned_run_mode(settings)
        await queue.put(
            (
                "mode",
                {
                    "is_fixture": settings.resolved_no_llm,
                    "run_mode": run_mode,
                    "lite": lite,
                    "message": {
                        "sample": "SAMPLE -- no AI keys added, sample answers only, $0 cost",
                        "free": "FREE -- free-tier models only, $0 cost",
                        "paid": "LIVE -- real API calls will be made and billed",
                    }[run_mode],
                },
            )
        )

        async def run() -> None:
            try:
                result = await run_deliberation(
                    ticker.upper(),
                    settings,
                    as_of=as_of_dt,
                    progress=progress,
                    context=context,
                    lite=lite,
                )
                await queue.put(("done", to_jsonable(result)))
            except RunStopped as exc:
                await queue.put(("stopped", {"message": str(exc), "link": exc.link}))
            except TickerNotFound as exc:
                await queue.put(("invalid_ticker", {"message": str(exc)}))
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


_PREDICTION_COLUMNS = (
    "p.id, p.created_at, p.ticker, p.horizon, p.resolve_at, p.council_vote, "
    "p.council_confidence, p.p_raw, p.blind_vote, p.entry, p.exit, p.invalidation, "
    "p.expected_move_pct, p.total_cost_usd, p.total_input_tokens, p.total_output_tokens, "
    "p.run_mode, p.run_shape, p.run_id, "
    "r.direction_correct, r.realised_move_pct, r.resolved_at"
)


def _prediction_row(row) -> dict:
    prediction = dict(row)
    prediction["run_mode"] = effective_run_mode(row["run_mode"], row["total_cost_usd"])
    prediction["run_shape"] = row["run_shape"] or "full"
    return prediction


def _group_runs(predictions: list[dict]) -> list[dict]:
    """One entry per run, its term rows under "terms". A row saved before
    terms existed has no run_id and stands alone, keyed by its old horizon."""
    runs: dict[str, dict] = {}
    for prediction in predictions:
        run_id = prediction["run_id"] or prediction["id"]
        run = runs.setdefault(
            run_id,
            {
                "run_id": run_id,
                "ticker": prediction["ticker"],
                "created_at": prediction["created_at"],
                "run_mode": prediction["run_mode"],
                "run_shape": prediction["run_shape"],
                # Run-wide totals, repeated on every row of the run.
                "total_cost_usd": prediction["total_cost_usd"],
                "legacy": prediction["run_id"] is None,
                "terms": {},
            },
        )
        run["terms"][prediction["horizon"]] = prediction
    return list(runs.values())


@app.get("/api/predictions")
async def list_predictions(
    ticker: str | None = None,
    term: str | None = None,
    mode: str | None = None,
    limit: int = 50,
):
    """`limit` counts runs; `term` keeps only that term's row of each run."""
    settings = get_settings()
    settings.ensure_dirs()
    conn = connect(settings.council_db_path)
    try:
        query = (
            f"SELECT {_PREDICTION_COLUMNS} "
            "FROM predictions p LEFT JOIN resolutions r ON r.prediction_id = p.id WHERE 1=1"
        )
        params: list = []
        if ticker:
            query += " AND p.ticker = ?"
            params.append(ticker.upper())
        if term:
            query += " AND p.horizon = ?"
            params.append(term)
        if mode:
            # Same rule as crypt.db.effective_run_mode, for rows saved
            # before runs were labeled.
            query += (
                " AND COALESCE(p.run_mode, CASE WHEN p.total_cost_usd > 0 "
                "THEN 'paid' ELSE 'sample' END) = ?"
            )
            params.append(mode)
        query += " ORDER BY p.created_at DESC, p.rowid ASC LIMIT ?"
        params.append(limit * 3)
        predictions = [_prediction_row(row) for row in conn.execute(query, params).fetchall()]
        runs = _group_runs(predictions)[:limit]
        kept = {run["run_id"] for run in runs}
        return {
            "runs": runs,
            "predictions": [p for p in predictions if (p["run_id"] or p["id"]) in kept],
        }
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


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    """Everything saved for one run: each term's row, resolution and seat
    votes, plus the Grand Master's synthesis. A pre-terms prediction id
    works too (its run is just that one row)."""
    settings = get_settings()
    settings.ensure_dirs()
    conn = connect(settings.council_db_path)
    try:
        preds = conn.execute(
            "SELECT * FROM predictions WHERE run_id = ? OR (run_id IS NULL AND id = ?) "
            "ORDER BY rowid ASC",
            (run_id, run_id),
        ).fetchall()
        if not preds:
            raise HTTPException(404, "run not found")
        terms = {}
        for pred in preds:
            votes = conn.execute(
                "SELECT * FROM seat_votes WHERE prediction_id = ?", (pred["id"],)
            ).fetchall()
            resolution = conn.execute(
                "SELECT * FROM resolutions WHERE prediction_id = ?", (pred["id"],)
            ).fetchone()
            prediction = dict(pred)
            prediction.pop("synthesis_json", None)
            terms[pred["horizon"]] = {
                "prediction": prediction,
                "seat_votes": [
                    {**dict(v), "verdict": json.loads(v["verdict_json"])} for v in votes
                ],
                "resolution": dict(resolution) if resolution else None,
            }
        first = preds[0]
        return {
            "run_id": run_id,
            "ticker": first["ticker"],
            "created_at": first["created_at"],
            "run_mode": effective_run_mode(first["run_mode"], first["total_cost_usd"]),
            "run_shape": first["run_shape"] or "full",
            "legacy": first["run_id"] is None,
            "synthesis": json.loads(first["synthesis_json"]) if first["synthesis_json"] else None,
            "terms": terms,
        }
    finally:
        conn.close()


@app.get("/api/archives")
async def archives(mode: str | None = None):
    """`mode` = "paid" / "free" keeps free-tier runs' track record apart
    from the paid council's; omitted means every run."""
    if mode not in (None, "paid", "free"):
        raise HTTPException(400, f"invalid mode '{mode}'")
    settings = get_settings()
    settings.ensure_dirs()
    conn = connect(settings.council_db_path)
    try:
        seats_summary = []
        for seat in TIER_I_SEATS:
            calib = compute_seat_calibration(conn, seat.id, run_mode=mode)
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
        benchmark = compute_benchmark(conn, run_mode=mode)
        return {"seats": seats_summary, "benchmark": benchmark, "mode": mode}
    finally:
        conn.close()


class _ModelOverrideRequest(BaseModel):
    role: str
    model_id: str | None = None  # None clears the override back to recommended


@app.get("/api/settings/models")
async def get_model_settings():
    settings = get_settings()
    settings.ensure_dirs()
    conn = model_settings.connect(settings.settings_db_path)
    try:
        overrides = model_settings.get_overrides(conn)
    finally:
        conn.close()

    catalog = [
        {
            "id": m.id,
            "provider": m.provider,
            "display_name": m.display_name,
            "input_price_per_mtok": m.input_price_per_mtok,
            "output_price_per_mtok": m.output_price_per_mtok,
            "typical_call_cost_usd": m.typical_call_cost_usd,
            "free": m.free,
        }
        for m in models_sorted_by_cost(descending=True)
    ]
    roles = [
        {
            "role": role,
            "title": _ROLE_TITLES.get(role, role),
            "recommended_model": RECOMMENDED[role],
            "current_model": overrides.get(role, RECOMMENDED[role]),
            "is_override": role in overrides,
        }
        for role in ALL_ROLES
    ]
    return {"catalog": catalog, "roles": roles}


@app.post("/api/settings/models")
async def set_model_setting(body: _ModelOverrideRequest):
    if body.role not in RECOMMENDED:
        raise HTTPException(400, f"unknown role '{body.role}'")
    settings = get_settings()
    settings.ensure_dirs()
    conn = model_settings.connect(settings.settings_db_path)
    try:
        if body.model_id is None:
            model_settings.clear_override(conn, body.role)
        else:
            try:
                model_settings.set_override(conn, body.role, body.model_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        current = model_settings.get_effective_model(conn, body.role)
    finally:
        conn.close()
    return {"role": body.role, "current_model": current, "is_override": body.model_id is not None}


class _ApiKeyRequest(BaseModel):
    name: str
    value: str


def _api_key_status(settings) -> list[dict]:
    """Where each key comes from and its last four characters -- never the
    key itself. Callers pass a freshly read get_settings() after any change,
    since that's what layers saved keys over .env."""
    saved = key_store.saved_keys(settings.settings_db_path)
    status = []
    for name in key_store.KEY_NAMES:
        if name in saved:
            value, source = saved[name], "app"
        else:
            value = getattr(settings, name, "") or ""
            source = "env" if value else None
        status.append(
            {"name": name, "is_set": bool(value), "source": source, "last4": value[-4:] if value else None}
        )
    return status


@app.get("/api/settings/keys")
async def get_api_keys():
    settings = get_settings()
    settings.ensure_dirs()
    return {"keys": _api_key_status(settings)}


@app.post("/api/settings/keys")
async def save_api_key(body: _ApiKeyRequest):
    if body.name not in key_store.KEY_NAMES:
        raise HTTPException(400, f"unknown key '{body.name}'")
    settings = get_settings()
    settings.ensure_dirs()
    try:
        key_store.save_key(settings.settings_db_path, body.name, body.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"keys": _api_key_status(get_settings())}


@app.delete("/api/settings/keys/{name}")
async def delete_api_key(name: str):
    if name not in key_store.KEY_NAMES:
        raise HTTPException(400, f"unknown key '{name}'")
    settings = get_settings()
    settings.ensure_dirs()
    key_store.delete_key(settings.settings_db_path, name)
    return {"keys": _api_key_status(get_settings())}


class _FreeModeRequest(BaseModel):
    enabled: bool


def _free_mode_status(settings) -> dict:
    conn = model_settings.connect(settings.settings_db_path)
    try:
        enabled = model_settings.free_mode_enabled(conn)
    finally:
        conn.close()
    return {
        "enabled": enabled,
        "gemini_key": bool(settings.google_api_key),
        "groq_key": bool(settings.groq_api_key),
        "run_mode": planned_run_mode(settings),
    }


@app.get("/api/settings/free-mode")
async def get_free_mode():
    settings = get_settings()
    settings.ensure_dirs()
    return _free_mode_status(settings)


@app.post("/api/settings/free-mode")
async def set_free_mode(body: _FreeModeRequest):
    settings = get_settings()
    settings.ensure_dirs()
    conn = model_settings.connect(settings.settings_db_path)
    try:
        if body.enabled:
            # Gemini first when both are there -- routing overflows to Groq
            # on its own when Gemini's daily limit runs out.
            if settings.google_api_key:
                free_model = FREE_MODELS["google"]
            elif settings.groq_api_key:
                free_model = FREE_MODELS["groq"]
            else:
                raise HTTPException(
                    400, "add a free Gemini or Groq key under API Keys first"
                )
            model_settings.enable_free_mode(conn, free_model)
        else:
            model_settings.disable_free_mode(conn)
    finally:
        conn.close()
    return _free_mode_status(settings)


@app.get("/api/settings/cost-estimate")
async def get_settings_cost_estimate(lite: bool = False):
    settings = get_settings()
    settings.ensure_dirs()
    if lite:
        settings = as_lite(settings)
    # The ticker is a placeholder -- cost depends only on which models are
    # routed to and how many calls each seat/round makes, never on the
    # symbol itself, so any value here estimates identically.
    estimate = estimate_deliberation_cost("EST", settings)
    return {
        "is_fixture": estimate.is_fixture,
        "run_mode": planned_run_mode(settings),
        "lite": lite,
        "total_cost_usd": estimate.total_cost_usd,
        "total_calls": estimate.total_calls,
        "line_items": [dataclasses.asdict(li) for li in estimate.line_items],
    }


if _UI_DIR.exists():
    app.mount("/", UIFiles(directory=str(_UI_DIR), html=True), name="ui")
