"""People and their sign-ins, in the settings database.

- Anyone can ask for an account; it stays `pending` until an admin
  approves it. `disabled` accounts can't sign in.
- The first account is the owner, created once from the setup page (which
  asks for the old shared APP_PASSWORD to prove it's the site's owner). The
  owner is account 0 everywhere else (keys, models, runs, seat memories),
  so everything from before accounts stays theirs.
- Passwords are hashed with scrypt. Sessions and reset links are random
  tokens stored only as their SHA-256, so the database never holds anything
  a thief could sign in with."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

SESSION_DAYS = 30
RESET_HOURS = 1
MIN_PASSWORD_LENGTH = 10
STATUSES = ("pending", "active", "disabled")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    password_hash TEXT,
    google_sub TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_owner INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reset_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
"""

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class User:
    id: int
    email: str
    name: str
    status: str
    is_admin: bool
    is_owner: bool
    has_password: bool
    google_linked: bool
    created_at: str
    approved_at: str | None
    last_login_at: str | None

    @property
    def account(self) -> int:
        """Whose keys, models and runs: 0 for the owner, else this id."""
        return 0 if self.is_owner else self.id

    def public(self) -> dict:
        return {
            "id": self.id, "email": self.email, "name": self.name, "status": self.status,
            "is_admin": self.is_admin, "is_owner": self.is_owner,
            "has_password": self.has_password, "google_linked": self.google_linked,
            "created_at": self.created_at, "approved_at": self.approved_at,
            "last_login_at": self.last_login_at,
        }


class AccountError(ValueError):
    """A problem the person can fix -- the message is shown to them."""


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _row_to_user(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None
    return User(
        id=row["id"], email=row["email"], name=row["name"], status=row["status"],
        is_admin=bool(row["is_admin"]), is_owner=bool(row["is_owner"]),
        has_password=bool(row["password_hash"]), google_linked=bool(row["google_sub"]),
        created_at=row["created_at"], approved_at=row["approved_at"], last_login_at=row["last_login_at"],
    )


# ---- validation and passwords ------------------------------------------------------


def clean_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if not _EMAIL.match(email) or len(email) > 254:
        raise AccountError("That doesn't look like an email address.")
    return email


def clean_name(raw: str) -> str:
    name = " ".join((raw or "").split())
    if not name:
        raise AccountError("Please enter your name.")
    if len(name) > 80:
        raise AccountError("That name is too long.")
    return name


def check_password(password: str) -> str:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AccountError(f"Use at least {MIN_PASSWORD_LENGTH} characters for your password.")
    if len(password) > 256:
        raise AccountError("That password is too long.")
    return password


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, n, r, p, salt, digest = stored.split("$")
        check = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(check.hex(), digest)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---- users --------------------------------------------------------------------------


def count_users(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def get_user(conn: sqlite3.Connection, user_id: int) -> User | None:
    return _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def get_user_by_email(conn: sqlite3.Connection, email: str) -> User | None:
    return _row_to_user(conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone())


def get_user_by_google(conn: sqlite3.Connection, google_sub: str) -> User | None:
    return _row_to_user(conn.execute("SELECT * FROM users WHERE google_sub = ?", (google_sub,)).fetchone())


def password_hash(conn: sqlite3.Connection, user_id: int) -> str | None:
    row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["password_hash"] if row else None


def owner(conn: sqlite3.Connection) -> User | None:
    return _row_to_user(conn.execute("SELECT * FROM users WHERE is_owner = 1").fetchone())


def list_users(conn: sqlite3.Connection) -> list[User]:
    rows = conn.execute("SELECT * FROM users ORDER BY status = 'pending' DESC, created_at DESC").fetchall()
    return [_row_to_user(r) for r in rows]


def create_user(
    conn: sqlite3.Connection,
    *,
    email: str,
    name: str,
    password: str | None = None,
    google_sub: str | None = None,
    status: str = "pending",
    is_admin: bool = False,
    is_owner: bool = False,
) -> User:
    email, name = clean_email(email), clean_name(name)
    if password is not None:
        check_password(password)
    if get_user_by_email(conn, email):
        raise AccountError("There's already an account with that email. Try signing in.")
    now = _now()
    cur = conn.execute(
        "INSERT INTO users (email, name, password_hash, google_sub, status, is_admin, is_owner, created_at, approved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            email, name, hash_password(password) if password else None, google_sub, status,
            int(is_admin), int(is_owner), now, now if status == "active" else None,
        ),
    )
    conn.commit()
    return get_user(conn, cur.lastrowid)


def set_status(conn: sqlite3.Connection, user_id: int, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    conn.execute(
        "UPDATE users SET status = ?, approved_at = CASE WHEN ? = 'active' THEN COALESCE(approved_at, ?) ELSE approved_at END "
        "WHERE id = ?",
        (status, status, _now(), user_id),
    )
    if status != "active":
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.commit()


def set_admin(conn: sqlite3.Connection, user_id: int, is_admin: bool) -> None:
    conn.execute("UPDATE users SET is_admin = ? WHERE id = ? AND is_owner = 0", (int(is_admin), user_id))
    conn.commit()


def set_password(conn: sqlite3.Connection, user_id: int, password: str) -> None:
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(check_password(password)), user_id)
    )
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM reset_tokens WHERE user_id = ?", (user_id,))
    conn.commit()


def link_google(conn: sqlite3.Connection, user_id: int, google_sub: str) -> None:
    conn.execute("UPDATE users SET google_sub = ? WHERE id = ?", (google_sub, user_id))
    conn.commit()


def record_login(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), user_id))
    conn.commit()


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    """The owner can't be deleted. Their saved runs stay in the Crypt,
    which is append-only, but no one can open them any more except
    through the Master Crypt."""
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM reset_tokens WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ? AND is_owner = 0", (user_id,))
    conn.commit()


# ---- sessions and reset links ---------------------------------------------------------


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (_token_hash(token), user_id, now.isoformat(timespec="seconds"),
         (now + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")),
    )
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(timespec="seconds"),))
    conn.commit()
    record_login(conn, user_id)
    return token


def session_user(conn: sqlite3.Connection, token: str | None) -> User | None:
    """The active person a session token belongs to, or None."""
    if not token:
        return None
    row = conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ? AND s.expires_at > ?",
        (_token_hash(token), _now()),
    ).fetchone()
    user = _row_to_user(row)
    return user if user and user.status == "active" else None


def delete_session(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
        conn.commit()


def create_reset_token(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.utcnow() + timedelta(hours=RESET_HOURS)
    conn.execute("DELETE FROM reset_tokens WHERE user_id = ?", (user_id,))
    conn.execute(
        "INSERT INTO reset_tokens (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (_token_hash(token), user_id, expires.isoformat(timespec="seconds")),
    )
    conn.commit()
    return token


def reset_token_user(conn: sqlite3.Connection, token: str | None) -> User | None:
    if not token:
        return None
    row = conn.execute(
        "SELECT u.* FROM reset_tokens t JOIN users u ON u.id = t.user_id "
        "WHERE t.token_hash = ? AND t.expires_at > ?",
        (_token_hash(token), _now()),
    ).fetchone()
    return _row_to_user(row)
