"""Trying a key with its provider before it's saved (the setup wizard and
Settings): turned-down keys are refused, real ones saved, and anything in
between saved with a note."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from council import key_check
from council.tests.test_auth import _signup_and_approve, make_app, site  # noqa: F401

GOOGLE = "AIza" + "x" * 35
NEW_GOOGLE = "AQ.Ab8RN6" + "x" * 40
ANTHROPIC = "sk-ant-" + "x" * 40
FRED = "a" * 32


def check(name, key, handler):
    return asyncio.run(key_check.check_key(name, key, transport=httpx.MockTransport(handler)))


def test_a_working_google_key_passes():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"models": []})

    assert check("google_api_key", GOOGLE, handler).status == "ok"
    assert seen[0].url.host == "generativelanguage.googleapis.com"
    assert seen[0].headers["x-goog-api-key"] == GOOGLE and "key" not in seen[0].url.params


def test_new_google_auth_keys_are_accepted():
    """Keys made in AI Studio now start with AQ. rather than AIza."""
    assert check("google_api_key", NEW_GOOGLE, lambda r: httpx.Response(200, json={"models": []})).status == "ok"


def test_a_turned_down_new_google_key_gets_the_plain_hint():
    result = check("google_api_key", NEW_GOOGLE, lambda r: httpx.Response(401, json={}))
    assert result.status == "bad" and "retiring" not in result.message


def test_a_turned_down_google_key_is_bad():
    result = check("google_api_key", GOOGLE, lambda r: httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}}))
    assert result.status == "bad" and "AQ." in result.message


def test_the_wrong_kind_of_key_is_caught_without_a_request():
    def handler(request):
        raise AssertionError("no request should be made")

    assert check("google_api_key", "sk-ant-abc", handler).status == "bad"
    assert check("anthropic_api_key", "AIzaabc", handler).status == "bad"
    assert check("fred_api_key", "short", handler).status == "bad"
    assert check("google_api_key", "", handler).status == "bad"


def test_an_anthropic_account_with_no_credit_is_a_warning_not_a_refusal():
    def handler(request):
        assert request.headers["x-api-key"] == ANTHROPIC
        body = json.loads(request.content)
        assert body["max_tokens"] == 1
        return httpx.Response(400, json={"error": {"message": "Your credit balance is too low to access the Anthropic API."}})

    result = check("anthropic_api_key", ANTHROPIC, handler)
    assert result.status == "warn" and "credit" in result.message
    assert result.as_dict()["ok"] is True


def test_a_turned_down_anthropic_key_is_bad():
    assert check("anthropic_api_key", ANTHROPIC, lambda r: httpx.Response(401, json={})).status == "bad"


def test_fred_checks_a_series():
    assert check("fred_api_key", FRED, lambda r: httpx.Response(200, json={"seriess": []})).status == "ok"
    bad = check("fred_api_key", FRED, lambda r: httpx.Response(400, json={"error_message": "Bad Request.  The value for variable api_key is not registered."}))
    assert bad.status == "bad"


def test_alpha_vantage_reads_errors_from_the_body():
    ok = check("alpha_vantage_api_key", "ABC123", lambda r: httpx.Response(200, json={"Global Quote": {}}))
    assert ok.status == "ok"
    bad = check("alpha_vantage_api_key", "ABC123", lambda r: httpx.Response(200, json={"Error Message": "Invalid API key."}))
    assert bad.status == "bad"
    limited = check("alpha_vantage_api_key", "ABC123", lambda r: httpx.Response(200, json={"Information": "Our standard API rate limit is 25 requests per day."}))
    assert limited.status == "warn"


def test_an_unreachable_provider_is_unchecked_and_still_saved():
    def handler(request):
        raise httpx.ConnectError("no route")

    result = check("google_api_key", GOOGLE, handler)
    assert result.status == "unchecked" and result.as_dict()["ok"] is True


def test_unknown_key_names_are_refused():
    with pytest.raises(ValueError):
        asyncio.run(key_check.check_key("nope", "x"))


# ---- through the API ------------------------------------------------------------------


def _fake_check(status, message="msg"):
    async def fake(name, key, **_):
        return key_check.KeyCheck(status, message)
    return fake


def test_saving_with_check_refuses_a_bad_key(site, monkeypatch):
    owner_client, settings = site
    friend, _ = _signup_and_approve(owner_client, settings)
    monkeypatch.setattr(key_check, "check_key", _fake_check("bad", "Google didn't accept that key."))
    resp = friend.post("/api/settings/keys", json={"name": "google_api_key", "value": GOOGLE, "check": True})
    assert resp.status_code == 400 and "didn't accept" in resp.json()["detail"]
    assert friend.get("/api/onboarding").json()["keys"]["google_api_key"] is False


@pytest.mark.parametrize("status", ["ok", "warn", "unchecked"])
def test_saving_with_check_keeps_a_key_that_isnt_turned_down(site, monkeypatch, status):
    owner_client, settings = site
    friend, _ = _signup_and_approve(owner_client, settings)
    monkeypatch.setattr(key_check, "check_key", _fake_check(status))
    resp = friend.post("/api/settings/keys", json={"name": "google_api_key", "value": GOOGLE, "check": True})
    assert resp.status_code == 200 and resp.json()["check"]["status"] == status
    assert friend.get("/api/onboarding").json()["keys"]["google_api_key"] is True


def test_saving_without_check_makes_no_request(site, monkeypatch):
    owner_client, settings = site
    friend, _ = _signup_and_approve(owner_client, settings)

    async def boom(*a, **k):
        raise AssertionError("shouldn't be checked")

    monkeypatch.setattr(key_check, "check_key", boom)
    resp = friend.post("/api/settings/keys", json={"name": "fred_api_key", "value": FRED})
    assert resp.status_code == 200 and resp.json()["check"] is None
