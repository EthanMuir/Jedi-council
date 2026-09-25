"""Weight releases (#124): the seat weights live runs use are never computed
on the fly. The owner computes a candidate from everyone's scored runs on
the admin page, compares it with what's live, and publishes it; runs then
use the last published release for their tier until the next one.

Two tiers, trained apart: paid runs train the paid weights, free-model runs
the free weights. Sample runs (canned answers) never count.

Pooling rules, so many people's runs make the weights better rather than
noisier:
- Each ticker counts once per day and term: 50 people running NVDA on the
  same day is one real outcome, so their leans are averaged into one call.
- No one person can make up more than MAX_USER_SHARE of a seat's record on
  a term; beyond that their runs are down-weighted.
- The rest is the Calibration Officer's rule: a seat's weight only moves
  once it has calibration_min_resolutions scored calls, between 0.25x and
  2x by Brier score, with the worst seats dropped once the cohort is big
  enough."""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from council.config import Settings
from council.crypt.db import effective_run_mode
from council.engine.horizons import TERMS

TIERS = ("paid", "free")
MAX_USER_SHARE = 0.2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS weight_releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tier TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',  -- draft | published | retired
    created_at TEXT NOT NULL,
    published_at TEXT,
    weights_json TEXT NOT NULL,   -- {term: {seat_id: weight}}
    stats_json TEXT NOT NULL,     -- {term: {seat_id: {n, hit_rate, brier}}}
    n_calls INTEGER NOT NULL,     -- de-duplicated scored calls behind it
    n_people INTEGER NOT NULL,
    note TEXT
);
"""


def connect(settings_db_path: str) -> sqlite3.Connection:
    Path(settings_db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings_db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


# ---- computing a candidate -----------------------------------------------------


def _scored_rows(council_conn: sqlite3.Connection, tier: str) -> list[dict]:
    rows = council_conn.execute(
        """
        SELECT sv.seat_id, sv.vote, sv.probability, p.horizon, p.ticker, p.created_at,
               p.run_mode, p.total_cost_usd, p.user_id, r.realised_move_pct
        FROM seat_votes sv
        JOIN predictions p ON p.id = sv.prediction_id
        JOIN resolutions r ON r.prediction_id = sv.prediction_id
        WHERE sv.vote IN ('BULLISH', 'BEARISH') AND p.horizon IN ('short', 'medium', 'long')
        """
    ).fetchall()
    out = []
    for r in rows:
        if effective_run_mode(r["run_mode"], r["total_cost_usd"]) != tier:
            continue
        p_up = r["probability"] if r["vote"] == "BULLISH" else 1 - r["probability"]
        out.append({
            "seat": r["seat_id"], "term": r["horizon"], "ticker": r["ticker"],
            "day": r["created_at"][:10], "user": r["user_id"] or 0,
            "p_up": p_up, "moved_up": r["realised_move_pct"] > 0,
        })
    return out


def _pooled_stats(rows: list[dict]) -> dict:
    """One seat on one term: cap each person's share, fold each ticker-day
    into one call, then score it the Calibration Officer's way."""
    total = len(rows)
    per_user = defaultdict(int)
    for r in rows:
        per_user[r["user"]] += 1
    many_people = len(per_user) > 1
    groups: dict[tuple, dict] = {}
    for r in rows:
        weight = min(1.0, MAX_USER_SHARE * total / per_user[r["user"]]) if many_people else 1.0
        g = groups.setdefault((r["ticker"], r["day"]), {"w": 0.0, "wp": 0.0, "up": r["moved_up"]})
        g["w"] += weight
        g["wp"] += weight * r["p_up"]
    scored = []
    for g in groups.values():
        p_up = g["wp"] / g["w"]
        if abs(p_up - 0.5) < 1e-9:
            continue
        correct = (p_up > 0.5) == g["up"]
        scored.append((max(p_up, 1 - p_up), 1.0 if correct else 0.0, g["w"]))
    if not scored:
        return {"n": 0, "hit_rate": None, "brier": None}
    wsum = sum(w for _, _, w in scored)
    return {
        "n": len(scored),
        "hit_rate": round(sum(o * w for _, o, w in scored) / wsum, 3),
        "brier": round(sum((p - o) ** 2 * w for p, o, w in scored) / wsum, 4),
    }


