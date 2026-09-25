"""Rebuilds council/ui/js/sample-run.js: the finished sample NVDA run that
the welcome slides show a new account before they've added a key.

It's a real run in sample mode (no AI key, the fixture market data), cut
down to what the slides draw. Re-run after changing the sample answers:

    .venv/bin/python scripts/make-sample-run.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import council.api.main as main_module
from council.config import Settings

OUT = Path(__file__).resolve().parent.parent / "council" / "ui" / "js" / "sample-run.js"
SHOWN_SEATS = ("technician", "analyst_ratings", "insider_reader")


def sample_run() -> dict:
    tmp = tempfile.mkdtemp()
    settings = Settings(
        no_llm=True, use_data_fixtures=True, auth_enabled=False,
        council_db_path=f"{tmp}/council.db", cache_db_path=f"{tmp}/cache.db", settings_db_path=f"{tmp}/settings.db",
    )
    main_module.get_settings = lambda: settings
    client = TestClient(main_module.app)
    run_id, event = None, None
    with client.stream("GET", "/api/deliberate/stream", params={"ticker": "NVDA"}) as resp:
        for line in resp.iter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: ") and event == "done":
                run_id = json.loads(line.removeprefix("data: "))["run_id"]
    return client.get(f"/api/runs/{run_id}").json()


def trim(run: dict) -> dict:
    synth = run["synthesis"]
    seats = {s["seat_id"]: s for s in synth["replay"]["seats"]}
    return {
        "ticker": run["ticker"],
        "headline": synth["plain_headline"],
        "terms": {
            key: {
                "window": t["window"],
                "p_bullish": t["p_bullish"],
                "summary": synth[f"plain_{key}"],
                "ticks": [{"seat_id": x["seat_id"], "p_bullish": x["p_bullish"], "weight": x["weight"]} for x in t["ticks"]],
            }
            for key, t in synth["terms"].items()
        },
        "seats": [
            {"seat_id": sid, "p_bullish": seats[sid]["terms"]["medium"]["p_bullish"],
             "rationale": seats[sid]["terms"]["medium"]["rationale"]}
            for sid in SHOWN_SEATS
        ],
    }


if __name__ == "__main__":
    data = trim(sample_run())
    OUT.write_text(
        "// Made by scripts/make-sample-run.py -- a finished sample NVDA run for the\n"
        "// welcome slides (welcome.js). Don't edit by hand.\n"
        f"const SAMPLE_RUN = {json.dumps(data, indent=1)};\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT}")
