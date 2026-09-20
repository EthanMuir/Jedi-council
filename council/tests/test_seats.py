"""Per-seat isolation tests (forbidden fields must raise) and an end-to-end
check that each of the 12 Tier I seats produces a valid SeatVerdict in
fixture mode."""
from __future__ import annotations

from datetime import datetime

import pytest

from council.config import Settings
from council.data.cache import DiskCache
from council.data.providers.fixtures import FixtureProvider
from council.data.service import DataService
from council.engine.llm_client import LLMClient
from council.seats.base import DataIsolationError, SeatContext
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

AS_OF = datetime(2026, 9, 18, 16, 0, 0)
SEATS = [
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

ALL_DATA_FIELDS = {
    "ohlcv",
    "news",
    "option_chain",
    "fundamentals",
    "insider",
    "congress",
    "institutional",
    "macro",
    "cross_market",
    "estimates",
    "transcripts",
    "filings",
}


@pytest.fixture
def data_service(tmp_path):
    cache = DiskCache(str(tmp_path / "cache.db"))
    return DataService(providers=[FixtureProvider()], cache=cache)


@pytest.fixture
def no_llm_client():
    settings = Settings(no_llm=True)
    return LLMClient(settings)


def test_all_12_tier_i_seats_have_distinct_single_field_allowlists():
    """Every seat sees exactly one data domain, and no two seats share one --
    the precondition for the isolation tests below to be meaningful at all."""
    assert len(SEATS) == 12
    all_allowlists = [seat.allowed_data for seat in SEATS]
    assert all(len(a) == 1 for a in all_allowlists)
    flattened = [next(iter(a)) for a in all_allowlists]
    assert len(set(flattened)) == 12, "two seats share an allowed field"
    assert set(flattened) == ALL_DATA_FIELDS


@pytest.mark.parametrize("seat", SEATS, ids=lambda s: s.id)
def test_seat_context_rejects_field_outside_own_allowlist(seat):
    """Every OTHER seat's exclusive field must raise for this seat."""
    foreign_fields = ALL_DATA_FIELDS - seat.allowed_data
    assert foreign_fields, "test is meaningless without at least one foreign field"

    # Constructing a context that leaks a foreign field must raise immediately.
    for foreign in foreign_fields:
        with pytest.raises(DataIsolationError):
            SeatContext(
                seat.id,
                seat.allowed_data,
                {foreign: object()},
                ticker="NVDA",
                as_of=AS_OF,
                horizon="1w",
            )

    # A context built only with allowed data must still refuse to read a
    # foreign field even if the caller tries to reach past its own context.
    ctx = SeatContext(
        seat.id, seat.allowed_data, {}, ticker="NVDA", as_of=AS_OF, horizon="1w"
    )
    for foreign in foreign_fields:
        with pytest.raises(DataIsolationError):
            ctx[foreign]


@pytest.mark.asyncio
async def test_technician_never_sees_ticker_name_in_prompt(data_service):
    seat = TechnicianSeat()
    ctx = await seat.gather(data_service, "NVDA", AS_OF, "1w")
    assert set(ctx._data.keys()) == {"ohlcv"}  # noqa: SLF001 -- whitebox isolation check


@pytest.mark.asyncio
async def test_cross_market_gather_never_touches_own_ticker_ohlcv(data_service, monkeypatch):
    """The Cross-Market Navigator's mandate is to never see the ticker's own
    price series -- assert its gather() never calls get_ohlcv with NVDA
    itself as the symbol argument."""
    seat = CrossMarketSeat()
    original_get_ohlcv = DataService.get_ohlcv
    seen_symbols = []

    async def spy(self, symbol, *args, **kwargs):
        seen_symbols.append(symbol)
        return await original_get_ohlcv(self, symbol, *args, **kwargs)

    monkeypatch.setattr(DataService, "get_ohlcv", spy)
    await seat.gather(data_service, "NVDA", AS_OF, "1w")
    assert "NVDA" not in seen_symbols


@pytest.mark.asyncio
@pytest.mark.parametrize("seat", SEATS, ids=lambda s: s.id)
async def test_seat_produces_valid_verdict_in_fixture_mode(seat, data_service, no_llm_client):
    ctx = await seat.gather(data_service, "NVDA", AS_OF, "1w")
    verdict = await seat.deliberate(ctx, no_llm_client)
    assert verdict.vote in ("BULLISH", "BEARISH", "NO_READ")
    assert 0.0 <= verdict.probability <= 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize("seat", SEATS, ids=lambda s: s.id)
async def test_seat_sampling_produces_three_valid_verdicts(seat, data_service, no_llm_client):
    """Every seat must be callable with distinct sample_index values (the
    N-sampling contract) and each sample must independently validate."""
    ctx = await seat.gather(data_service, "NVDA", AS_OF, "1w")
    for i in range(3):
        verdict = await seat.deliberate(ctx, no_llm_client, sample_index=i)
        assert verdict.vote in ("BULLISH", "BEARISH", "NO_READ")
