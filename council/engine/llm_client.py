"""Structured-output wrapper around the Anthropic SDK. Every call -- live or
fixture -- is logged (model, prompt hash, tokens, latency, cost) per
engineering rule #2. In --no-llm / fixture mode, no network call is made at
all; a recorded SeatVerdict is loaded and validated through the same schema
a live call would have to pass."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from council.config import Settings
from council.seats.base import SeatVerdict

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "seat_verdicts"

# Rough $ per million tokens. Approximate on purpose -- good enough for the
# Cost Auditor's relative comparisons, not an invoice.
_PRICING_PER_MTOK = {
    "claude-sonnet-5": {"input": 3.0, "output": 15.0},
    "claude-opus-5": {"input": 15.0, "output": 75.0},
}


@dataclass
class LLMCallRecord:
    seat_id: str
    model: str
    provider: str
    prompt_hash: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float
    attempt: int
    success: bool
    error: str | None = None


class SchemaRetryExhausted(Exception):
    pass


class LLMClient:
    def __init__(self, settings: Settings, call_log: list[LLMCallRecord] | None = None):
        self.settings = settings
        self.call_log: list[LLMCallRecord] = call_log if call_log is not None else []
        self._client = None
        if not settings.resolved_no_llm:
            import anthropic

            self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    def _estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        pricing = _PRICING_PER_MTOK.get(model)
        if not pricing:
            return 0.0
        return (input_tokens / 1_000_000) * pricing["input"] + (
            output_tokens / 1_000_000
        ) * pricing["output"]

    def _prompt_hash(self, system_prompt: str, user_prompt: str) -> str:
        return hashlib.sha256((system_prompt + "\x00" + user_prompt).encode()).hexdigest()[:16]

    async def _fixture_verdict(self, seat_id: str, fixture_name: str) -> SeatVerdict:
        path = _FIXTURE_DIR / f"{fixture_name}.json"
        with open(path) as f:
            data = json.load(f)
        verdict = SeatVerdict(**data)
        self.call_log.append(
            LLMCallRecord(
                seat_id=seat_id,
                model="fixture",
                provider="none",
                prompt_hash="fixture",
                input_tokens=0,
                output_tokens=0,
                latency_ms=0.0,
                cost_usd=0.0,
                attempt=1,
                success=True,
            )
        )
        return verdict

    async def get_verdict(
        self,
        *,
        seat_id: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        fixture_name: str | None = None,
        max_retries: int = 2,
    ) -> SeatVerdict:
        if self.settings.resolved_no_llm:
            return await self._fixture_verdict(seat_id, fixture_name or seat_id)

        schema = SeatVerdict.model_json_schema()
        prompt_hash = self._prompt_hash(system_prompt, user_prompt)
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 2):  # initial attempt + max_retries
            start = time.monotonic()
            try:
                resp = await self._client.messages.create(
                    model=model,
                    max_tokens=1500,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                    tools=[
                        {
                            "name": "submit_verdict",
                            "description": "Submit your structured seat verdict.",
                            "input_schema": schema,
                        }
                    ],
                    tool_choice={"type": "tool", "name": "submit_verdict"},
                )
                latency_ms = (time.monotonic() - start) * 1000
                tool_use = next(b for b in resp.content if b.type == "tool_use")
                verdict = SeatVerdict(**tool_use.input)
                self.call_log.append(
                    LLMCallRecord(
                        seat_id=seat_id,
                        model=model,
                        provider="anthropic",
                        prompt_hash=prompt_hash,
                        input_tokens=resp.usage.input_tokens,
                        output_tokens=resp.usage.output_tokens,
                        latency_ms=latency_ms,
                        cost_usd=self._estimate_cost(
                            model, resp.usage.input_tokens, resp.usage.output_tokens
                        ),
                        attempt=attempt,
                        success=True,
                    )
                )
                return verdict
            except (ValidationError, StopIteration, KeyError, TypeError) as exc:
                last_error = exc
                latency_ms = (time.monotonic() - start) * 1000
                self.call_log.append(
                    LLMCallRecord(
                        seat_id=seat_id,
                        model=model,
                        provider="anthropic",
                        prompt_hash=prompt_hash,
                        input_tokens=0,
                        output_tokens=0,
                        latency_ms=latency_ms,
                        cost_usd=0.0,
                        attempt=attempt,
                        success=False,
                        error=str(exc),
                    )
                )
                continue

        return SeatVerdict(
            vote="NO_READ",
            probability=0.5,
            expected_move_pct=0.0,
            thesis=f"Schema validation failed after {max_retries} retries: {last_error}"[:500],
            what_would_change_my_mind="N/A",
            data_quality="POOR",
            abstain_reason="schema_failure",
        )
