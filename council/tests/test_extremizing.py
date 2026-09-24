"""Addendum A4 Stage 3: extremizing is a no-op at alpha=1.0 (the default),
and both p_raw/p_extremized always get stored regardless."""
from __future__ import annotations

from datetime import datetime

import pytest

from council.config import Settings
from council.crypt.db import connect
from council.engine.aggregation import extremize
from council.engine.orchestrator import run_deliberation

AS_OF = datetime(2026, 9, 18, 16, 0, 0)


def test_extremize_is_noop_at_alpha_1():
    assert extremize(0.613, alpha=1.0) == 0.613


def test_extremize_pushes_away_from_half_when_alpha_above_1():
    p = extremize(0.6, alpha=1.5)
    assert p > 0.6


def test_extremize_symmetric_around_half():
    high = extremize(0.7, alpha=1.5)
    low = extremize(0.3, alpha=1.5)
    assert high == pytest.approx(1 - low, abs=1e-6)


def test_extremize_handles_boundary_probabilities():
    assert extremize(0.0, alpha=1.5) == 0.0
    assert extremize(1.0, alpha=1.5) == 1.0


@pytest.mark.asyncio
async def test_p_raw_and_p_extremized_stored_on_every_prediction(tmp_path):
    settings = Settings(
        no_llm=True,
        use_data_fixtures=True,
        council_db_path=str(tmp_path / "council.db"),
        cache_db_path=str(tmp_path / "cache.db"),
    )
    result = await run_deliberation("NVDA", settings, as_of=AS_OF)

    conn = connect(settings.council_db_path)
    rows = conn.execute(
        "SELECT p_raw, p_extremized FROM predictions WHERE run_id = ?", (result.run_id,)
    ).fetchall()
    conn.close()

    assert len(rows) == 3  # one per term
    for row in rows:
        assert row["p_raw"] is not None
        assert row["p_extremized"] is not None
        assert row["p_raw"] == row["p_extremized"]  # alpha=1.0 default -> no-op
