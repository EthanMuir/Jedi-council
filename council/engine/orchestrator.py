"""Phase A -- the blind round. All Tier I seats run isolated and in
parallel, with no peer visibility, per the deliberation protocol (spec
section 4). Tiers II-IV (debate, audit gates, synthesis) don't exist yet --
this writes only the A8 blind_vote/blind_probability/blind_consensus_pct
columns, never council_vote, so nothing here can be mistaken for a
synthesised verdict."""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from council.config import Settings
from council.crypt.db import connect
from council.crypt.ledger import write_blind_prediction, write_seat_vote
from council.data.cache import DiskCache
from council.data.providers.alpha_vantage import AlphaVantageProvider
from council.data.providers.fixtures import FixtureProvider
from council.data.providers.fmp import FMPProvider
from council.data.providers.yfinance_provider import YFinanceProvider
from council.data.service import DataService
from council.engine.horizons import is_competent, resolve_at_for
from council.engine.llm_client import LLMClient
from council.engine.sampling import aggregate_samples
from council.seats.base import SeatVerdict
from council.seats.catalyst_seer import CatalystSeerSeat
from council.seats.cross_market import CrossMarketSeat
from council.seats.estimate_scribe import EstimateScribeSeat
from council.seats.flow_cartographer import FlowCartographerSeat
from council.seats.fundamentalist import FundamentalistSeat
from council.seats.insider_reader import InsiderReaderSeat
from council.seats.macro_sage import MacroSageSeat
from council.seats.oracle_options import OracleOptionsSeat
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
    call_log: list


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


def aggregate_blind_round(verdicts: dict[str, SeatVerdict]) -> tuple[str, float, float]:
    """Unweighted for now -- the Calibration Officer's weight vector doesn't
    exist until Phase 3/4. NO_READ seats contribute to neither numerator nor
    denominator, per spec section 4 Phase D."""
    directional = {sid: v for sid, v in verdicts.items() if v.vote != "NO_READ"}
    if not directional:
        return "NO_CONVICTION", 0.5, 0.0

    p_bullish_values = [
        v.probability if v.vote == "BULLISH" else 1 - v.probability
        for v in directional.values()
    ]
    avg_p_bullish = sum(p_bullish_values) / len(p_bullish_values)
    blind_vote = "BULLISH" if avg_p_bullish >= 0.5 else "BEARISH"
    blind_probability = avg_p_bullish if blind_vote == "BULLISH" else 1 - avg_p_bullish
    agree = sum(1 for v in directional.values() if v.vote == blind_vote)
    consensus_pct = 100.0 * agree / len(directional)
    return blind_vote, round(blind_probability, 3), round(consensus_pct, 1)


async def run_blind_round(
    ticker: str,
    horizon: str,
    settings: Settings,
    as_of: datetime | None = None,
) -> DeliberationResult:
    as_of = as_of or datetime.utcnow()
    data_service = build_data_service(settings)
    llm_client = LLMClient(settings)

    eligible_seats = [s for s in TIER_I_SEATS if is_competent(s.id, horizon)]
    if not eligible_seats:
        raise ValueError(f"no Tier I seat is competent at horizon '{horizon}'")

    semaphore = asyncio.Semaphore(settings.max_concurrent_llm_calls)
    n_samples = settings.n_samples_per_seat

    async def deliberate_one(seat, ctx, sample_index):
        async with semaphore:
            return await seat.deliberate(ctx, llm_client, sample_index=sample_index)

    async def run_seat(seat) -> tuple:
        async with semaphore:
            ctx = await seat.gather(data_service, ticker, as_of, horizon)
        samples = await asyncio.gather(
            *(deliberate_one(seat, ctx, i) for i in range(n_samples))
        )
        sampled = aggregate_samples(seat.id, list(samples))
        return seat, ctx, sampled

    results: list[tuple] = await asyncio.gather(*(run_seat(seat) for seat in eligible_seats))

    verdicts_by_id = {seat.id: sampled.representative for seat, _ctx, sampled in results}
    blind_vote, blind_probability, blind_consensus_pct = aggregate_blind_round(verdicts_by_id)

    price_series = await data_service.get_ohlcv(ticker, as_of=as_of, lookback_days=5)
    if not price_series.bars:
        raise RuntimeError(f"no price data available for {ticker} as of {as_of.isoformat()}")
    price_at_prediction = price_series.bars[-1].close

    snapshot_fields = {
        "ticker": ticker,
        "horizon": horizon,
        "as_of": as_of.isoformat(),
        "seat_data_as_of": {seat.id: ctx.as_of.isoformat() for seat, ctx, _v in results},
    }
    data_snapshot_hash = hashlib.sha256(
        json.dumps(snapshot_fields, sort_keys=True, default=str).encode()
    ).hexdigest()

    resolve_at = resolve_at_for(horizon, as_of)

    conn = connect(settings.council_db_path)
    try:
        prediction_id = write_blind_prediction(
            conn,
            ticker=ticker,
            horizon=horizon,
            resolve_at=resolve_at,
            price_at_prediction=price_at_prediction,
            data_snapshot_hash=data_snapshot_hash,
            model_versions={seat.id: settings.seat_model for seat in eligible_seats},
            blind_vote=blind_vote,
            blind_probability=blind_probability,
            blind_consensus_pct=blind_consensus_pct,
            created_at=as_of,
        )
        for seat, _ctx, sampled in results:
            write_seat_vote(
                conn,
                prediction_id=prediction_id,
                seat_id=seat.id,
                verdict=sampled.representative,
                dispersion=sampled.dispersion,
                weight_applied=1.0,  # Calibration Officer weighting arrives Phase 3/4
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
        call_log=llm_client.call_log,
    )
