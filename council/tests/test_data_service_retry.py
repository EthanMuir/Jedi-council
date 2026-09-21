"""Phase 6: DataService retries a flaky provider with backoff before
falling through to the next one -- most failures at this layer are a
single transient blip, not the provider being genuinely down."""
from __future__ import annotations

import pytest

import council.data.service as data_service_module
from council.data.cache import DiskCache
from council.data.service import DataService


class _FlakyProvider:
    name = "flaky"

    def __init__(self, fail_times: int, result: str = "ok"):
        self._fail_times = fail_times
        self._result = result
        self.calls = 0

    async def fetch_thing(self, *args):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise ConnectionError("transient blip")
        return self._result


class _AlwaysFailsProvider:
    name = "always_fails"

    def __init__(self):
        self.calls = 0

    async def fetch_thing(self, *args):
        self.calls += 1
        raise ConnectionError("provider is genuinely down")


class _BackstopProvider:
    name = "backstop"

    def __init__(self):
        self.calls = 0

    async def fetch_thing(self, *args):
        self.calls += 1
        return "backstop result"


def _fast_backoff(monkeypatch):
    monkeypatch.setattr(data_service_module, "_PROVIDER_BACKOFF_BASE_SECONDS", 0.001)
    monkeypatch.setattr(data_service_module, "_PROVIDER_BACKOFF_CAP_SECONDS", 0.001)


@pytest.mark.asyncio
async def test_flaky_provider_recovers_within_retry_budget(tmp_path, monkeypatch):
    _fast_backoff(monkeypatch)
    provider = _FlakyProvider(fail_times=2)
    service = DataService(providers=[provider], cache=DiskCache(str(tmp_path / "cache.db")))

    result = await service._fetch_with_fallback("fetch_thing", "test:key:1", 60)

    assert result == "ok"
    assert provider.calls == 3


@pytest.mark.asyncio
async def test_provider_exhausting_retries_falls_through_to_next_provider(tmp_path, monkeypatch):
    _fast_backoff(monkeypatch)
    dead = _AlwaysFailsProvider()
    backstop = _BackstopProvider()
    service = DataService(providers=[dead, backstop], cache=DiskCache(str(tmp_path / "cache.db")))

    result = await service._fetch_with_fallback("fetch_thing", "test:key:2", 60)

    assert result == "backstop result"
    # initial attempt + 2 retries against the dead provider before falling through
    assert dead.calls == 3
    assert backstop.calls == 1


@pytest.mark.asyncio
async def test_all_providers_exhausted_raises_runtime_error(tmp_path, monkeypatch):
    _fast_backoff(monkeypatch)
    service = DataService(
        providers=[_AlwaysFailsProvider()], cache=DiskCache(str(tmp_path / "cache.db"))
    )
    with pytest.raises(RuntimeError):
        await service._fetch_with_fallback("fetch_thing", "test:key:3", 60)


class _NamedFailure:
    """A real bug hit in practice: the final RuntimeError only ever showed
    the LAST provider's failure -- usually the Yahoo backstop's expected
    "doesn't cover this domain" -- hiding what actually went wrong with the
    earlier providers (an unset key, a blocked free-tier endpoint, ...),
    the ones actually worth diagnosing."""

    def __init__(self, name: str, message: str):
        self.name = name
        self._message = message

    async def fetch_thing(self, *args):
        raise RuntimeError(self._message)


@pytest.mark.asyncio
async def test_all_provider_failures_are_reported_not_just_the_last(tmp_path, monkeypatch):
    _fast_backoff(monkeypatch)
    service = DataService(
        providers=[
            _NamedFailure("alpha_vantage", "rate limited"),
            _NamedFailure("fmp", "endpoint requires a paid plan"),
        ],
        cache=DiskCache(str(tmp_path / "cache.db")),
    )
    with pytest.raises(RuntimeError) as excinfo:
        await service._fetch_with_fallback("fetch_thing", "test:key:5", 60)
    message = str(excinfo.value)
    assert "alpha_vantage" in message and "rate limited" in message
    assert "fmp" in message and "endpoint requires a paid plan" in message


class _NoSuchMethodProvider:
    """A real bug hit in practice: AlphaVantageProvider doesn't implement
    fetch_analyst_estimates (only FMPProvider does), and resolving the
    method via getattr() happened outside any try/except -- so a provider
    simply not covering this data domain crashed immediately instead of
    falling through to the next provider that does."""

    name = "no_such_method"


@pytest.mark.asyncio
async def test_provider_missing_method_falls_through_immediately(tmp_path, monkeypatch):
    _fast_backoff(monkeypatch)
    missing = _NoSuchMethodProvider()
    backstop = _BackstopProvider()
    service = DataService(providers=[missing, backstop], cache=DiskCache(str(tmp_path / "cache.db")))

    result = await service._fetch_with_fallback("fetch_thing", "test:key:4", 60)

    assert result == "backstop result"
    # no retries wasted on a method that will never exist
    assert backstop.calls == 1
