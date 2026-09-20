"""The Prosecutor -- "The Devil's Advocate". Sees everything, including the
emerging consensus. Attacks whichever direction the council is converging
on. Hunts for: correlated evidence counted twice, seats citing the same
underlying source, price targets outside the plausible distribution, and
theses that would have been generated regardless of the data. Has veto
power -- can force the final verdict to NO_CONVICTION."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from council.engine.llm_client import LLMClient, SchemaRetryExhausted
from council.engine.schemas import (
    DebateArgument,
    ProsecutorVerdict,
    Tier1Summary,
    format_debate_transcript,
)
from council.seats.base import SeatVerdict

# Addendum A7: |implied - stated| beyond this is INCOHERENT_CONFIDENCE.
_COHERENCE_TOLERANCE = 0.15
# Weight discount applied to a seat flagged incoherent, for this deliberation only.
INCOHERENCE_WEIGHT_DISCOUNT = 0.30

_SYSTEM_PROMPT = """
You are the Prosecutor, "The Devil's Advocate" on a market-prediction
council. You see everything: every Tier I seat's summarised verdict, the
Bull/Bear debate so far, and the direction the council is currently
converging toward. Your job is to attack that direction, whichever it is.

You are handed three facts that were computed deterministically, not by
your own judgment -- treat them as established:
1. Any evidence sources cited by more than one Tier I seat (correlated
   evidence: it should count once, not once per seat that happened to read
   it).
2. Any Tier I seat whose price target was flagged IMPLAUSIBLE against the
   Reality Anchor (the base-rate/options-implied plausible range).
3. Any Tier I seat whose own stated decomposition sub-probabilities do not
   combine (by AND/OR/CONDITIONAL arithmetic) to within 0.15 of its
   headline probability -- INCOHERENT_CONFIDENCE, a seat whose reasoning
   doesn't actually support the number it gave.

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


@dataclass
class CoherenceCheckResult:
    seat_id: str
    has_decomposition: bool
    implied_probability: float | None
    stated_probability: float
    coherent: bool


def check_decomposition_coherence(seat_id: str, verdict: SeatVerdict) -> CoherenceCheckResult:
    """Addendum A7: verify a seat's sub-probabilities actually combine to its
    headline number. AND -> product; OR -> 1 - prod(1-p); CONDITIONAL ->
    chained product (same arithmetic as AND -- each step is already
    conditioned on the prior). All items in one seat's decomposition are
    treated as one combination chain, governed by the first item's relation
    (a seat mixing relation types within one chain is a seat that should
    just decompose more carefully -- not this function's problem to guess)."""
    if not verdict.decomposition:
        return CoherenceCheckResult(seat_id, False, None, verdict.probability, True)

    relation = verdict.decomposition[0].relation
    probs = [item.probability for item in verdict.decomposition]
    if relation == "OR":
        implied = 1.0
        for p in probs:
            implied *= 1 - p
        implied = 1 - implied
    else:  # AND / CONDITIONAL
        implied = 1.0
        for p in probs:
            implied *= p

    coherent = abs(implied - verdict.probability) <= _COHERENCE_TOLERANCE
    return CoherenceCheckResult(seat_id, True, round(implied, 3), verdict.probability, coherent)


def detect_incoherent_decompositions(verdicts: dict[str, SeatVerdict]) -> dict[str, CoherenceCheckResult]:
    results = {}
    for seat_id, v in verdicts.items():
        if v.vote == "NO_READ":
            continue
        result = check_decomposition_coherence(seat_id, v)
        if result.has_decomposition and not result.coherent:
            results[seat_id] = result
    return results


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
        incoherent_decompositions: dict[str, CoherenceCheckResult],
        converging_direction: str,
        round_n: int,
        llm_client: LLMClient,
        model: str,
    ) -> ProsecutorVerdict | None:
        implausible = [sid for sid, flag in plausibility_flags.items() if flag == "IMPLAUSIBLE"]
        incoherent_desc = [
            f"{sid}: stated {r.stated_probability}, decomposition implies {r.implied_probability}"
            for sid, r in incoherent_decompositions.items()
        ]
        user_prompt = (
            f"Round {round_n}. Council is converging toward: {converging_direction}.\n\n"
            f"Tier I verdicts:\n{_format_tier1(tier1_summaries)}\n\n"
            f"Debate so far:\n{format_debate_transcript(debate_so_far)}\n\n"
            f"Correlated evidence (computed, not judgment):\n"
            + ("\n".join(f"- {c}" for c in correlated_evidence) or "- none found")
            + f"\n\nSeats with IMPLAUSIBLE price targets: {implausible or 'none'}\n\n"
            f"Seats with INCOHERENT decomposition (their own sub-probabilities don't "
            f"combine to their headline number, computed not judgment): "
            f"{incoherent_desc or 'none'}\n\n"
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
