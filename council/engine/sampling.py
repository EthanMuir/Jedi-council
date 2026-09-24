"""Run-to-run dispersion is a first-class measurement (spec constraint #2):
LLMs produce materially different outputs across runs even under
deterministic decoding. Every seat is sampled N times and the *spread* of
its answers is recorded, then used to automatically discount confidence --
a seat that gives inconsistent answers should not get to keep its full
conviction."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from council.seats.base import SeatVerdict, is_abstention


@dataclass
class SampledSeatVerdict:
    seat_id: str
    samples: list[SeatVerdict]
    consensus_vote: str  # "BULLISH" | "BEARISH" | "NO_CONVICTION" | "NO_READ"
    dispersion: float  # 0..1, fraction of samples disagreeing with consensus_vote
    representative: SeatVerdict  # the verdict downstream aggregation should use


def _build_split(dispersion: float, sample_votes: list[str]) -> SeatVerdict:
    # The samples were real answers that disagreed -- a genuine "in between",
    # not a failure to read, so NO_CONVICTION rather than NO_READ.
    return SeatVerdict(
        vote="NO_CONVICTION",
        probability=0.5,
        expected_move_pct=0.0,
        thesis=(
            f"{len(sample_votes)} independent samples split across "
            f"{', '.join(sorted(set(sample_votes)))} with no majority -- "
            "run-to-run disagreement itself is the signal here."
        ),
        what_would_change_my_mind="A stable majority forming across repeated samples.",
        data_quality="PARTIAL",
        abstain_reason="run_to_run_disagreement",
    )


def aggregate_samples(seat_id: str, samples: list[SeatVerdict]) -> SampledSeatVerdict:
    """Majority vote across `samples` (ties -> NO_CONVICTION), dispersion = fraction
    of samples disagreeing with that majority, and a representative verdict
    whose probability is discounted toward 0.5 in proportion to dispersion."""
    if not samples:
        raise ValueError("cannot aggregate zero samples")

    votes = [s.vote for s in samples]
    counts = Counter(votes)
    max_count = max(counts.values())
    winners = [v for v, c in counts.items() if c == max_count]
    consensus_vote = winners[0] if len(winners) == 1 else "NO_CONVICTION"

    agreeing = [s for s in samples if s.vote == consensus_vote]
    n = len(samples)
    dispersion = round(1 - (len(agreeing) / n), 3)

    if is_abstention(consensus_vote):
        representative = agreeing[0] if agreeing else _build_split(dispersion, votes)
    else:
        sorted_agree = sorted(agreeing, key=lambda s: s.probability)
        median_sample = sorted_agree[(len(sorted_agree) - 1) // 2]
        discounted_p = round(0.5 + (median_sample.probability - 0.5) * (1 - dispersion), 3)
        representative = median_sample.model_copy(update={"probability": discounted_p})

    return SampledSeatVerdict(
        seat_id=seat_id,
        samples=samples,
        consensus_vote=consensus_vote,
        dispersion=dispersion,
        representative=representative,
    )
