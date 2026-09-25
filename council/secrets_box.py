"""Encrypts values at rest -- each person's API keys -- so a copy of the
settings database alone (a backup, a stray download) doesn't give anyone
working keys. The encryption key comes from SECRET_KEY in .env if set,
otherwise from a random one generated once into data/secret.key next to
the databases (readable only by the app's own user)."""
from __future__ import annotations

import base64
import hashlib
import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_KEY_FILE_NAME = "secret.key"


def _key_file(settings_db_path: str) -> Path:
    return Path(settings_db_path).resolve().parent / _KEY_FILE_NAME


def load_secret(settings_db_path: str, configured: str = "") -> str:
    """The server's own secret: SECRET_KEY if set, else the one on disk
    (created on first use)."""
    if configured:
        return configured
    path = _key_file(settings_db_path)
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = base64.urlsafe_b64encode(os.urandom(32)).decode()
    # Created with owner-only permissions from the start, never world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secret)
    return secret


@lru_cache(maxsize=8)
def _fernet(secret: str) -> Fernet:
    digest = hashlib.sha256(f"{secret}:api-keys-v1".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(secret: str, value: str) -> str:
    return _fernet(secret).encrypt(value.encode()).decode()


def decrypt(secret: str, token: str) -> str | None:
    """None if the value can't be read -- e.g. SECRET_KEY was changed."""
    try:
        return _fernet(secret).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
