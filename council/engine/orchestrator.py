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

from council.config import Settings
from council.crypt.db import connect, get_open_tickers
from council.crypt.ledger import write_prediction, write_seat_vote
from council.data.cache import DiskCache
from council.data.providers.alpha_vantage import AlphaVantageProvider
from council.data.providers.fixtures import FixtureProvider
from council.data.providers.fmp import FMPProvider
from council.data.providers.yfinance_provider import YFinanceProvider
from council.data.service import DataService
from council.engine.aggregation import DATA_QUALITY_MULTIPLIER, weighted_vote
from council.engine.base_rate import RealityAnchor, check_plausibility, compute_reality_anchor
from council.engine.cost_auditor import CostAuditResult, audit
from council.engine.horizons import competence, is_competent, resolve_at_for
from council.engine.llm_client import LLMClient
from council.engine.risk_warden import RiskSizing, size_position
from council.engine.sampling import aggregate_samples
from council.engine.schemas import (
    DebateArgument,
    GrandMasterVerdict,
    ProsecutorVerdict,
    Tier1Summary,
    summarize_dissent,
    summarize_tier1,
)
from council.seats.advocates import BearAdvocateSeat, BullAdvocateSeat
from council.seats.base import SeatVerdict
from council.seats.catalyst_seer import CatalystSeerSeat
from council.seats.cross_market import CrossMarketSeat
from council.seats.estimate_scribe import EstimateScribeSeat
from council.seats.flow_cartographer import FlowCartographerSeat
from council.seats.fundamentalist import FundamentalistSeat
from council.seats.grand_master import GrandMasterSeat
from council.seats.insider_reader import InsiderReaderSeat
from council.seats.macro_sage import MacroSageSeat
from council.seats.oracle_options import OracleOptionsSeat
from council.seats.prosecutor import ProsecutorSeat, detect_correlated_evidence
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
    gates_passed: bool
    gate_failure_reasons: list[str]
    cost_audit: CostAuditResult
    risk_sizing: RiskSizing
    grand_master_verdict: GrandMasterVerdict
    call_log: list = field(default_factory=list)


def build_data_service(settings: Settings) -> DataService:
    cache = DiskCache(settings.cache_db_path)
    providers = []
    if settings.resolved_use_data_fixtures:
        providers.append(FixtureProvider())
    else:
        if settings.alpha_vantage_api_key:
            providers.append(AlphaVantageProvider(settings.alpha_vantage_api_key))
        if settings.fmp_api_key:
            providers.append(FMPProvider(settings.fmp_api_key))
        providers.append(YFinanceProvider())  # backstop, per spec: "unreliable, use as fallback only"
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
) -> DeliberationResult:
    as_of = as_of or datetime.utcnow()
    data_service = build_data_service(settings)
    llm_client = LLMClient(settings)
    semaphore = asyncio.Semaphore(settings.max_concurrent_llm_calls)

    # ---- Phase A: blind round -------------------------------------------
    eligible_seats = [s for s in TIER_I_SEATS if is_competent(s.id, horizon)]
    if not eligible_seats:
        raise ValueError(f"no Tier I seat is competent at horizon '{horizon}'")

    n_samples = settings.n_samples_per_seat

    async def deliberate_one(seat, ctx, sample_index):
        async with semaphore:
            return await seat.deliberate(ctx, llm_client, sample_index=sample_index)

    async def run_seat(seat) -> tuple:
        async with semaphore:
            ctx = await seat.gather(data_service, ticker, as_of, horizon)
        samples = await asyncio.gather(*(deliberate_one(seat, ctx, i) for i in range(n_samples)))
        sampled = aggregate_samples(seat.id, list(samples))
        return seat, ctx, sampled

    results: list[tuple] = await asyncio.gather(*(run_seat(seat) for seat in eligible_seats))

    verdicts_by_id: dict[str, SeatVerdict] = {
        seat.id: sampled.representative for seat, _ctx, sampled in results
    }
    dispersion_by_id = {seat.id: sampled.dispersion for seat, _ctx, sampled in results}
    title_by_id = {seat.id: seat.title for seat, _ctx, _sampled in results}
    blind_vote, blind_probability, blind_consensus_pct = weighted_vote(verdicts_by_id)

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

    # ---- Phase C: Debate ---------------------------------------------------
    tier1_summaries = [
        summarize_tier1(seat.id, seat.title, sampled.representative, sampled.dispersion)
        for seat, _ctx, sampled in results
    ]
    correlated_evidence = detect_correlated_evidence(tier1_summaries)

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
                blind_vote,
                round_n,
                llm_client,
                settings.synthesis_model,
            )
        if pv:
            prosecutor_verdicts.append(pv)

    # ---- Phase D: weighted vote ---------------------------------------------
    phase_d_weights = {}
    for sid, v in verdicts_by_id.items():
        if v.vote == "NO_READ":
            continue
        plausibility_multiplier = 0.5 if plausibility_flags.get(sid) == "IMPLAUSIBLE" else 1.0
        phase_d_weights[sid] = (
            competence(sid, horizon)
            * DATA_QUALITY_MULTIPLIER.get(v.data_quality, 0.5)
            * plausibility_multiplier
        )
    weighted_vote_result = weighted_vote(verdicts_by_id, phase_d_weights)

    # ---- Phase E: audit gates -----------------------------------------------
    directional_count = sum(1 for v in verdicts_by_id.values() if v.vote != "NO_READ")
    min_seats_gate = directional_count >= settings.min_participating_seats

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
        gate_failure_reasons.append(
            f"only {directional_count} directional seats, minimum is {settings.min_participating_seats}"
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

    # ---- Risk Warden (sizing only, never sees the vote) ---------------------
    conn = connect(settings.council_db_path)
    open_tickers = get_open_tickers(conn)
    risk_sizing = size_position(
        atr_implied_range_pct=reality_anchor.atr_implied_range_pct,
        options_implied_move_pct=reality_anchor.options_implied_move_pct,
        risk_budget_pct=settings.risk_budget_pct,
        kelly_cap=settings.kelly_cap,
        ticker=ticker,
        other_open_tickers=open_tickers,
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
            created_at=as_of,
        )
        for seat, _ctx, sampled in results:
            write_seat_vote(
                conn,
                prediction_id=prediction_id,
                seat_id=seat.id,
                verdict=sampled.representative,
                dispersion=sampled.dispersion,
                weight_applied=phase_d_weights.get(seat.id, 0.0),
                model_id="fixture" if settings.resolved_no_llm else settings.seat_model,
                provider="none" if settings.resolved_no_llm else "anthropic",
            )
    finally:
        conn.close()

    seat_results = [
        SeatResult(seat.id, seat.title, sampled.representative, sampled.dispersion, len(sampled.samples))
        for seat, _ctx, sampled in results
    ]

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
        gates_passed=gates_passed,
        gate_failure_reasons=gate_failure_reasons,
        cost_audit=cost_audit_result,
        risk_sizing=risk_sizing,
        grand_master_verdict=gm_verdict,
        call_log=llm_client.call_log,
    )
