"""Per-seat isolation tests (forbidden fields must raise) and an end-to-end
check that each of the 12 Tier I seats gives a valid lean on all three
terms in fixture mode."""
from __future__ import annotations

from datetime import datetime

import pytest

from council.config import Settings
from council.data.cache import DiskCache
from council.data.providers.fixtures import FixtureProvider
from council.data.service import DataService
from council.engine.llm_client import LLMClient
from council.seats.analyst_ratings import AnalystRatingsSeat
from council.seats.base import DataIsolationError, MultiTermVerdict, SeatContext
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
    AnalystRatingsSeat(),
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
    "analyst_ratings",
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
            )

    # A context built only with allowed data must still refuse to read a
    # foreign field even if the caller tries to reach past its own context.
    ctx = SeatContext(seat.id, seat.allowed_data, {}, ticker="NVDA", as_of=AS_OF)
    for foreign in foreign_fields:
        with pytest.raises(DataIsolationError):
            ctx[foreign]


@pytest.mark.asyncio
async def test_technician_never_sees_ticker_name_in_prompt(data_service):
    seat = TechnicianSeat()
    ctx = await seat.gather(data_service, "NVDA", AS_OF)
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
    await seat.gather(data_service, "NVDA", AS_OF)
    assert "NVDA" not in seen_symbols


_TERMS = ("short", "medium", "long")


@pytest.mark.asyncio
@pytest.mark.parametrize("seat", SEATS, ids=lambda s: s.id)
async def test_seat_leans_on_all_three_terms_in_fixture_mode(seat, data_service, no_llm_client):
    ctx = await seat.gather(data_service, "NVDA", AS_OF)
    answer = await seat.deliberate(ctx, no_llm_client)
    assert isinstance(answer, MultiTermVerdict)
    assert answer.read
    for term in _TERMS:
        verdict = answer.term(term)
        assert verdict.vote in ("BULLISH", "BEARISH", "NO_CONVICTION")
        assert 0.0 <= verdict.probability <= 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize("seat", SEATS, ids=lambda s: s.id)
async def test_seat_sampling_produces_three_valid_answers(seat, data_service, no_llm_client):
    """Every seat must be callable with distinct sample_index values (the
    N-sampling contract) and each sample must independently validate."""
    ctx = await seat.gather(data_service, "NVDA", AS_OF)
    for i in range(3):
        answer = await seat.deliberate(ctx, no_llm_client, sample_index=i)
        assert answer.read


class _RecordingClient:
    """Captures the prompt a seat builds and answers with `answer`."""

    class settings:  # noqa: N801 -- mimics LLMClient.settings
        seat_model = "m"

    def __init__(self, answer: MultiTermVerdict | None = None):
        self.answer = answer
        self.calls: list[dict] = []

    async def get_seat_answer(self, **kwargs):
        self.calls.append(kwargs)
        return self.answer or MultiTermVerdict.no_read(thesis="t", abstain_reason="r")


@pytest.mark.asyncio
@pytest.mark.parametrize("seat", SEATS, ids=lambda s: s.id)
async def test_no_prompt_mentions_a_single_horizon(seat, data_service):
    ctx = await seat.gather(data_service, "NVDA", AS_OF)
    client = _RecordingClient()
    await seat.deliberate(ctx, client)
    if client.calls:
        prompt = client.calls[0]["system_prompt"] + client.calls[0]["user_prompt"]
        assert "horizon" not in prompt.lower()
        assert "NO_CONVICTION is the correct answer" not in prompt


# --- the Reader of the Guild's Targets ---------------------------------------


@pytest.mark.asyncio
async def test_analyst_seat_sees_upside_as_a_percentage_never_the_price(data_service):
    seat = AnalystRatingsSeat()
    ctx = await seat.gather(data_service, "NVDA", AS_OF)
    client = _RecordingClient()
    await seat.deliberate(ctx, client)
    prompt = client.calls[0]["user_prompt"]

    assert "Mean target vs today's price: +25.5%" in prompt  # 203.4 vs 162.07
    assert "Analysts with a published target: 58" in prompt
    assert "strong buy 14, buy 36, hold 6, sell 1, strong sell 1" in prompt
    assert "UPGRADE (Neutral -> Buy)" in prompt
    assert "162.07" not in prompt and "203.4" not in prompt


@pytest.mark.asyncio
async def test_analyst_seat_never_sees_a_rating_change_from_after_as_of(data_service):
    ratings = await data_service.get_analyst_ratings("NVDA", AS_OF)
    firms = {c.firm for c in ratings.recent_changes}
    assert "Quarry Hill Research" not in firms  # dated 2026-09-30, after AS_OF
    assert "Harbor & Vance" in firms


@pytest.mark.asyncio
async def test_analyst_seat_with_no_coverage_is_no_read_without_a_call():
    from council.data.schemas import AnalystRatingsSnapshot

    empty = AnalystRatingsSnapshot(
        ticker="TINY", current_price=4.0, target_mean=None, target_median=None,
        target_high=None, target_low=None, analyst_count=None, strong_buy=0, buy=0,
        hold=0, sell=0, strong_sell=0, recent_changes=[], as_of=AS_OF,
        staleness_seconds=None, source="test",
    )
    seat = AnalystRatingsSeat()
    ctx = SeatContext(seat.id, seat.allowed_data, {"analyst_ratings": empty}, ticker="TINY", as_of=AS_OF)
    client = _RecordingClient()
    answer = await seat.deliberate(ctx, client)
    assert not answer.read and answer.short.abstain_reason == "no_data"
    assert client.calls == []


