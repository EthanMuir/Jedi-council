"""Task #74 -- the single source of truth for "which models exist, what do
they cost, and which one does each seat/role use by default". Everything
that needs pricing (LLMClient's cost logging, cost_estimate.py's dry run,
the Settings pane's live estimate) reads from CATALOG instead of keeping
its own copy, so a price update is a one-line change in one place.

Prices are a point-in-time snapshot (checked September 2026), not a live
feed -- this landscape moves fast (new model families ship every few
months, old ones get renamed or discontinued) and nothing in this file can
be verified against a live API from this session's sandbox (network
egress is blocked the same as every other external host used this
session). Treat MODEL_ID strings the same way: they're the best real,
currently-documented API identifiers as of this snapshot, not a guarantee
today's ones still resolve by the time you read this. Update CATALOG
directly when a price or model id changes -- nothing else needs to."""
from __future__ import annotations

from dataclasses import dataclass

# Representative token counts for a single seat-style call (matches
# cost_estimate.py's own _TIER_I_TOKENS), used only to rank models
# most-to-least expensive in a way that reflects real usage shape --
# input-heavy, output-light -- rather than sorting on input or output
# price alone, which can disagree about which model is "more expensive".
_TYPICAL_INPUT_TOKENS = 900
_TYPICAL_OUTPUT_TOKENS = 350


@dataclass(frozen=True)
class ModelInfo:
    id: str  # the literal string passed as `model=` to the provider's API
    provider: str  # "anthropic" | "openai" | "google"
    display_name: str
    input_price_per_mtok: float
    output_price_per_mtok: float

    @property
    def typical_call_cost_usd(self) -> float:
        return (
            self.input_price_per_mtok * _TYPICAL_INPUT_TOKENS
            + self.output_price_per_mtok * _TYPICAL_OUTPUT_TOKENS
        ) / 1_000_000


CATALOG: list[ModelInfo] = [
    # --- Anthropic ---
    ModelInfo("claude-opus-5", "anthropic", "Claude Opus 5", 15.00, 75.00),
    ModelInfo("claude-sonnet-5", "anthropic", "Claude Sonnet 5", 3.00, 15.00),
    ModelInfo("claude-haiku-4-5-20251001", "anthropic", "Claude Haiku 4.5", 1.00, 5.00),
    # --- OpenAI ---
    ModelInfo("gpt-5", "openai", "GPT-5", 1.25, 10.00),
    ModelInfo("gpt-5-mini", "openai", "GPT-5 Mini", 0.25, 2.00),
    ModelInfo("gpt-5-nano", "openai", "GPT-5 Nano", 0.05, 0.40),
    # --- Google ---
    ModelInfo("gemini-3-pro", "google", "Gemini 3 Pro", 2.00, 12.00),
    ModelInfo("gemini-3-flash", "google", "Gemini 3 Flash", 1.50, 7.50),
    ModelInfo("gemini-2.5-flash-lite", "google", "Gemini 2.5 Flash-Lite", 0.10, 0.40),
]

_BY_ID: dict[str, ModelInfo] = {m.id: m for m in CATALOG}


def get_model(model_id: str) -> ModelInfo | None:
    return _BY_ID.get(model_id)


def models_sorted_by_cost(descending: bool = True) -> list[ModelInfo]:
    return sorted(CATALOG, key=lambda m: m.typical_call_cost_usd, reverse=descending)


# Every seat/role that makes an LLM call, mapped to the model this build
# recommends for it. Carried over from config/models.yaml's own existing
# per-seat assignments (Addendum A3's rationale: assign whole seats to
# whole models so different pretraining/RLHF gives genuinely independent
# errors, not cost-driven picks) -- this is "what we'd pick", the Settings
# pane's per-seat override defaults to this, not to "cheapest available".
RECOMMENDED: dict[str, str] = {
    "technician": "claude-sonnet-5",
    "fundamentalist": "gpt-5",
    "catalyst_seer": "claude-sonnet-5",
    "insider_reader": "claude-sonnet-5",
    "senate_watcher": "gemini-3-pro",
    "flow_cartographer": "claude-sonnet-5",
    "oracle_options": "claude-sonnet-5",
    "macro_sage": "gemini-3-pro",
    "cross_market": "claude-sonnet-5",
    "estimate_scribe": "gpt-5",
    "transcript_linguist": "gpt-5",
    "structure_archivist": "claude-sonnet-5",
    "bull_advocate": "claude-sonnet-5",
    "bear_advocate": "gpt-5",
    "prosecutor": "claude-opus-5",
    "grand_master": "claude-opus-5",
}

# Every role RECOMMENDED covers, in the fixed display order the Settings
# pane and cost estimator both use (Tier I seats in their usual order,
# then Tiers II-IV) -- not derived from RECOMMENDED.keys() so the order
# never depends on dict insertion order surviving a future edit.
ALL_ROLES: list[str] = [
    "technician",
    "fundamentalist",
    "catalyst_seer",
    "insider_reader",
    "senate_watcher",
    "flow_cartographer",
    "oracle_options",
    "macro_sage",
    "cross_market",
    "estimate_scribe",
    "transcript_linguist",
    "structure_archivist",
    "bull_advocate",
    "bear_advocate",
    "prosecutor",
    "grand_master",
]
