"""Price targets: a most-likely price and a likely range per term, worked
out from the run, with the chance calculated from how past ranges did."""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from statistics import NormalDist

import pytest

from council.crypt.db import connect
from council.crypt.ledger import write_resolution
from council.crypt.resolution import ResolutionOutcome
from council.data.schemas import OHLCVBar
from council.engine import price_target as pt
from council.tests.test_api import client  # noqa: F401


def test_a_toss_up_targets_todays_price():
    t = pt.price_target(100.0, 0.5, 0.10)
    assert t.target == 100.0
    assert t.low < 100 < t.high
    # Symmetric in log terms around today's price.
    assert math.isclose(math.log(t.high / 100), -math.log(t.low / 100), rel_tol=1e-3)


def test_the_target_follows_the_councils_lean_and_matches_its_odds():
    up = pt.price_target(100.0, 0.6, 0.10)
    down = pt.price_target(100.0, 0.4, 0.10)
    assert up.target > 100 > down.target
    # Under the curve the target implies, the chance of ending above today's
    # price is exactly the Council's chance of rising.
    drift = math.log(up.target / 100)
    assert math.isclose(1 - NormalDist(drift, 0.10).cdf(0.0), 0.6, abs_tol=0.002)


def test_the_range_covers_seventy_percent_of_the_curve():
    t = pt.price_target(100.0, 0.55, 0.20)
    drift = math.log(t.target / 100)
    curve = NormalDist(drift, 0.20)
    covered = curve.cdf(math.log(t.high / 100)) - curve.cdf(math.log(t.low / 100))
    assert math.isclose(covered, 0.70, abs_tol=0.005)
    assert t.chance_pct == 70 and t.scored_ranges == 0


def test_the_chance_comes_from_the_record_once_ranges_are_scored():
    assert pt.calibrated_chance(0, 0) == pytest.approx(0.70)
    # 100 scored, 50 held: pulled most of the way to 50%.
    assert pt.calibrated_chance(50, 100) == pytest.approx((50 + 14) / 120)
    t = pt.price_target(100.0, 0.55, 0.20, hits=50, scored=100)
    assert t.chance_pct == 53 and t.scored_ranges == 100


def test_no_target_without_a_price_or_volatility():
    assert pt.price_target(0, 0.6, 0.1) is None
    assert pt.price_target(100, 0.6, None) is None


def _bars(n: int, daily: float) -> list[OHLCVBar]:
    start = date(2025, 1, 1)
    price, bars = 100.0, []
    for i in range(n):
        price *= math.exp(daily if i % 2 else -daily)
        bars.append(OHLCVBar(trade_date=start + timedelta(days=i), open=price, high=price, low=price, close=price, volume=1))
    return bars


def test_sigma_scales_with_the_terms_length():
    bars = _bars(300, 0.02)
    week, year = pt.term_sigma(bars, "short"), pt.term_sigma(bars, "long")
    assert week == pytest.approx(0.02 * math.sqrt(5), rel=0.01)
    assert year == pytest.approx(0.02 * math.sqrt(252), rel=0.01)
    assert pt.term_sigma(bars[:10], "short") is None


def test_the_options_market_is_blended_in_for_the_next_week():
    bars = _bars(300, 0.02)
    plain = pt.term_sigma(bars, "short")
    blended = pt.term_sigma(bars, "short", options_implied_move_pct=10.0)
    assert blended == pytest.approx((plain + 0.10 / math.sqrt(2 / math.pi)) / 2)
    # Longer terms ignore the options market.
    assert pt.term_sigma(bars, "medium", 10.0) == pt.term_sigma(bars, "medium")


def test_landed_in_range():
    target = {"price_now": 100.0, "low": 90.0, "high": 115.0}
    assert pt.landed_in_range(target, 10.0) is True
    assert pt.landed_in_range(target, 20.0) is False
    assert pt.landed_in_range(target, -12.0) is False
    assert pt.landed_in_range(target, None) is None
    assert pt.final_price(target, 5.0) == 105.0


# ---- in a run, History and the record ---------------------------------------------


def _run(test_client) -> str:
    run_id, event = None, None
    with test_client.stream("GET", "/api/deliberate/stream", params={"ticker": "NVDA"}) as r:
        for line in r.iter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: ") and event == "done":
                run_id = json.loads(line.removeprefix("data: "))["run_id"]
    return run_id


def _resolve(settings, run_id: str, term: str, move_pct: float) -> None:
    conn = connect(settings.council_db_path)
    try:
        pid, price = conn.execute(
            "SELECT id, price_at_prediction FROM predictions WHERE run_id = ? AND horizon = ?", (run_id, term)
        ).fetchone()
        final = price * (1 + move_pct / 100)
        write_resolution(
            conn, prediction_id=pid, resolved_at=datetime.now(timezone.utc),
            outcome=ResolutionOutcome(
                price_at_resolve=final, high=final, low=final, direction_correct=move_pct > 0,
                entry_hit=None, exit_hit=None, invalidation_hit=None, mfe_pct=0, mae_pct=0,
                realised_move_pct=move_pct, brier=None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_every_run_saves_a_target_per_term(client):
    test_client, _ = client
    run = test_client.get(f"/api/runs/{_run(test_client)}").json()
    for term in ("short", "medium", "long"):
        t = run["synthesis"]["terms"][term]["price_target"]
        assert t["low"] < t["target"] < t["high"]
        assert t["price_now"] == run["terms"][term]["prediction"]["price_at_prediction"]
        assert t["chance_pct"] == 70


def test_history_scores_the_range_beside_the_direction(client):
    test_client, settings = client
    run_id = _run(test_client)
    before = test_client.get("/api/predictions").json()["runs"][0]["terms"]["short"]
    assert before["price_target"] and before["in_range"] is None
    _resolve(settings, run_id, "short", 0.5)
    after = test_client.get("/api/predictions").json()["runs"][0]["terms"]["short"]
    assert after["direction_correct"] == 1 and after["in_range"] is True
    assert after["final_price"] == pytest.approx(after["price_target"]["price_now"] * 1.005, abs=0.01)


def test_later_runs_draw_their_chance_from_past_ranges(client):
    test_client, settings = client
    for _ in range(3):
        _resolve(settings, _run(test_client), "short", 50.0)  # far outside every range
    conn = connect(settings.council_db_path)
    try:
        assert pt.range_record(conn)["short"] == (0, 3)
    finally:
        conn.close()
    t = test_client.get(f"/api/runs/{_run(test_client)}").json()["synthesis"]["terms"]["short"]["price_target"]
    assert t["scored_ranges"] == 3 and t["chance_pct"] == round(14 / 23 * 100)


def test_the_share_page_shows_the_target_and_where_it_ended(client):
    test_client, settings = client
    run_id = _run(test_client)
    url = test_client.post(f"/api/runs/{run_id}/share").json()["url"]
    path = url[url.index("/s/"):]
    assert "Price target" in test_client.get(path).text
    _resolve(settings, run_id, "short", 0.5)
    assert "inside the range" in test_client.get(path).text


def test_the_scored_email_mentions_the_range():
    from council import notify

    target = {"price_now": 100.0, "target": 101.0, "low": 95.0, "high": 108.0, "chance_pct": 70}
    text = notify.compose(
        "Sam",
        [{"ticker": "NVDA", "term": "short", "run_id": "r1", "called": "up", "correct": True, "move": 3.0, "target": target}],
        "https://example.com", "",
    ).text
    assert "RIGHT" in text
    assert "Price target about $101 (70% chance $95-$108): ended at $103, inside the range" in text
