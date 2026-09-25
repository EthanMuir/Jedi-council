"""Nightly backups of the two databases that can't be rebuilt: the Crypt
(council.db: every run and its score) and settings.db (accounts, saved
keys, weight releases). cache.db is only cached market data, so it's
skipped.

Each backup is a consistent snapshot taken with SQLite's own backup API
(safe while the server is running), packed into one .tar.gz, kept locally
(newest `keep`), and -- when BACKUP_UPLOAD_URL is set -- also uploaded off
the VM, so losing the VM doesn't lose the track record.

The encryption key (data/secret.key or SECRET_KEY) is never included:
anyone who got hold of a backup still couldn't read the saved API keys.
Keep a copy of the key somewhere safe yourself; without it a restored
backup still works, people just re-enter their API keys.

Run it with `python -m council backup`; scripts/setup-backups.sh installs a
daily timer for it.
"""
from __future__ import annotations

import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from council.config import Settings

BACKUP_PREFIX = "ticker-council-"


@dataclass
class BackupResult:
    path: Path
    size_bytes: int
    uploaded: bool
    removed: list[Path]


def _snapshot(source: Path, dest: Path) -> None:
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()


def make_backup(
    settings: Settings,
    dest_dir: str | Path | None = None,
    keep: int = 14,
    now: datetime | None = None,
) -> BackupResult:
    dest = Path(dest_dir or settings.backup_dir).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    archive = dest / f"{BACKUP_PREFIX}{stamp}.tar.gz"

    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(archive, "w:gz") as tar:
            for db in (settings.council_db_path, settings.settings_db_path):
                source = Path(db)
                if not source.exists():
                    continue
                copy = Path(tmp) / source.name
                _snapshot(source, copy)
                tar.add(copy, arcname=source.name)

    uploaded = False
    if settings.backup_upload_url:
        upload_backup(settings.backup_upload_url, archive)
        uploaded = True

    removed = prune_backups(dest, keep)
    return BackupResult(archive, archive.stat().st_size, uploaded, removed)


def upload_backup(url: str, archive: Path) -> None:
    """PUT the archive to an Oracle Object Storage pre-authenticated request
    (a bucket-level URL ending in /o/, allowing object writes); the file
    name is appended. Any URL that accepts a PUT of the file works."""
    target = url if not url.endswith("/") else url + archive.name
    with archive.open("rb") as body:
        resp = httpx.put(
            target,
            content=body.read(),
            headers={"Content-Type": "application/gzip"},
            timeout=120,
        )
    resp.raise_for_status()


def prune_backups(dest: Path, keep: int) -> list[Path]:
    backups = sorted(dest.glob(f"{BACKUP_PREFIX}*.tar.gz"))
    stale = backups[:-keep] if keep > 0 else []
    for path in stale:
        path.unlink()
    return stale
