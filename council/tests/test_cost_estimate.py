"""Phase 6: the dry-run cost estimator must produce a plausible, non-zero
breakdown without making any network call, and must match the actual
call-count shape of a real deliberation (same eligible-seat set, same
n_samples, same debate_rounds)."""
from __future__ import annotations

from council.config import Settings
from council.engine.cost_estimate import estimate_deliberation_cost
from council.engine.horizons import is_competent
from council.engine.orchestrator import TIER_I_SEATS


def test_line_items_cover_every_eligible_seat_plus_tiers_ii_iv():
    settings = Settings()
    estimate = estimate_deliberation_cost("NVDA", "1w", settings)

    eligible_ids = {s.id for s in TIER_I_SEATS if is_competent(s.id, "1w")}
    line_item_seat_ids = {li.label.split(" (")[0] for li in estimate.line_items}

    assert eligible_ids <= line_item_seat_ids
    assert any("Bull Advocate" in li.label for li in estimate.line_items)
    assert any("Bear Advocate" in li.label for li in estimate.line_items)
    assert any("Prosecutor" in li.label for li in estimate.line_items)
    assert any("Grand Master" in li.label for li in estimate.line_items)


def test_call_counts_match_settings():
    settings = Settings(n_samples_per_seat=5, debate_rounds=1)
    estimate = estimate_deliberation_cost("NVDA", "1w", settings)

    eligible = [s for s in TIER_I_SEATS if is_competent(s.id, "1w")]
    tier1_items = [li for li in estimate.line_items if "samples" in li.label]
    assert len(tier1_items) == len(eligible)
    assert all(li.call_count == 5 for li in tier1_items)

    prosecutor_item = next(li for li in estimate.line_items if "Prosecutor" in li.label)
    assert prosecutor_item.call_count == 1


def test_total_cost_is_positive_for_anthropic_routed_seats():
    settings = Settings()
    estimate = estimate_deliberation_cost("NVDA", "1w", settings)
    assert estimate.total_cost_usd > 0
    assert estimate.total_calls > 0


def test_different_horizons_yield_different_eligible_seat_counts():
    settings = Settings()
    estimate_1d = estimate_deliberation_cost("NVDA", "1d", settings)
    estimate_1y = estimate_deliberation_cost("NVDA", "1y", settings)
    # horizon competence (spec's 12x4 matrix) means not every seat is
    # eligible at every horizon -- the two estimates should differ.
    assert estimate_1d.total_calls != estimate_1y.total_calls or (
        estimate_1d.line_items != estimate_1y.line_items
    )
