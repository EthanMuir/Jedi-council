"""The Senate Watcher -- "Reader of the Republic". Sees only congressional
disclosure filings and pending legislation/regulatory dockets relevant to
the ticker. Must handle the disclosure lag explicitly -- filings appear well
after the underlying trade."""
from __future__ import annotations

from typing import Any

from council.data.service import DataService
from council.engine.horizons import competent_horizons
from council.engine.llm_client import LLMClient
from council.seats.base import SeatContext, SeatVerdict
from council.seats.debiasing import DEBIASING_PREAMBLE

_SYSTEM_PROMPT = DEBIASING_PREAMBLE + """
You are the Senate Watcher, "Reader of the Republic" on a market-prediction
council. You see ONLY congressional/senate trade disclosures and pending
legislation or regulatory dockets relevant to this ticker -- no price chart,
no news, no fundamentals.

Congressional disclosures are filed well after the underlying transaction --
the gap between `transaction_date` and `filed_at` is routinely 30-45 days or
more. Treat the disclosed trade as stale information by the time you see it,
and say so. Also weigh policy exposure: pending tariffs, export controls,
subsidies, or antitrust action relevant to this ticker's sector, and note
committee assignments that make a member's trade more or less informative
(a member sitting on a relevant committee trading ahead of legislation is a
different signal than an unrelated member's routine trade).
"""


class SenateWatcherSeat:
    id = "senate_watcher"
    title = "Reader of the Republic"
    allowed_data = frozenset({"congress"})
    horizons = competent_horizons("senate_watcher")

    async def gather(
        self, data_service: DataService, ticker: str, as_of: Any, horizon: str
    ) -> SeatContext:
        feed = await data_service.get_congress_trades(ticker, as_of=as_of)
        return SeatContext(
            self.id,
            self.allowed_data,
            {"congress": feed},
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
        feed = ctx["congress"]

        if not feed.trades and not feed.pending_legislation:
            return SeatVerdict(
                vote="NO_READ",
                probability=0.5,
                expected_move_pct=0.0,
                thesis="No congressional disclosures or relevant legislative activity in view.",
                what_would_change_my_mind="A new disclosure filing or docket entry.",
                data_quality="POOR",
                abstain_reason="no_data",
            )

        lines = []
        for t in feed.trades:
            lag_days = (t.filed_at.date() - t.transaction_date).days
            lines.append(
                f"- [{t.chamber}] {t.member_name} ({', '.join(t.committees) or 'no listed committee'}): "
                f"{t.transaction_type} {t.amount_range}, traded {t.transaction_date}, "
                f"filed {t.filed_at.date()} ({lag_days}d disclosure lag)"
            )

        legislation = "\n".join(f"- {item}" for item in feed.pending_legislation) or "- none in view"

        user_prompt = (
            f"Congressional disclosures, horizon {ctx.horizon}. Data as of "
            f"{feed.as_of.isoformat()}.\n\nTrades:\n"
            + ("\n".join(lines) if lines else "- none in lookback window")
            + f"\n\nPending legislation/regulatory dockets:\n{legislation}\n\n"
            "Weigh disclosure lag and committee relevance, then give your verdict."
        )

        return await llm_client.get_verdict(
            seat_id=self.id,
            model=llm_client.settings.seat_model,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            fixture_name=self.id,
            sample_index=sample_index,
        )
