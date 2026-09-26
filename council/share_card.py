"""Share images of a run: the three lean bars on a clean dark card.

- "og" (1200x630): the link preview shown when a share link is sent in
  Messages, WhatsApp, X, Slack and so on. Just enough to make someone tap.
- "story" (1080x1920): for Instagram and other stories, from the Share
  button on the run page. Adds the headline and each period's price target,
  and keeps clear of the top and bottom where the story app draws its own
  buttons.

Drawn at twice the size and scaled down, so the bars' curves come out
smooth. Fonts are Inter (council/assets/fonts, SIL Open Font License)."""
from __future__ import annotations

import io
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from council.api.run_views import direction, term_leans
from council.engine.horizons import TERMS

_ASSETS = Path(__file__).resolve().parent / "assets" / "fonts"
_ICON = Path(__file__).resolve().parent / "ui" / "icons" / "icon-192.png"

SIZES = {"og": (1200, 630), "story": (1080, 1920)}
SCALE = 2

BG_TOP = (11, 15, 23)
BG_BOTTOM = (20, 27, 40)
INK = (242, 244, 248)
MUTED = (154, 163, 178)
FAINT = (98, 108, 124)
TRACK = (38, 47, 61)
ACCENT = (110, 150, 255)
# Soft teal / rose / amber: reads as up / down / even without a flat traffic-light green.
COLOURS = {"up": (94, 234, 212), "down": (251, 113, 133), "even": (251, 191, 36), "noread": (120, 128, 140)}

TERM_LABEL = {"short": "Next week", "medium": "Next 3 months", "long": "Next year"}


@lru_cache(maxsize=32)
def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_ASSETS / f"Inter-{weight}.ttf"), size * SCALE)


def lean_words(p: float | None) -> str:
    """Same wording as the app's leanWords()."""
    if p is None:
        return "No read"
    d = abs(round((p - 0.5) * 1000) / 1000)
    if d == 0:
        return "Even"
    side = "up" if p > 0.5 else "down"
    if d < 0.02:
        return f"Barely {side}"
    if d < 0.05:
        return f"Leaning {side}"
    if d < 0.10:
        return side.capitalize()
    return f"Strongly {side}"


def _price(v: float, like: float) -> str:
    return f"${v:,.0f}" if like >= 100 else f"${v:,.2f}"


def _date(iso: str) -> str:
    try:
        when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return f"{when:%b} {when.day}, {when.year}"


class _Canvas:
    """Coordinates in output pixels; everything is drawn at SCALE."""

    def __init__(self, size: tuple[int, int]):
        self.w, self.h = size
        self.img = Image.new("RGB", (self.w * SCALE, self.h * SCALE), BG_TOP)
        self.draw = ImageDraw.Draw(self.img)
        # A soft top-to-bottom gradient.
        for y in range(self.h * SCALE):
            t = y / (self.h * SCALE)
            colour = tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM))
            self.draw.line([(0, y), (self.w * SCALE, y)], fill=colour)

    def s(self, v: float) -> int:
        return round(v * SCALE)

    def text(self, xy, text, weight, size, fill, anchor="la"):
        self.draw.text((self.s(xy[0]), self.s(xy[1])), text, font=_font(weight, size), fill=fill, anchor=anchor)

    def text_width(self, text, weight, size) -> float:
        return self.draw.textlength(text, font=_font(weight, size)) / SCALE

    def fit(self, text, weight, size, max_width) -> str:
        """Cuts text down with an ellipsis to fit max_width."""
        if self.text_width(text, weight, size) <= max_width:
            return text
        while text and self.text_width(text + "…", weight, size) > max_width:
            text = text[:-1]
        return text.rstrip() + "…"

    def wrap(self, text, weight, size, max_width, max_lines) -> list[str]:
        lines, line = [], ""
        for word in text.split():
            trial = f"{line} {word}".strip()
            if self.text_width(trial, weight, size) <= max_width:
                line = trial
                continue
            lines.append(line)
            line = word
            if len(lines) == max_lines:
                break
        if line and len(lines) < max_lines:
            lines.append(line)
        if len(lines) == max_lines and " ".join(lines) != text.strip():
            lines[-1] = self.fit(lines[-1] + " …", weight, size, max_width)
        return lines

    def rounded(self, box, radius, fill):
        x0, y0, x1, y1 = box
        self.draw.rounded_rectangle([self.s(x0), self.s(y0), self.s(x1), self.s(y1)], radius=self.s(radius), fill=fill)

    def circle(self, cx, cy, r, fill, outline=None, width=0):
        self.draw.ellipse(
            [self.s(cx - r), self.s(cy - r), self.s(cx + r), self.s(cy + r)],
            fill=fill, outline=outline, width=self.s(width),
        )

    def logo(self, x, y, size):
        try:
            icon = Image.open(_ICON).convert("RGBA").resize((self.s(size), self.s(size)), Image.LANCZOS)
        except OSError:
            return 0
        mask = Image.new("L", icon.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, *icon.size], radius=self.s(size * 0.22), fill=255)
        self.img.paste(icon, (self.s(x), self.s(y)), mask)
        return size

    def png(self) -> bytes:
        out = self.img.resize((self.w, self.h), Image.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, "PNG", optimize=True)
        return buf.getvalue()


