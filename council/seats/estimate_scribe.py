"""The Estimate Scribe -- "Keeper of Expectations". Sees only analyst
earnings estimates, revision history, guidance vs consensus, and historical
surprise pattern. Revision *momentum* matters more than the level. Price
targets and ratings belong to the Reader of the Guild's Targets, so the two
seats never count the same evidence twice."""
from __future__ import annotations

from typing import Any

from council.data.service import DataService
from council.engine.llm_client import LLMClient
from council.seats.base import MemoryLesson, MultiTermVerdict, SeatContext
from council.seats.debiasing import DEBIASING_PREAMBLE

_SYSTEM_PROMPT = DEBIASING_PREAMBLE + """
You are the Estimate Scribe, "Keeper of Expectations" on a market-prediction
council. You see ONLY analyst earnings estimates: consensus EPS/revenue,
revision history, guidance-vs-consensus, and the historical
earnings-surprise pattern. No price chart, no news, no price targets, no
fundamentals beyond what's implied by estimates.

The level of the estimate matters far less than its MOMENTUM (is the
consensus being revised up or down, and how fast). A consistent
multi-quarter beat pattern is itself information; treat one-off surprises
with more caution than a repeated pattern. The next earnings report is the
nearest test of these numbers, so revisions speak most to the medium term.
"""


class EstimateScribeSeat:
    id = "estimate_scribe"
    title = "Keeper of Expectations"
    allowed_data = frozenset({"estimates"})

    async def gather(
        self, data_service: DataService, ticker: str, as_of: Any
    ) -> SeatContext:
        snapshot = await data_service.get_analyst_estimates(ticker, as_of=as_of)
        return SeatContext(
            self.id,
            self.allowed_data,
            {"estimates": snapshot},
            ticker=ticker,
            as_of=as_of,
        )

    async def deliberate(
        self,
        ctx: SeatContext,
        llm_client: LLMClient,
        round_n: int = 0,
        sample_index: int = 0,
        memories: list[MemoryLesson] | None = None,
        peer_summaries: list[dict] | None = None,
    ) -> MultiTermVerdict:
        e = ctx["estimates"]

        surprises = "\n".join(
            f"  - {s.fiscal_period}: {s.surprise_pct:+.1f}%" for s in e.historical_surprises
        ) or "  - none on record"

        user_prompt = (
            f"Analyst estimates. Data as of {e.as_of.isoformat()}.\n\n"
            f"Consensus EPS next quarter: {e.consensus_eps_next_q}, "
            f"consensus revenue next quarter: {e.consensus_revenue_next_q:,.0f}\n"
            f"EPS revision, trailing 30d: {e.eps_revision_pct_30d:+.1f}%\n"
            f"Guidance vs consensus: {e.guidance_vs_consensus}\n"
            f"Historical surprise pattern:\n{surprises}\n\n"
            "Weigh revision momentum over the raw level, then give your lean for each term."
        )

        return await llm_client.get_seat_answer(
            seat_id=self.id,
            model=llm_client.settings.seat_model,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            fixture_name=self.id,
            sample_index=sample_index,
            memories=memories,
        )
