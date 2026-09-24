"""Task #74 -- model_catalog.py is the single source of truth for pricing
and per-seat recommendations that LLMClient, cost_estimate.py, and the
Settings API all read from."""
from __future__ import annotations

from council.engine.model_catalog import (
    ALL_ROLES,
    CATALOG,
    RECOMMENDED,
    get_model,
    models_sorted_by_cost,
)


def test_every_recommended_model_exists_in_the_catalog():
    for role, model_id in RECOMMENDED.items():
        assert get_model(model_id) is not None, f"{role} recommends unknown model {model_id!r}"


def test_every_role_has_a_recommendation():
    assert set(ALL_ROLES) == set(RECOMMENDED.keys())


def test_get_model_returns_none_for_unknown_id():
    assert get_model("not-a-real-model") is None


def test_catalog_covers_every_provider():
    assert {m.provider for m in CATALOG} == {"anthropic", "openai", "google", "groq"}


def test_free_entries_cost_nothing_and_each_free_provider_has_one():
    from council.engine.model_catalog import FREE_MODELS

    free = [m for m in CATALOG if m.free]
    assert all(m.input_price_per_mtok == m.output_price_per_mtok == 0 for m in free)
    assert {m.provider: m.id for m in free} == FREE_MODELS


def test_no_duplicate_model_ids():
    ids = [m.id for m in CATALOG]
    assert len(ids) == len(set(ids))


def test_sorted_by_cost_is_strictly_descending_by_default():
    ordered = models_sorted_by_cost()
    costs = [m.typical_call_cost_usd for m in ordered]
    assert costs == sorted(costs, reverse=True)
    assert ordered[0].id == "claude-opus-5"  # the most expensive model in the catalog


def test_sorted_by_cost_ascending():
    ordered = models_sorted_by_cost(descending=False)
    costs = [m.typical_call_cost_usd for m in ordered]
    assert costs == sorted(costs)
    assert ordered[0].typical_call_cost_usd <= ordered[-1].typical_call_cost_usd


def test_typical_call_cost_reflects_input_and_output_price():
    cheap = get_model("gpt-5-nano")
    expensive = get_model("claude-opus-5")
    assert cheap.typical_call_cost_usd < expensive.typical_call_cost_usd
