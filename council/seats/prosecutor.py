"""The Prosecutor -- "The Devil's Advocate". Sees everything, including the
emerging consensus. Attacks whichever direction the council is converging
on. Hunts for: correlated evidence counted twice, seats citing the same
underlying source, price targets outside the plausible distribution, and
theses that would have been generated regardless of the data. Has veto
power -- can force the final verdict to NO_CONVICTION."""
from __future__ import annotations

from collections import defaultdict

from council.engine.llm_client import LLMClient, SchemaRetryExhausted
from council.engine.schemas import (
    DebateArgument,
    ProsecutorVerdict,
    Tier1Summary,
    format_debate_transcript,
)

_SYSTEM_PROMPT = """
You are the Prosecutor, "The Devil's Advocate" on a market-prediction
council. You see everything: every Tier I seat's summarised verdict, the
Bull/Bear debate so far, and the direction the council is currently
converging toward. Your job is to attack that direction, whichever it is.

You are handed two facts that were computed deterministically, not by your
own judgment -- treat them as established:
1. Any evidence sources cited by more than one Tier I seat (correlated
   evidence: it should count once, not once per seat that happened to read
   it).
2. Any Tier I seat whose price target was flagged IMPLAUSIBLE against the
   Reality Anchor (the base-rate/options-implied plausible range).

On top of those facts, use your own judgment to hunt for theses that would
have been generated regardless of the specific data in front of the seat --
a generic bullish or bearish narrative dressed up with whichever evidence
was available, not a claim that is actually contingent on what the data
showed.

You have veto power. Set veto=true only when the case for the converging
direction is genuinely undermined -- heavy reliance on correlated evidence,
multiple implausible targets propping up the consensus, or a thesis pattern
that would not have changed regardless of the evidence. A well-supported
consensus with one minor flag does not warrant a veto.
"""


def detect_correlated_evidence(summaries: list[Tier1Summary]) -> list[str]:
    """Deterministic, not LLM judgment: any evidence source cited by 2+
    directional Tier I seats is correlated evidence by definition."""
    source_to_seats: dict[str, set[str]] = defaultdict(set)
    for s in summaries:
        if s.vote == "NO_READ":
            continue
        for source in s.evidence_sources:
            source_to_seats[source].add(s.seat_id)
    return [
        f"'{source}' cited by {len(seats)} seats: {', '.join(sorted(seats))}"
        for source, seats in sorted(source_to_seats.items())
        if len(seats) >= 2
    ]


def _format_tier1(summaries: list[Tier1Summary]) -> str:
    return "\n".join(
        f"- [{s.seat_id}] {s.vote} (p={s.probability}, data_quality={s.data_quality}, "
        f"dispersion={s.dispersion})"
        for s in summaries
    )


class ProsecutorSeat:
    id = "prosecutor"
    title = "The Devil's Advocate"

    async def review(
        self,
        tier1_summaries: list[Tier1Summary],
        debate_so_far: list[DebateArgument],
        plausibility_flags: dict[str, str],
        correlated_evidence: list[str],
        converging_direction: str,
        round_n: int,
        llm_client: LLMClient,
        model: str,
    ) -> ProsecutorVerdict | None:
        implausible = [sid for sid, flag in plausibility_flags.items() if flag == "IMPLAUSIBLE"]
        user_prompt = (
            f"Round {round_n}. Council is converging toward: {converging_direction}.\n\n"
            f"Tier I verdicts:\n{_format_tier1(tier1_summaries)}\n\n"
            f"Debate so far:\n{format_debate_transcript(debate_so_far)}\n\n"
            f"Correlated evidence (computed, not judgment):\n"
            + ("\n".join(f"- {c}" for c in correlated_evidence) or "- none found")
            + f"\n\nSeats with IMPLAUSIBLE price targets: {implausible or 'none'}\n\n"
            "Attack the converging direction and give your verdict."
        )
        try:
            return await llm_client.get_structured(
                seat_id=self.id,
                model=model,
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_model=ProsecutorVerdict,
                fixture_name=f"prosecutor_round{round_n}",
            )
        except SchemaRetryExhausted:
            return None