def _bar(c: _Canvas, x0: float, x1: float, cy: float, p: float | None, height: float, knob: float = 0) -> None:
    """The lean bar: a slim track spanning a 25%-75% chance of rising, the
    middle marked, and a fill running out from the middle to the Council's
    position -- faint at the middle, solid at its rounded end, with a soft
    glow. No knob."""
    c.rounded((x0, cy - height / 2, x1, cy + height / 2), height / 2, TRACK)
    mid = (x0 + x1) / 2
    if p is not None and direction(p) != "even":
        x = x0 + max(0.0, min(1.0, (p - 0.25) / 0.5)) * (x1 - x0)
        _gradient_fill(c, mid, x, cy, height, COLOURS[direction(p)])
    c.rounded((mid - 1.5, cy - height * 0.95, mid + 1.5, cy + height * 0.95), 1.5, FAINT)


def _gradient_fill(c: _Canvas, mid: float, end: float, cy: float, height: float, colour: tuple) -> None:
    left, right = sorted((mid, end))
    w, h = max(1, c.s(right - left)), max(1, c.s(height))
    # Alpha runs from faint at the middle line to solid at the far end.
    ramp = Image.linear_gradient("L").rotate(90, expand=True).resize((w, h))
    if end < mid:
        ramp = ramp.transpose(Image.FLIP_LEFT_RIGHT)
    alpha = ramp.point(lambda v: 50 + int(v * (255 - 50) / 255))
    shape = Image.new("L", (w, h), 0)
    r = h // 2
    draw = ImageDraw.Draw(shape)
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    # Square off the end that sits on the middle line.
    if end >= mid:
        draw.rectangle([0, 0, min(r, w - 1), h - 1], fill=255)
    else:
        draw.rectangle([max(0, w - 1 - r), 0, w - 1, h - 1], fill=255)
    mask = ImageChops.multiply(alpha, shape)
    x, y = c.s(left), c.s(cy - height / 2)
    # A soft glow under the fill.
    pad = h * 2
    glow = Image.new("L", (w + pad * 2, h + pad * 2), 0)
    glow.paste(mask, (pad, pad))
    glow = glow.filter(ImageFilter.GaussianBlur(h * 0.9)).point(lambda v: int(v * 0.55))
    c.img.paste(Image.new("RGB", glow.size, colour), (x - pad, y - pad), glow)
    c.img.paste(Image.new("RGB", (w, h), colour), (x, y), mask)


def _scored(run: dict, term: str) -> bool | None:
    res = (run["terms"].get(term) or {}).get("resolution")
    if not res or res.get("direction_correct") is None:
        return None
    return bool(res["direction_correct"])


def _brand(c: _Canvas, x: float, y: float, size: int) -> None:
    offset = c.logo(x, y - size * 0.72, size * 1.45)
    c.text((x + (offset + size * 0.45 if offset else 0), y), "Ticker Council", "SemiBold", size, INK, anchor="ls")


