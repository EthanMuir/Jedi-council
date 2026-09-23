"""Schemas for Tiers II-IV. These are orchestration-level structures, not
seat-isolation schemas: Bull/Bear/Prosecutor/Grand Master never touch
DataService, they only ever see Tier1Summary objects -- "all Tier I
verdicts (not raw data)", per spec section 3."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from council.seats.base import SeatVerdict


class Tier1Summary(BaseModel):
    seat_id: str
    title: str
    vote: Literal["BULLISH", "BEARISH", "NO_READ"]
    probability: float
    expected_move_pct: float
    thesis: str
    data_quality: Literal["GOOD", "PARTIAL", "POOR"]
    dispersion: float
    evidence_sources: list[str]


def summarize_tier1(seat_id: str, title: str, verdict: SeatVerdict, dispersion: float) -> Tier1Summary:
    return Tier1Summary(
        seat_id=seat_id,
        title=title,
        vote=verdict.vote,
        probability=verdict.probability,
        expected_move_pct=verdict.expected_move_pct,
        thesis=verdict.thesis,
        data_quality=verdict.data_quality,
        dispersion=dispersion,
        evidence_sources=[e.source for e in verdict.key_evidence],
    )


class DebateArgument(BaseModel):
    side: Literal["BULL", "BEAR"]
    round_n: int
    argument: str
    cites_seats: list[str] = Field(default_factory=list)
    rebuts: str | None = None


class ProsecutorFinding(BaseModel):
    category: Literal[
        "CORRELATED_EVIDENCE", "IMPLAUSIBLE_TARGET", "DATA_INDEPENDENT_THESIS", "OTHER"
    ]
    description: str


class ProsecutorVerdict(BaseModel):
    round_n: int
    target_direction: Literal["BULLISH", "BEARISH", "NONE"]
    findings: list[ProsecutorFinding] = Field(default_factory=list)
    veto: bool
    veto_reason: str | None = None


def summarize_dissent(summaries: list[Tier1Summary]) -> str:
    bulls = [s.seat_id for s in summaries if s.vote == "BULLISH"]
    bears = [s.seat_id for s in summaries if s.vote == "BEARISH"]
    abstains = [s.seat_id for s in summaries if s.vote == "NO_READ"]
    parts = []
    if bulls:
        parts.append(f"{len(bulls)} bullish ({', '.join(bulls)})")
    if bears:
        parts.append(f"{len(bears)} bearish ({', '.join(bears)})")
    if abstains:
        parts.append(f"{len(abstains)} abstained ({', '.join(abstains)})")
    return "; ".join(parts) if parts else "no seats participated"


def format_tier1_summaries(summaries: list[Tier1Summary]) -> str:
    return "\n".join(
        f"- [{s.seat_id}] {s.title}: {s.vote} (p={s.probability}, "
        f"data_quality={s.data_quality}, dispersion={s.dispersion}) -- {s.thesis}"
        for s in summaries
    )


def format_debate_transcript(debate: list[DebateArgument]) -> str:
    if not debate:
        return "(no debate yet)"
    return "\n".join(f"- [{d.side} round {d.round_n}] {d.argument}" for d in debate)


class GrandMasterVerdict(BaseModel):
    vote: Literal["BULLISH", "BEARISH", "NO_CONVICTION"]
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="REQUIRED. Your calibrated confidence in `vote`, 0-1 (use 0.5 for "
        "NO_CONVICTION). Should track the weighted council vote's own confidence unless "
        "the debate or Prosecutor findings gave you a specific reason to move it. Missing "
        "or malformed output here is rejected outright and this synthesis is discarded in "
        "favor of an uninformative NO_CONVICTION fallback -- don't leave it out.",
    )
    entry: float | None = None
    exit: float | None = None
    invalidation: float | None = None
    stop: float | None = None
    expected_move_pct: float = Field(
        description="REQUIRED (use 0.0 for NO_CONVICTION). Your own synthesized estimate "
        "of the expected magnitude of the underlying's price move over this horizon, as a "
        "percentage (e.g. 3.5 means +/-3.5%, not 0.035) -- weigh the Tier I seats' own "
        "estimates and the Reality Anchor's options-implied move, don't just copy one.",
    )
    # Hard rule (spec section 3, Tier IV): must report the STRUCTURE of
    # disagreement, not just a consensus number. Unanimity among correlated
    # agents is a warning sign, not a green light.
    dissent_summary: str
    correlated_evidence_warning: str | None = None
    reasoning: str
