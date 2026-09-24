"""SeatVerdict v2 (spec + Addendum A1), the data-isolation wrapper that makes
isolation a code-level property instead of a prompting convention, and the
Seat protocol every Council Member implements.

Isolation is enforced twice: once when a SeatContext is constructed (the
gathered data dict itself may not contain a field outside the seat's
allowlist -- catches a leaky `gather()`), and once on every read (catches a
seat trying to reach past its own context)."""
from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from council.engine.horizons import TERM_WINDOWS, TERMS


class DataIsolationError(Exception):
    """Raised the instant a seat's context is built or read with a field
    outside that seat's declared allowlist. This is THE architectural
    constraint of the whole system -- see JEDI_COUNCIL_SPEC.md section 0."""


class SeatContext:
    def __init__(
        self,
        seat_id: str,
        allowed: frozenset[str],
        data: dict[str, Any],
        *,
        ticker: str,
        as_of: Any,
    ):
        unknown = set(data) - allowed
        if unknown:
            raise DataIsolationError(
                f"Seat '{seat_id}' was gathered data outside its allowlist: "
                f"{sorted(unknown)} (allowed: {sorted(allowed)})"
            )
        self._seat_id = seat_id
        self._allowed = allowed
        self._data = data
        self.ticker = ticker
        self.as_of = as_of

    def __getitem__(self, key: str) -> Any:
        if key not in self._allowed:
            raise DataIsolationError(
                f"Seat '{self._seat_id}' requested forbidden field '{key}' "
                f"(allowed: {sorted(self._allowed)})"
            )
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        if key not in self._allowed:
            raise DataIsolationError(
                f"Seat '{self._seat_id}' requested forbidden field '{key}' "
                f"(allowed: {sorted(self._allowed)})"
            )
        return self._data.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self._allowed and key in self._data


class ComparisonClass(BaseModel):
    definition: str
    n_observations: int
    base_rate: float
    why_this_class: str


class DecompositionItem(BaseModel):
    sub_claim: str
    probability: float
    relation: Literal["AND", "OR", "CONDITIONAL"]


class MemoryLesson(BaseModel):
    lesson_id: str
    lesson: str
    from_date: str


def format_memory_context(memories: list["MemoryLesson"] | None) -> str:
    """Shared across every seat's prompt so live LLM calls actually see
    retrieved memory, not just have it bolted onto the output afterward."""
    if not memories:
        return ""
    lines = "\n".join(f"- [{m.from_date}] {m.lesson}" for m in memories)
    return (
        "\n\nRelevant lessons from your own history on this ticker (weigh these, "
        "don't ignore them, but don't let a past miss override what today's data "
        f"actually shows):\n{lines}\n"
    )


class EvidenceItem(BaseModel):
    claim: str
    source: str
    as_of: str


def _is_multiple_of_005(value: float) -> bool:
    scaled = value / 0.05
    return abs(scaled - round(scaled)) < 1e-6


# The two ways a seat can abstain, which count identically in every vote,
# weight and score:
# - NO_READ: the seat couldn't form a read at all -- the API call failed,
#   the answer came back malformed, or its data was missing/unusable.
# - NO_CONVICTION: the seat read its data fine and genuinely landed in the
#   middle (no edge either way, or its own samples split evenly).
ABSTENTIONS = frozenset({"NO_READ", "NO_CONVICTION"})


def is_abstention(vote: str) -> bool:
    return vote in ABSTENTIONS


