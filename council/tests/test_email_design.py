"""Branded HTML emails (#149): every email has a designed version and a
plain-text copy, names people typed are escaped, and admins can preview
and test-send each one."""
from __future__ import annotations

from council import emailer, notify
from council.tests.test_auth import site  # noqa: F401
from council.tests.test_auth import make_app  # noqa: F401


def test_every_email_has_html_and_text():
    messages = [
        emailer.signup_alert("Sam", "sam@example.com", "https://t.example/admin.html"),
        emailer.approved_message("Sam", "https://t.example/login?notice=approved"),
        emailer.reset_message("Sam", "https://t.example/reset?token=abc", 1),
        emailer.report_message("Sam (sam@example.com)", "Something's broken", "It broke", [("Ticker", "NVDA")],
                               "https://t.example/admin.html#reports"),
    ]
    for m in messages:
        assert m.subject and m.text and m.html.startswith("<!doctype html>")
        assert 'src="https://t.example/icons/icon-192.png"' in m.html


def test_the_reset_link_is_a_button_and_spelled_out():
    m = emailer.reset_message("Sam", "https://t.example/reset?token=abc", 2)
    assert m.html.count('href="https://t.example/reset?token=abc"') == 1
    assert "https://t.example/reset?token=abc</span>" in m.html
    assert "for the next 2 hours" in m.html and "for the next 2 hours" in m.text


def test_what_people_typed_is_escaped():
    m = emailer.signup_alert('<script>x</script>', 'a"b@example.com', "https://t.example/admin.html")
    assert "<script>x</script>" not in m.html and "&lt;script&gt;" in m.html
    r = emailer.report_message("Someone", "Idea or request", "<b>bold</b> & more", [], "https://t.example/a")
    assert "<b>bold</b>" not in r.html and "&lt;b&gt;bold&lt;/b&gt; &amp; more" in r.html


def test_scored_email_shows_each_call():
    target = {"price_now": 100.0, "target": 101.0, "low": 95.0, "high": 108.0, "chance_pct": 70}
    m = notify.compose(
        "Sam",
        [{"ticker": "NVDA", "company": "NVIDIA Corporation", "term": "short", "run_id": "r1", "called": "up",
          "p": 0.58, "correct": True, "move": 3.0, "target": target},
         {"ticker": "AAPL", "term": "long", "run_id": "r2", "called": "down", "correct": False, "move": 1.0,
          "target": None}],
        "https://t.example", "https://t.example/unsubscribe?u=1&t=x",
    )
    assert m.subject == "2 of your calls were scored: 1 right"
    assert "NVIDIA Corporation" in m.html and "✓ Right" in m.html and "✗ Wrong" in m.html
    assert "inside the 70% range $95–$108" in m.html
    assert 'href="https://t.example/index.html?run=r1"' in m.html
    assert "Stop these emails" in m.html and "https://t.example/unsubscribe?u=1&amp;t=x" in m.html


def test_admin_can_preview_every_email(site):  # noqa: F811
    client, _ = site
    kinds = [e["kind"] for e in client.get("/api/admin/emails").json()["emails"]]
    assert kinds == ["scored", "approved", "reset", "signup", "report"]
    for kind in kinds:
        resp = client.get(f"/api/admin/emails/{kind}/preview")
        assert resp.status_code == 200 and "Ticker" in resp.text
    assert client.get("/api/admin/emails/nope/preview").status_code == 404


def test_test_send_needs_email_set_up(site):  # noqa: F811
    client, _ = site
    resp = client.post("/api/admin/emails/reset/test")
    assert resp.status_code == 400 and "isn't set up" in resp.json()["detail"]


def test_test_send_goes_to_the_admin(make_app, monkeypatch):  # noqa: F811
    from council.tests.test_auth import OWNER, SITE_PASSWORD

    client, _ = make_app(app_password=SITE_PASSWORD, resend_api_key="re_test", email_from="TC <hi@t.example>")
    assert client.post("/api/auth/setup", json={**OWNER, "site_password": SITE_PASSWORD}).status_code == 200
    sent = []

    async def fake_send(_settings, to, subject, text, html=""):
        sent.append((to, subject, html))
        return True

    monkeypatch.setattr(emailer, "send_email", fake_send)
    resp = client.post("/api/admin/emails/scored/test")
    assert resp.status_code == 200 and resp.json()["to"] == OWNER["email"]
    assert sent[0][0] == OWNER["email"] and sent[0][1].startswith("[Test] ") and sent[0][2]
