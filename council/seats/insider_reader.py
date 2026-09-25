"""The Insider Reader -- "Student of the Inner Circle". Sees only Form 4
insider transactions. Signal grammar (spec section 3): cluster buys > single
buys; C-suite > directors; open-market purchases >> option exercises; size
relative to the insider's existing holding; routine 10b5-1 sales carry
near-zero information."""
from __future__ import annotations

from typing import Any

from council.data.service import DataService
from council.engine.llm_client import LLMClient
from council.seats.base import MemoryLesson, MultiTermVerdict, SeatContext
from council.seats.debiasing import DEBIASING_PREAMBLE

_SYSTEM_PROMPT = DEBIASING_PREAMBLE + """
You are the Insider Reader, "Student of the Inner Circle" on a
market-prediction council. You see ONLY Form 4 insider transactions -- no
price chart, no news, no fundamentals.

Signal grammar, apply it strictly:
- A CLUSTER of buys (multiple insiders, close in time) is far more
  informative than a single buy.
- C-suite transactions carry more signal than director transactions.
- Open-market purchases are a strong signal; option exercises are weak to
  neutral (often just compensation mechanics, not a view).
- Judge size relative to the insider's existing holding (`shares_owned_after`
  minus the transaction), not the absolute dollar amount.
- Routine Rule 10b5-1 sales carry NEAR-ZERO information -- they are
  pre-scheduled and say nothing about the insider's current view. Most
  insider SALES in general are noise (diversification, taxes, liquidity).
  Say so explicitly rather than reading bearish intent into them.

Insiders trade on their view of the business over months and years, not
next week -- their signal is strongest over the medium and long terms. If
the transactions in view don't rise above the noise floor, keep your leans
close to 0.5.
"""


class InsiderReaderSeat:
    id = "insider_reader"
    title = "Student of the Inner Circle"
    allowed_data = frozenset({"insider"})

    async def gather(
        self, data_service: DataService, ticker: str, as_of: Any
    ) -> SeatContext:
        feed = await data_service.get_insider_transactions(ticker, as_of=as_of)
        return SeatContext(
            self.id,
            self.allowed_data,
            {"insider": feed},
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
        feed = ctx["insider"]

        if not feed.transactions:
            # SEC answered and there was simply nothing filed: a real read
            # (insiders are quiet), not missing data. A failed fetch never
            # gets here -- it raises and the seat shows NO_READ.
            return MultiTermVerdict.dead_even(
                thesis="No insider buying or selling was filed with the SEC in the last "
                "6 months, so insiders give no signal either way.",
                reason="No Form 4 insider transactions in the last 6 months.",
                what_would_change_my_mind="A new Form 4 filing, especially an open-market "
                "purchase by an officer.",
            )

        lines = []
        for t in feed.transactions:
            pct_of_holding = (
                (t.shares / t.shares_owned_after) * 100 if t.shares_owned_after else None
            )
            lines.append(
                f"- [{t.filed_at.isoformat()}] {t.insider_name} ({t.insider_title}, "
                f"officer={t.is_officer}): {t.transaction_type}, {t.shares:,.0f} shares "
                f"@ {t.price}, now owns {t.shares_owned_after:,.0f} "
                f"(txn was ~{pct_of_holding:.1f}% of resulting holding)"
                if pct_of_holding is not None
                else f"- [{t.filed_at.isoformat()}] {t.insider_name}: {t.transaction_type}, "
                f"{t.shares:,.0f} shares @ {t.price}"
            )

        user_prompt = (
            f"Form 4 filings. Data as of "
            f"{feed.as_of.isoformat()}, staleness {feed.staleness_seconds:.0f}s.\n\n"
            + "\n".join(lines)
            + "\n\nApply the signal grammar and give your lean for each term."
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