class SeatVerdict(BaseModel):
    vote: Literal["BULLISH", "BEARISH", "NO_CONVICTION", "NO_READ"] = Field(
        description="BULLISH or BEARISH for a directional call. NO_CONVICTION when you "
        "read your data and it genuinely points neither way. NO_READ only when your data "
        "is missing or unusable -- you could not form a read at all."
    )

    # Addendum A1: three decimals, reject anything rounded to a multiple of
    # 0.05 for a directional call -- superforecaster-grade granularity is a
    # measured habit, not decoration.
    probability: float = Field(
        ge=0.0,
        le=1.0,
        description="REQUIRED for every vote, including NO_CONVICTION/NO_READ (use 0.5 there). For a "
        "directional vote (BULLISH/BEARISH) this must be a genuine probability estimate "
        "given to exactly three decimal places, and must NOT round to a multiple of 0.05 "
        "(0.700, 0.650, 0.500, etc. are all rejected) -- state real granularity, e.g. 0.632 "
        "or 0.714, not a round number rounded off. Both rules are enforced after generation: "
        "a violation is rejected outright and the call is wasted.",
    )

    comparison_class: ComparisonClass | None = None
    decomposition: list[DecompositionItem] = Field(default_factory=list)
    memory_applied: list[MemoryLesson] = Field(default_factory=list)

    entry: float | None = None
    exit: float | None = None
    invalidation: float | None = None
    expected_move_pct: float = Field(
        description="REQUIRED for every vote, including NO_CONVICTION/NO_READ (use 0.0 there). The "
        "expected magnitude of the underlying's price move over this horizon, as a "
        "percentage (e.g. 3.5 means +/-3.5%, not 0.035) -- a distribution width, not a "
        "directional target. Base it on whatever your seat's own data actually supports "
        "(realized volatility, an options-implied straddle, a historical base rate for "
        "similar setups); do not leave it at a placeholder value for a directional vote.",
    )

    # data_quality / what_would_change_my_mind / abstain_reason are declared
    # ahead of key_evidence/thesis on purpose (Task #67): a verbose model
    # can run out of room mid-tool-call and never reach later keys at all --
    # seen live as what_would_change_my_mind coming back as
    # `Field required [type=missing]`, not empty, meaning generation was cut
    # off before that key was ever started. Anthropic's tool use doesn't
    # hard-enforce the schema's required list server-side, so a truncated
    # call can still parse as syntactically valid JSON short of its later
    # keys. Putting the short, always-required fields first means a
    # truncation-prone field (thesis, free text; key_evidence, a
    # variable-length list) is the one left incomplete, not a field whose
    # absence fails validation outright.
    data_quality: Literal["GOOD", "PARTIAL", "POOR"]
    what_would_change_my_mind: str = Field(
        description="One or two sentences. Always include this field."
    )
    abstain_reason: str | None = Field(
        default=None,
        description="REQUIRED (a non-empty string) when vote is NO_CONVICTION or NO_READ -- "
        "for NO_CONVICTION, why the data points neither way; for NO_READ, what's missing or "
        "unusable about the data. Enforced after generation: an abstention with this left "
        "null is rejected outright and the call is wasted. Must be left null for a "
        "directional vote (BULLISH/BEARISH) -- do not use it as a hedge or caveat there.",
    )
    key_evidence: list[EvidenceItem] = Field(default_factory=list)
    thesis: str = Field(
        description="120 words or fewer. This is a hard limit enforced after "
        "generation -- a thesis over 120 words is rejected outright and the "
        "call is wasted. Be concise: state the read and the strongest reason "
        "for it, not every supporting detail."
    )
    # Set when this verdict is one term of a seat's three-term answer: why
    # the seat leans this way over this term specifically.
    term_rationale: str | None = None

    @field_validator("probability")
    @classmethod
    def _three_decimals(cls, v: float) -> float:
        if round(v, 3) != v:
            raise ValueError(f"probability {v} must be given to exactly three decimals")
        return v

    @field_validator("thesis")
    @classmethod
    def _thesis_word_limit(cls, v: str) -> str:
        word_count = len(v.split())
        if word_count > 120:
            raise ValueError(f"thesis is {word_count} words, must be <= 120")
        return v

    @model_validator(mode="after")
    def _cross_field_rules(self) -> "SeatVerdict":
        if self.vote in ABSTENTIONS:
            if not self.abstain_reason:
                raise ValueError(f"{self.vote} requires abstain_reason")
        else:
            if self.abstain_reason:
                raise ValueError("abstain_reason must be null unless the seat abstains")
            if self.comparison_class is None:
                raise ValueError(
                    "a directional vote requires a comparison_class; a seat "
                    "that cannot construct one should abstain instead"
                )
            if _is_multiple_of_005(self.probability):
                raise ValueError(
                    f"probability {self.probability} rounds to a multiple of "
                    "0.05 -- state a genuinely granular estimate"
                )
        return self


_TERM_RATIONALE_WORDS = 40


class TermCall(BaseModel):
    """One term of a seat's answer."""

    vote: Literal["BULLISH", "BEARISH", "NO_CONVICTION"] = Field(
        description="Lean BULLISH or BEARISH whenever the evidence tilts at all, even "
        "slightly. NO_CONVICTION only when the evidence for this term genuinely cancels out."
    )
    probability: float = Field(
        ge=0.0,
        le=1.0,
        description="How likely your vote is right over this term, to exactly three "
        "decimals. A weak lean sits near 0.5 (e.g. 0.532), a strong one well above "
        "(e.g. 0.741). Must not be a multiple of 0.05 (0.550, 0.600 ... are rejected). "
        "Use 0.5 for NO_CONVICTION.",
    )
    expected_move_pct: float = Field(
        description="Expected size of the price move over this term, as a percentage "
        "(3.5 means +/-3.5%). Use 0.0 for NO_CONVICTION."
    )
    rationale: str = Field(
        description=f"Why you lean this way over this term specifically. "
        f"{_TERM_RATIONALE_WORDS} words or fewer."
    )


