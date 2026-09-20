"""Bull Advocate and Bear Advocate -- Tier II, the Adversarial Chamber. See
all Tier I verdicts (not raw data). Each builds the strongest possible case
for its assigned side regardless of personal read. Assignment is fixed, not
chosen -- they are advocates, not analysts."""
from __future__ import annotations

from typing import Literal

from council.engine.llm_client import LLMClient, SchemaRetryExhausted
from council.engine.schemas import DebateArgument, Tier1Summary, format_tier1_summaries

_BASE_PROMPT = """
You are the {side} Advocate on a market-prediction council's Adversarial
Chamber. Your assignment is fixed -- you are arguing the {side} case
regardless of your own personal read of the evidence. You are an advocate,
not an analyst: your job is to build the STRONGEST possible case for this
side from the Tier I council's verdicts, not to be balanced.

You see only the Tier I seats' summarised verdicts (vote, probability,
thesis, data quality) -- never their raw underlying data. Cite specific
seats by id when you use their evidence. If this is round 2 or later,
directly rebut the opposing advocate's prior argument rather than repeating
your round 1 case unchanged.
"""


class _AdvocateSeat:
    side: Literal["BULL", "BEAR"]

    async def argue(
        self,
        tier1_summaries: list[Tier1Summary],
        round_n: int,
        opposing_prior: DebateArgument | None,
        llm_client: LLMClient,
        model: str,
    ) -> DebateArgument | None:
        system_prompt = _BASE_PROMPT.format(side=self.side)
        user_prompt = (
            f"Round {round_n}. Tier I council verdicts:\n{format_tier1_summaries(tier1_summaries)}\n\n"
        )
        if opposing_prior is not None:
            user_prompt += (
                f"Opposing ({'BEAR' if self.side == 'BULL' else 'BULL'}) advocate's prior "
                f"argument to rebut: {opposing_prior.argument}\n\n"
            )
        user_prompt += f"Make the strongest possible {self.side} case."

        try:
            return await llm_client.get_structured(
                seat_id=self.id,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=DebateArgument,
                fixture_name=f"{self.id}_round{round_n}",
            )
        except SchemaRetryExhausted:
            return None


class BullAdvocateSeat(_AdvocateSeat):
    id = "bull_advocate"
    title = "Bull Advocate"
    side = "BULL"


class BearAdvocateSeat(_AdvocateSeat):
    id = "bear_advocate"
    title = "Bear Advocate"
    side = "BEAR"
