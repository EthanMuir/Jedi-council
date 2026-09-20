"""The Flow Cartographer -- "Reader of Great Tides". Sees only 13F
institutional holdings, ETF inclusion, short interest, and days-to-cover.
Must note the quarterly lag on 13F data and discount accordingly."""
from __future__ import annotations

from typing import Any

from council.data.service import DataService
from council.engine.horizons import competent_horizons
from council.engine.llm_client import LLMClient
from council.seats.base import SeatContext, SeatVerdict
from council.seats.debiasing import DEBIASING_PREAMBLE

_SYSTEM_PROMPT = DEBIASING_PREAMBLE + """
You are the Flow Cartographer, "Reader of Great Tides" on a
market-prediction council. You see ONLY institutional ownership data: 13F
holdings and their quarter-over-quarter change, ETF inclusion/weight notes,
short interest, and days-to-cover. No price chart, no news, no fundamentals.

13F filings lag their quarter by up to 45 days -- the position data you see
can be two to four months stale relative to `as_of`. Explicitly discount
your confidence for this staleness, more so at short horizons than long
ones. Short interest and days-to-cover are comparatively fresher and can
carry a squeeze-risk signal independent of the 13F lag.
"""


class FlowCartographerSeat:
    id = "flow_cartographer"
    title = "Reader of Great Tides"
    allowed_data = frozenset({"institutional"})
    horizons = competent_horizons("flow_cartographer")

    async def gather(
        self, data_service: DataService, ticker: str, as_of: Any, horizon: str
    ) -> SeatContext:
        snapshot = await data_service.get_institutional_holdings(ticker, as_of=as_of)
        return SeatContext(
            self.id,
            self.allowed_data,
            {"institutional": snapshot},
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
        s = ctx["institutional"]

        if s.staleness_seconds is None:
            return SeatVerdict(
                vote="NO_READ",
                probability=0.5,
                expected_move_pct=0.0,
                thesis="No 13F has been filed as of this date.",
                what_would_change_my_mind="The next quarterly 13F filing.",
                data_quality="POOR",
                abstain_reason="no_data",
            )

        lag_days = s.staleness_seconds / 86400
        user_prompt = (
            f"Institutional ownership, horizon {ctx.horizon}. Quarter end {s.quarter_end}, "
            f"filed {s.filed_at.isoformat()} ({lag_days:.0f}d before as_of).\n\n"
            f"Total institutional shares: {s.total_institutional_shares:,}\n"
            f"Pct of float held: {s.pct_of_float_held:.1%}\n"
            f"QoQ share change: {s.qoq_share_change_pct:+.1f}%\n"
            f"Net buyers among top holders: {s.top_holders_net_buyers}, "
            f"net sellers: {s.top_holders_net_sellers}\n"
            f"ETF inclusion notes: {s.etf_inclusion_notes}\n"
            f"Short interest: {s.short_interest_shares:,} shares, "
            f"days-to-cover: {s.days_to_cover}\n\n"
            "Discount for the 13F lag and give your verdict."
        )

        return await llm_client.get_verdict(
            seat_id=self.id,
            model=llm_client.settings.seat_model,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            fixture_name=self.id,
            sample_index=sample_index,
        )
