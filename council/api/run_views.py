"""One saved run, read back from the Crypt -- shared by the run API, the
public share page and "What changed since last time"."""
from __future__ import annotations

import json
import sqlite3

from council.crypt.db import effective_run_mode, owner_filter
from council.engine.horizons import TERMS


def load_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    """Everything saved for one run: each term's row, resolution and seat
    votes, plus the Grand Master's synthesis. A pre-terms prediction id
    works too (its run is just that one row). None when there's no such run."""
    preds = conn.execute(
        "SELECT * FROM predictions WHERE run_id = ? OR (run_id IS NULL AND id = ?) ORDER BY rowid ASC",
        (run_id, run_id),
    ).fetchall()
    if not preds:
        return None
    terms = {}
    for pred in preds:
        votes = conn.execute("SELECT * FROM seat_votes WHERE prediction_id = ?", (pred["id"],)).fetchall()
        resolution = conn.execute(
            "SELECT * FROM resolutions WHERE prediction_id = ?", (pred["id"],)
        ).fetchone()
        prediction = dict(pred)
        prediction.pop("synthesis_json", None)
        terms[pred["horizon"]] = {
            "prediction": prediction,
            "seat_votes": [{**dict(v), "verdict": json.loads(v["verdict_json"])} for v in votes],
            "resolution": dict(resolution) if resolution else None,
        }
    first = preds[0]
    return {
        "run_id": run_id,
        "ticker": first["ticker"],
        "created_at": first["created_at"],
        "user_id": first["user_id"],
        "run_mode": effective_run_mode(first["run_mode"], first["total_cost_usd"]),
        "run_shape": first["run_shape"] or "full",
        "legacy": first["run_id"] is None,
        "synthesis": json.loads(first["synthesis_json"]) if first["synthesis_json"] else None,
        "terms": terms,
    }


def vote_p_bullish(verdict: dict) -> float | None:
    """A seat's term vote as a chance the price rises (None = couldn't read)."""
    vote = verdict.get("vote")
    if vote == "NO_READ":
        return None
    if vote == "NO_CONVICTION":
        return 0.5
    p = verdict.get("probability")
    if p is None:
        return None
    return p if vote == "BULLISH" else 1 - p


def direction(p: float | None) -> str:
    """Same rounding as the UI's dirOf(): up / down / even / noread."""
    if p is None:
        return "noread"
    d = round((p - 0.5) * 1000)
    return "up" if d > 0 else "down" if d < 0 else "even"


def term_leans(run: dict) -> dict[str, float | None]:
    """The Council's chance of a rise for each term."""
    synth_terms = (run.get("synthesis") or {}).get("terms") or {}
    out: dict[str, float | None] = {}
    for t in TERMS:
        st = synth_terms.get(t)
        if st is not None:
            out[t] = st.get("p_bullish") if st.get("seats_counted") != 0 else None
            continue
        pred = (run["terms"].get(t) or {}).get("prediction")
        if pred is None:
            continue
        if pred.get("p_raw") is not None:
            out[t] = pred["p_raw"]
        else:
            conf = pred.get("council_confidence")
            vote = pred.get("council_vote")
            out[t] = conf if vote == "BULLISH" else (1 - conf) if vote == "BEARISH" and conf is not None else 0.5
    return out


def seat_leans(run: dict) -> dict[str, dict[str, float | None]]:
    """seat_id -> term -> chance of a rise, from the saved seat votes."""
    out: dict[str, dict[str, float | None]] = {}
    for t, term in run["terms"].items():
        for vote in term["seat_votes"]:
            out.setdefault(vote["seat_id"], {})[t] = vote_p_bullish(vote["verdict"])
    return out


def previous_run_id(conn: sqlite3.Connection, run: dict, account: int) -> str | None:
    """The same person's latest earlier run of the same ticker."""
    mine, params = owner_filter(account, "user_id")
    row = conn.execute(
        f"SELECT COALESCE(run_id, id) AS rid FROM predictions WHERE {mine} AND ticker = ? "
        "AND created_at < ? ORDER BY created_at DESC, rowid ASC LIMIT 1",
        [*params, run["ticker"], run["created_at"]],
    ).fetchone()
    return row["rid"] if row else None


def compare_runs(previous: dict, current: dict) -> dict:
    """What moved between two runs of one ticker: each term's lean, and
    every seat whose direction flipped on a term."""
    before, after = term_leans(previous), term_leans(current)
    terms = {}
    for t in TERMS:
        if t not in before and t not in after:
            continue
        b, a = before.get(t), after.get(t)
        terms[t] = {
            "before": b,
            "after": a,
            "change": None if a is None or b is None else round(a - b, 4),
            "flipped": direction(a) != direction(b),
        }
    seats_before, seats_after = seat_leans(previous), seat_leans(current)
    flips = []
    for seat_id, now in seats_after.items():
        for t, a in now.items():
            b = seats_before.get(seat_id, {}).get(t)
            if seat_id not in seats_before or t not in seats_before[seat_id]:
                continue
            if direction(a) != direction(b):
                flips.append({"seat_id": seat_id, "term": t, "before": b, "after": a})
    return {
        "previous": {"run_id": previous["run_id"], "created_at": previous["created_at"]},
        "terms": terms,
        "flips": flips,
    }
