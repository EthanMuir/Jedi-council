"""'Your call was scored' emails (#135). After a resolution sweep, each
person whose calls were just scored gets one email listing them -- right or
wrong, and what the price did -- with links back to the runs.

On by default; turned off from Settings -> Account or the unsubscribe link
in every email (signed, so nobody can switch it off for someone else).
Sample runs are never emailed about. Needs sign-in and email set up."""
from __future__ import annotations

import hashlib
import hmac
import logging
import sqlite3
from pathlib import Path

from council import accounts, emailer, secrets_box
from council.config import Settings
from council.crypt.db import connect, effective_run_mode

log = logging.getLogger("council.notify")

TERM_NAMES = {"short": "next week", "medium": "next 3 months", "long": "next year"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS email_prefs (
    account INTEGER PRIMARY KEY,
    scored_calls INTEGER NOT NULL DEFAULT 1
);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn


def wants_scored_emails(settings_db_path: str, account: int) -> bool:
    conn = _connect(settings_db_path)
    try:
        row = conn.execute("SELECT scored_calls FROM email_prefs WHERE account = ?", (account,)).fetchone()
        return True if row is None else bool(row[0])
    finally:
        conn.close()


def set_scored_emails(settings_db_path: str, account: int, on: bool) -> None:
    conn = _connect(settings_db_path)
    try:
        conn.execute(
            "INSERT INTO email_prefs (account, scored_calls) VALUES (?, ?) "
            "ON CONFLICT(account) DO UPDATE SET scored_calls = excluded.scored_calls",
            (account, 1 if on else 0),
        )
        conn.commit()
    finally:
        conn.close()


def unsubscribe_token(settings: Settings, account: int) -> str:
    secret = secrets_box.load_secret(settings.settings_db_path, settings.secret_key)
    return hmac.new(secret.encode(), f"unsubscribe:{account}".encode(), hashlib.sha256).hexdigest()[:32]


def check_unsubscribe_token(settings: Settings, account: int, token: str) -> bool:
    return hmac.compare_digest(unsubscribe_token(settings, account), token or "")


def _lean(p: float | None) -> str:
    if p is None:
        return "no read"
    d = round((p - 0.5) * 1000)
    return "up" if d > 0 else "down" if d < 0 else "even"


def _person(conn, account: int) -> accounts.User | None:
    user = accounts.owner(conn) if account == 0 else accounts.get_user(conn, account)
    if user is None or user.status != "active" or not user.email:
        return None
    return user


def compose(name: str, lines: list[dict], base_url: str, unsubscribe_url: str) -> tuple[str, str]:
    right = sum(1 for x in lines if x["correct"])
    if len(lines) == 1:
        x = lines[0]
        subject = f"Your {x['ticker']} call was {'right' if x['correct'] else 'wrong'}"
    else:
        subject = f"{len(lines)} of your calls were scored: {right} right"
    body = [f"Hi {name},", "", "The Council's calls on these runs just reached their end date and were scored:", ""]
    for x in lines:
        move = f"price {x['move']:+.1f}%" if x["move"] is not None else "price change unknown"
        verdict = "RIGHT" if x["correct"] else "WRONG"
        body.append(f"- {x['ticker']}, {TERM_NAMES.get(x['term'], x['term'])}: called {x['called']} -> {verdict} ({move})")
        if base_url and x["run_id"]:
            body.append(f"  {base_url}/index.html?run={x['run_id']}")
    body += ["", f"{right} of {len(lines)} right this time. Every scored call counts toward each seat's track record."]
    if base_url:
        body += ["", f"Your full record: {base_url}/history.html"]
    body += ["", "Not financial advice.", "", f"Stop these emails: {unsubscribe_url}" if unsubscribe_url else ""]
    return subject, "\n".join(body).rstrip() + "\n"


async def email_scored_calls(settings: Settings, swept: list) -> int:
    """Emails everyone whose calls were just scored. Returns emails sent."""
    if not swept or not settings.resolved_auth_enabled or not emailer.email_configured(settings):
        return 0
    base_url = settings.public_url.rstrip("/")
    ids = [s.prediction_id for s in swept]
    by_id = {s.prediction_id: s for s in swept}
    council = connect(settings.council_db_path)
    try:
        marks = ",".join("?" * len(ids))
        rows = council.execute(
            f"SELECT id, run_id, ticker, horizon, user_id, p_raw, council_vote, council_confidence, "
            f"run_mode, total_cost_usd FROM predictions WHERE id IN ({marks})",
            ids,
        ).fetchall()
    finally:
        council.close()

    per_account: dict[int, list[dict]] = {}
    for row in rows:
        if effective_run_mode(row["run_mode"], row["total_cost_usd"]) == "sample":
            continue
        result = by_id[row["id"]]
        if result.direction_correct is None:
            continue
        p = row["p_raw"]
        if p is None and row["council_confidence"] is not None:
            p = row["council_confidence"] if row["council_vote"] == "BULLISH" else 1 - row["council_confidence"]
        per_account.setdefault(row["user_id"] or 0, []).append({
            "ticker": row["ticker"], "term": row["horizon"], "run_id": row["run_id"] or row["id"],
            "called": _lean(p), "correct": bool(result.direction_correct), "move": result.realised_move_pct,
        })

    sent = 0
    people = accounts.connect(settings.settings_db_path)
    try:
        for account, lines in per_account.items():
            person = _person(people, account)
            if person is None or not wants_scored_emails(settings.settings_db_path, account):
                continue
            unsub = f"{base_url}/unsubscribe?u={account}&t={unsubscribe_token(settings, account)}" if base_url else ""
            order = {"short": 0, "medium": 1, "long": 2}
            lines.sort(key=lambda x: (x["ticker"], order.get(x["term"], 9)))
            subject, text = compose(person.name, lines, base_url, unsub)
            try:
                if await emailer.send_email(settings, person.email, subject, text):
                    sent += 1
            except Exception as exc:  # noqa: BLE001 -- one failure mustn't stop the rest
                log.warning("couldn't email scored calls to account %s: %s", account, exc)
    finally:
        people.close()
    return sent
