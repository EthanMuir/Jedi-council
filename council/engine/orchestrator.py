"""The full deliberation pipeline, Phases A-G (spec section 4). Everything
runs in memory; the Crypt is written exactly once, at the very end (Phase
G), because the immutability trigger makes a later UPDATE impossible -- see
council/crypt/ledger.py.

Phase A  Blind round: all Tier I seats, isolated, no peer visibility, each
         sampled N times (dispersion capture).
Phase B  Reality Anchor: Base-Rate Keeper + Oracle of Options establish the
         plausible distribution; every Tier I target checked against it.
Phase C  Debate: Bull vs Bear, N rounds, Prosecutor intervenes after each.
Phase D  Weighted vote: horizon-competence x data-quality x plausibility.
Phase E  Audit gates: Cost Auditor, Prosecutor veto, base-rate plausibility,
         minimum participating seats -- any can force NO_CONVICTION.
Phase F  Synthesis: Grand Master (skipped, synthetic verdict, if a gate
         already failed -- no need to spend the call).
Phase G  Crypt write.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable

from council.calibration.officer import compute_weights
from council.config import Settings
from council.crypt.db import connect, get_open_tickers
from council.crypt.ledger import write_prediction, write_seat_vote
from council.data.cache import DiskCache
from council.data.providers.fixtures import FixtureProvider
from council.data.providers.fmp import FMPProvider
from council.data.providers.fred import FREDProvider
from council.data.providers.sec_edgar import SECEdgarProvider
from council.data.providers.yfinance_provider import YFinanceProvider
from council.data.service import DataService
from council.engine.aggregation import DATA_QUALITY_MULTIPLIER, extremize, weighted_vote
from council.engine.base_rate import RealityAnchor, check_plausibility, compute_reality_anchor
from council.engine.cost_auditor import CostAuditResult, audit
from council.engine.horizons import competence, is_competent, resolve_at_for
from council.engine.llm_client import LLMClient
from council.engine.risk_warden import RiskSizing, size_position
from council.engine.routing import resolve_route
from council.engine.sampling import SampledSeatVerdict, aggregate_samples
from council.memory.store import MemoryStore, to_seat_memory_lesson
from council.engine.schemas import (
    DebateArgument,
    GrandMasterVerdict,
    ProsecutorVerdict,
    Tier1Summary,
    summarize_dissent,
    summarize_tier1,
)
from council.seats.advocates import BearAdvocateSeat, BullAdvocateSeat
from council.seats.base import SeatContext, SeatVerdict
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
from council.seats.transcript_linguist import TranscriptLinguistSeat

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
    TranscriptLinguistSeat(),
    StructureArchivistSeat(),
]


@dataclass
class SeatResult:
    seat_id: str
    title: str
    verdict: SeatVerdict
    dispersion: float
    sample_count: int


@dataclass
class DeliberationResult:
    prediction_id: str
    ticker: str
    horizon: str
    as_of: datetime
    resolve_at: datetime
    price_at_prediction: float
    seat_results: list[SeatResult]
    blind_vote: str
    blind_probability: float
    blind_consensus_pct: float
    reality_anchor: RealityAnchor
    plausibility_flags: dict[str, str]
    debate_transcript: list[DebateArgument]
    prosecutor_verdicts: list[ProsecutorVerdict]
    correlated_evidence: list[str]
    weighted_vote_result: tuple[str, float, float]
    p_raw: float
    p_extremized: float
    incoherent_decompositions: dict
    gates_passed: bool
    gate_failure_reasons: list[str]
    cost_audit: CostAuditResult
    risk_sizing: RiskSizing
    grand_master_verdict: GrandMasterVerdict
    call_log: list = field(default_factory=list)


# Seats with no implemented data source for their domain (Task #69):
# congressional trading disclosures and earnings call transcripts have no
# solid free/official API -- researched directly, not assumed. FMP's plan
# doesn't cover either and there's no clean free alternative the way SEC
# EDGAR/FRED were for insider trades/macro. Both seats are still called
# (they're genuinely competent at these horizons) and still get a logged,
# real abstain_reason every run -- but they will return NO_READ on
# literally every call until one of them gets a working provider, so
# counting them toward the participation gate's denominator would be
# counting seats that can structurally never contribute, making every
# run's real signal look thinner than it is. Remove a seat from this set
# the moment it has a working data source (as insider_reader and
# structure_archivist's own gaps already were, via SEC EDGAR).
_STRUCTURALLY_NO_DATA_SEATS = frozenset({"senate_watcher", "transcript_linguist"})


def _gate_eligible_weight(seat_ids, horizon: str) -> float:
    """Competence-weighted denominator for the participation gate (Task
    #76). A plain headcount treats every seat's abstention the same, but a
    seat that's only 20-30% competent at this horizon (fundamentalist,
    macro_sage at 1w) is *expected* to abstain most weeks -- that's the
    seat correctly declining to manufacture a signal, not a sign the run
    lacks real conviction, and shouldn't weigh on the gate as heavily as a
    90%-competent seat (technician, oracle_options at 1w) abstaining does.
    Real case that motivated this: an ENB/1w run with 4/10 seats voting
    directionally failed the old >=50% headcount gate by exactly one seat,
    even though the 6 abstentions were mostly low-competence-at-1w seats
    giving genuinely well-reasoned "nothing here" answers -- the
    competence-weighted version of that same run clears 50% (52.5%).
    Still excludes _STRUCTURALLY_NO_DATA_SEATS -- they can't contribute
    regardless of their competence score."""
    return round(
        sum(competence(sid, horizon) for sid in seat_ids if sid not in _STRUCTURALLY_NO_DATA_SEATS), 4
    )


def _directional_weight(verdicts_by_id: dict[str, SeatVerdict], horizon: str) -> float:
    """Competence-weighted numerator: sum of horizon-competence across
    every seat that actually voted a direction (not NO_READ). Extracted
    for direct unit testing (Task #76), same as its denominator above."""
    return round(
        sum(competence(sid, horizon) for sid, v in verdicts_by_id.items() if v.vote != "NO_READ"), 4
    )


def build_data_service(settings: Settings) -> DataService:
    cache = DiskCache(settings.cache_db_path)
    providers = []
    if settings.resolved_use_data_fixtures:
        providers.append(FixtureProvider())
    else:
        # YFinance first, not last: it's free with no hard daily cap, and it
        # genuinely covers OHLCV, news, and options.
        providers.append(YFinanceProvider())
        # SEC EDGAR next, also free and keyless, also ahead of FMP: it's the
        # authoritative source for insider transactions / SEC filings, and
        # unlike FMP's free tier it isn't plan-gated away from those two
        # domains -- no point spending an HTTP round trip on FMP's 403 first.
        providers.append(SECEdgarProvider(settings.resolved_sec_edgar_user_agent))
        # FRED next -- the only remaining source for macro data now that
        # Alpha Vantage has been pulled from this chain entirely (Task #77:
        # its free tier's 25-requests/day cap made it structurally unusable
        # -- a single macro_sage gather() alone burned 7 of those 25 -- and
        # its options endpoints (REALTIME_OPTIONS / REALTIME_PUT_CALL_RATIO)
        # require a $199.99+/month plan, so fetch_option_chain never worked
        # on a free key regardless of quota). Without a FRED key, macro_sage
        # has no live source at all and abstains every run (the same
        # graceful per-seat failure path senate_watcher/transcript_linguist
        # already use for their own data gaps) -- get a free one at
        # fred.stlouisfed.org, no daily cap, 120 req/min.
        if settings.fred_api_key:
            providers.append(FREDProvider(settings.fred_api_key))
        if settings.fmp_api_key:
            providers.append(FMPProvider(settings.fmp_api_key))
    return DataService(providers=providers, cache=cache)


def _synthetic_grand_master_verdict(
    tier1_summaries: list[Tier1Summary], reasoning: str, correlated_evidence: list[str]
) -> GrandMasterVerdict:
    """Used when an audit gate already forces NO_CONVICTION (Grand Master
    call skipped -- nothing left for it to decide) or when Grand Master's
    own structured output fails schema validation after retries."""
    return GrandMasterVerdict(
        vote="NO_CONVICTION",
        confidence=0.5,
        expected_move_pct=0.0,
        dissent_summary=summarize_dissent(tier1_summaries),
        correlated_evidence_warning="; ".join(correlated_evidence) if correlated_evidence else None,
        reasoning=reasoning,
    )


async def run_deliberation(
    ticker: str,
    horizon: str,
    settings: Settings,
    as_of: datetime | None = None,
    progress: Callable[[str, dict], Awaitable[None]] | None = None,
    context: str | None = None,
) -> DeliberationResult:
    """`progress`, when given, is awaited as `progress(event, payload)` at
    each phase transition and seat completion -- purely additive, no
    caller that omits it (the CLI, every existing test) observes any
    behaviour change. This is what lets the UI show the deliberation as a
    spectacle instead of a spinner: seats illuminate as they actually
    finish, not all at once when the whole pipeline returns."""

    async def emit(event: str, payload: dict) -> None:
        if progress:
            await progress(event, payload)

    as_of = as_of or datetime.utcnow()
    data_service = build_data_service(settings)
    llm_client = LLMClient(settings)
    semaphore = asyncio.Semaphore(settings.max_concurrent_llm_calls)
    conn = connect(settings.council_db_path)

    # ---- Phase A: blind round -------------------------------------------
    eligible_seats = [s for s in TIER_I_SEATS if is_competent(s.id, horizon)]
    if not eligible_seats:
        raise ValueError(f"no Tier I seat is competent at horizon '{horizon}'")

    calibration_weights = compute_weights(conn, [s.id for s in eligible_seats], settings, horizon)
    memory = MemoryStore(conn, cap_per_seat=settings.memory_cap_per_seat)

    n_samples = settings.n_samples_per_seat

    async def deliberate_one(seat, ctx, sample_index, memory_lessons):
        async with semaphore:
            verdict = await seat.deliberate(
                ctx, llm_client, sample_index=sample_index, memories=memory_lessons
            )
            if memory_lessons and verdict.vote != "NO_READ":
                verdict = verdict.model_copy(update={"memory_applied": memory_lessons})
            return verdict

    async def run_seat(seat) -> tuple:
        try:
            await emit("seat_stage", {"seat_id": seat.id, "stage": "gathering"})
            async with semaphore:
                ctx = await seat.gather(data_service, ticker, as_of, horizon)
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
                verdict = await deliberate_one(seat, ctx, i, memory_lessons)
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
                return verdict

            await emit(
                "seat_stage",
                {
                    "seat_id": seat.id,
                    "stage": "deliberating",
                    "samples_done": 0,
                    "samples_total": n_samples,
                },
            )
            samples = await asyncio.gather(
                *(deliberate_one_tracked(i) for i in range(n_samples))
            )
            sampled = aggregate_samples(seat.id, list(samples))
        except Exception as exc:  # noqa: BLE001 -- one seat's data/LLM failure
            # must never take down the other eleven. Abstention is already a
            # valid answer for "no signal"; it's the right answer here too,
            # for "infrastructure failed before a signal could be formed".
            ctx = SeatContext(seat.id, frozenset(), {}, ticker=ticker, as_of=as_of, horizon=horizon)
            failed_verdict = SeatVerdict(
                vote="NO_READ",
                probability=0.5,
                expected_move_pct=0.0,
                thesis=f"Seat failed before producing a verdict: {exc}"[:500],
                what_would_change_my_mind="N/A",
                data_quality="POOR",
                abstain_reason="seat_infrastructure_failure",
            )
            sampled = SampledSeatVerdict(
                seat_id=seat.id,
                samples=[failed_verdict],
                consensus_vote="NO_READ",
                dispersion=0.0,
                representative=failed_verdict,
            )
        await emit(
            "seat_result",
            {
                "seat_id": seat.id,
                "title": seat.title,
                "vote": sampled.representative.vote,
                "probability": sampled.representative.probability,
                "dispersion": sampled.dispersion,
                "data_quality": sampled.representative.data_quality,
                "thesis": sampled.representative.thesis,
            },
        )
        return seat, ctx, sampled

    results: list[tuple] = await asyncio.gather(*(run_seat(seat) for seat in eligible_seats))

    verdicts_by_id: dict[str, SeatVerdict] = {
        seat.id: sampled.representative for seat, _ctx, sampled in results
    }
    dispersion_by_id = {seat.id: sampled.dispersion for seat, _ctx, sampled in results}
    title_by_id = {seat.id: seat.title for seat, _ctx, _sampled in results}
    blind_vote, blind_probability, blind_consensus_pct = weighted_vote(verdicts_by_id)
    await emit(
        "phase_a_complete",
        {"blind_vote": blind_vote, "blind_probability": blind_probability, "blind_consensus_pct": blind_consensus_pct},
    )

    price_series = await data_service.get_ohlcv(ticker, as_of=as_of, lookback_days=5)
    if not price_series.bars:
        raise RuntimeError(f"no price data available for {ticker} as of {as_of.isoformat()}")
    price_at_prediction = price_series.bars[-1].close

    # ---- Phase B: Reality Anchor ------------------------------------------
    anchor_series = await data_service.get_ohlcv(ticker, as_of=as_of, lookback_days=730)
    oracle_verdict = verdicts_by_id.get("oracle_options")
    options_implied_move_pct = (
        oracle_verdict.expected_move_pct
        if oracle_verdict and oracle_verdict.vote != "NO_READ"
        else None
    )
    reality_anchor = compute_reality_anchor(anchor_series.bars, horizon, options_implied_move_pct)
    plausibility_flags = {
        sid: check_plausibility(v.expected_move_pct, reality_anchor)
        for sid, v in verdicts_by_id.items()
        if v.vote != "NO_READ"
    }
    await emit(
        "phase_b_reality_anchor",
        {
            "max_plausible_move_pct": reality_anchor.max_plausible_move_pct,
            "hit_rate_up": reality_anchor.hit_rate_up,
            "options_implied_move_pct": reality_anchor.options_implied_move_pct,
            "plausibility_flags": plausibility_flags,
        },
    )

    # ---- Phase C: Debate ---------------------------------------------------
    tier1_summaries = [
        summarize_tier1(seat.id, seat.title, sampled.representative, sampled.dispersion)
        for seat, _ctx, sampled in results
    ]
    correlated_evidence = detect_correlated_evidence(tier1_summaries)
    incoherent_decompositions = detect_incoherent_decompositions(verdicts_by_id)

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
                blind_vote,
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
                "prosecutor_findings": [f.description for f in pv.findings] if pv else [],
            },
        )

    # ---- Phase D: weighted vote ---------------------------------------------
    phase_d_weights = {}
    for sid, v in verdicts_by_id.items():
        if v.vote == "NO_READ":
            continue
        plausibility_multiplier = 0.5 if plausibility_flags.get(sid) == "IMPLAUSIBLE" else 1.0
        coherence_multiplier = (
            1 - INCOHERENCE_WEIGHT_DISCOUNT if sid in incoherent_decompositions else 1.0
        )
        phase_d_weights[sid] = (
            competence(sid, horizon)
            * DATA_QUALITY_MULTIPLIER.get(v.data_quality, 0.5)
            * plausibility_multiplier
            * coherence_multiplier
            * calibration_weights.get(sid, 1.0)
        )
    weighted_vote_result = weighted_vote(verdicts_by_id, phase_d_weights)
    wv_vote, wv_confidence, _wv_consensus = weighted_vote_result
    p_raw = (
        wv_confidence if wv_vote == "BULLISH" else 1 - wv_confidence if wv_vote == "BEARISH" else 0.5
    )
    p_extremized = extremize(p_raw, settings.extremize_alpha)
    await emit(
        "phase_d_weighted_vote",
        {"vote": wv_vote, "confidence": wv_confidence, "p_raw": p_raw, "p_extremized": p_extremized},
    )

    # ---- Phase E: audit gates -----------------------------------------------
    directional_count = sum(1 for v in verdicts_by_id.values() if v.vote != "NO_READ")
    gate_eligible_weight = _gate_eligible_weight(verdicts_by_id.keys(), horizon)
    directional_weight = _directional_weight(verdicts_by_id, horizon)
    # gate_eligible_weight == 0 only when every called seat is in
    # _STRUCTURALLY_NO_DATA_SEATS -- nothing could ever have contributed
    # regardless of this run's data, so this gate has nothing to check
    # (aggregation.py's own "zero directional votes" fallback still applies).
    min_seats_gate = (
        gate_eligible_weight == 0
        or directional_weight >= gate_eligible_weight * settings.min_participating_seats_pct
    )

    implausible_count = sum(1 for f in plausibility_flags.values() if f == "IMPLAUSIBLE")
    base_rate_gate = not (plausibility_flags and implausible_count > len(plausibility_flags) / 2)

    directional_moves = [
        v.expected_move_pct for v in verdicts_by_id.values() if v.vote != "NO_READ"
    ]
    audit_move_pct = options_implied_move_pct or (
        sum(directional_moves) / len(directional_moves) if directional_moves else 0.0
    )
    cost_audit_result = audit(audit_move_pct, settings.assumed_spread_bps)
    cost_gate = cost_audit_result.passed

    prosecutor_gate = not any(pv.veto for pv in prosecutor_verdicts)

    gate_failure_reasons = []
    if not min_seats_gate:
        participation_pct = (
            0.0 if gate_eligible_weight == 0 else round(100 * directional_weight / gate_eligible_weight, 1)
        )
        gate_failure_reasons.append(
            f"competence-weighted participation is {participation_pct}% "
            f"(directional weight {directional_weight} of eligible weight {gate_eligible_weight}, "
            f"{directional_count} seats voted directionally), minimum is "
            f"{settings.min_participating_seats_pct:.0%}"
        )
    if not base_rate_gate:
        gate_failure_reasons.append(
            f"{implausible_count}/{len(plausibility_flags)} directional targets flagged IMPLAUSIBLE"
        )
    if not cost_gate:
        gate_failure_reasons.append(
            f"edge {cost_audit_result.edge_pct}% does not clear the assumed spread"
        )
    if not prosecutor_gate:
        gate_failure_reasons.append("Prosecutor veto")
    gates_passed = min_seats_gate and base_rate_gate and cost_gate and prosecutor_gate
    await emit("phase_e_gates", {"gates_passed": gates_passed, "reasons": gate_failure_reasons})

    # ---- Risk Warden (sizing only, never sees the vote) ---------------------
    open_tickers = get_open_tickers(conn)
    risk_sizing = size_position(
        atr_implied_range_pct=reality_anchor.atr_implied_range_pct,
        options_implied_move_pct=reality_anchor.options_implied_move_pct,
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
    if gates_passed:
        gm_verdict = await GrandMasterSeat().synthesize(
            tier1_summaries=tier1_summaries,
            debate_transcript=debate_transcript,
            prosecutor_verdicts=prosecutor_verdicts,
            weighted_vote_result=weighted_vote_result,
            reality_anchor=reality_anchor,
            cost_audit_result=cost_audit_result,
            correlated_evidence=correlated_evidence,
            llm_client=llm_client,
            model=settings.synthesis_model,
            user_context=context,
        )
        if gm_verdict is None:
            gm_verdict = _synthetic_grand_master_verdict(
                tier1_summaries,
                "Grand Master schema validation failed after retries.",
                correlated_evidence,
            )
    else:
        gm_verdict = _synthetic_grand_master_verdict(
            tier1_summaries,
            "Audit gates failed: " + "; ".join(gate_failure_reasons),
            correlated_evidence,
        )
    await emit(
        "phase_f_synthesis",
        {
            "vote": gm_verdict.vote,
            "confidence": gm_verdict.confidence,
            "dissent_summary": gm_verdict.dissent_summary,
            "correlated_evidence_warning": gm_verdict.correlated_evidence_warning,
            "reasoning": gm_verdict.reasoning,
        },
    )

    # ---- Phase G: Crypt write (exactly once) -----------------------------
    snapshot_fields = {
        "ticker": ticker,
        "horizon": horizon,
        "as_of": as_of.isoformat(),
        "seat_data_as_of": {seat.id: ctx.as_of.isoformat() for seat, ctx, _s in results},
    }
    data_snapshot_hash = hashlib.sha256(
        json.dumps(snapshot_fields, sort_keys=True, default=str).encode()
    ).hexdigest()
    resolve_at = resolve_at_for(horizon, as_of)

    model_versions = {seat.id: settings.seat_model for seat in eligible_seats}
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

    try:
        prediction_id = write_prediction(
            conn,
            ticker=ticker,
            horizon=horizon,
            resolve_at=resolve_at,
            price_at_prediction=price_at_prediction,
            data_snapshot_hash=data_snapshot_hash,
            model_versions=model_versions,
            blind_vote=blind_vote,
            blind_probability=blind_probability,
            blind_consensus_pct=blind_consensus_pct,
            council_vote=gm_verdict.vote,
            council_confidence=gm_verdict.confidence,
            consensus_pct=weighted_vote_result[2],
            entry=gm_verdict.entry,
            exit=gm_verdict.exit,
            invalidation=gm_verdict.invalidation,
            stop=gm_verdict.stop if gm_verdict.stop is not None else gm_verdict.invalidation,
            expected_move_pct=gm_verdict.expected_move_pct,
            base_rate_move_pct=reality_anchor.atr_implied_range_pct,
            dissent_summary=gm_verdict.dissent_summary,
            correlated_evidence_warning=gm_verdict.correlated_evidence_warning,
            prosecutor_verdict=json.dumps([pv.model_dump() for pv in prosecutor_verdicts]),
            cost_audit_passed=cost_audit_result.passed,
            p_raw=p_raw,
            p_extremized=p_extremized,
            total_cost_usd=total_cost_usd,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            created_at=as_of,
        )
        for seat, _ctx, sampled in results:
            route = resolve_route(seat.id, settings, default_model=settings.seat_model)
            write_seat_vote(
                conn,
                prediction_id=prediction_id,
                seat_id=seat.id,
                verdict=sampled.representative,
                dispersion=sampled.dispersion,
                weight_applied=phase_d_weights.get(seat.id, 0.0),
                model_id="fixture" if settings.resolved_no_llm else route.model,
                provider="none" if settings.resolved_no_llm else route.provider,
            )
    finally:
        conn.close()

    seat_results = [
        SeatResult(seat.id, seat.title, sampled.representative, sampled.dispersion, len(sampled.samples))
        for seat, _ctx, sampled in results
    ]

    await emit("phase_g_crypt_write", {"prediction_id": prediction_id})

    return DeliberationResult(
        prediction_id=prediction_id,
        ticker=ticker,
        horizon=horizon,
        as_of=as_of,
        resolve_at=resolve_at,
        price_at_prediction=price_at_prediction,
        seat_results=seat_results,
        blind_vote=blind_vote,
        blind_probability=blind_probability,
        blind_consensus_pct=blind_consensus_pct,
        reality_anchor=reality_anchor,
        plausibility_flags=plausibility_flags,
        debate_transcript=debate_transcript,
        prosecutor_verdicts=prosecutor_verdicts,
        correlated_evidence=correlated_evidence,
        weighted_vote_result=weighted_vote_result,
        p_raw=p_raw,
        p_extremized=p_extremized,
        incoherent_decompositions=incoherent_decompositions,
        gates_passed=gates_passed,
        gate_failure_reasons=gate_failure_reasons,
        cost_audit=cost_audit_result,
        risk_sizing=risk_sizing,
        grand_master_verdict=gm_verdict,
        call_log=llm_client.call_log,
    )
