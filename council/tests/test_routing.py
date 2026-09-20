"""Addendum A3 routing resolution: falls back to Anthropic whenever the
intended provider's key is missing, and never leaves a seat without a
usable model."""
from __future__ import annotations

from council.config import Settings
from council.engine.routing import load_routing_config, resolve_route


def test_seat_routed_to_anthropic_stays_anthropic():
    settings = Settings()
    route = resolve_route("technician", settings, default_model="claude-sonnet-5")
    assert route.provider == "anthropic"
    assert route.routed_as_intended is True


def test_seat_routed_to_openai_falls_back_without_key():
    settings = Settings(openai_api_key="")
    route = resolve_route("fundamentalist", settings, default_model="claude-sonnet-5")
    assert route.provider == "anthropic"
    assert route.model == "claude-sonnet-5"
    assert route.routed_as_intended is False


def test_seat_routed_to_openai_with_key_configured():
    settings = Settings(openai_api_key="sk-test-not-real")
    route = resolve_route("fundamentalist", settings, default_model="claude-sonnet-5")
    assert route.provider == "openai"
    assert route.model == "gpt-5"
    assert route.routed_as_intended is True


def test_seat_routed_to_google_falls_back_without_key():
    settings = Settings(google_api_key="")
    route = resolve_route("macro_sage", settings, default_model="claude-sonnet-5")
    assert route.provider == "anthropic"
    assert route.routed_as_intended is False


def test_unknown_seat_uses_caller_default():
    settings = Settings()
    route = resolve_route("nonexistent_seat", settings, default_model="claude-sonnet-5")
    assert route.provider == "anthropic"
    assert route.model == "claude-sonnet-5"


def test_every_tier1_and_tier2_4_seat_has_a_routing_entry():
    config = load_routing_config()
    seat_ids = set(config["seats"].keys())
    expected = {
        "technician", "fundamentalist", "catalyst_seer", "insider_reader",
        "senate_watcher", "flow_cartographer", "oracle_options", "macro_sage",
        "cross_market", "estimate_scribe", "transcript_linguist", "structure_archivist",
        "bull_advocate", "bear_advocate", "prosecutor", "grand_master",
    }
    assert expected <= seat_ids


def test_every_non_anthropic_seat_declares_a_fallback():
    config = load_routing_config()
    for seat_id, cfg in config["seats"].items():
        if cfg["provider"] != "anthropic":
            assert "fallback_provider" in cfg, f"{seat_id} has no fallback_provider"
            assert "fallback_model" in cfg, f"{seat_id} has no fallback_model"
