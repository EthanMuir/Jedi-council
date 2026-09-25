"""A small, privacy-friendly visitor counter (#138): how many people see the
landing page, open the sign-up form, and view shared results -- shown on
the admin page next to sign-ups.

Counted on the server when those pages are served, so there's no script, no
cookie and nothing to consent to. No IP address is stored: each visitor is
a hash of their IP and browser mixed with a random salt that changes every
day and is deleted after two, so the same person can be counted once a day
but never followed from one day to the next. Bots and link previews are
skipped."""
from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

EVENTS = ("landing", "signup_form", "share")

_BOT = re.compile(
    r"bot|crawl|spider|slurp|preview|facebookexternalhit|embedly|whatsapp|telegram|discord|skype|"
    r"curl|wget|python|httpx|aiohttp|go-http|java/|okhttp|axios|node-fetch|headless|monitor|uptime|lighthouse",
    re.I,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS site_visits (
    day TEXT NOT NULL,
    event TEXT NOT NULL,
    visitor TEXT NOT NULL,
    referrer TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS site_visits_day ON site_visits(day, event);
CREATE TABLE IF NOT EXISTS site_salts (
    day TEXT PRIMARY KEY,
    salt TEXT NOT NULL
);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _salt(conn: sqlite3.Connection, day: str) -> str:
    row = conn.execute("SELECT salt FROM site_salts WHERE day = ?", (day,)).fetchone()
    if row:
        return row["salt"]
    salt = secrets.token_hex(16)
    conn.execute("INSERT OR IGNORE INTO site_salts (day, salt) VALUES (?, ?)", (day, salt))
    # Yesterday's salt is kept a little while for late requests, then dropped
    # so old hashes can never be matched again.
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=2)).isoformat()
    conn.execute("DELETE FROM site_salts WHERE day < ?", (cutoff,))
    return conn.execute("SELECT salt FROM site_salts WHERE day = ?", (day,)).fetchone()["salt"]


def is_bot(user_agent: str) -> bool:
    return not user_agent or bool(_BOT.search(user_agent))


def referrer_domain(referer: str, own_host: str) -> str:
    host = (urlparse(referer).netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    own = own_host.lower().split(":")[0].removeprefix("www.")
    return "" if not host or host == own else host


def record(db_path: str, event: str, *, ip: str, user_agent: str, referer: str = "", own_host: str = "") -> bool:
    """Counts one visit. Returns False when it was skipped (a bot)."""
    if event not in EVENTS or is_bot(user_agent):
        return False
    day = _today()
    conn = _connect(db_path)
    try:
        visitor = hashlib.sha256(f"{_salt(conn, day)}|{ip}|{user_agent}".encode()).hexdigest()[:16]
        conn.execute(
            "INSERT INTO site_visits (day, event, visitor, referrer) VALUES (?, ?, ?, ?)",
            (day, event, visitor, referrer_domain(referer, own_host)),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def summary(db_path: str, days: int = 30) -> dict:
    since = (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()
    conn = _connect(db_path)
    try:
        per_day = {
            (r["day"], r["event"]): (r["views"], r["visitors"])
            for r in conn.execute(
                "SELECT day, event, COUNT(*) AS views, COUNT(DISTINCT visitor) AS visitors "
                "FROM site_visits WHERE day >= ? GROUP BY day, event",
                (since,),
            )
        }
        totals = {
            r["event"]: {"views": r["views"], "visitors": r["visitors"]}
            for r in conn.execute(
                "SELECT event, COUNT(*) AS views, COUNT(DISTINCT day || visitor) AS visitors "
                "FROM site_visits WHERE day >= ? GROUP BY event",
                (since,),
            )
        }
        referrers = [
            {"domain": r["referrer"], "visitors": r["visitors"]}
            for r in conn.execute(
                "SELECT referrer, COUNT(DISTINCT day || visitor) AS visitors FROM site_visits "
                "WHERE day >= ? AND referrer != '' GROUP BY referrer ORDER BY visitors DESC LIMIT 8",
                (since,),
            )
        ]
    finally:
        conn.close()
    start = datetime.fromisoformat(since).date()
    daily = []
    for i in range(days):
        day = (start + timedelta(days=i)).isoformat()
        daily.append({
            "day": day,
            "landing_visitors": per_day.get((day, "landing"), (0, 0))[1],
            "signup_form_visitors": per_day.get((day, "signup_form"), (0, 0))[1],
            "share_views": per_day.get((day, "share"), (0, 0))[0],
        })
    empty = {"views": 0, "visitors": 0}
    return {
        "days": days,
        "since": since,
        "totals": {e: totals.get(e, empty) for e in EVENTS},
        "daily": daily,
        "referrers": referrers,
    }
