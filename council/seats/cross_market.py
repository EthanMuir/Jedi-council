"""The Cross-Market Navigator -- "Reader of Distant Stars". Sees the sector
ETF, a peer basket, an index-futures proxy, and an overseas-session proxy --
NEVER the ticker's own price series. In tested opening-range work the only
filter with a statistically significant per-trade edge came from a
*different* market's confirmation; same-asset data was already priced."""
from __future__ import annotations

from typing import Any

from council.data.service import DataService
from council.engine.horizons import competent_horizons
from council.engine.llm_client import LLMClient
from council.seats.base import SeatContext, SeatVerdict
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
    horizons = competent_horizons("cross_market")

    async def gather(
        self, data_service: DataService, ticker: str, as_of: Any, horizon: str
    ) -> SeatContext:
        snapshot = await data_service.get_cross_market_snapshot(ticker, as_of=as_of)
        return SeatContext(
            self.id,
            self.allowed_data,
            {"cross_market": snapshot},
            ticker=ticker,
            as_of=as_of,
            horizon=horizon,
        )

    async def deliberate(
        self,
        ctx: SeatContext,
        llm_client: LLMClient,
        round_n: int = 0,
        sample_index: int = 0,
        peer_summaries: list[dict] | None = None,
    ) -> SeatVerdict:
        x = ctx["cross_market"]

        user_prompt = (
            f"Cross-market snapshot, horizon {ctx.horizon}. Data as of {x.as_of.isoformat()}.\n\n"
            f"Sector ETF ({x.sector_etf_symbol}) 5-day return: {x.sector_etf_return_5d_pct:+.2f}%\n"
            f"Peer basket 5-day return: {x.peer_basket_return_5d_pct:+.2f}%\n"
            f"Index futures proxy change: {x.index_futures_change_pct:+.2f}%\n"
            f"Dollar index change: {x.dollar_index_change_pct:+.2f}%\n"
            f"Oil change: {x.oil_change_pct:+.2f}%\n"
            f"Overseas session return: {x.overseas_session_return_pct:+.2f}%\n\n"
            "Reason only from co-movement/divergence across these other markets and give your verdict."
        )

        return await llm_client.get_verdict(
            seat_id=self.id,
            model=llm_client.settings.seat_model,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            fixture_name=self.id,
            sample_index=sample_index,
        )
