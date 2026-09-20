"""Phase 6: CSV/JSON export of the Crypt's predictions + resolutions, for
taking the ledger outside SQLite (a spreadsheet, a notebook, whatever else).
Read-only -- export never touches the append-only tables, it only reads
them."""
from __future__ import annotations

import csv
import json
import sqlite3

COLUMNS = [
    "id",
    "created_at",
    "ticker",
    "horizon",
    "resolve_at",
    "council_vote",
    "council_confidence",
    "consensus_pct",
    "blind_vote",
    "blind_probability",
    "blind_consensus_pct",
    "entry",
    "exit",
    "invalidation",
    "stop",
    "price_at_prediction",
    "expected_move_pct",
    "base_rate_move_pct",
    "p_raw",
    "p_extremized",
    "cost_audit_passed",
    "total_cost_usd",
    "total_input_tokens",
    "total_output_tokens",
    "dissent_summary",
    "correlated_evidence_warning",
    "resolved_at",
    "price_at_resolve",
    "direction_correct",
    "realised_move_pct",
    "mfe_pct",
    "mae_pct",
    "brier",
]


def fetch_predictions_for_export(
    conn: sqlite3.Connection, *, ticker: str | None = None, horizon: str | None = None
) -> list[dict]:
    query = (
        "SELECT p.id, p.created_at, p.ticker, p.horizon, p.resolve_at, "
        "p.council_vote, p.council_confidence, p.consensus_pct, "
        "p.blind_vote, p.blind_probability, p.blind_consensus_pct, "
        "p.entry, p.exit, p.invalidation, p.stop, "
        "p.price_at_prediction, p.expected_move_pct, p.base_rate_move_pct, "
        "p.p_raw, p.p_extremized, p.cost_audit_passed, "
        "p.total_cost_usd, p.total_input_tokens, p.total_output_tokens, "
        "p.dissent_summary, p.correlated_evidence_warning, "
        "r.resolved_at, r.price_at_resolve, r.direction_correct, "
        "r.realised_move_pct, r.mfe_pct, r.mae_pct, r.brier "
        "FROM predictions p LEFT JOIN resolutions r ON r.prediction_id = p.id WHERE 1=1"
    )
    params: list = []
    if ticker:
        query += " AND p.ticker = ?"
        params.append(ticker.upper())
    if horizon:
        query += " AND p.horizon = ?"
        params.append(horizon)
    query += " ORDER BY p.created_at ASC"
    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def write_csv(rows: list[dict], path: str) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def write_json(rows: list[dict], path: str) -> None:
    with open(path, "w") as f:
        json.dump(rows, f, indent=2, default=str)
