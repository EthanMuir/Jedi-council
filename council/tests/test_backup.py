import sqlite3
import tarfile
from datetime import datetime, timedelta, timezone

from council import backup
from council.config import Settings


def _settings(tmp_path, **extra):
    for name in ("council.db", "settings.db", "cache.db"):
        conn = sqlite3.connect(tmp_path / name)
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES (?)", (name,))
        conn.commit()
        conn.close()
    (tmp_path / "secret.key").write_text("do-not-ship")
    return Settings(
        council_db_path=str(tmp_path / "council.db"),
        settings_db_path=str(tmp_path / "settings.db"),
        cache_db_path=str(tmp_path / "cache.db"),
        backup_dir=str(tmp_path / "backups"),
        **extra,
    )


def test_backup_holds_both_databases_and_never_the_key(tmp_path):
    result = backup.make_backup(_settings(tmp_path))
    assert result.path.exists() and not result.uploaded
    with tarfile.open(result.path) as tar:
        assert sorted(tar.getnames()) == ["council.db", "settings.db"]
        tar.extractall(tmp_path / "restored")
    conn = sqlite3.connect(tmp_path / "restored" / "council.db")
    assert conn.execute("SELECT v FROM t").fetchone()[0] == "council.db"
    conn.close()


def test_only_the_newest_backups_are_kept(tmp_path):
    settings = _settings(tmp_path)
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for day in range(5):
        result = backup.make_backup(settings, keep=3, now=start + timedelta(days=day))
    kept = sorted(p.name for p in (tmp_path / "backups").iterdir())
    assert len(kept) == 3 and kept[-1] == result.path.name
    assert kept[0].startswith("ticker-council-20260903")


def test_upload_puts_the_archive_to_the_bucket_url(tmp_path, monkeypatch):
    sent = {}

    class _Resp:
        def raise_for_status(self):
            pass

    def fake_put(url, content, headers, timeout):
        sent["url"], sent["size"] = url, len(content)
        return _Resp()

    monkeypatch.setattr(backup.httpx, "put", fake_put)
    url = "https://objectstorage.example.com/p/token/n/ns/b/council-backups/o/"
    result = backup.make_backup(_settings(tmp_path, backup_upload_url=url))
    assert result.uploaded
    assert sent["url"] == url + result.path.name and sent["size"] == result.size_bytes
