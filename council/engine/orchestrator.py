"""The full deliberation pipeline, Phases A-G (spec section 4). Every run
covers all three terms at once -- short (the next week), medium (the next 3
months), long (the next year and beyond) -- and every seat gives a lean on
each in a single call. Everything runs in memory; the Crypt is written
once, at the very end (Phase G): one row per term, tied together by a
run_id, because the immutability trigger makes a later UPDATE impossible --
see council/crypt/ledger.py.

Phase A  Blind round: all Tier I seats, isolated, no peer visibility, each
         sampled N times (dispersion capture), aggregated per term.
Phase B  Reality Anchor per term: Base-Rate Keeper (+ the Oracle of
         Options' implied move, short term only) establish the plausible
         distribution; every seat's expected move is checked against it.
Phase C  Debate: Bull vs Bear, N rounds, Prosecutor intervenes after each --
         once for the whole run, arguing across the terms.
Phase D  Council position per term: the weighted average of every seat's
         lean -- term competence x data quality x plausibility x coherence
         x calibration.
Phase E  Warnings per term: thin participation, implausible targets, an
         edge that doesn't clear costs, a Prosecutor objection. They sit
         next to the term's position; they never override it.
Phase F  Synthesis: the Grand Master explains the three positions.
Phase G  Crypt write.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Awaitable, Callable

from council.calibration.releases import live_weights
from council.config import Settings, as_lite
from council.crypt.db import connect, get_open_tickers
from council.crypt.ledger import write_prediction, write_seat_vote
from council.data.cache import DiskCache
from council.data.providers.alpha_vantage import AlphaVantageCongressProvider
from council.data.providers.fixtures import FixtureProvider
from council.data.providers.fred import FREDProvider
from council.data.providers.sec_edgar import SECEdgarProvider
from council.data.providers.yfinance_provider import YFinanceProvider
from council.data.service import DataService
from council.engine.aggregation import (
    DATA_QUALITY_MULTIPLIER,
    CouncilPosition,
    council_position,
    extremize,
    p_bullish,
)
from council.engine.base_rate import RealityAnchor, check_plausibility, compute_reality_anchor
from council.engine.price_target import price_target, range_record, term_sigma
from council.engine.cost_auditor import CostAuditResult, audit
from council.engine.horizons import TERM_NAMES, TERM_WINDOWS, TERMS, competence, resolve_at_for
from council.engine.llm_client import LLMClient
from council.engine.risk_warden import RiskSizing, size_position
from council.engine.routing import planned_run_mode, resolve_route
from council.engine.sampling import SampledSeatVerdict, aggregate_samples
from council.engine.schemas import (
    DebateArgument,
    GrandMasterSynthesis,
    ProsecutorVerdict,
    Tier1Summary,
    summarize_dissent,
    summarize_tier1,
)
from council.memory.store import MemoryStore, to_seat_memory_lesson
from council.seats.advocates import BearAdvocateSeat, BullAdvocateSeat
from council.seats.analyst_ratings import AnalystRatingsSeat
from council.seats.base import MultiTermVerdict, SeatVerdict, is_abstention
from council.seats.catalyst_seer import CatalystSeerSeat
from council.seats.cross_market import CrossMarketSeat
from council.seats.estimate_scribe import EstimateScribeSeat
from council.seats.flow_cartographer import FlowCartographerSeat
from council.seats.fundamentalist import FundamentalistSeat
from council.seats.grand_master import GrandMasterSeat
from council.seats.insider_reader import InsiderReaderSeat
from council.seats.macro_sage import MacroSageSeat
from council.seats.oracle_options import OracleOptionsSeat
from council.seats.prosecutor import (
    INCOHERENCE_WEIGHT_DISCOUNT,
    ProsecutorSeat,
    detect_correlated_evidence,
    detect_incoherent_decompositions,
)
from council.seats.senate_watcher import SenateWatcherSeat
from council.seats.structure_archivist import StructureArchivistSeat
from council.seats.technician import TechnicianSeat

# Full Tier I roster (spec section 3), in roster order.
TIER_I_SEATS = [
    TechnicianSeat(),
    FundamentalistSeat(),
    CatalystSeerSeat(),
    InsiderReaderSeat(),
    SenateWatcherSeat(),
    FlowCartographerSeat(),
    OracleOptionsSeat(),
    MacroSageSeat(),
    CrossMarketSeat(),
    EstimateScribeSeat(),
    AnalystRatingsSeat(),
    StructureArchivistSeat(),
]


@dataclass
class SeatResult:
    seat_id: str
    title: str
    verdicts: MultiTermVerdict
    dispersion: dict[str, float]
    sample_count: int
    # The seat's leans across the terms, averaged by how much each term
    # counts for this seat -- what colours its chair.
    lean: str
    lean_p_bullish: float | None


@dataclass
class TermWarning:
    kind: str  # "participation" | "base_rate" | "cost" | "prosecutor" | "earnings"
    message: str


EARNINGS_WARNING_DAYS = 7


def earnings_notice(next_earnings: date | None, today: date) -> dict | None:
    """The "earnings are due" banner, when they're within a week of the run."""
    if next_earnings is None:
        return None
    days = (next_earnings - today).days
    if not 0 <= days <= EARNINGS_WARNING_DAYS:
        return None
    when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
    label = f"{next_earnings:%a %b} {next_earnings.day}"
    return {
        "date": next_earnings.isoformat(),
        "days": days,
        "message": (
            f"Earnings are due {when} ({label}). The price often jumps on the report in a way "
            "no one can call, so treat next week's lean with extra caution."
        ),
    }


