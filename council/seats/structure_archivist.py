"""The Structure Archivist -- "Keeper of Charters". Sees only SEC filings
(8-K, S-1, S-3, 13D/G), buybacks, dilution, debt maturities, covenants, and
ESG/litigation flags. Low frequency, high impact -- abstains often, and that
is correct behaviour."""
from __future__ import annotations

from typing import Any

from council.data.service import DataService
from council.engine.horizons import competent_horizons
from council.engine.llm_client import LLMClient
from council.seats.base import SeatContext, SeatVerdict
from council.seats.debiasing import DEBIASING_PREAMBLE

_SYSTEM_PROMPT = DEBIASING_PREAMBLE + """
You are the Structure Archivist, "Keeper of Charters" on a
market-prediction council. You see ONLY SEC filings: 8-Ks, S-1/S-3
registrations, 13D/G ownership filings, and any flagged buyback, dilution,
debt-maturity, covenant, ESG, or litigation items. No price chart, no news,
no fundamentals.

Most routine filings (a standard 10-Q, a routine 8-K) carry no tradeable
signal. This seat should abstain often -- that is correct, not a failure.
Only vote when a filing is structurally significant: a new dilutive
registration, a large buyback authorization actually being executed, a
covenant or maturity wall, or an ownership filing signalling a real change
in control or activist involvement.
"""


class StructureArchivistSeat:
    id = "structure_archivist"
    title = "Keeper of Charters"
    allowed_data = frozenset({"filings"})
    horizons = competent_horizons("structure_archivist")

    async def gather(
        self, data_service: DataService, ticker: str, as_of: Any, horizon: str
    ) -> SeatContext:
        feed = await data_service.get_sec_filings(ticker, as_of=as_of)
        return SeatContext(
            self.id,
            self.allowed_data,
            {"filings": feed},
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
        feed = ctx["filings"]

        if not feed.filings:
            return SeatVerdict(
                vote="NO_READ",
                probability=0.5,
                expected_move_pct=0.0,
                thesis="No SEC filings in the lookback window.",
                what_would_change_my_mind="A new structurally significant filing.",
                data_quality="POOR",
                abstain_reason="no_data",
            )

        lines = [
            f"- [{f.filed_at.isoformat()}] {f.form_type}: {f.headline} "
            f"(flags: {', '.join(f.flags) or 'none'}) -- {f.summary}"
            for f in feed.filings
        ]

        user_prompt = (
            f"SEC filings, horizon {ctx.horizon}. Data as of {feed.as_of.isoformat()}.\n\n"
            + "\n".join(lines)
            + "\n\nAbstain unless something here is structurally significant. Give your verdict."
        )

        return await llm_client.get_verdict(
            seat_id=self.id,
            model=llm_client.settings.seat_model,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            fixture_name=self.id,
            sample_index=sample_index,
        )
