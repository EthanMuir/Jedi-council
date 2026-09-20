"""Phase 6: estimate the $ cost of a real deliberation before running it,
without making a single network call. Token counts below are rough
constants per call type, not measured -- good enough to tell whether a run
costs a dollar or a nickel, not an invoice. Same honesty caveat as
llm_client._PRICING_PER_MTOK on the price side, and the same graceful
fallback: a model with no pricing entry (e.g. gpt-5, gemini-3-pro, still
routing-only per Addendum A3) prices at $0.00, not a crash."""
from __future__ import annotations

from dataclasses import dataclass, field

from council.config import Settings
from council.engine.horizons import is_competent
from council.engine.llm_client import _PRICING_PER_MTOK
from council.engine.routing import resolve_route

# Rough per-call token counts, by call type.
_TIER_I_TOKENS = {"input": 900, "output": 350}
_ADVOCATE_TOKENS = {"input": 700, "output": 300}
_PROSECUTOR_TOKENS = {"input": 1200, "output": 350}
_GRAND_MASTER_TOKENS = {"input": 2000, "output": 500}


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
    horizon: str
    line_items: list[CostLineItem] = field(default_factory=list)

    @property
    def total_cost_usd(self) -> float:
        return round(sum(li.est_cost_usd for li in self.line_items), 4)

    @property
    def total_calls(self) -> int:
        return sum(li.call_count for li in self.line_items)


def _cost_for(model: str, calls: int, tokens: dict) -> float:
    pricing = _PRICING_PER_MTOK.get(model)
    if not pricing:
        return 0.0
    return calls * (
        (tokens["input"] / 1_000_000) * pricing["input"]
        + (tokens["output"] / 1_000_000) * pricing["output"]
    )


def estimate_deliberation_cost(ticker: str, horizon: str, settings: Settings) -> CostEstimate:
    # Imported here, not at module level, to avoid a load-time cycle:
    # orchestrator.py doesn't need cost_estimate.py, but pulling in its
    # heavier import graph (data providers, Crypt, ...) just for the seat
    # roster is unnecessary work for every other caller of this module.
    from council.engine.orchestrator import TIER_I_SEATS

    estimate = CostEstimate(ticker=ticker, horizon=horizon)
    eligible = [s for s in TIER_I_SEATS if is_competent(s.id, horizon)]
    n_samples = settings.n_samples_per_seat

    for seat in eligible:
        route = resolve_route(seat.id, settings, default_model=settings.seat_model)
        estimate.line_items.append(
            CostLineItem(
                label=f"{seat.id} (x{n_samples} samples)",
                model=route.model,
                call_count=n_samples,
                est_input_tokens=_TIER_I_TOKENS["input"] * n_samples,
                est_output_tokens=_TIER_I_TOKENS["output"] * n_samples,
                est_cost_usd=round(_cost_for(route.model, n_samples, _TIER_I_TOKENS), 6),
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
                est_cost_usd=round(_cost_for(route.model, rounds, _ADVOCATE_TOKENS), 6),
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
            est_cost_usd=round(_cost_for(prosecutor_route.model, rounds, _PROSECUTOR_TOKENS), 6),
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
            est_cost_usd=round(_cost_for(gm_route.model, 1, _GRAND_MASTER_TOKENS), 6),
        )
    )

    return estimate
