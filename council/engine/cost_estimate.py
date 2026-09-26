"""Phase 6: estimate the $ cost of a real deliberation before running it,
without making a single network call. Token counts below are rough
constants per call type, not measured -- good enough to tell whether a run
costs a dollar or a nickel, not an invoice. Pricing comes from
model_catalog.py, the single source of truth also used by LLMClient's own
cost logging and the Settings pane -- same honesty caveat as that file's
own docstring on prices being a point-in-time snapshot. A model with no
catalog entry prices at $0.00, not a crash (shouldn't happen for anything
resolve_route can actually return, but stay defensive)."""
from __future__ import annotations

from dataclasses import dataclass, field

from council.config import Settings
from council.engine.model_catalog import caches_seat_prompts, get_model
from council.engine.routing import resolve_route

# Per-call token counts, by call type, measured from real prompts (Sept 2026:
# the system prompt, the seat's data, the answer format sent as a tool, and
# the tool-use overhead). The first guesses were about half this, which made
# a full run look like $0.59 when a real one cost well over a dollar. A
# seat's answer covers all three terms at once, so its output runs long.
_TIER_I_TOKENS = {"input": 3000, "output": 800}
_ADVOCATE_TOKENS = {"input": 2500, "output": 500}
_PROSECUTOR_TOKENS = {"input": 2500, "output": 700}
_GRAND_MASTER_TOKENS = {"input": 4500, "output": 1400}


@dataclass
class CostLineItem:
    label: str
    model: str
    call_count: int
    est_input_tokens: int
    est_output_tokens: int
    est_cost_usd: float


@dataclass
class CostEstimate:
    ticker: str
    is_fixture: bool = False
    line_items: list[CostLineItem] = field(default_factory=list)

    @property
    def total_cost_usd(self) -> float:
        return round(sum(li.est_cost_usd for li in self.line_items), 4)

    @property
    def total_calls(self) -> int:
        return sum(li.call_count for li in self.line_items)


def _cached_seat_tokens(model: str, samples: int) -> dict:
    """A seat's per-call token counts with prompt caching folded in: the
    first sample writes the cache (1.25x input), the rest read it (0.1x).
    Expressed as input tokens at the plain price, averaged per call."""
    if samples < 2 or not caches_seat_prompts(model):
        return _TIER_I_TOKENS
    factor = (1.25 + 0.1 * (samples - 1)) / samples
    return {"input": round(_TIER_I_TOKENS["input"] * factor), "output": _TIER_I_TOKENS["output"]}


def _cost_for(model: str, calls: int, tokens: dict, is_fixture: bool) -> float:
    # A run in fixture mode (settings.resolved_no_llm) makes zero network
    # calls -- pricing it as if every seat/debate/synthesis call were live
    # was actively misleading (Task #63: a real deliberation billed real
    # money despite the user believing NO_LLM=true made it free; this
    # dry-run's own estimate never having reflected that was part of why
    # it wasn't caught sooner).
    if is_fixture:
        return 0.0
    model_info = get_model(model)
    if not model_info:
        return 0.0
    return calls * (
        (tokens["input"] / 1_000_000) * model_info.input_price_per_mtok
        + (tokens["output"] / 1_000_000) * model_info.output_price_per_mtok
    )


def estimate_deliberation_cost(ticker: str, settings: Settings) -> CostEstimate:
    # Imported here, not at module level, to avoid a load-time cycle:
    # orchestrator.py doesn't need cost_estimate.py, but pulling in its
    # heavier import graph (data providers, Crypt, ...) just for the seat
    # roster is unnecessary work for every other caller of this module.
    from council.engine.orchestrator import TIER_I_SEATS

    is_fixture = settings.resolved_no_llm
    estimate = CostEstimate(ticker=ticker, is_fixture=is_fixture)
    n_samples = settings.n_samples_per_seat

    for seat in TIER_I_SEATS:
        route = resolve_route(seat.id, settings, default_model=settings.seat_model)
        estimate.line_items.append(
            CostLineItem(
                label=f"{seat.id} (x{n_samples} samples)",
                model=route.model,
                call_count=n_samples,
                est_input_tokens=_TIER_I_TOKENS["input"] * n_samples,
                est_output_tokens=_TIER_I_TOKENS["output"] * n_samples,
                est_cost_usd=round(
                    _cost_for(route.model, n_samples, _cached_seat_tokens(route.model, n_samples), is_fixture), 6
                ),
            )
        )

    rounds = settings.debate_rounds
    for seat_id, label in (("bull_advocate", "Bull Advocate"), ("bear_advocate", "Bear Advocate")):
        route = resolve_route(seat_id, settings, default_model=settings.seat_model)
        estimate.line_items.append(
            CostLineItem(
                label=f"{label} (x{rounds} rounds)",
                model=route.model,
                call_count=rounds,
                est_input_tokens=_ADVOCATE_TOKENS["input"] * rounds,
                est_output_tokens=_ADVOCATE_TOKENS["output"] * rounds,
                est_cost_usd=round(_cost_for(route.model, rounds, _ADVOCATE_TOKENS, is_fixture), 6),
            )
        )

    prosecutor_route = resolve_route("prosecutor", settings, default_model=settings.synthesis_model)
    estimate.line_items.append(
        CostLineItem(
            label=f"Prosecutor (x{rounds} rounds)",
            model=prosecutor_route.model,
            call_count=rounds,
            est_input_tokens=_PROSECUTOR_TOKENS["input"] * rounds,
            est_output_tokens=_PROSECUTOR_TOKENS["output"] * rounds,
            est_cost_usd=round(_cost_for(prosecutor_route.model, rounds, _PROSECUTOR_TOKENS, is_fixture), 6),
        )
    )

    gm_route = resolve_route("grand_master", settings, default_model=settings.synthesis_model)
    estimate.line_items.append(
        CostLineItem(
            label="Grand Master synthesis",
            model=gm_route.model,
            call_count=1,
            est_input_tokens=_GRAND_MASTER_TOKENS["input"],
            est_output_tokens=_GRAND_MASTER_TOKENS["output"],
            est_cost_usd=round(_cost_for(gm_route.model, 1, _GRAND_MASTER_TOKENS, is_fixture), 6),
        )
    )

    return estimate
