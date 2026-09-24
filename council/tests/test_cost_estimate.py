"""Phase 6: the dry-run cost estimator must produce a plausible, non-zero
breakdown without making any network call, and must match the actual
call-count shape of a real deliberation (every seat -- each answers all
three terms in one call -- same n_samples, same debate_rounds).

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
from council.engine.orchestrator import TIER_I_SEATS

_LIVE_SETTINGS = dict(anthropic_api_key="test-anthropic-key", no_llm=False)


def test_line_items_cover_every_seat_plus_tiers_ii_iv():
    settings = Settings(**_LIVE_SETTINGS)
    estimate = estimate_deliberation_cost("NVDA", settings)

    line_item_seat_ids = {li.label.split(" (")[0] for li in estimate.line_items}

    assert {s.id for s in TIER_I_SEATS} <= line_item_seat_ids
    assert any("Bull Advocate" in li.label for li in estimate.line_items)
    assert any("Bear Advocate" in li.label for li in estimate.line_items)
    assert any("Prosecutor" in li.label for li in estimate.line_items)
    assert any("Grand Master" in li.label for li in estimate.line_items)


def test_call_counts_match_settings():
    settings = Settings(n_samples_per_seat=5, debate_rounds=1, **_LIVE_SETTINGS)
    estimate = estimate_deliberation_cost("NVDA", settings)

    tier1_items = [li for li in estimate.line_items if "samples" in li.label]
    assert len(tier1_items) == len(TIER_I_SEATS) == 12
    assert all(li.call_count == 5 for li in tier1_items)

    prosecutor_item = next(li for li in estimate.line_items if "Prosecutor" in li.label)
    assert prosecutor_item.call_count == 1


def test_total_cost_is_positive_for_anthropic_routed_seats():
    settings = Settings(**_LIVE_SETTINGS)
    estimate = estimate_deliberation_cost("NVDA", settings)
    assert estimate.is_fixture is False
    assert estimate.total_cost_usd > 0
    assert estimate.total_calls > 0


def test_full_run_is_43_calls_and_lite_is_16():
    from council.config import as_lite

    settings = Settings(**_LIVE_SETTINGS)
    assert estimate_deliberation_cost("NVDA", settings).total_calls == 43
    assert estimate_deliberation_cost("NVDA", as_lite(settings)).total_calls == 16


def test_fixture_mode_estimate_is_zero_even_with_a_key_present():
    # The exact scenario that motivated this: a real key configured, but
    # NO_LLM explicitly forced true -- the estimate must say $0, not price
    # every seat as if it were about to make a live call.
    settings = Settings(anthropic_api_key="test-anthropic-key", no_llm=True)
    estimate = estimate_deliberation_cost("NVDA", settings)
    assert estimate.is_fixture is True
    assert estimate.total_cost_usd == 0.0
    assert all(li.est_cost_usd == 0.0 for li in estimate.line_items)
    # Call counts/token estimates stay informative -- only the $ is zeroed.
    assert estimate.total_calls > 0


def test_fixture_mode_auto_detected_with_no_key_is_also_zero():
    settings = Settings(anthropic_api_key="", no_llm=None)
    estimate = estimate_deliberation_cost("NVDA", settings)
    assert estimate.is_fixture is True
    assert estimate.total_cost_usd == 0.0