@dataclass
class SeatTick:
    """One seat's mark on a term's bearish<->bullish bar."""

    seat_id: str
    title: str
    vote: str
    p_bullish: float | None  # None: the seat couldn't read its data
    weight: float


@dataclass
class TermResult:
    term: str
    name: str
    window: str
    prediction_id: str
    resolve_at: datetime
    blind: CouncilPosition
    position: CouncilPosition
    p_raw: float
    p_extremized: float
    expected_move_pct: float
    entry: float | None
    exit: float | None
    invalidation: float | None
    reality_anchor: RealityAnchor
    plausibility_flags: dict[str, str]
    cost_audit: CostAuditResult
    warnings: list[TermWarning]
    ticks: list[SeatTick]
    note: str
    dissent_summary: str


@dataclass
class DeliberationResult:
    run_id: str
    ticker: str
    as_of: datetime
    price_at_prediction: float
    seat_results: list[SeatResult]
    terms: dict[str, TermResult]
    debate_transcript: list[DebateArgument]
    prosecutor_verdicts: list[ProsecutorVerdict]
    correlated_evidence: list[str]
    incoherent_decompositions: dict
    risk_sizing: RiskSizing
    synthesis: GrandMasterSynthesis
    call_log: list = field(default_factory=list)
    run_mode: str = "paid"  # "free" | "paid" | "sample" -- see _run_mode
    run_shape: str = "full"  # "full" | "lite"


class TickerNotFound(ValueError):
    """No recent price data for the ticker -- a typo, a delisted stock, or a
    symbol that doesn't exist. Raised before any seat runs, so a bad ticker
    costs nothing."""


def _run_mode(settings: Settings, call_log: list) -> str:
    """How the Archives label this run, so cheap free-tier runs never mix
    into (or drag down) the paid council's track record: "sample" when no
    model was called at all, "free" when every answer came from a free
    tier, "paid" as soon as any answer cost money."""
    if settings.resolved_no_llm:
        return "sample"
    answered = [c for c in call_log if c.success]
    if answered and all(c.free_tier for c in answered):
        return "free"
    return "paid"


# The congress seat's only source is Alpha Vantage's CONGRESS_TRADES (free
# key, #106). Without that key it returns NO_READ on every call, so
# counting it toward the participation warning would make every run's real
# evidence look thinner than it is.
_STRUCTURALLY_NO_DATA_SEATS = frozenset({"senate_watcher"})


def _no_data_seats(settings: Settings) -> frozenset[str]:
    """Seats that can't possibly read on this server, given its keys."""
    return frozenset() if settings.alpha_vantage_api_key else _STRUCTURALLY_NO_DATA_SEATS


def _eligible_weight(seat_ids, term: str, no_data=_STRUCTURALLY_NO_DATA_SEATS) -> float:
    """Competence-weighted denominator for the participation warning (Task
    #76): a seat that barely counts on this term (the Fundamentalist on the
    short term) missing its data matters less than one that counts fully
    (the Technician on the short term). Excludes `no_data` seats -- they
    can't contribute regardless."""
    return round(sum(competence(sid, term) for sid in seat_ids if sid not in no_data), 4)


def _read_weight(
    verdicts_by_id: dict[str, SeatVerdict], term: str, no_data=_STRUCTURALLY_NO_DATA_SEATS
) -> float:
    """Competence-weighted numerator: every seat that could read its data
    on this term -- a dead-even lean is still a read."""
    return round(
        sum(
            competence(sid, term)
            for sid, v in verdicts_by_id.items()
            if v.vote != "NO_READ" and sid not in no_data
        ),
        4,
    )


def seat_lean(seat_id: str, verdict: MultiTermVerdict) -> tuple[str, float | None]:
    """A seat's overall lean across the terms, each weighted by how much it
    counts for this seat."""
    weighted_sum = weight_total = 0.0
    for term in TERMS:
        p = p_bullish(verdict.term(term))
        if p is None:
            continue
        w = competence(seat_id, term)
        weighted_sum += w * p
        weight_total += w
    if weight_total == 0:
        return "NO_READ", None
    p = round(weighted_sum / weight_total, 3)
    if p == 0.5:
        return "NO_CONVICTION", p
    return ("BULLISH" if p > 0.5 else "BEARISH"), p


