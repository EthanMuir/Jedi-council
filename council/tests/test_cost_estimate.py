"""Phase 6: the dry-run cost estimator must produce a plausible, non-zero
breakdown without making any network call, and must match the actual
call-count shape of a real deliberation (same eligible-seat set, same
n_samples, same debate_rounds).

Also (the bug this file's last two tests guard against): a real
deliberation billed real money despite the user believing NO_LLM=true made
it free. The gate that actually decides whether a network call happens
(LLMClient.get_structured checking settings.resolved_no_llm) was never
wrong -- but this dry-run estimator priced every run as if it were live
regardless of that flag, which meant it could never have caught a
misconfiguration before the fact. It's NO_LLM-aware now."""
from __future__ import annotations

from council.config import Settings
from council.engine.cost_estimate import estimate_deliberation_cost
from council.engine.horizons import is_competent
from council.engine.orchestrator import TIER_I_SEATS

_LIVE_SETTINGS = dict(anthropic_api_key="test-anthropic-key", no_llm=False)


def test_line_items_cover_every_eligible_seat_plus_tiers_ii_iv():
    settings = Settings(**_LIVE_SETTINGS)
    estimate = estimate_deliberation_cost("NVDA", "1w", settings)

    eligible_ids = {s.id for s in TIER_I_SEATS if is_competent(s.id, "1w")}
    line_item_seat_ids = {li.label.split(" (")[0] for li in estimate.line_items}

    assert eligible_ids <= line_item_seat_ids
    assert any("Bull Advocate" in li.label for li in estimate.line_items)
    assert any("Bear Advocate" in li.label for li in estimate.line_items)
    assert any("Prosecutor" in li.label for li in estimate.line_items)
    assert any("Grand Master" in li.label for li in estimate.line_items)


def test_call_counts_match_settings():
    settings = Settings(n_samples_per_seat=5, debate_rounds=1, **_LIVE_SETTINGS)
    estimate = estimate_deliberation_cost("NVDA", "1w", settings)

    eligible = [s for s in TIER_I_SEATS if is_competent(s.id, "1w")]
    tier1_items = [li for li in estimate.line_items if "samples" in li.label]
    assert len(tier1_items) == len(eligible)
    assert all(li.call_count == 5 for li in tier1_items)

    prosecutor_item = next(li for li in estimate.line_items if "Prosecutor" in li.label)
    assert prosecutor_item.call_count == 1


def test_total_cost_is_positive_for_anthropic_routed_seats():
    settings = Settings(**_LIVE_SETTINGS)
    estimate = estimate_deliberation_cost("NVDA", "1w", settings)
    assert estimate.is_fixture is False
    assert estimate.total_cost_usd > 0
    assert estimate.total_calls > 0


def test_different_horizons_yield_different_eligible_seat_counts():
    settings = Settings(**_LIVE_SETTINGS)
    estimate_1d = estimate_deliberation_cost("NVDA", "1d", settings)
    estimate_1y = estimate_deliberation_cost("NVDA", "1y", settings)
    # horizon competence (spec's 12x4 matrix) means not every seat is
    # eligible at every horizon -- the two estimates should differ.
    assert estimate_1d.total_calls != estimate_1y.total_calls or (
        estimate_1d.line_items != estimate_1y.line_items
    )


def test_fixture_mode_estimate_is_zero_even_with_a_key_present():
    # The exact scenario that motivated this: a real key configured, but
    # NO_LLM explicitly forced true -- the estimate must say $0, not price
    # every seat as if it were about to make a live call.
    settings = Settings(anthropic_api_key="test-anthropic-key", no_llm=True)
    estimate = estimate_deliberation_cost("NVDA", "1w", settings)
    assert estimate.is_fixture is True
    assert estimate.total_cost_usd == 0.0
    assert all(li.est_cost_usd == 0.0 for li in estimate.line_items)
    # Call counts/token estimates stay informative -- only the $ is zeroed.
    assert estimate.total_calls > 0


def test_fixture_mode_auto_detected_with_no_key_is_also_zero():
    settings = Settings(anthropic_api_key="", no_llm=None)
    estimate = estimate_deliberation_cost("NVDA", "1w", settings)
    assert estimate.is_fixture is True
    assert estimate.total_cost_usd == 0.0