class SeatAnswer(BaseModel):
    """What a Tier I seat's model fills in: one read of its data, with a
    lean for each of the three terms. Converted into three ordinary
    SeatVerdicts (to_multi_term) so voting, scoring and the Crypt keep
    working per term. Short, always-required fields come first so a
    truncated answer loses free text, not a required field (see the note on
    SeatVerdict)."""

    status: Literal["READ", "NO_READ"] = Field(
        description="READ if you could analyse your data. NO_READ only if your data is "
        "missing or unusable -- then leave the three terms out."
    )
    data_quality: Literal["GOOD", "PARTIAL", "POOR"]
    abstain_reason: str | None = Field(
        default=None,
        description="REQUIRED for NO_READ: what's missing or unusable. Leave null for READ.",
    )
    what_would_change_my_mind: str = Field(description="One or two sentences.")
    short: TermCall | None = Field(default=None, description=f"Short term: {TERM_WINDOWS['short']}.")
    medium: TermCall | None = Field(default=None, description=f"Medium term: {TERM_WINDOWS['medium']}.")
    long: TermCall | None = Field(
        default=None,
        description=f"Long term: {TERM_WINDOWS['long']}. You may reason years out; the call "
        "is checked after one year.",
    )
    comparison_class: ComparisonClass | None = Field(
        default=None,
        description="REQUIRED whenever any term is BULLISH or BEARISH: the reference class "
        "of similar past situations your leans are anchored to.",
    )
    decomposition: list[DecompositionItem] = Field(
        default_factory=list,
        description="Optional: sub-claims that combine into your MEDIUM-term probability.",
    )
    key_evidence: list[EvidenceItem] = Field(default_factory=list)
    thesis: str = Field(
        description="Your overall read across all three terms, 120 words or fewer."
    )

    @model_validator(mode="after")
    def _converts_cleanly(self) -> "SeatAnswer":
        if self.status == "READ" and any(getattr(self, t) is None for t in TERMS):
            raise ValueError("a READ answer must give a lean for all three terms: short, medium, long")
        if self.status == "NO_READ" and not self.abstain_reason:
            raise ValueError("NO_READ requires abstain_reason")
        try:
            self.to_multi_term()
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def to_multi_term(self) -> "MultiTermVerdict":
        if self.status == "NO_READ":
            return MultiTermVerdict.no_read(
                thesis=self.thesis,
                abstain_reason=self.abstain_reason or "no_read",
                data_quality=self.data_quality,
                what_would_change_my_mind=self.what_would_change_my_mind,
                key_evidence=self.key_evidence,
            )
        verdicts = {}
        for term in TERMS:
            call: TermCall = getattr(self, term)
            directional = call.vote != "NO_CONVICTION"
            verdicts[term] = SeatVerdict(
                vote=call.vote,
                probability=call.probability if directional else 0.5,
                expected_move_pct=call.expected_move_pct if directional else 0.0,
                comparison_class=self.comparison_class if directional else None,
                decomposition=self.decomposition if term == "medium" and directional else [],
                data_quality=self.data_quality,
                what_would_change_my_mind=self.what_would_change_my_mind,
                abstain_reason=None if directional else call.rationale,
                key_evidence=self.key_evidence,
                thesis=self.thesis,
                term_rationale=call.rationale,
            )
        return MultiTermVerdict(**verdicts)


class MultiTermVerdict(BaseModel):
    """A Tier I seat's answer, as one ordinary SeatVerdict per term."""

    short: SeatVerdict
    medium: SeatVerdict
    long: SeatVerdict

    def term(self, term: str) -> SeatVerdict:
        return getattr(self, term)

    @property
    def read(self) -> bool:
        return self.short.vote != "NO_READ"

    @classmethod
    def no_read(
        cls,
        *,
        thesis: str,
        abstain_reason: str,
        data_quality: Literal["GOOD", "PARTIAL", "POOR"] = "POOR",
        what_would_change_my_mind: str = "N/A",
        key_evidence: list[EvidenceItem] | None = None,
    ) -> "MultiTermVerdict":
        """The seat couldn't form a read (missing data, a failed call) --
        the same NO_READ for every term."""
        verdict = SeatVerdict(
            vote="NO_READ",
            probability=0.5,
            expected_move_pct=0.0,
            thesis=thesis[:500],
            what_would_change_my_mind=what_would_change_my_mind,
            data_quality=data_quality,
            abstain_reason=abstain_reason,
            key_evidence=key_evidence or [],
        )
        return cls(short=verdict, medium=verdict, long=verdict)

    def with_memories(self, memories: list[MemoryLesson]) -> "MultiTermVerdict":
        if not memories or not self.read:
            return self
        return MultiTermVerdict(
            **{t: self.term(t).model_copy(update={"memory_applied": memories}) for t in TERMS}
        )


class Seat(Protocol):
    id: str
    title: str
    allowed_data: frozenset[str]

    async def gather(self, data_service: Any, ticker: str, as_of: Any) -> SeatContext: ...

    async def deliberate(
        self,
        ctx: SeatContext,
        llm_client: Any,
        round_n: int = 0,
        sample_index: int = 0,
        memories: list[MemoryLesson] | None = None,
        peer_summaries: list[dict] | None = None,
    ) -> MultiTermVerdict: ...
