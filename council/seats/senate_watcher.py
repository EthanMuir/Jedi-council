"""The Senate Watcher -- "Reader of the Republic". Sees only congressional
disclosure filings and pending legislation/regulatory dockets relevant to
the ticker. Must handle the disclosure lag explicitly -- filings appear well
after the underlying trade."""
from __future__ import annotations

from typing import Any

from council.data.service import DataService
from council.engine.llm_client import LLMClient
from council.seats.base import MemoryLesson, MultiTermVerdict, SeatContext
from council.seats.debiasing import DEBIASING_PREAMBLE

_SYSTEM_PROMPT = DEBIASING_PREAMBLE + """
You are the Senate Watcher, "Reader of the Republic" on a market-prediction
council. You see ONLY the stock trades members of the House and Senate have
disclosed in this company over the last year (and any pending legislation
or regulatory dockets, when available) -- no price chart, no news, no
fundamentals.

Members' trades can be a real signal: a member buying with their own money,
several members buying around the same time, or buys from members whose
committees oversee this company's industry. Routine small trades, trades by
a spouse's advisor, or sales that look like rebalancing say much less. A
purchase is usually more informative than a sale (people sell for many
reasons, but buy for one).

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

    async def gather(
        self, data_service: DataService, ticker: str, as_of: Any
    ) -> SeatContext:
        feed = await data_service.get_congress_trades(ticker, as_of=as_of)
        return SeatContext(
            self.id,
            self.allowed_data,
            {"congress": feed},
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
        feed = ctx["congress"]

        if not feed.trades and not feed.pending_legislation:
            # The source answered and no member traded this stock in a year:
            # a real read (Congress isn't trading it), not missing data. A
            # failed fetch never gets here -- it raises and shows NO_READ.
            return MultiTermVerdict.dead_even(
                thesis="No member of Congress disclosed a trade in this stock in the last "
                "year, so Congress gives no signal either way.",
                reason="No congressional trades in this stock in the last year.",
                what_would_change_my_mind="A member disclosing a purchase, especially one "
                "on a committee that oversees this company's industry.",
            )

        lines = []
        for t in feed.trades:
            lag_days = (t.filed_at.date() - t.transaction_date).days
            who = t.member_name
            details = [d for d in (t.party, t.state) if d]
            if details:
                who += f" ({'-'.join(details)})"
            if t.committees:
                who += f", committees: {', '.join(t.committees)}"
            owner = f", owner: {t.owner.lower()}" if t.owner else ""
            lines.append(
                f"- [{t.chamber}] {who}: {t.transaction_type} {t.amount_range}{owner}, "
                f"traded {t.transaction_date}, filed {t.filed_at.date()} ({lag_days}d disclosure lag)"
            )

        legislation = "\n".join(f"- {item}" for item in feed.pending_legislation) or "- none in view"

        user_prompt = (
            f"Congressional disclosures. Data as of "
            f"{feed.as_of.isoformat()}.\n\nTrades:\n"
            + ("\n".join(lines) if lines else "- none in lookback window")
            + (f"\n\nPending legislation/regulatory dockets:\n{legislation}" if feed.pending_legislation else "")
            + "\n\nWeigh who is trading, buys against sells, clustering and the disclosure "
            "lag, then give your lean for each term."
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
