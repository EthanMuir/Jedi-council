"""Per-person data (#114): keys saved before accounts move over to the
owner, encrypted; seat memories stay with the person whose runs made them;
model choices from before accounts stay the owner's."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from council import key_store
from council.config import Settings, settings_for_account
from council.engine import model_settings
from council.memory.store import MemoryStore


def test_plaintext_keys_from_before_accounts_become_the_owners_encrypted(tmp_path):
    db = str(tmp_path / "settings.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE api_keys (name TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT)")
    conn.execute("INSERT INTO api_keys (name, value) VALUES ('google_api_key', 'AIza-old-plain')")
    conn.commit()
    conn.close()

    assert key_store.saved_keys(db) == {"google_api_key": "AIza-old-plain"}
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    stored = conn.execute("SELECT value_enc FROM account_api_keys").fetchone()[0]
    assert "api_keys" not in tables and "AIza-old-plain" not in stored
    assert key_store.saved_keys(db, account=7) == {}


def test_other_people_never_get_the_env_keys(tmp_path):
    base = Settings(settings_db_path=str(tmp_path / "settings.db"), anthropic_api_key="sk-env-owner")
    assert settings_for_account(base, 0).anthropic_api_key == "sk-env-owner"
    friend = settings_for_account(base, 5)
    assert friend.anthropic_api_key == "" and friend.council_account == 5
    key_store.save_key(base.settings_db_path, "groq_api_key", "gsk_friend", account=5)
    assert settings_for_account(base, 5).groq_api_key == "gsk_friend"
    assert settings_for_account(base, 0).groq_api_key == ""


def test_single_user_model_choices_become_the_owners(tmp_path):
    db = str(tmp_path / "settings.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE model_overrides (role TEXT PRIMARY KEY, model_id TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT (datetime('now')))")
    conn.execute("INSERT INTO model_overrides (role, model_id) VALUES ('technician', 'gpt-5')")
    conn.commit()
    conn.close()
    conn = model_settings.connect(db)
    assert model_settings.get_overrides(conn) == {"technician": "gpt-5"}
    assert model_settings.get_overrides(conn, 3) == {}
    model_settings.set_override(conn, "technician", "claude-sonnet-5", 3)
    assert model_settings.get_overrides(conn) == {"technician": "gpt-5"}


def test_seat_memories_are_private(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "council.db"))
    conn.row_factory = sqlite3.Row
    owner, friend = MemoryStore(conn), MemoryStore(conn, account=4)
    past = datetime.utcnow() - timedelta(days=3)
    friend.add(seat_id="technician", ticker="NVDA", kind="lesson", as_of=past, horizon="short",
               content={"lesson": "friend's lesson"})
    now = datetime.utcnow()
    assert owner.retrieve(seat_id="technician", ticker="NVDA", as_of=now) == []
    assert [e.content["lesson"] for e in friend.retrieve(seat_id="technician", ticker="NVDA", as_of=now)] == ["friend's lesson"]
