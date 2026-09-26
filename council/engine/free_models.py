"""Free mode's model choice. Free-tier model names change every few months
(new versions ship, old ones get retired or moved off the free tier), so
instead of hardcoding one, ask the provider which models this key can use
and pick the best free-tier one from that list. The answer is cached for a
few hours per process -- listing models is free but not instant.

Gemini: the newest non-preview "Flash-Lite" model, the family with the most
generous free daily limit. Groq: its strongest open models, with seats
spread across them, because Groq's free limits (tokens per minute and per
day) apply to each model separately."""
from __future__ import annotations

import re
import time
import zlib

_CACHE_SECONDS = 6 * 3600
_cache: dict[str, tuple[float, list[str]]] = {}

# Used when listing fails -- Google's own always-current alias.
GEMINI_FALLBACK = "gemini-flash-lite-latest"

# Best first. Only models that handle forced tool calls reliably.
GROQ_PREFERENCE = [
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "qwen/qwen3-32b",
    "openai/gpt-oss-20b",
]
_GROQ_SPREAD = 3

_FLASH_LITE = re.compile(r"^(?:models/)?gemini-(\d+(?:\.\d+)?)-flash-lite(.*)$")
_UNSUITABLE_SUFFIXES = ("tts", "image", "audio", "live", "embedding")


def pick_gemini(names: list[str]) -> str | None:
    best: tuple[float, int, int, str] | None = None
    for name in names:
        match = _FLASH_LITE.match(name)
        if not match:
            continue
        version, suffix = float(match.group(1)), match.group(2)
        if any(word in suffix for word in _UNSUITABLE_SUFFIXES):
            continue
        stable = 0 if ("preview" in suffix or "exp" in suffix) else 1
        # Newest version first, then stable over preview, then the plain
        # name over dated variants of it.
        rank = (version, stable, -len(suffix), name.removeprefix("models/"))
        if best is None or rank[:3] > best[:3]:
            best = rank
    return best[3] if best else None


def pick_groq(model_ids: list[str], seat_id: str) -> str | None:
    available = [m for m in GROQ_PREFERENCE if m in model_ids][:_GROQ_SPREAD]
    if not available:
        return None
    return available[zlib.crc32(seat_id.encode()) % len(available)]


async def _cached_list(provider: str, fetch) -> list[str]:
    hit = _cache.get(provider)
    if hit and time.monotonic() - hit[0] < _CACHE_SECONDS:
        return hit[1]
    try:
        names = await fetch()
    except Exception:  # noqa: BLE001 -- fall back to a known-good name below
        return []
    _cache[provider] = (time.monotonic(), names)
    return names


def _gemini_fetcher(client):
    async def fetch() -> list[str]:
        names = []
        async for model in await client.aio.models.list():
            actions = getattr(model, "supported_actions", None)
            if actions is None or "generateContent" in actions:
                names.append(model.name)
        return names

    return fetch


async def resolve_gemini(client) -> str:
    return pick_gemini(await _cached_list("google", _gemini_fetcher(client))) or GEMINI_FALLBACK


def pick_google_model(names: list[str], wanted: str) -> str | None:
    """The name Google actually uses for a paid Gemini model the catalog
    names by family ("gemini-3-pro" may only exist as
    "gemini-3-pro-preview"): the exact name if listed, else the stable
    variant, else the shortest preview of it."""
    plain = [n.removeprefix("models/") for n in names]
    if wanted in plain:
        return wanted
    variants = [
        n for n in plain
        if n.startswith(wanted + "-") and not any(w in n[len(wanted):] for w in _UNSUITABLE_SUFFIXES)
    ]
    if not variants:
        return None
    return min(variants, key=lambda n: ("preview" in n or "exp" in n, len(n), n))


async def resolve_google_model(client, wanted: str) -> str:
    names = await _cached_list("google", _gemini_fetcher(client))
    return pick_google_model(names, wanted) or wanted


async def resolve_groq(client, seat_id: str) -> str:
    async def fetch() -> list[str]:
        page = await client.models.list()
        return [model.id for model in page.data]

    ids = await _cached_list("groq", fetch)
    return pick_groq(ids, seat_id) or pick_groq(GROQ_PREFERENCE, seat_id)


def clear_cache() -> None:
    _cache.clear()


# ---- pacing: stay under free tiers' per-minute limits ----------------------
# Sending requests in bursts (the Council runs up to 8 at once) earned real
# "429 Too Many Requests" replies on free Gemini, which allows about 15
# requests a minute. Free-tier calls are instead given evenly spaced slots a
# little under each limit. Groq's free models are also capped on tokens per
# minute, per model, which is the tighter limit for seat-sized prompts.
GEMINI_REQUESTS_PER_MINUTE = 14
GROQ_REQUESTS_PER_MINUTE = 28
GROQ_TOKENS_PER_MINUTE = {
    "openai/gpt-oss-120b": 7_500,
    "llama-3.3-70b-versatile": 11_000,
    "openai/gpt-oss-20b": 7_500,
}
GROQ_DEFAULT_TOKENS_PER_MINUTE = 5_500


def estimate_call_tokens(system_prompt: str, user_prompt: str) -> int:
    """Rough prompt size (about 4 characters a token) plus a typical
    structured answer."""
    return (len(system_prompt) + len(user_prompt)) // 4 + 1_200


def pace_interval_seconds(provider: str, model: str, est_tokens: int) -> float:
    """Seconds to leave before the next free-tier call to this model."""
    if provider == "google":
        return 60.0 / GEMINI_REQUESTS_PER_MINUTE
    if provider == "groq":
        tokens_per_minute = GROQ_TOKENS_PER_MINUTE.get(model, GROQ_DEFAULT_TOKENS_PER_MINUTE)
        return max(60.0 / GROQ_REQUESTS_PER_MINUTE, 60.0 * est_tokens / tokens_per_minute)
    return 0.0
