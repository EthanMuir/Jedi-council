"""Free mode: free Gemini/Groq models chosen from what the key can use,
seats routed to whichever provider has a key, short rate limits waited
out, Gemini's daily limit overflowing to Groq, and a clean stop -- with
the reason -- when nothing usable is left."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import google.genai.errors as genai_errors
import httpx
import openai
import pytest

import council.engine.llm_client as llm_client_module
from council.config import Settings
from council.engine import free_models, model_settings
from council.engine.llm_client import LLMCallFailed, LLMClient, RunStopped, _retry_hint_seconds
from council.engine.routing import resolve_route
from council.tests.seat_answers import seat_answer_payload

_VERDICT = seat_answer_payload()

_GEMINI_DAILY = {
    "error": {
        "code": 429,
        "message": "You exceeded your current quota. Please retry in 26.9s.",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
            {"retryDelay": "26s"},
        ],
    }
}
_GROQ_REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def _groq_rate_limit(message: str) -> openai.RateLimitError:
    return openai.RateLimitError(
        message, response=httpx.Response(429, request=_GROQ_REQUEST), body=None
    )


class _Gemini:
    def __init__(self, responder):
        self.calls = 0
        self._responder = responder
        outer = self

        class _Models:
            async def generate_content(self, **kwargs):
                outer.calls += 1
                return outer._responder(outer.calls, kwargs)

        self.aio = SimpleNamespace(models=_Models())


class _Groq:
    def __init__(self, responder):
        self.calls = 0
        self.models_used: list[str] = []
        self._responder = responder
        outer = self

        class _Completions:
            async def create(self, **kwargs):
                outer.calls += 1
                outer.models_used.append(kwargs["model"])
                return outer._responder(outer.calls, kwargs)

        self.chat = SimpleNamespace(completions=_Completions())


def _gemini_ok(n, kwargs):
    usage = SimpleNamespace(prompt_token_count=200, candidates_token_count=80)
    return SimpleNamespace(text=json.dumps(_VERDICT), usage_metadata=usage)


def _groq_ok(n, kwargs):
    tool_call = SimpleNamespace(function=SimpleNamespace(arguments=json.dumps(_VERDICT)))
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[tool_call]))],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=60),
    )


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    free_models.clear_cache()
    monkeypatch.setattr(llm_client_module, "_BACKOFF_BASE_SECONDS", 0.001)
    monkeypatch.setattr(llm_client_module, "_BACKOFF_CAP_SECONDS", 0.001)
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(llm_client_module.asyncio, "sleep", fake_sleep)
    return sleeps


def _free_settings(tmp_path, **keys) -> Settings:
    base = dict(anthropic_api_key="", openai_api_key="", google_api_key="", groq_api_key="")
    return Settings(settings_db_path=str(tmp_path / "settings.db"), **{**base, **keys})


def _use_free_model(settings, role, model_id):
    conn = model_settings.connect(settings.settings_db_path)
    model_settings.set_override(conn, role, model_id)
    conn.close()


async def _verdict(client, seat_id="technician"):
    answer = await client.get_seat_answer(
        seat_id=seat_id, model="claude-sonnet-5", system_prompt="s", user_prompt="u"
    )
    return answer.short


# --- picking free models ------------------------------------------------


def test_picks_newest_stable_flash_lite():
    names = [
        "models/gemini-3.5-flash",
        "models/gemini-3.1-flash-lite",
        "models/gemini-3.5-flash-lite-preview-06-17",
        "models/gemini-3.5-flash-lite",
        "models/gemini-3.5-flash-lite-tts",
        "models/gemini-2.5-flash-lite",
    ]
    assert free_models.pick_gemini(names) == "gemini-3.5-flash-lite"
    assert free_models.pick_gemini(["models/gemini-4.0-flash-lite-preview"]) == "gemini-4.0-flash-lite-preview"
    assert free_models.pick_gemini(["models/gemini-3.5-pro"]) is None


def test_groq_seats_are_spread_across_available_models():
    available = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "qwen/qwen3-32b", "whisper-large-v3"]
    picks = {free_models.pick_groq(available, seat) for seat in (
        "technician", "fundamentalist", "catalyst_seer", "insider_reader", "macro_sage",
        "cross_market", "oracle_options", "flow_cartographer",
    )}
    assert picks <= set(free_models.GROQ_PREFERENCE[:3]) and len(picks) > 1
    assert free_models.pick_groq(["whisper-large-v3"], "technician") is None


# --- routing with only free keys ------------------------------------------


def test_seats_route_to_the_free_provider_that_has_a_key(tmp_path):
    only_gemini = _free_settings(tmp_path, google_api_key="AIza-test")
    for seat in ("technician", "fundamentalist", "grand_master"):
        route = resolve_route(seat, only_gemini, default_model="claude-sonnet-5")
        assert (route.provider, route.model) == ("google", "free:gemini")

    only_groq = _free_settings(tmp_path, groq_api_key="gsk-test")
    route = resolve_route("technician", only_groq, default_model="claude-sonnet-5")
    assert (route.provider, route.model) == ("groq", "free:groq")


# --- rate limits and daily limits -------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Please try again in 7.66s. Need more tokens?", 7.66),
        ("Rate limit reached ... Please try again in 1m2.5s.", 62.5),
        ("Please try again in 2h3m0s", 7380.0),
        ("Please try again in 450ms", 0.45),
        ("You exceeded your current quota. Please retry in 26.9s.", 26.9),
        ("{'retryDelay': '26s'}", 26.0),
        ("no hint here", None),
    ],
)
def test_retry_hints_are_read_from_provider_messages(text, expected):
    assert _retry_hint_seconds(text) == pytest.approx(expected) if expected else _retry_hint_seconds(text) is None


def test_retry_after_header_is_the_fallback_hint():
    assert _retry_hint_seconds("slow down", {"retry-after": "12"}) == 12.0


async def test_short_rate_limits_are_waited_out_without_using_up_retries(tmp_path, _fresh):
    settings = _free_settings(tmp_path, groq_api_key="gsk-test")
    client = LLMClient(settings)

    def responder(n, kwargs):
        if n <= 4:  # more rate limits than max_retries allows as failures
            raise _groq_rate_limit("Rate limit reached for tokens per minute (TPM). Please try again in 7.5s.")
        return _groq_ok(n, kwargs)

    client._groq_client = _Groq(responder)
    verdict = await _verdict(client)
    assert verdict.vote == "BULLISH"
    assert _fresh.count(8.0) == 4  # waited the hinted 7.5s (+0.5s margin) each time
    assert client.call_log[-1].free_tier is True
    assert client.call_log[-1].cost_usd == 0.0


async def test_gemini_daily_limit_overflows_to_groq_with_one_notice(tmp_path):
    settings = _free_settings(tmp_path, google_api_key="AIza-test", groq_api_key="gsk-test")
    notices: list[str] = []

    async def on_notice(message):
        notices.append(message)

    client = LLMClient(settings, on_notice=on_notice)
    client._gemini_client = _Gemini(lambda n, kw: (_ for _ in ()).throw(genai_errors.ClientError(429, _GEMINI_DAILY)))
    client._groq_client = _Groq(_groq_ok)

    first = await _verdict(client, "technician")
    second = await _verdict(client, "fundamentalist")

    assert first.vote == second.vote == "BULLISH"
    assert client._gemini_client.calls == 1  # never asked again once it's used up
    assert client._groq_client.calls == 2
    assert len(notices) == 1
    assert "Gemini's free daily limit is used up" in notices[0]
    assert "Groq" in notices[0]
    assert client.stop_reason is None


async def test_free_gemini_key_on_a_paid_gemini_model_drops_to_the_free_model(tmp_path):
    # macro_sage defaults to Gemini 3 Pro, which the free tier doesn't
    # include: Google answers with a free-tier quota error (limit 0). That
    # must not stop the run -- the seat just uses the free model. (The key
    # is marked as having billing, or Gemini 3 Pro wouldn't be tried.)
    settings = _free_settings(tmp_path, google_api_key="AIza-test")
    conn = model_settings.connect(settings.settings_db_path)
    model_settings.set_google_billing(conn, True)
    conn.close()
    no_pro = {
        "error": {
            "code": 429,
            "message": "Quota exceeded for metric: generate_content_free_tier_requests, limit: 0",
            "status": "RESOURCE_EXHAUSTED",
            "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}],
        }
    }
    models_asked = []

    def responder(n, kwargs):
        models_asked.append(kwargs["model"])
        if kwargs["model"] == "gemini-3-pro":
            raise genai_errors.ClientError(429, no_pro)
        return _gemini_ok(n, kwargs)

    notices = []

    async def on_notice(message):
        notices.append(message)

    client = LLMClient(settings, on_notice=on_notice)
    client._gemini_client = _Gemini(responder)
    verdict = await _verdict(client, "macro_sage")

    assert verdict.vote == "BULLISH"
    assert models_asked == ["gemini-3-pro", free_models.GEMINI_FALLBACK]
    assert client.stop_reason is None
    assert len(notices) == 1 and "free tier" in notices[0]


async def test_a_free_gemini_key_never_tries_paid_gemini(tmp_path):
    # Without billing marked on the Google key, a seat set to Gemini 3 Pro
    # goes straight to the free model: no failed call first.
    settings = _free_settings(tmp_path, google_api_key="AIza-test")
    models_asked = []

    def responder(n, kwargs):
        models_asked.append(kwargs["model"])
        return _gemini_ok(n, kwargs)

    client = LLMClient(settings)
    client._gemini_client = _Gemini(responder)
    verdict = await _verdict(client, "macro_sage")
    assert verdict.vote == "BULLISH"
    assert models_asked == [free_models.GEMINI_FALLBACK]


async def test_run_stops_when_every_free_provider_is_used_up(tmp_path):
    settings = _free_settings(tmp_path, google_api_key="AIza-test", groq_api_key="gsk-test")
    client = LLMClient(settings)
    client._gemini_client = _Gemini(lambda n, kw: (_ for _ in ()).throw(genai_errors.ClientError(429, _GEMINI_DAILY)))
    client._groq_client = _Groq(
        lambda n, kw: (_ for _ in ()).throw(
            _groq_rate_limit("Rate limit reached for requests per day (RPD). Please try again in 3h10m0s.")
        )
    )

    verdict = await _verdict(client)
    assert verdict.vote == "NO_READ"
    assert "Gemini's free daily limit" in client.stop_reason
    assert "Groq's free daily limit" in client.stop_reason and "3 hours" in client.stop_reason
    with pytest.raises(RunStopped):
        client.raise_if_stopped()

    calls_before = (client._gemini_client.calls, client._groq_client.calls)
    with pytest.raises(LLMCallFailed):
        await client.get_structured(
            seat_id="macro_sage", model="claude-sonnet-5", system_prompt="s",
            user_prompt="u", response_model=llm_client_module.SeatAnswer,
        )
    assert (client._gemini_client.calls, client._groq_client.calls) == calls_before


async def test_paid_account_out_of_credit_stops_with_a_top_up_link(tmp_path):
    settings = Settings(
        settings_db_path=str(tmp_path / "settings.db"), no_llm=False, anthropic_api_key="sk-ant-test"
    )
    client = LLMClient(settings)
    calls = []

    class _Messages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            import anthropic

            raise anthropic.BadRequestError(
                "Your credit balance is too low to access the Anthropic API.",
                response=httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com")),
                body=None,
            )

    client._client = SimpleNamespace(messages=_Messages())
    verdict = await _verdict(client)
    assert verdict.vote == "NO_READ"
    assert len(calls) == 1  # no retries on an empty account
    assert client.stop_reason == "Your Anthropic account is out of credit."
    with pytest.raises(RunStopped) as stopped:
        client.raise_if_stopped()
    assert stopped.value.link == "https://console.anthropic.com/settings/billing"


async def test_openai_insufficient_quota_is_not_retried(tmp_path):
    settings = _free_settings(tmp_path, openai_api_key="sk-test")
    client = LLMClient(settings)
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")

    def responder(n, kwargs):
        raise openai.RateLimitError(
            "Error code: 429 - {'error': {'code': 'insufficient_quota'}}",
            response=httpx.Response(429, request=request),
            body=None,
        )

    client._openai_client = _Groq(responder)
    await _verdict(client)
    assert client._openai_client.calls == 1
    assert client.stop_reason == "Your OpenAI account is out of credit."


# --- whole runs: lite shape, labels, and stopping before the Crypt ---------

AS_OF = __import__("datetime").datetime(2026, 9, 18, 16, 0, 0)


def _sample_settings(tmp_path) -> Settings:
    return Settings(
        no_llm=True,
        use_data_fixtures=True,
        council_db_path=str(tmp_path / "council.db"),
        cache_db_path=str(tmp_path / "cache.db"),
        settings_db_path=str(tmp_path / "settings.db"),
    )


async def test_lite_run_uses_one_sample_and_one_debate_round(tmp_path):
    from council.crypt.db import connect
    from council.engine.orchestrator import run_deliberation

    settings = _sample_settings(tmp_path)
    result = await run_deliberation("NVDA", settings, as_of=AS_OF, lite=True)

    assert all(s.sample_count == 1 for s in result.seat_results)
    assert len(result.prosecutor_verdicts) == 1
    assert (result.run_mode, result.run_shape) == ("sample", "lite")
    conn = connect(settings.council_db_path)
    rows = conn.execute("SELECT run_mode, run_shape FROM predictions").fetchall()
    conn.close()
    assert len(rows) == 3  # one per term
    assert {(r["run_mode"], r["run_shape"]) for r in rows} == {("sample", "lite")}


async def test_a_stopped_run_is_never_saved(tmp_path, monkeypatch):
    from council.crypt.db import connect
    from council.engine.orchestrator import run_deliberation

    async def out_of_everything(self, seat_id, *args, **kwargs):
        self.stop_reason = "Gemini's free daily limit is used up."
        raise LLMCallFailed(self.stop_reason)

    monkeypatch.setattr(LLMClient, "_fixture_structured", out_of_everything)
    settings = _sample_settings(tmp_path)
    events = []

    async def progress(event, payload):
        events.append(event)

    with pytest.raises(RunStopped, match="daily limit"):
        await run_deliberation("NVDA", settings, as_of=AS_OF, progress=progress)

    assert "phase_a_complete" not in events
    conn = connect(settings.council_db_path)
    assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0
    conn.close()


def test_run_mode_labels():
    from council.engine.llm_client import LLMCallRecord
    from council.engine.orchestrator import _run_mode

    live = Settings(no_llm=False, anthropic_api_key="sk-ant-test")

    def record(free, success=True):
        return LLMCallRecord("technician", "m", "p", "h", 0, 0, 0.0, 0.0, 1, success, free_tier=free)

    assert _run_mode(live, [record(True), record(True), record(False, success=False)]) == "free"
    assert _run_mode(live, [record(True), record(False)]) == "paid"
    assert _run_mode(Settings(no_llm=True), [record(True)]) == "sample"


# --- API: the free-mode switch, lite estimates, Archives split -----------------


@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import council.api.main as main_module
    from council import key_store

    settings = Settings(
        council_db_path=str(tmp_path / "council.db"),
        cache_db_path=str(tmp_path / "cache.db"),
        settings_db_path=str(tmp_path / "settings.db"),
        anthropic_api_key="", openai_api_key="", google_api_key="", groq_api_key="",
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: key_store.apply_saved_keys(settings))
    return TestClient(main_module.app), settings


def test_free_mode_needs_a_free_key(api):
    client, _ = api
    response = client.post("/api/settings/free-mode", json={"enabled": True})
    assert response.status_code == 400
    assert "Gemini or Groq" in response.json()["detail"]


def test_free_mode_switches_every_seat_and_restores_previous_choices(api):
    client, _ = api
    client.post("/api/settings/keys", json={"name": "anthropic_api_key", "value": "sk-ant-test"})
    client.post("/api/settings/keys", json={"name": "google_api_key", "value": "AIza-test"})
    client.post("/api/settings/models", json={"role": "technician", "model_id": "claude-haiku-4-5-20251001"})
    assert client.get("/api/settings/free-mode").json()["run_mode"] == "paid"

    on = client.post("/api/settings/free-mode", json={"enabled": True}).json()
    assert on["enabled"] is True and on["run_mode"] == "free"
    roles = client.get("/api/settings/models").json()["roles"]
    assert {r["current_model"] for r in roles} == {"free:gemini"}
    estimate = client.get("/api/settings/cost-estimate", params={}).json()
    assert estimate["total_cost_usd"] == 0 and estimate["run_mode"] == "free"

    off = client.post("/api/settings/free-mode", json={"enabled": False}).json()
    assert off["enabled"] is False and off["run_mode"] == "paid"
    roles = {r["role"]: r for r in client.get("/api/settings/models").json()["roles"]}
    assert roles["technician"]["current_model"] == "claude-haiku-4-5-20251001"
    assert roles["grand_master"]["is_override"] is False


def test_groq_is_used_when_it_is_the_only_free_key(api):
    client, _ = api
    client.post("/api/settings/keys", json={"name": "groq_api_key", "value": "gsk-test"})
    client.post("/api/settings/free-mode", json={"enabled": True})
    roles = client.get("/api/settings/models").json()["roles"]
    assert {r["current_model"] for r in roles} == {"free:groq"}


def test_lite_estimate_makes_far_fewer_calls(api):
    client, _ = api
    full = client.get("/api/settings/cost-estimate", params={}).json()
    lite = client.get("/api/settings/cost-estimate", params={"lite": "true"}).json()
    assert (full["total_calls"], lite["total_calls"]) == (43, 16)


def test_archives_can_be_split_by_run_mode(api):
    client, _ = api
    for mode in ("paid", "free", None):
        response = client.get("/api/archives", params={"mode": mode} if mode else {})
        assert response.status_code == 200
        assert response.json()["mode"] == mode
    assert client.get("/api/archives", params={"mode": "bogus"}).status_code == 400


def test_old_runs_without_a_label_count_as_paid_or_sample():
    from council.crypt.db import effective_run_mode

    assert effective_run_mode(None, 0.42) == "paid"
    assert effective_run_mode(None, 0.0) == "sample"
    assert effective_run_mode(None, None) == "sample"
    assert effective_run_mode("free", 0.0) == "free"


def test_a_stopped_run_streams_the_reason_instead_of_an_error(api, monkeypatch):
    import council.api.main as main_module

    async def stopped_run(*args, **kwargs):
        raise RunStopped("Your Anthropic account is out of credit.", "https://console.anthropic.com/settings/billing")

    monkeypatch.setattr(main_module, "run_deliberation", stopped_run)
    client, _ = api
    with client.stream("GET", "/api/deliberate/stream", params={"ticker": "NVDA"}) as response:
        lines = list(response.iter_lines())
    events = [line.removeprefix("event: ") for line in lines if line.startswith("event: ")]
    assert events == ["mode", "stopped"]
    payload = json.loads([line for line in lines if line.startswith("data: ")][-1].removeprefix("data: "))
    assert payload == {
        "message": "Your Anthropic account is out of credit.",
        "link": "https://console.anthropic.com/settings/billing",
    }


def test_crypt_list_filters_and_labels_runs_by_mode(api):
    from datetime import datetime, timedelta

    from council.crypt.db import connect
    from council.crypt.ledger import write_prediction

    client, settings = api
    settings.ensure_dirs()
    conn = connect(settings.council_db_path)
    now = datetime(2026, 9, 18, 16, 0, 0)
    common = dict(
        horizon="1w", resolve_at=now + timedelta(days=7), price_at_prediction=100.0,
        data_snapshot_hash="x", model_versions={}, blind_vote="BULLISH",
        blind_probability=0.6, blind_consensus_pct=0.6, created_at=now,
    )
    write_prediction(conn, ticker="FREE", run_mode="free", run_shape="lite", total_cost_usd=0.0, **common)
    write_prediction(conn, ticker="PAID", run_mode="paid", run_shape="full", total_cost_usd=0.41, **common)
    write_prediction(conn, ticker="OLD", total_cost_usd=0.39, **common)  # saved before labels existed
    conn.close()

    def tickers(**params):
        rows = client.get("/api/predictions", params=params).json()["predictions"]
        return sorted(r["ticker"] for r in rows)

    assert tickers(mode="free") == ["FREE"]
    assert tickers(mode="paid") == ["OLD", "PAID"]
    assert tickers() == ["FREE", "OLD", "PAID"]
    labels = {r["ticker"]: (r["run_mode"], r["run_shape"]) for r in client.get("/api/predictions").json()["predictions"]}
    assert labels == {"FREE": ("free", "lite"), "PAID": ("paid", "full"), "OLD": ("paid", "full")}


# --- #111: nothing hangs forever, and waits are visible -----------------------


async def test_a_call_that_never_answers_is_cut_off_and_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_client_module, "_CALL_BACKSTOP_SECONDS", 0.05)
    settings = _free_settings(tmp_path, groq_api_key="gsk-test")
    client = LLMClient(settings)
    never = asyncio.Event()  # asyncio.sleep is faked in this module; wait on an event instead

    def responder(n, kwargs):
        if n == 1:
            return never.wait()  # the first call hangs
        return _groq_ok(n, kwargs)

    class _MaybeHanging(_Groq):
        def __init__(self):
            super().__init__(responder)
            outer = self

            class _Completions:
                async def create(self, **kwargs):
                    outer.calls += 1
                    result = responder(outer.calls, kwargs)
                    if asyncio.iscoroutine(result):
                        await result
                    return result

            self.chat = SimpleNamespace(completions=_Completions())

    client._groq_client = _MaybeHanging()
    verdict = await _verdict(client)
    assert verdict.vote == "BULLISH"
    assert client._groq_client.calls == 2
    assert "no answer from Groq" in client.call_log[0].error


async def test_rate_limit_waits_are_reported_to_the_ui(tmp_path):
    settings = _free_settings(tmp_path, groq_api_key="gsk-test")
    waits = []

    async def on_wait(seat_id, provider, seconds):
        waits.append((seat_id, provider, seconds))

    client = LLMClient(settings, on_wait=on_wait)

    def responder(n, kwargs):
        if n == 1:
            raise _groq_rate_limit("Rate limit reached for requests per minute (RPM). Please try again in 40s.")
        return _groq_ok(n, kwargs)

    client._groq_client = _Groq(responder)
    await _verdict(client, "cross_market")
    assert waits == [("cross_market", "Groq", 40.0)]


def test_every_provider_client_has_a_timeout_and_no_hidden_retries(tmp_path):
    settings = _free_settings(
        tmp_path, anthropic_api_key="sk-ant-test", openai_api_key="sk-test",
        google_api_key="AIza-test", groq_api_key="gsk-test",
    )
    client = LLMClient(settings)
    for sdk_client in (client._client, client._openai_client, client._groq_client):
        assert sdk_client.timeout == llm_client_module._CALL_TIMEOUT_SECONDS
        assert sdk_client.max_retries == 0
    gemini_timeout_ms = client._gemini_client._api_client._http_options.timeout
    assert gemini_timeout_ms == llm_client_module._CALL_TIMEOUT_SECONDS * 1000


# --- #115: free-tier requests are paced, paid ones aren't ---------------------


async def test_free_gemini_calls_get_evenly_spaced_slots(tmp_path, _fresh):
    settings = _free_settings(tmp_path, google_api_key="AIza-test")
    queued = []

    async def on_queue(seat_id, provider, seconds):
        queued.append((seat_id, provider, seconds))

    client = LLMClient(settings, on_queue=on_queue)
    client._gemini_client = _Gemini(_gemini_ok)
    # Seats routed to the free Gemini model (not macro_sage/senate_watcher,
    # which default to Gemini Pro -- a paid model, never paced).
    for seat in ("technician", "fundamentalist", "catalyst_seer"):
        assert (await _verdict(client, seat)).vote == "BULLISH"

    gap = 60 / free_models.GEMINI_REQUESTS_PER_MINUTE
    # asyncio.sleep is faked here, so the clock barely moves: each call's
    # wait is its distance from the first slot.
    assert _fresh == [pytest.approx(gap, abs=0.1), pytest.approx(2 * gap, abs=0.1)]
    assert [(s, p) for s, p, _ in queued] == [("fundamentalist", "Gemini"), ("catalyst_seer", "Gemini")]


def test_groq_is_paced_by_tokens_per_minute():
    small = free_models.pace_interval_seconds("groq", "openai/gpt-oss-120b", 100)
    seat_sized = free_models.pace_interval_seconds("groq", "openai/gpt-oss-120b", 3_750)
    assert small == pytest.approx(60 / free_models.GROQ_REQUESTS_PER_MINUTE)
    assert seat_sized == pytest.approx(60 * 3_750 / 7_500)  # ~2 seat calls a minute
    unknown = free_models.pace_interval_seconds("groq", "some/new-model", 3_750)
    assert unknown > seat_sized  # unknown models get the cautious default
    assert free_models.pace_interval_seconds("anthropic", "claude-sonnet-5", 3_750) == 0.0


async def test_paid_calls_are_never_paced(tmp_path, _fresh):
    settings = _free_settings(tmp_path, openai_api_key="sk-test")
    client = LLMClient(settings)
    client._openai_client = _Groq(_groq_ok)  # same OpenAI-shaped fake
    for seat in ("fundamentalist", "estimate_scribe", "bear_advocate"):
        await _verdict(client, seat)
    assert _fresh == []
