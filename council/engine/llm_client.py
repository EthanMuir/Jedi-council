"""Structured-output wrapper around the Anthropic SDK. Every call -- live or
fixture -- is logged (model, prompt hash, tokens, latency, cost) per
engineering rule #2. In --no-llm / fixture mode, no network call is made at
all; a recorded object is loaded and validated through the same schema a
live call would have to pass.

`get_structured` is the generic primitive (any Pydantic response model);
`get_verdict` is a thin SeatVerdict-specific wrapper that preserves the
NO_READ-on-schema-failure fallback Tier I seats rely on. Tier II-IV
(debate, Prosecutor, Grand Master) call `get_structured` directly and pick
their own fallback, since "abstain" doesn't mean the same thing for a
debate argument as it does for an analyst's vote.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from council.config import Settings
from council.engine.routing import resolve_route
from council.seats.base import SeatVerdict, format_memory_context

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "seat_verdicts"

# Connection/timeout/rate-limit/5xx -- transient, worth retrying with backoff.
# Everything else under APIStatusError (auth, bad request, permission, not
# found, conflict, unprocessable) is a client-side problem retrying won't fix.
_RETRYABLE_ANTHROPIC_ERRORS = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 8.0


def _backoff_seconds(attempt: int) -> float:
    return min(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), _BACKOFF_CAP_SECONDS)

# Rough $ per million tokens. Approximate on purpose -- good enough for the
# Cost Auditor's relative comparisons, not an invoice.
_PRICING_PER_MTOK = {
    "claude-sonnet-5": {"input": 3.0, "output": 15.0},
    "claude-opus-5": {"input": 15.0, "output": 75.0},
}

T = TypeVar("T", bound=BaseModel)


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
    """Raised by get_structured when every retry fails schema validation.
    Callers choose their own fallback -- Tier I abstains (NO_READ), a
    debate round skips, the Prosecutor stays silent, Grand Master aborts."""


class LLMCallFailed(Exception):
    """Raised by get_structured when the network/API call itself fails --
    either a transient error (connection, timeout, rate limit, 5xx) that
    exhausted its backoff retries, or a non-retryable error (auth, bad
    request, ...) that failed immediately. Distinct from
    SchemaRetryExhausted, which means the API responded fine but the
    structured payload never validated. Callers degrade the same way for
    both -- see SchemaRetryExhausted's own docstring."""


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

    async def _fixture_structured(
        self, seat_id: str, fixture_name: str, response_model: type[T], sample_index: int = 0
    ) -> T:
        """Sample N looks for `{fixture_name}_{N}.json` (1-indexed, for
        seats with hand-authored per-sample variants -- see
        insider_reader_1/2/3.json) and falls back to the single base
        fixture, which every sample then shares (dispersion 0, a legitimate
        degenerate case for a deterministic recorded fixture)."""
        variant_path = _FIXTURE_DIR / f"{fixture_name}_{sample_index + 1}.json"
        path = variant_path if variant_path.exists() else _FIXTURE_DIR / f"{fixture_name}.json"
        with open(path) as f:
            data = json.load(f)
        result = response_model(**data)
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
        return result

    async def get_structured(
        self,
        *,
        seat_id: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        fixture_name: str | None = None,
        sample_index: int = 0,
        max_retries: int = 2,
    ) -> T:
        if self.settings.resolved_no_llm:
            return await self._fixture_structured(
                seat_id, fixture_name or seat_id, response_model, sample_index
            )

        route = resolve_route(seat_id, self.settings, default_model=model)
        if route.provider != "anthropic":
            # Routing infrastructure (Addendum A3) exists and correctly
            # resolved a heterogeneous provider -- but live calling for
            # anything other than Anthropic isn't wired up yet. This should
            # be unreachable until an OPENAI_API_KEY / GOOGLE_API_KEY is
            # actually configured, at which point it's the next thing to build.
            raise NotImplementedError(
                f"seat '{seat_id}' routed to provider '{route.provider}' "
                f"(model '{route.model}'), but live calling for non-Anthropic "
                "providers is not implemented yet -- only routing resolution is."
            )
        model = route.model

        schema = response_model.model_json_schema()
        prompt_hash = self._prompt_hash(system_prompt, user_prompt)
        last_error: Exception | None = None
        tool_name = f"submit_{response_model.__name__.lower()}"

        for attempt in range(1, max_retries + 2):  # initial attempt + max_retries
            start = time.monotonic()
            try:
                resp = await self._client.messages.create(
                    model=model,
                    max_tokens=1500,
                    temperature=0.3,  # Addendum A3: fixed across providers so dispersion
                    # measures model behaviour, not sampling config drift
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                    tools=[
                        {
                            "name": tool_name,
                            "description": f"Submit your structured {response_model.__name__}.",
                            "input_schema": schema,
                        }
                    ],
                    tool_choice={"type": "tool", "name": tool_name},
                )
            except _RETRYABLE_ANTHROPIC_ERRORS as exc:
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
                if attempt <= max_retries:
                    await asyncio.sleep(_backoff_seconds(attempt))
                continue
            except anthropic.APIStatusError as exc:
                # Auth, bad request, permission, not found, conflict,
                # unprocessable -- a client-side problem no retry fixes.
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
                raise LLMCallFailed(
                    f"{seat_id}: non-retryable API error ({exc.__class__.__name__}): {exc}"
                ) from exc
            except Exception as exc:
                # Anything else escaping the SDK call itself -- e.g. a
                # TypeError from an unsupported kwarg on an unexpected SDK
                # version -- is a code/environment problem, not the model's
                # fault, and retrying it 2 more times will fail identically
                # every time. Fail fast with a clear, single-layer message
                # instead of silently burning retries and mislabeling this
                # as a schema validation failure (the next except clause,
                # which is specifically about the model's own output shape).
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
                raise LLMCallFailed(
                    f"{seat_id}: unexpected error calling the API ({exc.__class__.__name__}): {exc}"
                ) from exc

            # A response came back -- only failures from HERE on are
            # genuinely "the model didn't produce a valid structured answer".
            try:
                latency_ms = (time.monotonic() - start) * 1000
                tool_use = next(b for b in resp.content if b.type == "tool_use")
                result = response_model(**tool_use.input)
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
            return result

        if isinstance(last_error, _RETRYABLE_ANTHROPIC_ERRORS):
            raise LLMCallFailed(
                f"{seat_id}: still failing after {max_retries} retries: {last_error}"
            ) from last_error
        raise SchemaRetryExhausted(
            f"{seat_id}: schema validation failed after {max_retries} retries: {last_error}"
        )

    async def get_verdict(
        self,
        *,
        seat_id: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        fixture_name: str | None = None,
        sample_index: int = 0,
        memories: list | None = None,
        max_retries: int = 2,
    ) -> SeatVerdict:
        if memories:
            user_prompt = user_prompt + format_memory_context(memories)
        try:
            return await self.get_structured(
                seat_id=seat_id,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=SeatVerdict,
                fixture_name=fixture_name,
                sample_index=sample_index,
                max_retries=max_retries,
            )
        except SchemaRetryExhausted as exc:
            return SeatVerdict(
                vote="NO_READ",
                probability=0.5,
                expected_move_pct=0.0,
                thesis=f"Schema validation failed after {max_retries} retries: {exc}"[:500],
                what_would_change_my_mind="N/A",
                data_quality="POOR",
                abstain_reason="schema_failure",
            )
        except LLMCallFailed as exc:
            return SeatVerdict(
                vote="NO_READ",
                probability=0.5,
                expected_move_pct=0.0,
                thesis=f"LLM call failed: {exc}"[:500],
                what_would_change_my_mind="N/A",
                data_quality="POOR",
                abstain_reason="llm_call_failed",
            )
