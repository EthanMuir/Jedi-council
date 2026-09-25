"""Tries a key before it's saved, so a half-copied or wrong key shows up in
the setup wizard (and Settings) rather than as a failed first run.

Each check is one small request to the provider: listing models for the AI
providers (free), a one-word Claude reply for Anthropic (costs a fraction of
a cent, and is the only way to tell an account with no credit from a working
one), one series lookup for FRED and one quote for Alpha Vantage (one of its
25 free requests a day).

The result is one of:
  "ok"          the key works
  "warn"        the key is real but something needs doing (no credit, a
                used-up daily limit); save it and say what
  "bad"         the provider turned the key down; don't save it
  "unchecked"   the provider couldn't be reached; save it and say so"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

TIMEOUT = 12


@dataclass
class KeyCheck:
    status: str
    message: str

    def as_dict(self) -> dict:
        return {"status": self.status, "message": self.message, "ok": self.status in ("ok", "warn", "unchecked")}


_PREFIX_HINT = {
    "anthropic_api_key": "It should start with sk-ant-.",
    "google_api_key": "It should start with AIza.",
    "groq_api_key": "It should start with gsk_.",
    "openai_api_key": "It should start with sk-.",
    "fred_api_key": "It should be 32 letters and numbers.",
    "alpha_vantage_api_key": "",
}

_LABEL = {
    "anthropic_api_key": "Anthropic",
    "google_api_key": "Google",
    "groq_api_key": "Groq",
    "openai_api_key": "OpenAI",
    "fred_api_key": "FRED",
    "alpha_vantage_api_key": "Alpha Vantage",
}


def _bad(name: str) -> KeyCheck:
    hint = _PREFIX_HINT.get(name, "")
    return KeyCheck("bad", f"{_LABEL[name]} didn't accept that key. Check you copied all of it. {hint}".strip())


def _body_text(resp: httpx.Response) -> str:
    try:
        return resp.text.lower()
    except Exception:  # noqa: BLE001
        return ""


async def _anthropic(client: httpx.AsyncClient, key: str) -> KeyCheck:
    resp = await client.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": "claude-haiku-4-5-20251001", "max_tokens": 1, "messages": [{"role": "user", "content": "Hi"}]},
    )
    body = _body_text(resp)
    if resp.status_code == 200:
        return KeyCheck("ok", "Key works.")
    if resp.status_code in (401, 403):
        return _bad("anthropic_api_key")
    if "credit balance" in body or "billing" in body:
        return KeyCheck(
            "warn",
            "The key is real, but your Anthropic account has no credit yet. Add some under Billing "
            "before your first run (runs will fail until you do).",
        )
    if resp.status_code == 429 or resp.status_code >= 500:
        return KeyCheck("unchecked", "Anthropic is busy right now, so the key couldn't be checked. It's saved.")
    return _bad("anthropic_api_key")


async def _google(client: httpx.AsyncClient, key: str) -> KeyCheck:
    resp = await client.get(
        "https://generativelanguage.googleapis.com/v1beta/models", params={"key": key, "pageSize": 1}
    )
    if resp.status_code == 200:
        return KeyCheck("ok", "Key works.")
    body = _body_text(resp)
    if resp.status_code == 403 and "not been used" in body:
        return KeyCheck(
            "bad",
            "That key's Google project doesn't have the Gemini API switched on. Make the key from "
            "Google AI Studio (aistudio.google.com/apikey) instead.",
        )
    if resp.status_code in (400, 401, 403):
        return _bad("google_api_key")
    return KeyCheck("unchecked", "Google couldn't be reached to check the key. It's saved.")


async def _bearer_models(client: httpx.AsyncClient, key: str, url: str, name: str) -> KeyCheck:
    resp = await client.get(url, headers={"Authorization": f"Bearer {key}"})
    if resp.status_code == 200:
        return KeyCheck("ok", "Key works.")
    if resp.status_code in (401, 403):
        return _bad(name)
    return KeyCheck("unchecked", f"{_LABEL[name]} couldn't be reached to check the key. It's saved.")


async def _fred(client: httpx.AsyncClient, key: str) -> KeyCheck:
    resp = await client.get(
        "https://api.stlouisfed.org/fred/series", params={"series_id": "GDP", "api_key": key, "file_type": "json"}
    )
    if resp.status_code == 200:
        return KeyCheck("ok", "Key works.")
    body = _body_text(resp)
    if resp.status_code in (400, 401, 403) and "api_key" in body:
        return _bad("fred_api_key")
    return KeyCheck("unchecked", "FRED couldn't be reached to check the key. It's saved.")


async def _alpha_vantage(client: httpx.AsyncClient, key: str) -> KeyCheck:
    resp = await client.get(
        "https://www.alphavantage.co/query", params={"function": "GLOBAL_QUOTE", "symbol": "IBM", "apikey": key}
    )
    if resp.status_code != 200:
        return KeyCheck("unchecked", "Alpha Vantage couldn't be reached to check the key. It's saved.")
    try:
        data = resp.json()
    except ValueError:
        return KeyCheck("unchecked", "Alpha Vantage sent back something unexpected. The key is saved.")
    text = " ".join(str(data.get(k, "")) for k in ("Error Message", "Information", "Note")).lower()
    if "invalid" in text and "key" in text:
        return _bad("alpha_vantage_api_key")
    if "rate limit" in text or "requests per day" in text or "25 requests" in text:
        return KeyCheck("warn", "The key works, but today's 25 free requests are used up. It'll work again tomorrow.")
    return KeyCheck("ok", "Key works.")


_CHECKS = {
    "anthropic_api_key": _anthropic,
    "google_api_key": _google,
    "groq_api_key": lambda c, k: _bearer_models(c, k, "https://api.groq.com/openai/v1/models", "groq_api_key"),
    "openai_api_key": lambda c, k: _bearer_models(c, k, "https://api.openai.com/v1/models", "openai_api_key"),
    "fred_api_key": _fred,
    "alpha_vantage_api_key": _alpha_vantage,
}


def _format_problem(name: str, key: str) -> str | None:
    """A key that can't be right, caught before any request goes out."""
    if not key:
        return "Paste a key first."
    if any(c.isspace() for c in key):
        return "That has a space in it. Copy just the key."
    prefixes = {"anthropic_api_key": "sk-ant-", "google_api_key": "AIza", "groq_api_key": "gsk_"}
    want = prefixes.get(name)
    if want and not key.startswith(want):
        return f"That doesn't look like a {_LABEL[name]} key. {_PREFIX_HINT[name]}"
    if name == "fred_api_key" and (len(key) != 32 or not key.isalnum()):
        return f"That doesn't look like a FRED key. {_PREFIX_HINT[name]}"
    return None


async def check_key(name: str, key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> KeyCheck:
    if name not in _CHECKS:
        raise ValueError(f"unknown key '{name}'")
    problem = _format_problem(name, key)
    if problem:
        return KeyCheck("bad", problem)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as client:
            return await _CHECKS[name](client, key)
    except httpx.HTTPError:
        return KeyCheck("unchecked", f"{_LABEL[name]} couldn't be reached to check the key. It's saved.")
