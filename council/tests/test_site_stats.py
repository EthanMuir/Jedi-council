"""The admin page's visitor counter (#138)."""
from __future__ import annotations

from council import site_stats
from council.tests.test_auth import _new_client, _signup_and_approve, make_app, site  # noqa: F401

BROWSER = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1"


def _visit(settings, path, ua=BROWSER, ip="203.0.113.7", referer=""):
    client = _new_client(settings)
    headers = {"user-agent": ua, "x-forwarded-for": ip}
    if referer:
        headers["referer"] = referer
    return client.get(path, headers=headers, follow_redirects=False)


def test_landing_signup_and_referrers_are_counted(site):
    owner_client, settings = site
    _visit(settings, "/welcome", referer="https://www.reddit.com/r/stocks")
    _visit(settings, "/welcome", referer="https://www.reddit.com/r/stocks")  # same person, same day
    _visit(settings, "/welcome", ip="198.51.100.2", referer="https://x.com/someone")
    _visit(settings, "/signup")
    _visit(settings, "/welcome", ua="Twitterbot/1.0")  # link previews aren't people
    _visit(settings, "/welcome", ua="curl/8.0")

    stats = owner_client.get("/api/admin/site-stats").json()
    assert stats["totals"]["landing"] == {"views": 3, "visitors": 2}
    assert stats["totals"]["signup_form"]["visitors"] == 1
    assert {r["domain"]: r["visitors"] for r in stats["referrers"]} == {"reddit.com": 1, "x.com": 1}
    assert stats["daily"][-1]["landing_visitors"] == 2
    assert len(stats["daily"]) == 30


def test_signups_show_up_and_nothing_personal_is_stored(site):
    owner_client, settings = site
    _signup_and_approve(owner_client, settings)
    stats = owner_client.get("/api/admin/site-stats").json()
    assert stats["signups"] == {"total": 1, "approved": 1}
    _visit(settings, "/welcome")
    import sqlite3
    conn = sqlite3.connect(settings.settings_db_path)
    stored = " ".join(str(v) for row in conn.execute("SELECT * FROM site_visits") for v in row)
    conn.close()
    assert "203.0.113.7" not in stored and "iPhone" not in stored


def test_signed_in_visits_to_the_landing_page_are_not_counted(site):
    owner_client, settings = site
    owner_client.get("/welcome", headers={"user-agent": BROWSER})
    assert owner_client.get("/api/admin/site-stats").json()["totals"]["landing"]["views"] == 0


def test_only_admins_see_visitor_stats(site):
    owner_client, settings = site
    friend, _ = _signup_and_approve(owner_client, settings)
    assert friend.get("/api/admin/site-stats").status_code == 403


def test_referrer_domain_ignores_own_site():
    assert site_stats.referrer_domain("https://tickercouncil.com/guide.html", "tickercouncil.com") == ""
    assert site_stats.referrer_domain("https://www.google.com/", "tickercouncil.com") == "google.com"
    assert site_stats.referrer_domain("", "tickercouncil.com") == ""
