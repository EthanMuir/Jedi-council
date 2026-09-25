"""A run keeps going, and still saves, when its page drops the live
connection -- a phone locking its screen does exactly this."""
from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import uvicorn

from council.config import Settings


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_run_finishes_after_the_client_disconnects(tmp_path, monkeypatch):
    import council.api.main as main_module

    settings = Settings(
        no_llm=True,
        use_data_fixtures=True,
        council_db_path=str(tmp_path / "council.db"),
        cache_db_path=str(tmp_path / "cache.db"),
        settings_db_path=str(tmp_path / "settings.db"),
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    outcome = {"finished": False, "cancelled": False}

    async def slow_run(ticker, _settings, **kw):
        try:
            await kw["progress"]("notice", {"message": "working"})
            await asyncio.sleep(1.5)
            outcome["finished"] = True
            return {"ok": True}
        except asyncio.CancelledError:
            outcome["cancelled"] = True
            raise

    monkeypatch.setattr(main_module, "run_deliberation", slow_run)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(main_module.app, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(50):
            if server.started:
                break
            time.sleep(0.1)
        url = f"http://127.0.0.1:{port}/api/deliberate/stream"
        with httpx.stream("GET", url, params={"ticker": "NVDA"}, timeout=10) as resp:
            for line in resp.iter_lines():
                if "working" in line:
                    break  # the page goes away mid-run
        for _ in range(40):
            if outcome["finished"] or outcome["cancelled"]:
                break
            time.sleep(0.1)
        assert outcome == {"finished": True, "cancelled": False}
    finally:
        server.should_exit = True
        thread.join(timeout=5)
