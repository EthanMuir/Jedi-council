"""The UI must never render new HTML against stale cached JS/CSS after a deploy."""
from __future__ import annotations

import os
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from council.api.ui_files import _CLASSIC_PAGES, LOOK_COOKIE, UIFiles, look_redirect


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


def test_real_settings_pages_stamp_their_scripts():
    import council.api.main as main_module

    client = TestClient(main_module.app)
    html = client.get("/settings.html").text
    for asset in ("/js/app.js", "/js/settings.js", "/css/clean.css", "/css/pages.css"):
        assert re.search(re.escape(asset) + r'\?v=\d+"', html), asset

    client.cookies.set(LOOK_COOKIE, "classic")
    html = client.get("/classic/settings.html").text
    for asset in ("/classic/js/common.js", "/classic/js/settings.js", "/classic/css/theme.css"):
        assert re.search(re.escape(asset) + r'\?v=\d+"', html), asset


def test_every_clean_page_has_a_classic_counterpart_and_back():
    from pathlib import Path

    ui = Path(__file__).resolve().parents[1] / "ui"
    for clean, classic in _CLASSIC_PAGES.items():
        assert (ui / clean).exists(), clean
        assert (ui / "classic" / classic).exists(), classic


def test_look_redirect():
    # The clean look is the default: no cookie serves the root pages.
    assert look_redirect("", None) is None
    assert look_redirect("index.html", None) is None
    assert look_redirect("history.html", "clean") is None
    # ...and sends an old 8-bit link to its clean counterpart.
    assert look_redirect("classic/crypt.html", None) == "/history.html"
    assert look_redirect("classic/", None) == "/index.html"
    # The 8-bit look sends root pages to theirs, whatever form the root takes.
    assert look_redirect(".", "classic") == "/classic/index.html"
    assert look_redirect("", "classic") == "/classic/index.html"
    assert look_redirect("seats.html", "classic") == "/classic/archives.html"
    assert look_redirect("classic/archives.html", "classic") is None
    # Assets are never redirected.
    assert look_redirect("js/app.js", "classic") is None
    assert look_redirect("classic/js/common.js", None) is None


def test_look_cookie_redirects_keep_the_query(tmp_path):
    ui = _ui(tmp_path)
    (ui / "classic").mkdir()
    (ui / "classic" / "index.html").write_text("classic")
    client = _client(ui)
    client.cookies.set(LOOK_COOKIE, "classic")
    resp = client.get("/index.html?run=abc", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/classic/index.html?run=abc"
    assert client.get("/").text == "classic"
