"""The UI must never render new HTML against stale cached JS/CSS after a deploy."""
from __future__ import annotations

import os
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from council.api.ui_files import UIFiles


def _client(ui_dir) -> TestClient:
    app = FastAPI()
    app.mount("/", UIFiles(directory=str(ui_dir), html=True), name="ui")
    return TestClient(app)


def _ui(tmp_path):
    (tmp_path / "js").mkdir()
    (tmp_path / "css").mkdir()
    (tmp_path / "js" / "app.js").write_text("console.log(1)")
    (tmp_path / "css" / "app.css").write_text("body{}")
    (tmp_path / "page.html").write_text(
        '<link rel="stylesheet" href="/css/app.css" />\n'
        '<a href="/other.html">x</a>\n'
        '<script src="/js/app.js"></script>\n'
        '<script src="/js/missing.js"></script>\n'
    )
    (tmp_path / "index.html").write_text('<script src="/js/app.js"></script>')
    return tmp_path


def _js_version(html: str) -> str:
    return re.search(r'/js/app\.js\?v=(\d+)"', html).group(1)


def test_pages_link_assets_by_version_and_are_never_cached(tmp_path):
    client = _client(_ui(tmp_path))
    for url in ("/page.html", "/"):
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "no-store"
        assert _js_version(resp.text)

    html = client.get("/page.html").text
    assert re.search(r'href="/css/app\.css\?v=\d+"', html)
    assert 'href="/other.html"' in html            # page links left alone
    assert 'src="/js/missing.js"' in html          # unknown files left alone


def test_assets_are_revalidated_before_reuse(tmp_path):
    client = _client(_ui(tmp_path))
    resp = client.get("/js/app.js")
    assert resp.headers["cache-control"] == "no-cache"
    again = client.get("/js/app.js", headers={"if-none-match": resp.headers["etag"]})
    assert again.status_code == 304


def test_a_returning_browser_gets_new_asset_links_after_a_deploy(tmp_path):
    ui = _ui(tmp_path)
    client = _client(ui)
    first = client.get("/page.html")
    before = _js_version(first.text)

    # A deploy rewrites app.js; the page itself is unchanged.
    js = ui / "js" / "app.js"
    js.write_text("console.log(2)")
    stat = js.stat()
    os.utime(js, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    # Even when the browser asks "has the page changed?", it gets the full
    # page back, now pointing at the new app.js.
    etag = first.headers.get("etag", '"anything"')
    after = client.get("/page.html", headers={"if-none-match": etag})
    assert after.status_code == 200
    assert _js_version(after.text) != before


def test_real_settings_page_stamps_its_scripts():
    import council.api.main as main_module

    html = TestClient(main_module.app).get("/settings.html").text
    for asset in ("/js/common.js", "/js/settings.js", "/css/theme.css", "/css/settings.css"):
        assert re.search(re.escape(asset) + r'\?v=\d+"', html), asset
