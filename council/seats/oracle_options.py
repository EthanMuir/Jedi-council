"""The Oracle of Options -- "Reader of Probabilities". Sees only the option
chain / IV surface / put-call ratio. Its most valuable output is the implied
expected move, not direction -- every other seat's price target gets checked
against this seat's distribution downstream (the Base-Rate Keeper, Phase 2)."""
from __future__ import annotations

from typing import Any

from council.data.service import DataService
from council.engine.horizons import competent_horizons
from council.engine.llm_client import LLMClient
from council.seats.base import SeatContext, SeatVerdict
from council.seats.debiasing import DEBIASING_PREAMBLE

_SYSTEM_PROMPT = DEBIASING_PREAMBLE + """
You are the Oracle of Options, "Reader of Probabilities" on a
market-prediction council. You see ONLY the option chain: strikes,
expiries, bid/ask, volume, open interest, implied volatility, and the
put/call volume ratio. No price chart beyond the underlying quote, no news,
no fundamentals.

Your single most valuable output is `expected_move_pct`, derived from the
ATM straddle / implied volatility -- treat this as the primary contribution
of your seat. Skew and the put/call ratio may inform a directional lean, but
treat that lean cautiously and say so explicitly; this seat's job is to
supply the distribution other seats' targets get checked against, not to
call direction confidently.
"""


class OracleOptionsSeat:
    id = "oracle_options"
    title = "Reader of Probabilities"
    allowed_data = frozenset({"option_chain"})
    horizons = competent_horizons("oracle_options")

    async def gather(
        self, data_service: DataService, ticker: str, as_of: Any, horizon: str
    ) -> SeatContext:
        chain = await data_service.get_option_chain(ticker, as_of=as_of)
        return SeatContext(
            self.id,
            self.allowed_data,
            {"option_chain": chain},
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
        chain = ctx["option_chain"]

        if not chain.contracts:
            return SeatVerdict(
                vote="NO_READ",
                probability=0.5,
                expected_move_pct=0.0,
                thesis="No option chain data available.",
                what_would_change_my_mind="A populated option chain for this ticker.",
                data_quality="POOR",
                abstain_reason="no_data",
            )

        atm = min(chain.contracts, key=lambda c: abs(c.strike - chain.underlying_price))
        near_expiry = min(c.expiry for c in chain.contracts)
        near_month_ivs = [
            c.implied_volatility
            for c in chain.contracts
            if c.expiry == near_expiry and c.implied_volatility is not None
        ]
        avg_iv = sum(near_month_ivs) / len(near_month_ivs) if near_month_ivs else None

        user_prompt = (
            f"Option chain, horizon {ctx.horizon}. Data as of "
            f"{chain.as_of.isoformat()}, staleness {chain.staleness_seconds:.0f}s.\n\n"
            f"Underlying price: {chain.underlying_price}\n"
            f"Put/call volume ratio: {chain.put_call_ratio}\n"
            f"Nearest expiry: {near_expiry}\n"
            f"ATM strike: {atm.strike}, ATM implied volatility: {atm.implied_volatility}\n"
            f"Average near-month implied volatility across strikes: {avg_iv}\n"
            f"Contracts in chain: {len(chain.contracts)}\n\n"
            "Derive the implied expected move from the ATM straddle / IV, note any "
            "skew, and give your verdict."
        )

        return await llm_client.get_verdict(
            seat_id=self.id,
            model=llm_client.settings.seat_model,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            fixture_name="oracle_options",
            sample_index=sample_index,
        )