def render_og(run: dict, site: str) -> bytes:
    c = _Canvas(SIZES["og"])
    pad = 64
    leans = term_leans(run)
    company = (run.get("synthesis") or {}).get("company") or ""

    _brand(c, pad, pad + 26, 28)
    c.text((c.w - pad, pad + 26), _date(run["created_at"]), "Regular", 24, MUTED, anchor="rs")

    ticker_y = 196
    c.text((pad, ticker_y), run["ticker"], "Bold", 84, INK, anchor="ls")
    tw = c.text_width(run["ticker"], "Bold", 84)
    if company:
        c.text((pad + tw + 22, ticker_y - 4), c.fit(company, "Regular", 32, c.w - pad * 2 - tw - 22), "Regular", 32, MUTED, anchor="ls")

    row_y, row_gap = 276, 86
    label_w, words_w = 250, 250
    for i, term in enumerate(TERMS):
        p = leans.get(term)
        cy = row_y + i * row_gap
        c.text((pad, cy), TERM_LABEL[term], "SemiBold", 30, INK, anchor="lm")
        _bar(c, pad + label_w, c.w - pad - words_w, cy, p, 10)
        words = lean_words(p)
        scored = _scored(run, term)
        c.text((c.w - pad, cy), words, "Bold", 32, COLOURS[direction(p)], anchor="rm")
        if scored is not None:
            c.text((c.w - pad, cy + 32), "✓ Right" if scored else "✗ Wrong", "SemiBold", 20, COLOURS["up" if scored else "down"], anchor="rm")

    c.text((pad, c.h - pad + 6), "See the full analysis", "SemiBold", 28, ACCENT, anchor="ls")
    if site:
        c.text((c.w - pad, c.h - pad + 6), site, "Regular", 24, MUTED, anchor="rs")
    return c.png()


def render_story(run: dict, site: str) -> bytes:
    c = _Canvas(SIZES["story"])
    pad = 84
    leans = term_leans(run)
    synth = run.get("synthesis") or {}
    company = synth.get("company") or ""
    terms = synth.get("terms") or {}

    # Story apps draw their own bar across the top ~220px and reply boxes
    # along the bottom ~250px; everything sits between.
    _brand(c, pad, 290, 38)
    c.text((pad, 440), run["ticker"], "Bold", 140, INK, anchor="ls")
    y = 498
    if company:
        c.text((pad, y), c.fit(company, "Regular", 42, c.w - pad * 2), "Regular", 42, MUTED, anchor="ls")
        y += 52
    c.text((pad, y), _date(run["created_at"]), "Regular", 32, FAINT, anchor="ls")

    headline = synth.get("plain_headline") or synth.get("headline") or ""
    y += 92
    for line in c.wrap(headline, "SemiBold", 42, c.w - pad * 2, 3):
        c.text((pad, y), line, "SemiBold", 42, INK, anchor="ls")
        y += 56

    card_h, gap = 190, 20
    card_y = max(y + 12, 870)
    for i, term in enumerate(TERMS):
        p = leans.get(term)
        top = card_y + i * (card_h + gap)
        c.rounded((pad, top, c.w - pad, top + card_h), 28, (26, 33, 46))
        inner = pad + 36
        c.text((inner, top + 56), TERM_LABEL[term], "SemiBold", 34, INK, anchor="ls")
        right_edge = c.w - inner
        scored = _scored(run, term)
        if scored is not None:
            result = "✓ Right" if scored else "✗ Wrong"
            c.text((right_edge, top + 56), result, "Bold", 34, COLOURS["up" if scored else "down"], anchor="rs")
            right_edge -= c.text_width(result, "Bold", 34) + 28
            c.text((right_edge + 14, top + 56), "·", "Bold", 34, FAINT, anchor="ms")
            right_edge -= 14
        c.text((right_edge, top + 56), lean_words(p), "Bold", 34, COLOURS[direction(p)], anchor="rs")
        _bar(c, inner, c.w - inner, top + 104, p, 12)
        target = (terms.get(term) or {}).get("price_target")
        if target and p is not None:
            like = target["target"]
            c.text(
                (inner, top + 160),
                f"Target about {_price(target['target'], like)}  ·  {target['chance_pct']}% chance "
                f"{_price(target['low'], like)}–{_price(target['high'], like)}",
                "Regular", 28, MUTED, anchor="ls",
            )

    foot = card_y + 3 * (card_h + gap) + 60
    c.text((c.w / 2, foot), "Twelve AI analysts. One verdict.", "SemiBold", 38, INK, anchor="ms")
    if site:
        c.text((c.w / 2, foot + 54), f"Full analysis at {site}", "Regular", 32, ACCENT, anchor="ms")
    c.text((c.w / 2, foot + 98), "Not financial advice.", "Regular", 24, FAINT, anchor="ms")
    return c.png()


def render(run: dict, fmt: str, site: str = "") -> bytes:
    if fmt == "story":
        return render_story(run, site)
    return render_og(run, site)