def _weights_from_stats(stats: dict[str, dict], settings: Settings) -> dict[str, float]:
    weights = {sid: 1.0 for sid in stats}
    qualifying = {
        sid: s for sid, s in stats.items()
        if s["n"] >= settings.calibration_min_resolutions and s["brier"] is not None
    }
    if not qualifying:
        return weights
    if len(qualifying) >= settings.calibration_min_cohort_for_exclusion:
        ranked = sorted(qualifying.items(), key=lambda kv: kv[1]["brier"])
        keep = {sid for sid, _ in ranked[: math.ceil(len(ranked) * (1 - settings.calibration_exclude_worst_pct))]}
        for sid in qualifying:
            if sid not in keep:
                weights[sid] = 0.0
    mean_brier = sum(s["brier"] for s in qualifying.values()) / len(qualifying)
    for sid, s in qualifying.items():
        if weights[sid] == 0.0:
            continue
        raw = 1.0 + (mean_brier - s["brier"]) / mean_brier if mean_brier > 0 else 1.0
        weights[sid] = round(max(0.25, min(2.0, raw)), 3)
    return weights


def compute_candidate(council_conn: sqlite3.Connection, seat_ids: list[str], tier: str, settings: Settings) -> dict:
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}")
    rows = _scored_rows(council_conn, tier)
    stats, weights = {}, {}
    calls = set()
    for term in TERMS:
        stats[term] = {}
        for sid in seat_ids:
            seat_rows = [r for r in rows if r["seat"] == sid and r["term"] == term]
            stats[term][sid] = _pooled_stats(seat_rows)
            calls.update((r["ticker"], r["day"], term) for r in seat_rows)
        weights[term] = _weights_from_stats(stats[term], settings)
    return {
        "tier": tier,
        "weights": weights,
        "stats": stats,
        "n_calls": len(calls),
        "n_people": len({r["user"] for r in rows}),
    }


# ---- drafts, publishing, and what runs use ----------------------------------------


def save_draft(conn: sqlite3.Connection, candidate: dict, note: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO weight_releases (tier, status, created_at, weights_json, stats_json, n_calls, n_people, note) "
        "VALUES (?, 'draft', ?, ?, ?, ?, ?, ?)",
        (
            candidate["tier"], datetime.utcnow().isoformat(timespec="seconds"),
            json.dumps(candidate["weights"]), json.dumps(candidate["stats"]),
            candidate["n_calls"], candidate["n_people"], note,
        ),
    )
    conn.commit()
    return cur.lastrowid


def publish(conn: sqlite3.Connection, release_id: int) -> None:
    row = conn.execute("SELECT tier, status FROM weight_releases WHERE id = ?", (release_id,)).fetchone()
    if row is None:
        raise ValueError("no such release")
    if row["status"] == "published":
        return
    conn.execute(
        "UPDATE weight_releases SET status = 'retired' WHERE tier = ? AND status = 'published'", (row["tier"],)
    )
    conn.execute(
        "UPDATE weight_releases SET status = 'published', published_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(timespec="seconds"), release_id),
    )
    conn.commit()


def _row_to_release(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    release = dict(row)
    release["weights"] = json.loads(release.pop("weights_json"))
    release["stats"] = json.loads(release.pop("stats_json"))
    return release


def published_release(conn: sqlite3.Connection, tier: str) -> dict | None:
    return _row_to_release(conn.execute(
        "SELECT * FROM weight_releases WHERE tier = ? AND status = 'published'", (tier,)
    ).fetchone())


def get_release(conn: sqlite3.Connection, release_id: int) -> dict | None:
    return _row_to_release(conn.execute("SELECT * FROM weight_releases WHERE id = ?", (release_id,)).fetchone())


def list_releases(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        "SELECT id, tier, status, created_at, published_at, n_calls, n_people, note "
        "FROM weight_releases ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def live_weights(settings_db_path: str, tier: str) -> dict[str, dict[str, float]]:
    """{term: {seat_id: weight}} a run of this tier uses: the published
    release, or nothing (every seat at 1.0) before the first one. Sample
    runs always get nothing."""
    if tier not in TIERS or not Path(settings_db_path).exists():
        return {}
    conn = connect(settings_db_path)
    try:
        release = published_release(conn, tier)
    finally:
        conn.close()
    return release["weights"] if release else {}
