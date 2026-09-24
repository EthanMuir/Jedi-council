"""The Prosecutor -- "The Devil's Advocate". Sees everything, including the
emerging consensus. Attacks whichever direction the council is converging
on. Hunts for: correlated evidence counted twice, seats citing the same
underlying source, price targets outside the plausible distribution, and
theses that would have been generated regardless of the data. Its veto no
longer overrides the council: it is shown as a warning next to the terms it
objects to, so a reader sees the objection without losing the lean."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from council.engine.llm_client import LLMCallFailed, LLMClient, SchemaRetryExhausted
from council.engine.schemas import (
    DebateArgument,
    ProsecutorVerdict,
    Tier1Summary,
    format_debate_transcript,
)
from council.engine.horizons import TERMS
from council.seats.base import SeatVerdict, is_abstention

# Addendum A7: |implied - stated| beyond this is INCOHERENT_CONFIDENCE.
_COHERENCE_TOLERANCE = 0.15
# Weight discount applied to a seat flagged incoherent, for this deliberation only.
INCOHERENCE_WEIGHT_DISCOUNT = 0.30

_SYSTEM_PROMPT = """
You are the Prosecutor, "The Devil's Advocate" on a market-prediction
council. The council leans a way on three terms at once -- short (the next
week), medium (the next 3 months) and long (the next year and beyond). You
see everything: every Tier I seat's leans, the Bull/Bear debate so far, and
the direction the council is currently leaning on each term. Your job is to
attack those leans, whichever way they point.

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

You have veto power: a veto is shown to the reader as a warning beside the
terms you name in veto_terms (leave it empty to object to all three). Set
veto=true only when the case for a lean is genuinely undermined -- heavy
reliance on correlated evidence, multiple implausible targets propping up
the consensus, or a thesis pattern that would not have changed regardless
of the evidence. A well-supported lean with one minor flag does not warrant
a veto. target_direction is the direction you attack hardest.
"""


def detect_correlated_evidence(summaries: list[Tier1Summary]) -> list[str]:
    """Deterministic, not LLM judgment: any evidence source cited by 2+
    directional Tier I seats is correlated evidence by definition."""
    source_to_seats: dict[str, set[str]] = defaultdict(set)
    for s in summaries:
        if not s.directional:
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
        if is_abstention(v.vote):
            continue
        result = check_decomposition_coherence(seat_id, v)
        if result.has_decomposition and not result.coherent:
            results[seat_id] = result
    return results


def _format_lean(s: Tier1Summary, term: str) -> str:
    lean = s.term(term)
    if is_abstention(lean.vote):
        return f"{term} {lean.vote}"
    return f"{term} {lean.vote} p={lean.probability} disp={lean.dispersion}"


def _format_tier1(summaries: list[Tier1Summary]) -> str:
    return "\n".join(
        f"- [{s.seat_id}] data_quality={s.data_quality}: "
        + ", ".join(_format_lean(s, t) for t in TERMS)
        for s in summaries
    )


class ProsecutorSeat:
    id = "prosecutor"
    title = "The Devil's Advocate"

    async def review(
        self,
        tier1_summaries: list[Tier1Summary],
        debate_so_far: list[DebateArgument],
        plausibility_flags: dict[str, dict[str, str]],
        correlated_evidence: list[str],
        incoherent_decompositions: dict[str, CoherenceCheckResult],
        converging: dict[str, str],
        round_n: int,
        llm_client: LLMClient,
        model: str,
    ) -> ProsecutorVerdict | None:
        """`plausibility_flags` and `converging` are keyed by term;
        `incoherent_decompositions` covers the medium term, the only one a
        seat decomposes."""
        implausible = [
            f"{sid} ({term})"
            for term, flags in plausibility_flags.items()
            for sid, flag in flags.items()
            if flag == "IMPLAUSIBLE"
        ]
        incoherent_desc = [
            f"{sid}: stated {r.stated_probability}, decomposition implies {r.implied_probability}"
            for sid, r in incoherent_decompositions.items()
        ]
        leaning = "; ".join(f"{term}: {converging[term]}" for term in TERMS if term in converging)
        user_prompt = (
            f"Round {round_n}. The council currently leans -- {leaning}.\n\n"
            f"Tier I leans:\n{_format_tier1(tier1_summaries)}\n\n"
            f"Debate so far:\n{format_debate_transcript(debate_so_far)}\n\n"
            f"Correlated evidence (computed, not judgment):\n"
            + ("\n".join(f"- {c}" for c in correlated_evidence) or "- none found")
            + f"\n\nSeats with IMPLAUSIBLE price targets: {implausible or 'none'}\n\n"
            f"Seats with INCOHERENT medium-term decomposition (their own sub-probabilities "
            f"don't combine to their headline number, computed not judgment): "
            f"{incoherent_desc or 'none'}\n\n"
            "Attack the council's leans and give your verdict."
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
        except (SchemaRetryExhausted, LLMCallFailed):
            return None
