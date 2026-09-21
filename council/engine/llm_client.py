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
from council.engine.model_catalog import get_model
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


T = TypeVar("T", bound=BaseModel)


class _RetryableProviderError(Exception):
    """A one-shot provider call failed transiently (connection, timeout,
    rate limit, 5xx) -- worth retrying with backoff. Every provider adapter
    below translates its own SDK's exceptions into this or
    _NonRetryableProviderError so the retry loop in get_structured stays
    provider-agnostic instead of special-casing three different exception
    hierarchies."""


class _NonRetryableProviderError(Exception):
    """A one-shot provider call failed for a reason retrying won't fix
    (auth, bad request, permission, ...)."""


async def _call_anthropic(
    client, model: str, system_prompt: str, user_prompt: str, schema: dict, tool_name: str
) -> tuple[dict, int, int]:
    try:
        resp = await client.messages.create(
            model=model,
            # A verbose model can run out of room mid-structure and drop a
            # later required field entirely rather than just writing a
            # too-long thesis -- some headroom above the realistic size of
            # one SeatVerdict's fields.
            max_tokens=3000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[
                {
                    "name": tool_name,
                    "description": f"Submit your structured {tool_name}.",
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
        )
    except _RETRYABLE_ANTHROPIC_ERRORS as exc:
        raise _RetryableProviderError(str(exc)) from exc
    except anthropic.APIStatusError as exc:
        raise _NonRetryableProviderError(str(exc)) from exc
    tool_use = next(b for b in resp.content if b.type == "tool_use")
    return tool_use.input, resp.usage.input_tokens, resp.usage.output_tokens


async def _call_openai(
    client, model: str, system_prompt: str, user_prompt: str, schema: dict, tool_name: str
) -> tuple[dict, int, int]:
    """Built and tested against a faked SDK object, same caveat as every
    other provider integration built this session without a real key to
    test against: this is the OpenAI SDK's long-stable function-calling
    shape (tools/tool_choice forcing a named function), not verified
    against a live response."""
    import openai as openai_sdk

    try:
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=3000,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": f"Submit your structured {tool_name}.",
                        "parameters": schema,
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": tool_name}},
        )
    except (
        openai_sdk.APIConnectionError,
        openai_sdk.APITimeoutError,
        openai_sdk.RateLimitError,
        openai_sdk.InternalServerError,
    ) as exc:
        raise _RetryableProviderError(str(exc)) from exc
    except openai_sdk.APIStatusError as exc:
        raise _NonRetryableProviderError(str(exc)) from exc
    tool_call = resp.choices[0].message.tool_calls[0]
    raw = json.loads(tool_call.function.arguments)
    usage = resp.usage
    return raw, usage.prompt_tokens, usage.completion_tokens


async def _call_gemini(
    client, model: str, system_prompt: str, user_prompt: str, schema: dict, tool_name: str
) -> tuple[dict, int, int]:
    """Built and tested against a faked SDK object -- unverified against a
    live Gemini response, same caveat as _call_openai above. Uses Gemini's
    native structured-output support (response_schema + a JSON mime type)
    rather than function calling, since it needs no named-tool indirection
    for a single-shape response the way Anthropic/OpenAI's tool-call
    pattern does."""
    from google.genai import errors as genai_errors
    from google.genai import types as genai_types

    try:
        resp = await client.aio.models.generate_content(
            model=model,
            contents=user_prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=schema,
                max_output_tokens=3000,
            ),
        )
    except genai_errors.ServerError as exc:
        raise _RetryableProviderError(str(exc)) from exc
    except genai_errors.ClientError as exc:
        raise _NonRetryableProviderError(str(exc)) from exc
    raw = json.loads(resp.text)
    usage = resp.usage_metadata
    input_tokens = getattr(usage, "prompt_token_count", 0) or 0
    output_tokens = getattr(usage, "candidates_token_count", 0) or 0
    return raw, input_tokens, output_tokens


# provider name -> (client attribute on LLMClient, one-shot call adapter).
# "_client" (not "_anthropic_client") for anthropic on purpose -- that's
# the attribute name every existing test already monkeypatches to inject a
# fake SDK object; renaming it would silently break them rather than fail
# loudly, since they'd be setting an attribute nothing reads anymore.
_PROVIDER_ADAPTERS = {
    "anthropic": ("_client", _call_anthropic),
    "openai": ("_openai_client", _call_openai),
    "google": ("_gemini_client", _call_gemini),
}


