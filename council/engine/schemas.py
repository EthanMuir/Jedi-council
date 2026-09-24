"""Schemas for Tiers II-IV. These are orchestration-level structures, not
seat-isolation schemas: Bull/Bear/Prosecutor/Grand Master never touch
DataService, they only ever see Tier1Summary objects -- "all Tier I
verdicts (not raw data)", per spec section 3."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from council.engine.horizons import TERM_NAMES, TERMS
from council.seats.base import MultiTermVerdict, is_abstention

Term = Literal["short", "medium", "long"]


class TermLean(BaseModel):
    vote: Literal["BULLISH", "BEARISH", "NO_CONVICTION", "NO_READ"]
    probability: float
    expected_move_pct: float
    dispersion: float
    rationale: str | None = None


class Tier1Summary(BaseModel):
    seat_id: str
    title: str
    short: TermLean
    medium: TermLean
    long: TermLean
    thesis: str
    data_quality: Literal["GOOD", "PARTIAL", "POOR"]
    evidence_sources: list[str]

    def term(self, term: str) -> TermLean:
        return getattr(self, term)

    @property
    def read(self) -> bool:
        return self.short.vote != "NO_READ"

    @property
    def directional(self) -> bool:
        return any(not is_abstention(self.term(t).vote) for t in TERMS)


def summarize_tier1(
    seat_id: str, title: str, verdict: MultiTermVerdict, dispersion: dict[str, float]
) -> Tier1Summary:
    def lean(term: str) -> TermLean:
        v = verdict.term(term)
        return TermLean(
            vote=v.vote,
            probability=v.probability,
            expected_move_pct=v.expected_move_pct,
            dispersion=dispersion.get(term, 0.0),
            rationale=v.term_rationale,
        )

    first = verdict.short
    return Tier1Summary(
        seat_id=seat_id,
        title=title,
        short=lean("short"),
        medium=lean("medium"),
        long=lean("long"),
        thesis=first.thesis,
        data_quality=first.data_quality,
        evidence_sources=[e.source for e in first.key_evidence],
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
    veto_terms: list[Term] = Field(
        default_factory=list,
        description="When veto is true: which terms (short, medium, long) the objection "
        "applies to. Leave empty to object to all three.",
    )

    def vetoes(self, term: str) -> bool:
        return self.veto and (not self.veto_terms or term in self.veto_terms)


def summarize_dissent(summaries: list[Tier1Summary], term: str) -> str:
    groups: dict[str, list[str]] = {"BULLISH": [], "BEARISH": [], "NO_CONVICTION": [], "NO_READ": []}
    for s in summaries:
        groups[s.term(term).vote].append(s.seat_id)
    parts = []
    if groups["BULLISH"]:
        parts.append(f"{len(groups['BULLISH'])} bullish ({', '.join(groups['BULLISH'])})")
    if groups["BEARISH"]:
        parts.append(f"{len(groups['BEARISH'])} bearish ({', '.join(groups['BEARISH'])})")
    if groups["NO_CONVICTION"]:
        parts.append(f"{len(groups['NO_CONVICTION'])} dead even ({', '.join(groups['NO_CONVICTION'])})")
    if groups["NO_READ"]:
        parts.append(f"{len(groups['NO_READ'])} couldn't read ({', '.join(groups['NO_READ'])})")
    return "; ".join(parts) if parts else "no seats participated"


def _format_lean(lean: TermLean) -> str:
    if lean.vote == "NO_READ":
        return "no read"
    if lean.vote == "NO_CONVICTION":
        return "dead even"
    return f"{lean.vote} p={lean.probability} move {lean.expected_move_pct}%"


def format_tier1_summaries(summaries: list[Tier1Summary]) -> str:
    lines = []
    for s in summaries:
        if not s.read:
            lines.append(f"- [{s.seat_id}] {s.title}: NO READ -- {s.thesis}")
            continue
        leans = "; ".join(f"{t}: {_format_lean(s.term(t))}" for t in TERMS)
        lines.append(
            f"- [{s.seat_id}] {s.title} (data {s.data_quality}): {leans} -- {s.thesis}"
        )
    return "\n".join(lines)


def format_debate_transcript(debate: list[DebateArgument]) -> str:
    if not debate:
        return "(no debate yet)"
    return "\n".join(f"- [{d.side} round {d.round_n}] {d.argument}" for d in debate)


_NOTE_WORDS = 60


class GrandMasterSynthesis(BaseModel):
    """The Grand Master explains the council's three positions; it never
    moves them. The positions themselves are computed from the seats'
    weighted leans (council/engine/aggregation.py), so a weak lean stays
    visibly weak no matter how the synthesis reads."""

    headline: str = Field(
        description="One plain-English sentence (25 words or fewer) summing up the "
        "picture across all three terms, e.g. 'Leaning bullish over the next year, but "
        "the next week is close to a coin flip.'"
    )
    short: str = Field(
        description=f"{TERM_NAMES['short']}: what drives the lean and how much to trust "
        f"it. {_NOTE_WORDS} words or fewer."
    )
    medium: str = Field(description=f"{TERM_NAMES['medium']}: same. {_NOTE_WORDS} words or fewer.")
    long: str = Field(description=f"{TERM_NAMES['long']}: same. {_NOTE_WORDS} words or fewer.")
    # Hard rule (spec section 3, Tier IV): must report the STRUCTURE of
    # disagreement, not just a consensus number. Unanimity among correlated
    # agents is a warning sign, not a green light.
    dissent_summary: str = Field(
        description="Who disagreed, on which terms, and why -- not a restated vote tally."
    )
    correlated_evidence_warning: str | None = Field(
        default=None,
        description="If correlated evidence was flagged: what it means for trust in "
        "the leans. Otherwise null.",
    )
    reasoning: str = Field(description="150 words or fewer.")

    def note(self, term: str) -> str:
        return getattr(self, term)
