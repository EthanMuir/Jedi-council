"""The Cross-Market Navigator -- "Reader of Distant Stars". Sees the sector
ETF, a peer basket, an index-futures proxy, and an overseas-session proxy --
NEVER the ticker's own price series. In tested opening-range work the only
filter with a statistically significant per-trade edge came from a
*different* market's confirmation; same-asset data was already priced."""
from __future__ import annotations

from typing import Any

from council.data.service import DataService
from council.engine.llm_client import LLMClient
from council.seats.base import MemoryLesson, MultiTermVerdict, SeatContext
from council.seats.debiasing import DEBIASING_PREAMBLE

_SYSTEM_PROMPT = DEBIASING_PREAMBLE + """
You are the Cross-Market Navigator, "Reader of Distant Stars" on a
market-prediction council. You see ONLY other markets: the sector ETF, a
peer basket, an index-futures proxy, and an overseas-session proxy. You are
NEVER shown this ticker's own price series -- that data is already priced
into the ticker itself and is the Technician's exclusive domain, not yours.

Your edge exists specifically because this is a DIFFERENT market's
confirmation, not the asset's own pre-market or recent data (which tested
poorly as a filter). Reason from co-movement and divergence between the
sector, peers, and broader tape, not from anything ticker-specific.
"""


class CrossMarketSeat:
    id = "cross_market"
    title = "Reader of Distant Stars"
    allowed_data = frozenset({"cross_market"})

    async def gather(
        self, data_service: DataService, ticker: str, as_of: Any
    ) -> SeatContext:
        snapshot = await data_service.get_cross_market_snapshot(ticker, as_of=as_of)
        return SeatContext(
            self.id,
            self.allowed_data,
            {"cross_market": snapshot},
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
        x = ctx["cross_market"]

        def pct(value: float | None) -> str:
            return "unavailable" if value is None else f"{value:+.2f}%"

        sector = f"Sector ETF ({x.sector_etf_symbol})" if x.sector_etf_symbol else "Sector ETF"
        peers = f"Peer basket ({', '.join(x.peer_symbols)})" if x.peer_symbols else "Peer basket"
        user_prompt = (
            f"Cross-market snapshot. Data as of {x.as_of.isoformat()}. "
            "All changes are 5-day returns.\n\n"
            f"{sector}: {pct(x.sector_etf_return_5d_pct)}\n"
            f"{peers}: {pct(x.peer_basket_return_5d_pct)}\n"
            f"Index proxy (SPY): {pct(x.index_futures_change_pct)}\n"
            f"Dollar (UUP): {pct(x.dollar_index_change_pct)}\n"
            f"Oil (USO): {pct(x.oil_change_pct)}\n"
            f"Overseas proxy (EWJ): {pct(x.overseas_session_return_pct)}\n\n"
            "Treat an unavailable input as missing, not as flat. Reason only from "
            "co-movement/divergence across these other markets and give your lean for each term."
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
