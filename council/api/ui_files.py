"""Serves council/ui/ so that browsers always pick up a fresh deploy.

Plain StaticFiles sends no Cache-Control header, which lets browsers keep
using a stale copy of a .js/.css file for hours after a `git pull` -- and
a page with new HTML but old JS renders half-built. So:

- every HTML page is sent fresh (no-store), with each /js/ and /css/ link
  stamped with the file's modification time (?v=...), so a changed file
  gets a new URL the browser has never cached;
- every other file is sent with no-cache, so the browser checks with the
  server (a cheap 304 when nothing changed) before reusing its copy.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from starlette.responses import HTMLResponse, Response
from starlette.types import Scope

_ASSET_LINK = re.compile(r'(\s(?:src|href)=")(/(?:js|css)/[^"?#]+)(")')


class UIFiles(StaticFiles):
    def file_response(
        self,
        full_path: os.PathLike,
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        if str(full_path).endswith(".html"):
            html = Path(full_path).read_text(encoding="utf-8")
            return HTMLResponse(
                _ASSET_LINK.sub(self._stamp, html),
                status_code=status_code,
                headers={"Cache-Control": "no-store"},
            )
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = "no-cache"
        return response

    def _stamp(self, match: re.Match) -> str:
        url = match.group(2)
        try:
            version = os.stat(Path(self.directory) / url.lstrip("/")).st_mtime_ns
        except OSError:
            return match.group(0)
        return f"{match.group(1)}{url}?v={version}{match.group(3)}"
