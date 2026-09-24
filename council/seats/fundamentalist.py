"""The Fundamentalist -- "Keeper of the Ledgers". Sees only financial
statements, margins, growth, and valuation multiples vs sector. No price
action beyond the fact a current price exists (not exposed here at all).
Weak over the short term, strongest over the long term -- the competence
matrix counts its short-term lean for little."""
from __future__ import annotations

from typing import Any

from council.data.service import DataService
from council.engine.llm_client import LLMClient
from council.seats.base import MemoryLesson, MultiTermVerdict, SeatContext
from council.seats.debiasing import DEBIASING_PREAMBLE

_SYSTEM_PROMPT = DEBIASING_PREAMBLE + """
You are the Fundamentalist, "Keeper of the Ledgers" on a market-prediction
council. You see ONLY financial statement data: revenue, margins, growth
rates, balance sheet, free cash flow, and valuation multiples versus sector
medians. No price chart, no news, no options data.

Your evidence is weakest over the short term -- a quarter's fundamentals
rarely move a stock in a week, so keep that lean close to 0.5 -- and
strongest over the long term, where valuation and growth decide where a
stock ends up. Anchor your comparison class on valuation-multiple regimes
relative to the sector, not on the company's own story.
"""


class FundamentalistSeat:
    id = "fundamentalist"
    title = "Keeper of the Ledgers"
    allowed_data = frozenset({"fundamentals"})

    async def gather(
        self, data_service: DataService, ticker: str, as_of: Any
    ) -> SeatContext:
        fundamentals = await data_service.get_fundamentals(ticker, as_of=as_of)
        return SeatContext(
            self.id,
            self.allowed_data,
            {"fundamentals": fundamentals},
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
        f = ctx["fundamentals"]

        user_prompt = (
            f"Fundamentals. Fiscal period {f.fiscal_period}.\n\n"
            f"Revenue: {f.revenue:,.0f}, YoY growth: {f.revenue_growth_yoy:.1%}\n"
            f"Gross margin: {f.gross_margin:.1%}, operating margin: {f.operating_margin:.1%}, "
            f"net margin: {f.net_margin:.1%}\n"
            f"Total debt: {f.total_debt:,.0f}, cash: {f.cash_and_equivalents:,.0f}, "
            f"free cash flow: {f.free_cash_flow:,.0f}\n"
            f"P/E: {f.pe_ratio} (sector median {f.sector_median_pe}), "
            f"EV/EBITDA: {f.ev_to_ebitda} (sector median {f.sector_median_ev_ebitda}), "
            f"P/S: {f.price_to_sales}\n\n"
            "Give your lean for each term, anchored on a valuation/growth comparison class."
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