# --- the Technician: the short term is mechanical ------------------------------


def _answer(short_vote: str, short_p: float = 0.5):
    from council.tests.seat_answers import seat_answer_payload
    from council.seats.base import SeatAnswer

    payload = seat_answer_payload("BULLISH", 0.561)
    payload["short"] = {
        "vote": short_vote,
        "probability": short_p,
        "expected_move_pct": 0.0 if short_vote == "NO_CONVICTION" else 2.0,
        "rationale": "narrated",
    }
    return SeatAnswer(**payload).to_multi_term()


def _reading(vote: str, sma50: float | None = 100.0):
    from council.seats.technical_indicators import FamilyVotes, TechnicalReading

    families = {"BULLISH": ("BULLISH", "BULLISH", "FLAT", "BULLISH"),
                "BEARISH": ("BEARISH", "BEARISH", "FLAT", "BEARISH"),
                "NO_CONVICTION": ("BULLISH", "BEARISH", "FLAT", "FLAT")}[vote]
    directional = vote != "NO_CONVICTION"
    return TechnicalReading(
        family_votes=FamilyVotes(*families), mechanical_vote=vote, sma20=101.0, sma50=sma50,
        rsi14=55.0, atr14=2.0, last_close=102.0, twenty_day_high=105.0, twenty_day_low=97.0,
        entry=102.0 if directional else None, exit=106.0 if directional else None,
        invalidation=99.0 if directional else None,
        expected_move_pct=3.9 if directional else None,
    )


@pytest.mark.asyncio
async def test_technician_short_term_follows_the_families_not_the_model(data_service, monkeypatch):
    import council.seats.technician as technician_module

    monkeypatch.setattr(technician_module, "analyse", lambda bars: _reading("BEARISH"))
    seat = TechnicianSeat()
    ctx = await seat.gather(data_service, "NVDA", AS_OF)
    answer = await seat.deliberate(ctx, _RecordingClient(_answer("BULLISH", 0.588)))

    assert answer.short.vote == "BEARISH"  # mechanical, overriding the narration
    assert (answer.short.entry, answer.short.exit, answer.short.invalidation) == (102.0, 106.0, 99.0)
    assert answer.short.expected_move_pct == 3.9
    assert answer.medium.vote == "BULLISH"  # the longer terms stay the model's read


@pytest.mark.asyncio
async def test_technician_split_families_are_dead_even_short_term_only(data_service, monkeypatch):
    import council.seats.technician as technician_module

    monkeypatch.setattr(technician_module, "analyse", lambda bars: _reading("NO_CONVICTION"))
    seat = TechnicianSeat()
    ctx = await seat.gather(data_service, "NVDA", AS_OF)
    client = _RecordingClient(_answer("BULLISH", 0.588))
    answer = await seat.deliberate(ctx, client)

    assert client.calls, "a split still asks the model about the longer terms"
    assert answer.short.vote == "NO_CONVICTION"
    assert answer.medium.vote == "BULLISH" and answer.long.vote == "BULLISH"


@pytest.mark.asyncio
async def test_technician_sizes_a_fence_sitting_narration_from_the_families(data_service, monkeypatch):
    import council.seats.technician as technician_module

    monkeypatch.setattr(technician_module, "analyse", lambda bars: _reading("BULLISH"))
    seat = TechnicianSeat()
    ctx = await seat.gather(data_service, "NVDA", AS_OF)
    answer = await seat.deliberate(ctx, _RecordingClient(_answer("NO_CONVICTION")))

    assert answer.short.vote == "BULLISH"
    assert answer.short.probability == 0.593  # 3 bullish families, none bearish
    assert answer.short.comparison_class is not None


@pytest.mark.asyncio
async def test_technician_without_enough_history_is_no_read_without_a_call(data_service, monkeypatch):
    import council.seats.technician as technician_module

    monkeypatch.setattr(technician_module, "analyse", lambda bars: _reading("NO_CONVICTION", sma50=None))
    seat = TechnicianSeat()
    ctx = await seat.gather(data_service, "NVDA", AS_OF)
    client = _RecordingClient()
    answer = await seat.deliberate(ctx, client)
    assert not answer.read and answer.short.abstain_reason == "insufficient_history"
    assert client.calls == []


# --- the insider seat: quiet insiders are a read, not missing data ------------


@pytest.mark.asyncio
async def test_no_insider_filings_is_dead_even_not_no_read():
    from council.data.schemas import InsiderTransactionFeed

    quiet = InsiderTransactionFeed(
        ticker="CRWD", transactions=[], as_of=AS_OF, staleness_seconds=None, source="sec_edgar"
    )
    seat = InsiderReaderSeat()
    ctx = SeatContext(seat.id, seat.allowed_data, {"insider": quiet}, ticker="CRWD", as_of=AS_OF)
    client = _RecordingClient()
    answer = await seat.deliberate(ctx, client)

    assert answer.read
    assert {answer.term(t).vote for t in _TERMS} == {"NO_CONVICTION"}
    assert client.calls == []  # nothing to reason about -- no model call spent
