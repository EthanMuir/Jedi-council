"""'Your call was scored' emails (#135) and the /health check (#132)."""
from __future__ import annotations

import asyncio
import json

import pytest

from council import emailer, notify
from council.engine.resolution_sweep import SweepResult
from council.tests.test_auth import OWNER, SITE_PASSWORD, _new_client, make_app  # noqa: F401


@pytest.fixture
def mail_site(make_app, monkeypatch):
    client, settings = make_app(
        app_password=SITE_PASSWORD, resend_api_key="re_test", email_from="Ticker Council <hi@t.example>",
        public_url="https://t.example",
    )
    assert client.post("/api/auth/setup", json={**OWNER, "site_password": SITE_PASSWORD}).status_code == 200
    sent = []

    async def fake_send(_settings, to, subject, text, html=""):
        sent.append({"to": to, "subject": subject, "text": text, "html": html})
        return True

    monkeypatch.setattr(emailer, "send_email", fake_send)
    # Sample runs are never emailed about; the test runs are sample runs, so
    # treat them as paid here.
    monkeypatch.setattr(notify, "effective_run_mode", lambda mode, cost: "paid")
    return client, settings, sent


def _run(client):
    ids = None
    with client.stream("GET", "/api/deliberate/stream", params={"ticker": "NVDA", "as_of": "2026-08-01T16:00:00"}) as r:
        event = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: ") and event == "phase_g_crypt_write":
                data = json.loads(line.removeprefix("data: "))
                ids = (data["run_id"], data["prediction_ids"])
    return ids


def _swept(pid, correct=True, move=4.1):
    return SweepResult(prediction_id=pid, ticker="NVDA", horizon="short", council_vote="BULLISH",
                       direction_correct=correct, realised_move_pct=move, lessons_written=0)


def test_owner_is_emailed_about_a_scored_call(mail_site):
    client, settings, sent = mail_site
    run_id, pids = _run(client)
    n = asyncio.run(notify.email_scored_calls(settings, [_swept(pids["short"])]))
    assert n == 1
    mail = sent[0]
    assert mail["to"] == OWNER["email"] and mail["subject"] == "Your NVDA call was right"
    assert "RIGHT (price +4.1%)" in mail["text"]
    assert f"https://t.example/index.html?run={run_id}" in mail["text"]
    assert "https://t.example/unsubscribe?u=0&t=" in mail["text"]


def test_several_calls_are_bundled_into_one_email(mail_site):
    client, settings, sent = mail_site
    _, pids = _run(client)
    swept = [_swept(pids["short"], True, 3.0), _swept(pids["medium"], False, -2.0)]
    assert asyncio.run(notify.email_scored_calls(settings, swept)) == 1
    assert sent[0]["subject"] == "2 of your calls were scored: 1 right"


def test_unsubscribe_link_turns_emails_off(mail_site):
    client, settings, sent = mail_site
    _, pids = _run(client)
    token = notify.unsubscribe_token(settings, 0)
    visitor = _new_client(settings)
    assert visitor.get("/unsubscribe?u=0&t=wrong").status_code == 400
    assert notify.wants_scored_emails(settings.settings_db_path, 0)
    page = visitor.get(f"/unsubscribe?u=0&t={token}")
    assert page.status_code == 200 and "turned off" in page.text
    assert asyncio.run(notify.email_scored_calls(settings, [_swept(pids["short"])])) == 0
    assert sent == []


def test_settings_toggle_for_scored_emails(mail_site):
    client, settings, _ = mail_site
    assert client.get("/api/settings/notifications").json() == {"scored_calls": True, "email_enabled": True}
    assert client.post("/api/settings/notifications", json={"scored_calls": False}).status_code == 200
    assert client.get("/api/settings/notifications").json()["scored_calls"] is False
    assert not notify.wants_scored_emails(settings.settings_db_path, 0)


def test_no_email_without_email_setup(make_app):
    client, settings = make_app(app_password=SITE_PASSWORD)
    client.post("/api/auth/setup", json={**OWNER, "site_password": SITE_PASSWORD})
    assert asyncio.run(notify.email_scored_calls(settings, [_swept("whatever")])) == 0


def test_health_is_public(make_app):
    client, settings = make_app(app_password=SITE_PASSWORD)
    # Before the owner exists, and signed out after: still answers.
    assert client.get("/health", follow_redirects=False).json() == {"ok": True}
    client.post("/api/auth/setup", json={**OWNER, "site_password": SITE_PASSWORD})
    assert _new_client(settings).get("/health", follow_redirects=False).status_code == 200