def _term_expected_move(
    verdicts: dict[str, SeatVerdict], weights: dict[str, float], anchor: RealityAnchor
) -> float:
    """The options market's implied move when there is one (short term),
    otherwise the leaning seats' expected moves averaged by weight, falling
    back to the stock's ATR-implied range."""
    if anchor.options_implied_move_pct:
        return round(abs(anchor.options_implied_move_pct), 2)
    moves = [
        (weights[sid], abs(v.expected_move_pct))
        for sid, v in verdicts.items()
        if not is_abstention(v.vote) and weights.get(sid, 0) > 0 and v.expected_move_pct
    ]
    if moves:
        return round(sum(w * m for w, m in moves) / sum(w for w, _ in moves), 2)
    return anchor.atr_implied_range_pct or 0.0


def _position_payload(position: CouncilPosition) -> dict:
    return dataclasses.asdict(position)


def _fallback_synthesis(
    tier1_summaries: list[Tier1Summary],
    positions: dict[str, CouncilPosition],
    warnings: dict[str, list[TermWarning]],
    correlated_evidence: list[str],
) -> GrandMasterSynthesis:
    """Used when the Grand Master's own answer fails after retries: plain
    notes built from the positions themselves, so the run still reads."""

    def note(term: str) -> str:
        p = positions[term]
        text = (
            f"{p.lean_label}: {p.p_bullish:.0%} chance of rising, from "
            f"{p.seats_counted} seats' leans."
        )
        if warnings[term]:
            text += " " + " ".join(w.message for w in warnings[term])
        return text

    return GrandMasterSynthesis(
        headline="; ".join(f"{TERM_NAMES[t]}: {positions[t].lean_label.lower()}" for t in TERMS) + ".",
        short=note("short"),
        medium=note("medium"),
        long=note("long"),
        dissent_summary="; ".join(
            f"{TERM_NAMES[t]}: {summarize_dissent(tier1_summaries, t)}" for t in TERMS
        ),
        correlated_evidence_warning="; ".join(correlated_evidence) if correlated_evidence else None,
        reasoning="The Grand Master's own synthesis failed after retries, so these notes "
        "are built directly from the council's positions.",
        plain_headline="; ".join(f"{TERM_NAMES[t]}: {plain_lean(positions[t])}" for t in TERMS) + ".",
        plain_short=f"{plain_lean(positions['short'])}.",
        plain_medium=f"{plain_lean(positions['medium'])}.",
        plain_long=f"{plain_lean(positions['long'])}.",
    )


def plain_lean(position: CouncilPosition) -> str:
    """'Leaning up', 'Barely down', 'Even' -- a position in everyday words."""
    if position.seats_counted == 0 or position.lean_label.lower() in ("dead even", "no conviction"):
        return "Even: no lean either way"
    return (
        position.lean_label.replace("bullish", "up").replace("Bullish", "Up")
        .replace("bearish", "down").replace("Bearish", "Down")
    )


def build_data_service(settings: Settings) -> DataService:
    use_sample_data = settings.resolved_use_data_fixtures
    cache = DiskCache(settings.cache_db_path, namespace="sample" if use_sample_data else "")
    providers = []
    if use_sample_data:
        providers.append(FixtureProvider())
    else:
        # YFinance first, not last: it's free with no hard daily cap, and it
        # genuinely covers OHLCV, news, and options.
        providers.append(YFinanceProvider())
        # SEC EDGAR next, also free and keyless: the authoritative source
        # for insider transactions / SEC filings.
        providers.append(SECEdgarProvider(settings.resolved_sec_edgar_user_agent))
        # FRED next -- the only remaining source for macro data now that
        # Alpha Vantage has been pulled from this chain entirely (Task #77:
        # its free tier's 25-requests/day cap made it structurally unusable
        # -- a single macro_sage gather() alone burned 7 of those 25 -- and
        # its options endpoints (REALTIME_OPTIONS / REALTIME_PUT_CALL_RATIO)
        # require a $199.99+/month plan, so fetch_option_chain never worked
        # on a free key regardless of quota). Without a FRED key, macro_sage
        # has no live source at all and abstains every run (the same
        # graceful per-seat failure path senate_watcher already uses for
        # its own data gap) -- get a free one at
        # fred.stlouisfed.org, no daily cap, 120 req/min.
        if settings.fred_api_key:
            providers.append(FREDProvider(settings.fred_api_key))
        # Congressional trades only -- see AlphaVantageCongressProvider.
        if settings.alpha_vantage_api_key:
            providers.append(AlphaVantageCongressProvider(settings.alpha_vantage_api_key))
    return DataService(providers=providers, cache=cache)


