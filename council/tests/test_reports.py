"""Report a problem: people send reports from the Guide; admins see and
resolve them on the admin page."""
from __future__ import annotations

from council.tests.test_auth import _new_client, _signup_and_approve, make_app, site  # noqa: F401


def test_a_report_reaches_the_admin_page(site):
    owner_client, settings = site
    friend, _ = _signup_and_approve(owner_client, settings)
    resp = friend.post("/api/reports", json={
        "kind": "result", "message": "NVDA said strongly up but every seat was down.",
        "ticker": "nvda", "run_id": "run-123", "page": "/index.html",
    })
    assert resp.status_code == 200, resp.text

    assert owner_client.get("/api/me").json()["open_reports"] == 1
    listed = owner_client.get("/api/admin/reports").json()
    assert listed["open_count"] == 1
    report = listed["reports"][0]
    assert report["email"] == "friend@example.com" and report["ticker"] == "NVDA"
    assert report["run_id"] == "run-123" and report["kind_label"] == "A result looks wrong"

    assert owner_client.post(f"/api/admin/reports/{report['id']}", json={"status": "resolved"}).status_code == 200
    assert owner_client.get("/api/admin/reports").json()["reports"] == []
    resolved = owner_client.get("/api/admin/reports?status=resolved").json()["reports"]
    assert resolved[0]["status"] == "resolved" and resolved[0]["resolved_at"]
    assert owner_client.get("/api/me").json()["open_reports"] == 0


def test_only_admins_see_reports(site):
    owner_client, settings = site
    friend, _ = _signup_and_approve(owner_client, settings)
    assert friend.get("/api/admin/reports").status_code == 403
    assert friend.post("/api/admin/reports/1", json={"status": "resolved"}).status_code == 403
    assert "open_reports" in friend.get("/api/me").json()
    assert friend.get("/api/me").json()["open_reports"] == 0


def test_reports_need_sign_in_and_a_real_message(site):
    owner_client, settings = site
    assert _new_client(settings).post("/api/reports", json={"message": "hello there"}).status_code == 401
    assert owner_client.post("/api/reports", json={"message": "hi"}).status_code == 400
    assert owner_client.post("/api/reports", json={"message": "x" * 5000}).status_code == 400
    # Unknown kinds are kept as "other" rather than refused.
    assert owner_client.post("/api/reports", json={"kind": "weird", "message": "Something odd"}).status_code == 200
    assert owner_client.get("/api/admin/reports").json()["reports"][0]["kind"] == "other"


def test_reports_are_rate_limited(site):
    owner_client, settings = site
    friend, _ = _signup_and_approve(owner_client, settings)
    for i in range(5):
        assert friend.post("/api/reports", json={"message": f"Report number {i}"}).status_code == 200
    assert friend.post("/api/reports", json={"message": "One too many"}).status_code == 429
    # Someone else isn't affected.
    assert owner_client.post("/api/reports", json={"message": "Owner's own note"}).status_code == 200
