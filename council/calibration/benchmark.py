"""Addendum A10's scoreboard: council vs buy-and-hold vs always-bullish vs
best/median single seat. "If the council can't beat 'always bullish,' that
must be visible" (spec section 5) -- this is the module that makes it
visible, not just computed and buried."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from council.crypt.db import effective_run_mode


@dataclass
class ArmScore:
    n: int
    hit_rate: float | None
    brier: float | None
    on_right_side_of_50pct: float | None


def _score_arm(rows: list[sqlite3.Row], vote_key: str, prob_key: str | None) -> ArmScore:
    hits: list[float] = []
    briers: list[float] = []
    for r in rows:
        vote = r[vote_key]
        if vote not in ("BULLISH", "BEARISH"):
            continue
        correct = (vote == "BULLISH" and r["realised_move_pct"] > 0) or (
            vote == "BEARISH" and r["realised_move_pct"] < 0
        )
        outcome = 1.0 if correct else 0.0
        hits.append(outcome)
        if prob_key is not None and r[prob_key] is not None:
            briers.append((r[prob_key] - outcome) ** 2)
    n = len(hits)
    return ArmScore(
        n=n,
        hit_rate=round(sum(hits) / n, 3) if n else None,
        brier=round(sum(briers) / len(briers), 4) if briers else None,
        on_right_side_of_50pct=round(sum(hits) / n, 3) if n else None,
    )


def compute_benchmark(conn: sqlite3.Connection, run_mode: str | None = None) -> dict:
    rows = conn.execute(
        """
        SELECT p.blind_vote, p.blind_probability, p.council_vote, p.council_confidence,
               p.p_extremized, r.realised_move_pct, p.run_mode, p.total_cost_usd
        FROM predictions p JOIN resolutions r ON r.prediction_id = p.id
        """
    ).fetchall()
    if run_mode:
        rows = [r for r in rows if effective_run_mode(r["run_mode"], r["total_cost_usd"]) == run_mode]
    n = len(rows)
    if n == 0:
        return {"n_resolutions": 0}

    always_bullish_hits = [1.0 if r["realised_move_pct"] > 0 else 0.0 for r in rows]
    buy_and_hold_avg_return_pct = round(sum(r["realised_move_pct"] for r in rows) / n, 3)

    def arm(vote_key, prob_key):
        score = _score_arm(rows, vote_key, prob_key)
        return {
            "n": score.n,
            "hit_rate": score.hit_rate,
            "brier": score.brier,
            "on_right_side_of_50pct": score.on_right_side_of_50pct,
        }

    return {
        "n_resolutions": n,
        "council_blind": arm("blind_vote", "blind_probability"),
        "council_final": arm("council_vote", "council_confidence"),
        "council_extremized": arm("council_vote", "p_extremized"),
        "always_bullish": {
            "n": n,
            "hit_rate": round(sum(always_bullish_hits) / n, 3),
            "brier": None,
            "on_right_side_of_50pct": round(sum(always_bullish_hits) / n, 3),
        },
        "buy_and_hold_avg_return_pct": buy_and_hold_avg_return_pct,
    }
