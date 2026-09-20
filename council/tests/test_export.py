"""Phase 6: CSV/JSON export must round-trip every prediction+resolution
column, respect ticker/horizon filters, and never touch the append-only
tables (it's a read-only SELECT)."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta

from council.crypt.db import connect
from council.crypt.export import COLUMNS, fetch_predictions_for_export, write_csv, write_json
from council.crypt.ledger import write_prediction


def _seed_prediction(conn, ticker="NVDA", horizon="1w"):
    created_at = datetime(2026, 9, 1, 16, 0, 0)
    return write_prediction(
        conn,
        ticker=ticker,
        horizon=horizon,
        resolve_at=created_at + timedelta(days=7),
        price_at_prediction=100.0,
        data_snapshot_hash="deadbeef",
        model_versions={"technician": "claude-sonnet-5"},
        blind_vote="BULLISH",
        blind_probability=0.6,
        blind_consensus_pct=80.0,
        council_vote="BULLISH",
        council_confidence=0.6,
        consensus_pct=80.0,
        total_cost_usd=0.1234,
        total_input_tokens=1000,
        total_output_tokens=300,
        created_at=created_at,
    )


def test_fetch_returns_seeded_prediction(tmp_path):
    conn = connect(str(tmp_path / "council.db"))
    _seed_prediction(conn)
    rows = fetch_predictions_for_export(conn)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "NVDA"
    assert rows[0]["council_vote"] == "BULLISH"
    assert rows[0]["total_cost_usd"] == 0.1234
    assert rows[0]["total_input_tokens"] == 1000
    assert set(rows[0].keys()) == set(COLUMNS)


def test_ticker_and_horizon_filters(tmp_path):
    conn = connect(str(tmp_path / "council.db"))
    _seed_prediction(conn, ticker="NVDA", horizon="1w")
    _seed_prediction(conn, ticker="AAPL", horizon="1m")

    assert len(fetch_predictions_for_export(conn, ticker="NVDA")) == 1
    assert len(fetch_predictions_for_export(conn, ticker="AAPL")) == 1
    assert len(fetch_predictions_for_export(conn, horizon="1w")) == 1
    assert len(fetch_predictions_for_export(conn)) == 2


def test_write_csv_round_trips(tmp_path):
    conn = connect(str(tmp_path / "council.db"))
    _seed_prediction(conn)
    rows = fetch_predictions_for_export(conn)

    out = tmp_path / "export.csv"
    write_csv(rows, str(out))

    with open(out) as f:
        reader = csv.DictReader(f)
        read_rows = list(reader)
    assert len(read_rows) == 1
    assert read_rows[0]["ticker"] == "NVDA"
    assert read_rows[0]["council_vote"] == "BULLISH"


def test_write_json_round_trips(tmp_path):
    conn = connect(str(tmp_path / "council.db"))
    _seed_prediction(conn)
    rows = fetch_predictions_for_export(conn)

    out = tmp_path / "export.json"
    write_json(rows, str(out))

    with open(out) as f:
        data = json.load(f)
    assert len(data) == 1
    assert data[0]["ticker"] == "NVDA"
    assert data[0]["total_cost_usd"] == 0.1234
