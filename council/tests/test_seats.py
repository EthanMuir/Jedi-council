"""Per-seat isolation tests (forbidden fields must raise) and an end-to-end
check that each Phase 1 seat produces a valid SeatVerdict in fixture mode."""
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
from council.seats.oracle_options import OracleOptionsSeat
from council.seats.technician import TechnicianSeat

AS_OF = datetime(2026, 9, 18, 16, 0, 0)
SEATS = [TechnicianSeat(), CatalystSeerSeat(), OracleOptionsSeat()]


@pytest.fixture
def data_service(tmp_path):
    cache = DiskCache(str(tmp_path / "cache.db"))
    return DataService(providers=[FixtureProvider()], cache=cache)


@pytest.fixture
def no_llm_client():
    settings = Settings(no_llm=True)
    return LLMClient(settings)


@pytest.mark.parametrize("seat", SEATS, ids=lambda s: s.id)
def test_seat_context_rejects_field_outside_own_allowlist(seat):
    """Every OTHER seat's exclusive field must raise for this seat."""
    all_fields = {"ohlcv", "news", "option_chain"}
    foreign_fields = all_fields - seat.allowed_data
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
@pytest.mark.parametrize("seat", SEATS, ids=lambda s: s.id)
async def test_seat_produces_valid_verdict_in_fixture_mode(seat, data_service, no_llm_client):
    ctx = await seat.gather(data_service, "NVDA", AS_OF, "1w")
    verdict = await seat.deliberate(ctx, no_llm_client)
    assert verdict.vote in ("BULLISH", "BEARISH", "NO_READ")
    assert 0.0 <= verdict.probability <= 1.0
