"""A real bug hit in practice: a live provider sent a garbage sentinel
expiry ("2099-99-99", not even a valid month) on one contract in an
options chain. Constructing every OptionContract in a single list
comprehension meant that one bad contract raised and killed the entire
snapshot -- taking the whole Oracle of Options seat down with it. One
malformed contract must not cost the other (valid) contracts in the same
chain."""
from __future__ import annotations

import pytest

from council.data.cache import DiskCache
from council.data.service import DataService


class _MixedContractsProvider:
    name = "mixed"

    async def fetch_option_chain(self, ticker: str) -> dict:
        return {
            "underlying_price": 100.0,
            "put_call_ratio": 0.9,
            "contracts": [
                {"strike": 100.0, "expiry": "2026-10-16", "option_type": "call", "bid": 2.1, "ask": 2.3},
                {"strike": 105.0, "expiry": "2099-99-99", "option_type": "call", "bid": 1.0, "ask": 1.2},
                {"strike": 95.0, "expiry": "2026-10-16", "option_type": "put", "bid": 1.5, "ask": 1.7},
            ],
        }


@pytest.mark.asyncio
async def test_malformed_contract_skipped_valid_ones_kept(tmp_path):
    service = DataService(
        providers=[_MixedContractsProvider()], cache=DiskCache(str(tmp_path / "cache.db"))
    )
    from datetime import datetime

    snapshot = await service.get_option_chain("TEST", as_of=datetime(2026, 9, 21))
    assert len(snapshot.contracts) == 2
    assert all(c.expiry.year == 2026 for c in snapshot.contracts)


class _AllMalformedProvider:
    name = "all_bad"

    async def fetch_option_chain(self, ticker: str) -> dict:
        return {
            "underlying_price": 100.0,
            "put_call_ratio": None,
            "contracts": [
                {"strike": 105.0, "expiry": "2099-99-99", "option_type": "call"},
            ],
        }


@pytest.mark.asyncio
async def test_all_contracts_malformed_yields_empty_list_not_a_crash(tmp_path):
    service = DataService(
        providers=[_AllMalformedProvider()], cache=DiskCache(str(tmp_path / "cache.db"))
    )
    from datetime import datetime

    snapshot = await service.get_option_chain("TEST", as_of=datetime(2026, 9, 21))
    assert snapshot.contracts == []
