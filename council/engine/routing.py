"""Addendum A3: resolves which (provider, model) a seat actually uses this
run. Falls back to another provider (Anthropic first) whenever the
intended provider's API key isn't configured, or when the model is a paid Gemini
one and the Google key isn't marked as having billing. The fallback is silent to the seat -- it always gets a
valid model string back -- but the resolution is logged (see
orchestrator.py's write_seat_vote calls) so it's visible in the Crypt which
provider a vote actually came from.

Task #74: a per-seat override set from the Settings pane
(model_settings.py) now outranks config/models.yaml's static assignment --
that file is still the *recommended* default (and what the Settings pane
itself defaults to), but an explicit user choice wins. Live calling for
OpenAI and Google is wired up in llm_client.py."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from council.config import Settings
from council.engine import model_settings
from council.engine.model_catalog import FREE_MODELS, get_model

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "models.yaml"


@dataclass
class ResolvedRoute:
    seat_id: str
    provider: str
    model: str
    routed_as_intended: bool  # False if we fell back to another provider


@lru_cache(maxsize=1)
def load_routing_config(path: Path = _CONFIG_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


_PROVIDER_KEY_ATTR = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "google": "google_api_key",
    "groq": "groq_api_key",
}


def _provider_key_available(provider: str, settings: Settings) -> bool:
    attr = _PROVIDER_KEY_ATTR.get(provider)
    if attr and getattr(settings, attr, ""):
        return True
    # With no AI key at all nothing is actually called (sample answers), so
    # Anthropic stays the nominal route everything resolves to, as before.
    return provider == "anthropic" and not settings.has_any_ai_key


def _google_billing(settings: Settings) -> bool:
    conn = model_settings.connect(settings.settings_db_path)
    try:
        return model_settings.google_billing_enabled(conn, settings.council_account)
    finally:
        conn.close()


def model_block(model_id: str, settings: Settings) -> str | None:
    """Why this person can't use a model right now -- "key:<provider>" (no
    key for its service) or "billing:google" (a paid Gemini model on a key
    not marked as having billing) -- or None when they can."""
    info = get_model(model_id)
    provider = info.provider if info else "anthropic"
    if not _provider_key_available(provider, settings):
        return f"key:{provider}"
    if provider == "google" and info is not None and not info.free and not _google_billing(settings):
        return "billing:google"
    return None


def _fallback_route(seat_id: str, settings: Settings, default_model: str) -> ResolvedRoute:
    """The intended provider has no key: use the first provider that does
    -- Anthropic (the caller's default model), then OpenAI, then the free
    tiers -- so a setup with only a free Gemini or Groq key still runs
    every seat instead of routing them all to a keyless Anthropic."""
    candidates = [
        ("anthropic", default_model),
        ("openai", "gpt-5"),
        ("google", FREE_MODELS["google"]),
        ("groq", FREE_MODELS["groq"]),
    ]
    for provider, model in candidates:
        if model_block(model, settings) is None:
            return ResolvedRoute(seat_id, provider, model, routed_as_intended=False)
    return ResolvedRoute(seat_id, "anthropic", default_model, routed_as_intended=False)


def _override_model(seat_id: str, settings: Settings) -> str | None:
    conn = model_settings.connect(settings.settings_db_path)
    try:
        return model_settings.get_overrides(conn, settings.council_account).get(seat_id)
    finally:
        conn.close()


def resolve_route(
    seat_id: str,
    settings: Settings,
    default_model: str,
    routing_config: dict | None = None,
) -> ResolvedRoute:
    """`default_model` is the caller's own fallback (settings.seat_model or
    settings.synthesis_model) used when the seat has no routing entry at
    all, or when it does but even its own fallback_provider lacks a key
    (shouldn't happen for the anthropic fallback, but stay defensive)."""
    override_model = _override_model(seat_id, settings)
    if override_model is not None:
        model_info = get_model(override_model)
        provider = model_info.provider if model_info else "anthropic"
        if model_block(override_model, settings) is None:
            return ResolvedRoute(seat_id, provider, override_model, routed_as_intended=True)
        # The override's provider has no key configured (or it's a paid
        # Gemini model on a free key) -- fall back the same way an unusable
        # yaml assignment would.
        return _fallback_route(seat_id, settings, default_model)

    routing_config = routing_config or load_routing_config()
    seat_cfg = routing_config.get("seats", {}).get(seat_id)
    if not seat_cfg:
        if model_block(default_model, settings) is None:
            return ResolvedRoute(seat_id, "anthropic", default_model, routed_as_intended=True)
        return _fallback_route(seat_id, settings, default_model)

    provider = seat_cfg["provider"]
    model = seat_cfg["model"]
    if model_block(model, settings) is None:
        return ResolvedRoute(seat_id, provider, model, routed_as_intended=True)

    fallback_provider = seat_cfg.get("fallback_provider", "anthropic")
    fallback_model = seat_cfg.get("fallback_model", default_model)
    if model_block(fallback_model, settings) is None:
        return ResolvedRoute(seat_id, fallback_provider, fallback_model, routed_as_intended=False)

    # Neither has a key: whatever provider does, so a seat is never left
    # without a usable model.
    return _fallback_route(seat_id, settings, default_model)


def planned_run_mode(settings: Settings) -> str:
    """What a run started now would be labeled, before it starts: "sample"
    (no AI key), "free" (every role routes to a free tier) or "paid". The
    run's final label can still change if a free tier overflows, see
    orchestrator._run_mode."""
    from council.engine.model_catalog import ALL_ROLES

    if settings.resolved_no_llm:
        return "sample"
    for role in ALL_ROLES:
        default = settings.synthesis_model if role in ("prosecutor", "grand_master") else settings.seat_model
        model = get_model(resolve_route(role, settings, default_model=default).model)
        if not (model and model.free):
            return "paid"
    return "free"
