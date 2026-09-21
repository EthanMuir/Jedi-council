"""Task #73 -- oracle_options picks its "ATM" contract as whichever strike
is closest to underlying_price (`min(contracts, key=lambda c: abs(c.strike
- underlying_price))`). With no real underlying price that pick is
meaningless -- it silently becomes the lowest-strike contract in the whole
chain, typically a near-worthless, zero-volume one with a garbage IV. This
is very likely the real cause of "ATM IV wildly inconsistent with the rest
of the chain" showing up on every ticker tested live so far, including the
exact same 0.78% on two unrelated tickers -- too precise a match to be two
independent bad quotes, far more likely the same systematic wrong-contract
selection every time.

Fixed two ways: a more robust underlying-price fallback chain in the
yfinance provider (see test_yfinance_provider.py), and this seat-level
guard, which abstains mechanically -- without spending a real LLM call on
data already known to be unusable -- whenever the price genuinely can't be
resolved."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from council.seats.base import SeatContext
from council.seats.oracle_options import OracleOptionsSeat


class _ExplodingLLMClient:
    """Fails the test if deliberate() reaches the LLM at all -- proves the
    underlying_price<=0 guard short-circuits before spending a real call."""

    class _Settings:
        seat_model = "claude-sonnet-5"

    settings = _Settings()

    async def get_verdict(self, **kwargs):
        raise AssertionError("get_verdict should not be called when underlying_price <= 0")


def _make_contract(strike: float, iv: float | None) -> dict:
    return {
        "strike": strike,
        "expiry": date(2026, 10, 16),
        "option_type": "call",
        "bid": 1.0,
        "ask": 1.2,
        "last": 1.1,
        "volume": 10,
        "open_interest": 100,
        "implied_volatility": iv,
    }


def _make_ctx(underlying_price: float, contracts: list[dict]) -> SeatContext:
    from council.data.schemas import OptionChainSnapshot

    chain = OptionChainSnapshot(
        ticker="NFLX",
        underlying_price=underlying_price,
        contracts=contracts,
        put_call_ratio=0.7,
        as_of=datetime(2026, 9, 21, 16, 0, 0),
        staleness_seconds=0.0,
        source="fixtures",
    )
    return SeatContext(
        seat_id="oracle_options",
        allowed=frozenset({"option_chain"}),
        data={"option_chain": chain},
        ticker="NFLX",
        as_of=datetime(2026, 9, 21, 16, 0, 0),
        horizon="1w",
    )


@pytest.mark.asyncio
async def test_zero_underlying_price_abstains_without_calling_the_llm():
    seat = OracleOptionsSeat()
    ctx = _make_ctx(underlying_price=0.0, contracts=[_make_contract(5.0, 0.0078)])

    verdict = await seat.deliberate(ctx, _ExplodingLLMClient())

    assert verdict.vote == "NO_READ"
    assert verdict.abstain_reason == "no_underlying_price"


@pytest.mark.asyncio
async def test_negative_underlying_price_also_abstains():
    # Shouldn't happen in practice, but a defensive <= 0 check should catch
    # it the same way a genuine 0.0 does.
    seat = OracleOptionsSeat()
    ctx = _make_ctx(underlying_price=-1.0, contracts=[_make_contract(5.0, 0.0078)])

    verdict = await seat.deliberate(ctx, _ExplodingLLMClient())

    assert verdict.vote == "NO_READ"
    assert verdict.abstain_reason == "no_underlying_price"


@pytest.mark.asyncio
async def test_positive_underlying_price_proceeds_to_the_llm(monkeypatch):
    seat = OracleOptionsSeat()
    ctx = _make_ctx(
        underlying_price=100.0,
        contracts=[_make_contract(100.0, 0.30), _make_contract(150.0, 0.28)],
    )

    called = {}

    class _RecordingLLMClient:
        class _Settings:
            seat_model = "claude-sonnet-5"

        settings = _Settings()

        async def get_verdict(self, **kwargs):
            called["user_prompt"] = kwargs["user_prompt"]
            from council.seats.base import SeatVerdict

            return SeatVerdict(
                vote="NO_READ",
                probability=0.5,
                expected_move_pct=0.0,
                thesis="fine",
                what_would_change_my_mind="n/a",
                data_quality="GOOD",
                abstain_reason="test",
            )

    await seat.deliberate(ctx, _RecordingLLMClient())
    assert "ATM strike: 100.0" in called["user_prompt"]
