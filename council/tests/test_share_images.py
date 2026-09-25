"""Share images (council/share_card.py) and the company name shown next to
tickers."""
from __future__ import annotations

import io
import json

from PIL import Image

from council import share_card
from council.tests.test_auth import _signup_and_approve, make_app, site  # noqa: F401
from council.tests.test_share_and_changes import _run, client  # noqa: F401


def _size(png: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(png)).size


def test_a_run_saves_the_company_name(client):
    test_client, _ = client
    run_id = _run(test_client)
    assert test_client.get(f"/api/runs/{run_id}").json()["company"] == "NVIDIA Corporation"
    assert test_client.get("/api/predictions").json()["runs"][0]["company"] == "NVIDIA Corporation"


def test_the_company_name_streams_before_the_seats(client):
    test_client, _ = client
    events, event = [], None
    with test_client.stream("GET", "/api/deliberate/stream", params={"ticker": "NVDA"}) as r:
        for line in r.iter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
                events.append(event)
            elif line.startswith("data: ") and event == "company":
                assert json.loads(line.removeprefix("data: "))["name"] == "NVIDIA Corporation"
    assert "company" in events and events.index("company") < events.index("seat_result")


def test_renders_both_sizes(client):
    test_client, _ = client
    run = test_client.get(f"/api/runs/{_run(test_client)}").json()
    assert _size(share_card.render(run, "og", "tickercouncil.com")) == (1200, 630)
    assert _size(share_card.render(run, "story", "tickercouncil.com")) == (1080, 1920)
    # A run from before company names and price targets still renders.
    run["synthesis"].pop("company", None)
    run["company"] = None
    for t in run["synthesis"]["terms"].values():
        t.pop("price_target", None)
    assert _size(share_card.render(run, "story", "")) == (1080, 1920)


def test_share_links_serve_their_images_and_preview_with_them(client):
    test_client, _ = client
    run_id = _run(test_client)
    url = test_client.post(f"/api/runs/{run_id}/share").json()["url"]
    path = url[url.index("/s/"):]
    page = test_client.get(path).text
    assert f'content="{url}/card.png"' in page and "NVIDIA Corporation" in page
    card = test_client.get(f"{path}/card.png")
    assert card.status_code == 200 and card.headers["content-type"] == "image/png"
    assert _size(card.content) == (1200, 630)
    assert _size(test_client.get(f"{path}/story.png").content) == (1080, 1920)
    assert test_client.get(f"{path}/other.png").status_code == 404
    test_client.delete(f"/api/runs/{run_id}/share")
    assert test_client.get(f"{path}/card.png").status_code == 404


def test_the_run_image_is_only_for_its_owner(site):
    owner_client, settings = site
    friend, _ = _signup_and_approve(owner_client, settings)
    run_id = _run(friend)
    resp = friend.get(f"/api/runs/{run_id}/image.png")
    assert resp.status_code == 200 and _size(resp.content) == (1080, 1920)
    stranger, _ = _signup_and_approve(owner_client, settings, email="other@example.com")
    assert stranger.get(f"/api/runs/{run_id}/image.png").status_code == 404
