"""to_jsonable must handle the full DeliberationResult tree -- dataclasses
containing pydantic models containing dataclasses -- and the result must
be genuinely JSON-serializable, not just superficially dict-shaped."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import pytest
from pydantic import BaseModel

from council.api.serialize import to_jsonable
from council.config import Settings
from council.engine.orchestrator import run_deliberation


class _InnerModel(BaseModel):
    value: float
    when: datetime


@dataclass
class _OuterDataclass:
    name: str
    inner: _InnerModel
    items: list[_InnerModel]


def test_dataclass_wrapping_pydantic_model_is_fully_converted():
    obj = _OuterDataclass(
        name="x",
        inner=_InnerModel(value=1.5, when=datetime(2026, 1, 1)),
        items=[_InnerModel(value=2.5, when=datetime(2026, 1, 2))],
    )
    result = to_jsonable(obj)
    assert result == {
        "name": "x",
        "inner": {"value": 1.5, "when": "2026-01-01T00:00:00"},
        "items": [{"value": 2.5, "when": "2026-01-02T00:00:00"}],
    }
    json.dumps(result)  # must not raise


def test_plain_values_pass_through():
    assert to_jsonable(1) == 1
    assert to_jsonable("s") == "s"
    assert to_jsonable(None) is None
    assert to_jsonable([1, 2, 3]) == [1, 2, 3]


@pytest.mark.asyncio
async def test_full_deliberation_result_is_json_serializable(tmp_path):
    settings = Settings(
        no_llm=True,
        use_data_fixtures=True,
        council_db_path=str(tmp_path / "council.db"),
        cache_db_path=str(tmp_path / "cache.db"),
    )
    result = await run_deliberation("NVDA", settings, as_of=datetime(2026, 9, 18, 16, 0, 0))

    payload = to_jsonable(result)
    dumped = json.dumps(payload)  # the real test: must not raise
    assert '"synthesis"' in dumped
    assert '"seat_results"' in dumped
    # spot-check a nested pydantic-inside-dataclass field actually unwrapped
    assert payload["seat_results"][0]["verdicts"]["short"]["vote"] in ("BULLISH", "BEARISH", "NO_CONVICTION")
    assert payload["terms"]["long"]["position"]["lean_label"]