def _repair_seat_verdict_input(raw: dict) -> dict:
    """Two SeatVerdict failure modes keep recurring in live use even after
    telling the model about them via schema descriptions (Task #66's
    abstain_reason description, Task #67's max_tokens raise + field
    reorder) -- fixed here in code instead of spending two more real
    retries hoping the model gets it right this time:

    - vote=NO_READ with abstain_reason missing or empty. If the model
      instead put its reasoning in `thesis` -- a common substitution,
      since that's the field it's used to writing an explanation into --
      reuse that text rather than lose it; otherwise fall back to a
      generic placeholder. The model plainly tried to explain itself
      somewhere in the response; this just uses it in the right field.
    - thesis over the enforced 120-word cap. The model doesn't count
      words precisely; truncated to 120 rather than rejected outright.

    Only touches what's actually wrong -- everything else passes through
    for Pydantic to validate normally, including a genuinely malformed
    response that this can't repair (e.g. a bad vote/probability type),
    which still fails and still retries as before."""
    repaired = dict(raw)

    if repaired.get("vote") == "NO_READ" and not repaired.get("abstain_reason"):
        fallback = repaired.get("thesis") or "Seat abstained; no explicit reason was provided."
        repaired["abstain_reason"] = str(fallback)[:500]

    thesis = repaired.get("thesis")
    if isinstance(thesis, str):
        words = thesis.split()
        if len(words) > 120:
            repaired["thesis"] = " ".join(words[:120])

    return repaired


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
        self._openai_client = None
        self._gemini_client = None
        if not settings.resolved_no_llm:
            import anthropic

            self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            # Only constructed when routing could actually pick these --
            # resolve_route (Addendum A3) only ever resolves a seat to
            # openai/google when that provider's own key is configured, so
            # a seat can never reach _call_openai/_call_gemini with a None
            # client here.
            if settings.openai_api_key:
                import openai

                self._openai_client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
            if settings.google_api_key:
                from google import genai

                self._gemini_client = genai.Client(api_key=settings.google_api_key)

    def _estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        model_info = get_model(model)
        if not model_info:
            return 0.0
        return (input_tokens / 1_000_000) * model_info.input_price_per_mtok + (
            output_tokens / 1_000_000
        ) * model_info.output_price_per_mtok

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
        model = route.model
        client_attr, call_adapter = _PROVIDER_ADAPTERS[route.provider]
        client = getattr(self, client_attr)

        schema = response_model.model_json_schema()
        prompt_hash = self._prompt_hash(system_prompt, user_prompt)
        last_error: Exception | None = None
        last_error_retryable = False
        tool_name = f"submit_{response_model.__name__.lower()}"

        for attempt in range(1, max_retries + 2):  # initial attempt + max_retries
            start = time.monotonic()
            try:
                raw_input, input_tokens, output_tokens = await call_adapter(
                    client, model, system_prompt, user_prompt, schema, tool_name
                )
            except _RetryableProviderError as exc:
                last_error = exc
                last_error_retryable = True
                latency_ms = (time.monotonic() - start) * 1000
                self.call_log.append(
                    LLMCallRecord(
                        seat_id=seat_id,
                        model=model,
                        provider=route.provider,
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
            except _NonRetryableProviderError as exc:
                # Auth, bad request, permission, not found, conflict,
                # unprocessable -- a client-side problem no retry fixes.
                latency_ms = (time.monotonic() - start) * 1000
                self.call_log.append(
                    LLMCallRecord(
                        seat_id=seat_id,
                        model=model,
                        provider=route.provider,
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
                # Anything else escaping the adapter's own call -- e.g. a
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
                        provider=route.provider,
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
                if response_model is SeatVerdict:
                    raw_input = _repair_seat_verdict_input(raw_input)
                result = response_model(**raw_input)
            except (ValidationError, StopIteration, KeyError, TypeError) as exc:
                last_error = exc
                last_error_retryable = False
                latency_ms = (time.monotonic() - start) * 1000
                self.call_log.append(
                    LLMCallRecord(
                        seat_id=seat_id,
                        model=model,
                        provider=route.provider,
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
                    provider=route.provider,
                    prompt_hash=prompt_hash,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    cost_usd=self._estimate_cost(model, input_tokens, output_tokens),
                    attempt=attempt,
                    success=True,
                )
            )
            return result

        if last_error_retryable:
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
