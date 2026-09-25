"""The admin page's API (#114, #124): approve and manage people, see every
run (the Master Crypt), usage and analytics, and review and publish seat
weight releases. The sign-in gate (auth.py) lets only admins reach
/api/admin/*; each route checks again in case it's ever mounted elsewhere."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from council import accounts, emailer
from council.api import auth
from council.calibration import releases
from council.calibration.benchmark import compute_benchmark
from council.config import get_settings
from council.crypt.db import connect, effective_run_mode
from council.engine.horizons import TERMS
from council.engine.orchestrator import TIER_I_SEATS

router = APIRouter(prefix="/api/admin")


def _require_admin(request: Request) -> accounts.User | None:
    user = getattr(request.state, "user", None)
    if get_settings().resolved_auth_enabled and (user is None or not user.is_admin):
        raise HTTPException(403, "admins only")
    return user


def _get_target(conn, user_id: int) -> accounts.User:
    target = accounts.get_user(conn, user_id)
    if target is None:
        raise HTTPException(404, "no such person")
    return target


def _usage_by_account() -> dict[int, dict]:
    """Runs, cost and last run per account (0 = the owner)."""
    settings = get_settings()
    conn = connect(settings.council_db_path)
    try:
        rows = conn.execute(
            "SELECT COALESCE(user_id, 0) AS account, COALESCE(run_id, id) AS run, "
            "MAX(total_cost_usd) AS cost, MIN(created_at) AS created_at, MAX(run_mode) AS run_mode "
            "FROM predictions GROUP BY account, run"
        ).fetchall()
    finally:
        conn.close()
    usage: dict[int, dict] = defaultdict(lambda: {"runs": 0, "paid_runs": 0, "cost_usd": 0.0, "last_run_at": None})
    for r in rows:
        u = usage[r["account"]]
        u["runs"] += 1
        u["cost_usd"] += r["cost"] or 0.0
        if effective_run_mode(r["run_mode"], r["cost"]) == "paid":
            u["paid_runs"] += 1
        if not u["last_run_at"] or r["created_at"] > u["last_run_at"]:
            u["last_run_at"] = r["created_at"]
    return usage


# ---- people ---------------------------------------------------------------------------


@router.get("/users")
async def list_users(request: Request):
    _require_admin(request)
    conn = auth._conn()
    try:
        people = accounts.list_users(conn)
    finally:
        conn.close()
    usage = _usage_by_account()
    return {
        "users": [
            {**p.public(), "usage": usage.get(p.account, {"runs": 0, "paid_runs": 0, "cost_usd": 0.0, "last_run_at": None})}
            for p in people
        ],
        "email_enabled": emailer.email_configured(get_settings()),
    }


class _StatusRequest(BaseModel):
    status: str


@router.post("/users/{user_id}/status")
async def set_user_status(user_id: int, body: _StatusRequest, request: Request):
    _require_admin(request)
    if body.status not in ("active", "disabled"):
        raise HTTPException(400, "status must be active or disabled")
    conn = auth._conn()
    try:
        target = _get_target(conn, user_id)
        if target.is_owner:
            raise HTTPException(400, "the owner's account can't be changed")
        was_pending = target.status == "pending"
        accounts.set_status(conn, user_id, body.status)
        emailed = False
        if was_pending and body.status == "active":
            subject, text = emailer.approved_message(target.name, f"{auth.site_url(request)}/login?notice=approved")
            emailed = await emailer.send_email(get_settings(), target.email, subject, text)
        return {"ok": True, "emailed": emailed}
    finally:
        conn.close()


class _AdminRequest(BaseModel):
    is_admin: bool


@router.post("/users/{user_id}/admin")
async def set_user_admin(user_id: int, body: _AdminRequest, request: Request):
    _require_admin(request)
    conn = auth._conn()
    try:
        target = _get_target(conn, user_id)
        if target.is_owner:
            raise HTTPException(400, "the owner is always an admin")
        accounts.set_admin(conn, user_id, body.is_admin)
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, request: Request):
    """Declining a sign-up, or removing someone: their account, keys and
    settings go. Their scored runs stay in the (append-only) Crypt."""
    _require_admin(request)
    conn = auth._conn()
    try:
        target = _get_target(conn, user_id)
        if target.is_owner:
            raise HTTPException(400, "the owner's account can't be deleted")
        auth.delete_account_data(get_settings().settings_db_path, target)
        accounts.delete_user(conn, user_id)
        return {"ok": True}
    finally:
        conn.close()


@router.post("/users/{user_id}/reset-link")
async def make_reset_link(user_id: int, request: Request):
    """A one-hour reset link to pass on by hand -- for when email isn't set
    up, or it went to spam."""
    _require_admin(request)
    conn = auth._conn()
    try:
        target = _get_target(conn, user_id)
        token = accounts.create_reset_token(conn, target.id)
        return {"link": f"{auth.site_url(request)}/reset?token={token}", "hours": accounts.RESET_HOURS}
    finally:
        conn.close()


# ---- the Master Crypt and analytics ------------------------------------------------------


@router.get("/runs")
async def all_runs(request: Request, limit: int = 200, account: int | None = None):
    _require_admin(request)
    settings = get_settings()
    conn = auth._conn()
    try:
        people = {p.account: p for p in accounts.list_users(conn)}
    finally:
        conn.close()
    cconn = connect(settings.council_db_path)
    try:
        where, params = "1=1", []
        if account is not None:
            where, params = ("(user_id IS NULL OR user_id = 0)", []) if account == 0 else ("user_id = ?", [account])
        rows = cconn.execute(
            "SELECT p.id, p.run_id, p.ticker, p.horizon, p.created_at, p.p_raw, p.council_vote, "
            "p.council_confidence, p.total_cost_usd, p.run_mode, p.run_shape, COALESCE(p.user_id, 0) AS account, "
            "r.direction_correct, r.realised_move_pct "
            f"FROM predictions p LEFT JOIN resolutions r ON r.prediction_id = p.id WHERE {where} "
            "ORDER BY p.created_at DESC, p.rowid ASC LIMIT ?",
            (*params, limit * 3),
        ).fetchall()
    finally:
        cconn.close()
    runs: dict[str, dict] = {}
    for r in rows:
        key = r["run_id"] or r["id"]
        person = people.get(r["account"])
        run = runs.setdefault(key, {
            "run_id": key, "ticker": r["ticker"], "created_at": r["created_at"],
            "run_mode": effective_run_mode(r["run_mode"], r["total_cost_usd"]),
            "run_shape": r["run_shape"] or "full", "total_cost_usd": r["total_cost_usd"],
            "account": r["account"],
            "person": {"name": person.name, "email": person.email} if person else None,
            "terms": {},
        })
        run["terms"][r["horizon"]] = {
            "p_raw": r["p_raw"], "council_vote": r["council_vote"], "council_confidence": r["council_confidence"],
            "direction_correct": r["direction_correct"], "realised_move_pct": r["realised_move_pct"],
        }
    return {"runs": list(runs.values())[:limit]}


@router.get("/analytics")
async def analytics(request: Request):
    _require_admin(request)
    settings = get_settings()
    conn = auth._conn()
    try:
        people = accounts.list_users(conn)
    finally:
        conn.close()
    usage = _usage_by_account()
    cconn = connect(settings.council_db_path)
    try:
        since = (datetime.utcnow() - timedelta(days=30)).date().isoformat()
        per_day = cconn.execute(
            "SELECT substr(created_at, 1, 10) AS day, COUNT(DISTINCT COALESCE(run_id, id)) AS runs "
            "FROM predictions WHERE created_at >= ? GROUP BY day ORDER BY day", (since,)
        ).fetchall()
        tickers = cconn.execute(
            "SELECT ticker, COUNT(DISTINCT COALESCE(run_id, id)) AS runs FROM predictions "
            "GROUP BY ticker ORDER BY runs DESC LIMIT 10"
        ).fetchall()
        scored = cconn.execute("SELECT COUNT(*) FROM resolutions").fetchone()[0]
        bench = {tier: compute_benchmark(cconn, run_mode=tier) for tier in ("paid", "free")}
    finally:
        cconn.close()
    return {
        "people": {s: sum(1 for p in people if p.status == s) for s in accounts.STATUSES},
        "runs_total": sum(u["runs"] for u in usage.values()),
        "cost_total_usd": round(sum(u["cost_usd"] for u in usage.values()), 4),
        "runs_per_day": [dict(r) for r in per_day],
        "top_tickers": [dict(r) for r in tickers],
        "scored_calls": scored,
        "benchmark": bench,
    }


# ---- weight releases ---------------------------------------------------------------------


def _with_changes(candidate: dict, live: dict | None) -> dict:
    """Each seat and term: live weight, new weight, and the change."""
    changes = {}
    for term in TERMS:
        changes[term] = {}
        for seat in TIER_I_SEATS:
            new = candidate["weights"].get(term, {}).get(seat.id, 1.0)
            old = (live["weights"].get(term, {}).get(seat.id, 1.0) if live else 1.0)
            changes[term][seat.id] = {"live": old, "new": new, "change": round(new - old, 3)}
    return changes


@router.get("/weights")
async def weights_overview(request: Request):
    _require_admin(request)
    rconn = releases.connect(get_settings().settings_db_path)
    try:
        return {
            "live": {tier: releases.published_release(rconn, tier) for tier in releases.TIERS},
            "releases": releases.list_releases(rconn),
            "min_calls": get_settings().calibration_min_resolutions,
        }
    finally:
        rconn.close()


class _CandidateRequest(BaseModel):
    tier: str
    note: str = ""


@router.post("/weights/candidate")
async def make_candidate(body: _CandidateRequest, request: Request):
    """Computes a new set from everyone's scored runs and saves it as a
    draft to review. Nothing changes for live runs until it's published."""
    _require_admin(request)
    if body.tier not in releases.TIERS:
        raise HTTPException(400, "tier must be paid or free")
    settings = get_settings()
    cconn = connect(settings.council_db_path)
    try:
        candidate = releases.compute_candidate(cconn, [s.id for s in TIER_I_SEATS], body.tier, settings)
    finally:
        cconn.close()
    rconn = releases.connect(settings.settings_db_path)
    try:
        release_id = releases.save_draft(rconn, candidate, body.note.strip()[:300])
        draft = releases.get_release(rconn, release_id)
        live = releases.published_release(rconn, body.tier)
    finally:
        rconn.close()
    return {"release": draft, "changes": _with_changes(draft, live)}


@router.get("/weights/{release_id}")
async def get_release(release_id: int, request: Request):
    _require_admin(request)
    rconn = releases.connect(get_settings().settings_db_path)
    try:
        release = releases.get_release(rconn, release_id)
        if release is None:
            raise HTTPException(404, "no such release")
        live = releases.published_release(rconn, release["tier"])
    finally:
        rconn.close()
    return {"release": release, "changes": _with_changes(release, live)}


@router.post("/weights/{release_id}/publish")
async def publish_release(release_id: int, request: Request):
    _require_admin(request)
    rconn = releases.connect(get_settings().settings_db_path)
    try:
        if releases.get_release(rconn, release_id) is None:
            raise HTTPException(404, "no such release")
        releases.publish(rconn, release_id)
        return {"ok": True}
    finally:
        rconn.close()


def install_admin(app) -> None:
    app.include_router(router)