async def run_deliberation(
    ticker: str,
    settings: Settings,
    as_of: datetime | None = None,
    progress: Callable[[str, dict], Awaitable[None]] | None = None,
    context: str | None = None,
    lite: bool = False,
) -> DeliberationResult:
    """`lite` runs one sample per seat and one debate round (config.LITE_RUN).

    If every provider the run can use hits a daily limit or runs out of
    credit, RunStopped is raised before the Crypt write -- a half-finished
    run is never saved or scored.

    `progress`, when given, is awaited as `progress(event, payload)` at
    each phase transition and seat completion -- purely additive, no
    caller that omits it (the CLI, every existing test) observes any
    behaviour change. This is what lets the UI show the deliberation as a
    spectacle instead of a spinner: seats illuminate as they actually
    finish, not all at once when the whole pipeline returns."""

    # What the live page showed, kept so a saved run can be reopened in
    # full later (the History page) -- not only its positions and notes.
    replay: dict = {"seats": [], "debate": [], "reality_anchor": None, "risk": None, "earnings": None}

    async def emit(event: str, payload: dict) -> None:
        if event == "seat_result":
            replay["seats"].append(payload)
        elif event == "debate_round":
            replay["debate"].append(payload)
        elif event == "phase_b_reality_anchor":
            replay["reality_anchor"] = payload
        elif event == "risk_warden":
            replay["risk"] = payload
        elif event == "earnings_soon":
            replay["earnings"] = payload
        if progress:
            await progress(event, payload)

    if lite:
        settings = as_lite(settings)
    run_shape = "lite" if lite else "full"
    as_of = as_of or datetime.utcnow()
    data_service = build_data_service(settings)

    async def notice(message: str) -> None:
        await emit("notice", {"message": message})

    async def waiting(seat_id: str, provider: str, seconds: float) -> None:
        await emit(
            "seat_stage",
            {"seat_id": seat_id, "stage": "waiting", "provider": provider, "resume_in": round(seconds)},
        )

    async def queued(seat_id: str, provider: str, seconds: float) -> None:
        await emit(
            "seat_stage",
            {"seat_id": seat_id, "stage": "queued", "provider": provider, "resume_in": round(seconds)},
        )

    llm_client = LLMClient(settings, on_notice=notice, on_wait=waiting, on_queue=queued)

    # Checked first, before any seat gathers data or calls a model: a ticker
    # with no recent prices can't be predicted, and finding that out after
    # Phase A used to cost a full run's worth of API calls. Ten days of
    # lookback so a long weekend or holiday doesn't read as "not found".
    try:
        price_series = await data_service.get_ohlcv(ticker, as_of=as_of, lookback_days=10)
    except RuntimeError:
        price_series = None
    if price_series is None or not price_series.bars:
        raise TickerNotFound(
            f'Couldn\'t find any recent price data for "{ticker}". Check the ticker '
            "symbol is right (e.g. NVDA for Nvidia, MCD for McDonald's). If it is, the "
            "price feed may be briefly down -- try again in a few minutes. Nothing was "
            "run or charged."
        )
    price_at_prediction = price_series.bars[-1].close

    # Earnings in the next week make the short term close to a coin flip:
    # the price jumps on the report in a way no seat can call.
    next_earnings = await data_service.get_next_earnings(ticker, as_of)
    earnings_soon = earnings_notice(next_earnings, as_of.date())
    if earnings_soon:
        await emit("earnings_soon", earnings_soon)

    semaphore = asyncio.Semaphore(settings.max_concurrent_llm_calls)
    conn = connect(settings.council_db_path)

    # ---- Phase A: blind round -------------------------------------------
    seat_ids = [s.id for s in TIER_I_SEATS]
    # Track-record weights come only from a release the owner reviewed and
    # published (council/calibration/releases.py), for this run's tier.
    calibration_weights = live_weights(settings.settings_db_path, planned_run_mode(settings))
    memory = MemoryStore(
        conn, cap_per_seat=settings.memory_cap_per_seat, account=settings.council_account
    )

    n_samples = settings.n_samples_per_seat

    async def deliberate_one(seat, ctx, sample_index, memory_lessons) -> MultiTermVerdict:
        async with semaphore:
            answer = await seat.deliberate(
                ctx, llm_client, sample_index=sample_index, memories=memory_lessons
            )
            return answer.with_memories(memory_lessons)

    async def run_seat(seat) -> tuple:
        try:
            await emit("seat_stage", {"seat_id": seat.id, "stage": "gathering"})
            async with semaphore:
                ctx = await seat.gather(data_service, ticker, as_of)
            retrieved = memory.retrieve(seat_id=seat.id, ticker=ticker, as_of=as_of, limit=5)
            memory_lessons = [to_seat_memory_lesson(e) for e in retrieved]

            # samples_done is only ever mutated between awaits inside this
            # single coroutine's event-loop turn, never truly in parallel --
            # asyncio is cooperative, so this plain counter is safe to share
            # across all n_samples concurrent calls without a lock, and each
            # sample still fires its own completion event the instant it
            # lands rather than waiting for its siblings.
            samples_done = 0

            async def deliberate_one_tracked(i):
                nonlocal samples_done
                answer = await deliberate_one(seat, ctx, i, memory_lessons)
                samples_done += 1
                await emit(
                    "seat_stage",
                    {
                        "seat_id": seat.id,
                        "stage": "deliberating",
                        "samples_done": samples_done,
                        "samples_total": n_samples,
                    },
                )
                return answer

            await emit(
                "seat_stage",
                {
                    "seat_id": seat.id,
                    "stage": "deliberating",
                    "samples_done": 0,
                    "samples_total": n_samples,
                },
            )
            answers = await asyncio.gather(
                *(deliberate_one_tracked(i) for i in range(n_samples))
            )
            sampled = {
                t: aggregate_samples(seat.id, [a.term(t) for a in answers]) for t in TERMS
            }
        except Exception as exc:  # noqa: BLE001 -- one seat's data/LLM failure
            # must never take down the other eleven. NO_READ is the honest
            # answer for "infrastructure failed before a read could be formed".
            failed = MultiTermVerdict.no_read(
                thesis=f"Seat failed before producing a verdict: {exc}",
                abstain_reason="seat_infrastructure_failure",
            )
            sampled = {
                t: SampledSeatVerdict(
                    seat_id=seat.id,
                    samples=[failed.term(t)],
                    consensus_vote="NO_READ",
                    dispersion=0.0,
                    representative=failed.term(t),
                )
                for t in TERMS
            }
        verdicts = MultiTermVerdict(**{t: sampled[t].representative for t in TERMS})
        lean, lean_p = seat_lean(seat.id, verdicts)
        result = SeatResult(
            seat_id=seat.id,
            title=seat.title,
            verdicts=verdicts,
            dispersion={t: sampled[t].dispersion for t in TERMS},
            sample_count=len(sampled["short"].samples),
            lean=lean,
            lean_p_bullish=lean_p,
        )
        await emit(
            "seat_result",
            {
                "seat_id": seat.id,
                "title": seat.title,
                "vote": lean,
                "lean_p_bullish": lean_p,
                "data_quality": verdicts.short.data_quality,
                "thesis": verdicts.short.thesis,
                "key_evidence": [
                    {"claim": e.claim, "source": e.source, "as_of": e.as_of}
                    for e in verdicts.short.key_evidence
                ],
                "what_would_change_my_mind": verdicts.short.what_would_change_my_mind,
                "terms": {
                    t: {
                        "vote": verdicts.term(t).vote,
                        "probability": verdicts.term(t).probability,
                        "p_bullish": p_bullish(verdicts.term(t)),
                        "expected_move_pct": verdicts.term(t).expected_move_pct,
                        "dispersion": sampled[t].dispersion,
                        "rationale": verdicts.term(t).term_rationale
                        or verdicts.term(t).abstain_reason,
                    }
                    for t in TERMS
                },
            },
        )
        return result

    seat_results: list[SeatResult] = await asyncio.gather(*(run_seat(seat) for seat in TIER_I_SEATS))
    llm_client.raise_if_stopped()

    by_id: dict[str, SeatResult] = {r.seat_id: r for r in seat_results}

    def term_verdicts(term: str) -> dict[str, SeatVerdict]:
        return {sid: r.verdicts.term(term) for sid, r in by_id.items()}

    blind = {t: council_position(term_verdicts(t)) for t in TERMS}
    await emit("phase_a_complete", {"terms": {t: _position_payload(blind[t]) for t in TERMS}})

    # ---- Phase B: Reality Anchor, per term ----------------------------------
    anchor_series = await data_service.get_ohlcv(ticker, as_of=as_of, lookback_days=730)
    oracle = by_id.get("oracle_options")
    # The option chain's nearest expiries speak to the next week, not to
    # months or years -- only the short term is anchored to it.
    options_implied_move_pct = (
        oracle.verdicts.short.expected_move_pct
        if oracle and not is_abstention(oracle.verdicts.short.vote)
        else None
    )
    reality_anchors = {
        t: compute_reality_anchor(
            anchor_series.bars, t, options_implied_move_pct if t == "short" else None
        )
        for t in TERMS
    }
    plausibility_flags = {
        t: {
            sid: check_plausibility(v.expected_move_pct, reality_anchors[t])
            for sid, v in term_verdicts(t).items()
            if not is_abstention(v.vote)
        }
        for t in TERMS
    }
    await emit(
        "phase_b_reality_anchor",
        {
            "terms": {
                t: {
                    "max_plausible_move_pct": reality_anchors[t].max_plausible_move_pct,
                    "hit_rate_up": reality_anchors[t].hit_rate_up,
                    "options_implied_move_pct": reality_anchors[t].options_implied_move_pct,
                    "plausibility_flags": plausibility_flags[t],
                }
                for t in TERMS
            }
        },
    )

    # ---- Phase C: Debate ---------------------------------------------------
    tier1_summaries = [
        summarize_tier1(r.seat_id, r.title, r.verdicts, r.dispersion) for r in seat_results
    ]
    correlated_evidence = detect_correlated_evidence(tier1_summaries)
    # Seats decompose their medium-term probability only.
    incoherent_decompositions = detect_incoherent_decompositions(term_verdicts("medium"))
    converging = {t: f"{blind[t].lean_label} ({blind[t].p_bullish:.1%} chance of rising)" for t in TERMS}

    bull, bear, prosecutor = BullAdvocateSeat(), BearAdvocateSeat(), ProsecutorSeat()
    debate_transcript: list[DebateArgument] = []
    prosecutor_verdicts: list[ProsecutorVerdict] = []
    prior_bull: DebateArgument | None = None
    prior_bear: DebateArgument | None = None

    for round_n in range(1, settings.debate_rounds + 1):
        async with semaphore:
            bull_arg, bear_arg = await asyncio.gather(
                bull.argue(tier1_summaries, round_n, prior_bear, llm_client, settings.seat_model),
                bear.argue(tier1_summaries, round_n, prior_bull, llm_client, settings.seat_model),
            )
        if bull_arg:
            debate_transcript.append(bull_arg)
            prior_bull = bull_arg
        if bear_arg:
            debate_transcript.append(bear_arg)
            prior_bear = bear_arg

        async with semaphore:
            pv = await prosecutor.review(
                tier1_summaries,
                debate_transcript,
                plausibility_flags,
                correlated_evidence,
                incoherent_decompositions,
                converging,
                round_n,
                llm_client,
                settings.synthesis_model,
            )
        if pv:
            prosecutor_verdicts.append(pv)

        await emit(
            "debate_round",
            {
                "round_n": round_n,
                "bull_argument": bull_arg.argument if bull_arg else None,
                "bear_argument": bear_arg.argument if bear_arg else None,
                "prosecutor_veto": pv.veto if pv else False,
                "prosecutor_veto_terms": (pv.veto_terms or list(TERMS)) if pv and pv.veto else [],
                "prosecutor_findings": [f.description for f in pv.findings] if pv else [],
            },
        )

    llm_client.raise_if_stopped()

    # ---- Phase D: the council's position on each term -------------------------
    weights: dict[str, dict[str, float]] = {}
    for t in TERMS:
        weights[t] = {}
        for sid, v in term_verdicts(t).items():
            if v.vote == "NO_READ":
                continue
            plausibility_multiplier = 0.5 if plausibility_flags[t].get(sid) == "IMPLAUSIBLE" else 1.0
            coherence_multiplier = (
                1 - INCOHERENCE_WEIGHT_DISCOUNT
                if t == "medium" and sid in incoherent_decompositions
                else 1.0
            )
            weights[t][sid] = round(
                competence(sid, t)
                * DATA_QUALITY_MULTIPLIER.get(v.data_quality, 0.5)
                * plausibility_multiplier
                * coherence_multiplier
                * calibration_weights.get(t, {}).get(sid, 1.0),
                4,
            )
    positions = {t: council_position(term_verdicts(t), weights[t]) for t in TERMS}
    p_raw = {t: positions[t].p_bullish for t in TERMS}
    p_extremized = {t: extremize(p_raw[t], settings.extremize_alpha) for t in TERMS}
    expected_moves = {
        t: _term_expected_move(term_verdicts(t), weights[t], reality_anchors[t]) for t in TERMS
    }
    await emit(
        "phase_d_weighted_vote",
        {
            "terms": {
                t: {
                    **_position_payload(positions[t]),
                    "p_raw": p_raw[t],
                    "p_extremized": p_extremized[t],
                    "expected_move_pct": expected_moves[t],
                }
                for t in TERMS
            }
        },
    )

    # ---- Phase E: warnings, per term ------------------------------------------
    warnings: dict[str, list[TermWarning]] = {t: [] for t in TERMS}
    cost_audits: dict[str, CostAuditResult] = {}
    for t in TERMS:
        no_data = _no_data_seats(settings)
        eligible = _eligible_weight(seat_ids, t, no_data)
        read = _read_weight(term_verdicts(t), t, no_data)
        if eligible > 0 and read < eligible * settings.min_participating_seats_pct:
            warnings[t].append(
                TermWarning(
                    "participation",
                    f"Thin evidence: only {read / eligible:.0%} of the seats that matter "
                    "for this term could read their data.",
                )
            )

        flags = plausibility_flags[t]
        implausible = sum(1 for f in flags.values() if f == "IMPLAUSIBLE")
        if flags and implausible > len(flags) / 2:
            warnings[t].append(
                TermWarning(
                    "base_rate",
                    f"{implausible} of {len(flags)} leaning seats expect a bigger move than "
                    f"this stock has made over {TERM_WINDOWS[t]} 95% of the time.",
                )
            )

        cost_audits[t] = audit(expected_moves[t], settings.assumed_spread_bps)
        if not cost_audits[t].passed:
            warnings[t].append(
                TermWarning(
                    "cost",
                    f"The expected move ({expected_moves[t]}%) doesn't clear the "
                    f"~{cost_audits[t].round_trip_cost_pct}% cost of trading in and out.",
                )
            )

        if earnings_soon and t == "short":
            warnings[t].append(TermWarning("earnings", earnings_soon["message"]))

        objections = [pv for pv in prosecutor_verdicts if pv.vetoes(t)]
        if objections:
            warnings[t].append(
                TermWarning(
                    "prosecutor",
                    "The Prosecutor objected: "
                    + (objections[-1].veto_reason or "the case for this lean doesn't hold up."),
                )
            )
    await emit(
        "phase_e_warnings",
        {"terms": {t: [dataclasses.asdict(w) for w in warnings[t]] for t in TERMS}},
    )

    # ---- Risk Warden (sizing only, never sees the leans) ------------------------
    open_tickers = get_open_tickers(conn, settings.council_account)
    risk_sizing = size_position(
        atr_implied_range_pct=reality_anchors["short"].atr_implied_range_pct,
        options_implied_move_pct=reality_anchors["short"].options_implied_move_pct,
        risk_budget_pct=settings.risk_budget_pct,
        kelly_cap=settings.kelly_cap,
        ticker=ticker,
        other_open_tickers=open_tickers,
    )
    await emit(
        "risk_warden",
        {
            "position_size_pct_of_book": risk_sizing.position_size_pct_of_book,
            "concentration_warning": risk_sizing.concentration_warning,
        },
    )

    # ---- Phase F: synthesis ---------------------------------------------------
    synthesis = await GrandMasterSeat().synthesize(
        tier1_summaries=tier1_summaries,
        debate_transcript=debate_transcript,
        prosecutor_verdicts=prosecutor_verdicts,
        positions=positions,
        warnings={t: [w.message for w in warnings[t]] for t in TERMS},
        reality_anchors=reality_anchors,
        correlated_evidence=correlated_evidence,
        llm_client=llm_client,
        model=settings.synthesis_model,
        user_context=context,
    )
    if synthesis is None:
        synthesis = _fallback_synthesis(tier1_summaries, positions, warnings, correlated_evidence)

    ticks = {
        t: [
            SeatTick(
                seat_id=r.seat_id,
                title=r.title,
                vote=r.verdicts.term(t).vote,
                p_bullish=p_bullish(r.verdicts.term(t)),
                weight=weights[t].get(r.seat_id, 0.0),
            )
            for r in seat_results
        ]
        for t in TERMS
    }
    dissent = {t: summarize_dissent(tier1_summaries, t) for t in TERMS}

    # Short-term levels come from the Technician's chart read, and only when
    # it leans the same way as the council -- no other seat sets prices.
    technician = by_id.get("technician")
    levels = {t: (None, None, None) for t in TERMS}
    if (
        technician
        and technician.verdicts.short.vote == positions["short"].vote
        and not is_abstention(positions["short"].vote)
    ):
        tv = technician.verdicts.short
        levels["short"] = (tv.entry, tv.exit, tv.invalidation)

    # A most-likely price and a likely range per term (price_target.py),
    # with the chance calculated from how past ranges did.
    record = range_record(conn)
    price_targets = {}
    for t in TERMS:
        target = None
        if positions[t].p_bullish is not None and positions[t].seats_counted:
            hits, scored = record.get(t, (0, 0))
            target = price_target(
                price_at_prediction,
                positions[t].p_bullish,
                term_sigma(anchor_series.bars, t, reality_anchors[t].options_implied_move_pct),
                hits=hits,
                scored=scored,
            )
        price_targets[t] = target.as_dict() if target else None

    terms_payload = {
        t: {
            **_position_payload(positions[t]),
            "name": TERM_NAMES[t],
            "window": TERM_WINDOWS[t],
            "expected_move_pct": expected_moves[t],
            "price_target": price_targets[t],
            "warnings": [dataclasses.asdict(w) for w in warnings[t]],
            "ticks": [dataclasses.asdict(k) for k in ticks[t]],
            "note": synthesis.note(t),
            "dissent_summary": dissent[t],
        }
        for t in TERMS
    }
    await emit("phase_f_synthesis", {**synthesis.model_dump(), "terms": terms_payload})

    llm_client.raise_if_stopped()

    # ---- Phase G: Crypt write -- one row per term, one run_id ------------------
    run_id = str(uuid.uuid4())
    snapshot_fields = {
        "ticker": ticker,
        "terms": list(TERMS),
        "as_of": as_of.isoformat(),
        "seats": seat_ids,
    }
    data_snapshot_hash = hashlib.sha256(
        json.dumps(snapshot_fields, sort_keys=True, default=str).encode()
    ).hexdigest()

    model_versions = {sid: settings.seat_model for sid in seat_ids}
    model_versions.update(
        {
            "bull_advocate": settings.seat_model,
            "bear_advocate": settings.seat_model,
            "prosecutor": settings.synthesis_model,
            "grand_master": settings.synthesis_model,
        }
    )

    total_cost_usd = round(sum(c.cost_usd for c in llm_client.call_log), 6)
    total_input_tokens = sum(c.input_tokens for c in llm_client.call_log)
    total_output_tokens = sum(c.output_tokens for c in llm_client.call_log)
    run_mode = _run_mode(settings, llm_client.call_log)
    # What each seat's answer actually came from -- the concrete free-tier
    # model, or Groq after an overflow, not just what routing intended.
    model_used = {c.seat_id: (c.model, c.provider) for c in llm_client.call_log if c.success}
    synthesis_json = json.dumps(
        {**synthesis.model_dump(), "terms": terms_payload, "replay": replay}, default=str
    )
    prosecutor_json = json.dumps([pv.model_dump() for pv in prosecutor_verdicts])

    prediction_ids: dict[str, str] = {}
    try:
        for t in TERMS:
            entry, exit_, invalidation = levels[t]
            prediction_ids[t] = write_prediction(
                conn,
                ticker=ticker,
                horizon=t,
                resolve_at=resolve_at_for(t, as_of),
                price_at_prediction=price_at_prediction,
                data_snapshot_hash=data_snapshot_hash,
                model_versions=model_versions,
                blind_vote=blind[t].vote,
                blind_probability=blind[t].confidence,
                blind_consensus_pct=blind[t].consensus_pct,
                council_vote=positions[t].vote,
                council_confidence=positions[t].confidence,
                consensus_pct=positions[t].consensus_pct,
                entry=entry,
                exit=exit_,
                invalidation=invalidation,
                stop=invalidation,
                expected_move_pct=expected_moves[t],
                base_rate_move_pct=reality_anchors[t].atr_implied_range_pct,
                dissent_summary=dissent[t],
                correlated_evidence_warning=synthesis.correlated_evidence_warning,
                prosecutor_verdict=prosecutor_json,
                cost_audit_passed=cost_audits[t].passed,
                p_raw=p_raw[t],
                p_extremized=p_extremized[t],
                # Run-wide totals, repeated on each of the run's rows --
                # count them once per run_id.
                total_cost_usd=total_cost_usd,
                total_input_tokens=total_input_tokens,
                total_output_tokens=total_output_tokens,
                run_mode=run_mode,
                run_shape=run_shape,
                run_id=run_id,
                synthesis_json=synthesis_json,
                user_id=settings.council_account or None,
                created_at=as_of,
            )
            for r in seat_results:
                route = resolve_route(r.seat_id, settings, default_model=settings.seat_model)
                used_model, used_provider = model_used.get(r.seat_id, (route.model, route.provider))
                write_seat_vote(
                    conn,
                    prediction_id=prediction_ids[t],
                    seat_id=r.seat_id,
                    verdict=r.verdicts.term(t),
                    dispersion=r.dispersion[t],
                    weight_applied=weights[t].get(r.seat_id, 0.0),
                    model_id="fixture" if settings.resolved_no_llm else used_model,
                    provider="none" if settings.resolved_no_llm else used_provider,
                )
    finally:
        conn.close()

    await emit("phase_g_crypt_write", {"run_id": run_id, "prediction_ids": prediction_ids})

    term_results = {
        t: TermResult(
            term=t,
            name=TERM_NAMES[t],
            window=TERM_WINDOWS[t],
            prediction_id=prediction_ids[t],
            resolve_at=resolve_at_for(t, as_of),
            blind=blind[t],
            position=positions[t],
            p_raw=p_raw[t],
            p_extremized=p_extremized[t],
            expected_move_pct=expected_moves[t],
            entry=levels[t][0],
            exit=levels[t][1],
            invalidation=levels[t][2],
            reality_anchor=reality_anchors[t],
            plausibility_flags=plausibility_flags[t],
            cost_audit=cost_audits[t],
            warnings=warnings[t],
            ticks=ticks[t],
            note=synthesis.note(t),
            dissent_summary=dissent[t],
        )
        for t in TERMS
    }

    return DeliberationResult(
        run_id=run_id,
        ticker=ticker,
        as_of=as_of,
        price_at_prediction=price_at_prediction,
        seat_results=seat_results,
        terms=term_results,
        debate_transcript=debate_transcript,
        prosecutor_verdicts=prosecutor_verdicts,
        correlated_evidence=correlated_evidence,
        incoherent_decompositions=incoherent_decompositions,
        risk_sizing=risk_sizing,
        synthesis=synthesis,
        call_log=llm_client.call_log,
        run_mode=run_mode,
        run_shape=run_shape,
    )
