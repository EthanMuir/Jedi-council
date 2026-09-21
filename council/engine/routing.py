"""Addendum A3: resolves which (provider, model) a seat actually uses this
run. Falls back to Anthropic whenever the intended provider's API key
isn't configured. The fallback is silent to the seat -- it always gets a
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
from council.engine.model_catalog import get_model

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "models.yaml"


@dataclass
class ResolvedRoute:
    seat_id: str
    provider: str
    model: str
    routed_as_intended: bool  # False if we fell back to Anthropic


@lru_cache(maxsize=1)
def load_routing_config(path: Path = _CONFIG_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


_PROVIDER_KEY_ATTR = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "google": "google_api_key",
}


def _provider_key_available(provider: str, settings: Settings) -> bool:
    attr = _PROVIDER_KEY_ATTR.get(provider)
    return bool(attr and getattr(settings, attr, ""))


def _override_model(seat_id: str, settings: Settings) -> str | None:
    conn = model_settings.connect(settings.settings_db_path)
    try:
        return model_settings.get_overrides(conn).get(seat_id)
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
        if provider == "anthropic" or _provider_key_available(provider, settings):
            return ResolvedRoute(seat_id, provider, override_model, routed_as_intended=True)
        # The override's provider has no key configured -- fall back the
        # same way an unconfigured yaml provider would, rather than
        # silently ignoring what the user explicitly chose.
        return ResolvedRoute(seat_id, "anthropic", default_model, routed_as_intended=False)

    routing_config = routing_config or load_routing_config()
    seat_cfg = routing_config.get("seats", {}).get(seat_id)
    if not seat_cfg:
        return ResolvedRoute(seat_id, "anthropic", default_model, routed_as_intended=True)

    # Anthropic's own key presence is resolved_no_llm's concern (whether to
    # call any LLM at all), not routing's -- a seat whose primary IS
    # anthropic is always "routed as intended" regardless of key state here.
    provider = seat_cfg["provider"]
    model = seat_cfg["model"]
    if provider == "anthropic" or _provider_key_available(provider, settings):
        return ResolvedRoute(seat_id, provider, model, routed_as_intended=True)

    fallback_provider = seat_cfg.get("fallback_provider", "anthropic")
    fallback_model = seat_cfg.get("fallback_model", default_model)
    if fallback_provider == "anthropic" or _provider_key_available(fallback_provider, settings):
        return ResolvedRoute(seat_id, fallback_provider, fallback_model, routed_as_intended=False)

    # Last resort: the caller's own default, so a seat is never left without
    # a usable model even if its yaml entry is misconfigured.
    return ResolvedRoute(seat_id, "anthropic", default_model, routed_as_intended=False)
