"""Task #61 -- `inspect-data` runs each competent seat's real gather() and
prints exactly what came back, with zero LLM calls anywhere in the path.
This is the only way to get certainty the data layer actually works when
NO_LLM=true, since every seat's thesis/probability is a canned fixture
placeholder in that mode regardless of what gather() fetched -- see the
technician-seat-output-identical-across-tickers bug this was built to
prevent a repeat of."""
from __future__ import annotations

import pytest

import council.cli as cli
from council.config import Settings


@pytest.fixture
def fixture_settings(tmp_path):
    return Settings(
        use_data_fixtures=True,
        council_db_path=str(tmp_path / "council.db"),
        cache_db_path=str(tmp_path / "cache.db"),
    )


@pytest.mark.asyncio
async def test_dump_data_prints_real_ticker_specific_ohlcv(fixture_settings, capsys):
    await cli._dump_data("NVDA", "1w", fixture_settings)
    out = capsys.readouterr().out

    assert "RAW DATA DUMP -- NVDA @ 1w" in out
    assert "[technician]" in out
    assert '"trade_date": "2026-09-18"' in out
    assert '"close": 162.07' in out


@pytest.mark.asyncio
async def test_dump_data_skips_seats_not_competent_at_horizon(fixture_settings, capsys):
    # macro_sage and fundamentalist are both 0.0-competence at 1d (see
    # COMPETENCE_MATRIX in horizons.py) -- they must be listed as skipped,
    # not silently omitted, and must not get a gather() section.
    await cli._dump_data("NVDA", "1d", fixture_settings)
    out = capsys.readouterr().out

    assert "skipped: " in out
    assert "macro_sage" in out.split("skipped:")[1].split("\n")[0]
    assert "fundamentalist" in out.split("skipped:")[1].split("\n")[0]
    assert "[macro_sage]" not in out
    assert "[fundamentalist]" not in out


@pytest.mark.asyncio
async def test_dump_data_reports_seat_gather_failure_without_stopping(
    fixture_settings, capsys, monkeypatch
):
    class _ExplodingSeat:
        id = "technician"
        title = "The Technician"
        allowed_data = frozenset({"ohlcv"})

        async def gather(self, data_service, ticker, as_of, horizon):
            raise RuntimeError("provider is on fire")

    real_seats = cli.TIER_I_SEATS
    patched = [_ExplodingSeat() if s.id == "technician" else s for s in real_seats]
    monkeypatch.setattr(cli, "TIER_I_SEATS", patched)

    await cli._dump_data("NVDA", "1w", fixture_settings)
    out = capsys.readouterr().out

    assert "[technician]" in out
    assert "FAILED: provider is on fire" in out
    # a later competent seat still gets its own section -- one seat
    # blowing up must not kill the rest of the dump
    assert "[oracle_options]" in out


# Task #71 -- the full 12-seat dump routinely exceeds a terminal's
# scrollback (confirmed live: a user unable to see macro_sage's output
# because a later seat's error had pushed it off screen). --seat narrows
# the dump to exactly one seat.


@pytest.mark.asyncio
async def test_dump_data_seat_filter_prints_only_that_seat(fixture_settings, capsys):
    await cli._dump_data("NVDA", "1w", fixture_settings, seat_id="macro_sage")
    out = capsys.readouterr().out

    assert "[macro_sage]" in out
    assert "[technician]" not in out
    assert "[oracle_options]" not in out
    assert "skipped:" not in out  # not relevant when only one seat was asked for


@pytest.mark.asyncio
async def test_dump_data_seat_filter_rejects_unknown_seat_id(fixture_settings, capsys):
    await cli._dump_data("NVDA", "1w", fixture_settings, seat_id="not_a_real_seat")
    out = capsys.readouterr().out

    assert "Unknown seat 'not_a_real_seat'" in out
    assert "macro_sage" in out  # the valid-ids list is printed to help


@pytest.mark.asyncio
async def test_dump_data_seat_filter_rejects_seat_not_competent_at_horizon(
    fixture_settings, capsys
):
    # macro_sage is 0.0-competence at 1d -- asking for it there should say
    # so clearly, not silently print nothing.
    await cli._dump_data("NVDA", "1d", fixture_settings, seat_id="macro_sage")
    out = capsys.readouterr().out

    assert "not competent at 1d" in out
    assert "[macro_sage]" not in out
