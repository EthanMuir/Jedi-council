"""Recursive JSON-safe converter. DeliberationResult and friends mix
dataclasses (RealityAnchor, CostAuditResult, RiskSizing, SeatResult,
LLMCallRecord, ...) with pydantic BaseModels nested inside them
(SeatVerdict, GrandMasterVerdict, ...) -- neither dataclasses.asdict() nor
BaseModel.model_dump() alone handles that mix, since each stops recursing
at the other's boundary. This walks the whole tree itself."""
from __future__ import annotations

import dataclasses
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return {k: to_jsonable(v) for k, v in obj.model_dump().items()}
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    return obj
