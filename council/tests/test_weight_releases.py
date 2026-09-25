"""Weight releases (#124): pooled across people with each ticker-day counted
once and a cap on any one person's share, trained apart for paid and free
runs, and only ever used by live runs once the owner publishes them."""
from __future__ import annotations

from council.calibration import releases
from council.config import Settings


def _row(user, ticker, day, p_up, moved_up):
    return {"seat": "technician", "term": "short", "ticker": ticker, "day": day,
            "user": user, "p_up": p_up, "moved_up": moved_up}


def test_same_ticker_same_day_counts_once():
    # 50 people all calling NVDA up on the same day, and it went up: one call, not 50.
    rows = [_row(u, "NVDA", "2026-09-01", 0.6, True) for u in range(50)]
    stats = releases._pooled_stats(rows)
    assert stats["n"] == 1 and stats["hit_rate"] == 1.0


def test_one_person_cannot_dominate_a_seats_record():
    # One person makes 90 right calls on different days; four others make
    # ten wrong ones between them. Uncapped, the seat would look 90% right.
    heavy = [_row(1, "AAA", f"2026-01-{d:02d}", 0.6, True) for d in range(1, 31)] * 3
    heavy = [dict(r, day=f"{r['day']}-{i}") for i, r in enumerate(heavy)]
    others = [_row(u, "BBB", f"2026-02-{u}{i}", 0.6, False) for u in range(2, 6) for i in range(1, 3)]
    others += [_row(2, "BBB", "2026-03-01", 0.6, False), _row(3, "BBB", "2026-03-02", 0.6, False)]
    stats = releases._pooled_stats(heavy + others)
    uncapped = 90 / (90 + len(others))
    assert stats["hit_rate"] < uncapped - 0.2


def test_dead_even_folds_are_skipped():
    rows = [_row(1, "NVDA", "2026-09-01", 0.6, True), _row(2, "NVDA", "2026-09-01", 0.4, True)]
    assert releases._pooled_stats(rows)["n"] == 0


def test_weights_only_move_after_enough_calls():
    settings = Settings(calibration_min_resolutions=20)
    few = {"a": {"n": 5, "brier": 0.1}, "b": {"n": 5, "brier": 0.4}}
    assert releases._weights_from_stats(few, settings) == {"a": 1.0, "b": 1.0}
    many = {"a": {"n": 30, "brier": 0.15}, "b": {"n": 30, "brier": 0.35}}
    w = releases._weights_from_stats(many, settings)
    assert w["a"] > 1.0 > w["b"] >= 0.25


def test_nothing_is_live_until_published(tmp_path):
    db = str(tmp_path / "settings.db")
    assert releases.live_weights(db, "paid") == {}
    conn = releases.connect(db)
    candidate = {"tier": "paid", "weights": {"short": {"technician": 1.4}}, "stats": {}, "n_calls": 40, "n_people": 3}
    first = releases.save_draft(conn, candidate, "first")
    assert releases.live_weights(db, "paid") == {}  # a draft isn't used

    releases.publish(conn, first)
    assert releases.live_weights(db, "paid") == {"short": {"technician": 1.4}}
    assert releases.live_weights(db, "free") == {}  # tiers are separate
    assert releases.live_weights(db, "sample") == {}

    second = releases.save_draft(conn, {**candidate, "weights": {"short": {"technician": 0.8}}})
    releases.publish(conn, second)
    assert releases.live_weights(db, "paid") == {"short": {"technician": 0.8}}
    statuses = {r["id"]: r["status"] for r in releases.list_releases(conn)}
    assert statuses == {first: "retired", second: "published"}


def test_candidate_from_an_empty_crypt_is_all_ones(tmp_path):
    from council.crypt.db import connect

    conn = connect(str(tmp_path / "council.db"))
    candidate = releases.compute_candidate(conn, ["technician", "macro_sage"], "paid", Settings())
    assert candidate["n_calls"] == 0
    assert candidate["weights"]["long"] == {"technician": 1.0, "macro_sage": 1.0}
