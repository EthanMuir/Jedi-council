"""The look of every email the app sends (#149): one branded layout -- the
emu and "Ticker Council" on top, a white card on the app's light grey, a
clear button for the main action, a small footer -- plus the pieces the
emails are built from.

Email HTML isn't web HTML: Gmail, Outlook and Apple Mail each drop
different CSS, so this sticks to tables, inline styles and bgcolor
attributes, which all of them keep. Apple Mail and Outlook.com also
honour the dark-mode block in <style>; Gmail darkens emails its own way.
Every email also goes out with a plain-text copy (see Message).

Anything a person typed (names, report messages) goes through esc()."""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from urllib.parse import urlsplit

# The clean look's light palette (council/ui/css/clean.css).
BG = "#f3f5f8"
SURFACE = "#ffffff"
SURFACE_2 = "#f7f8fa"
INK = "#141820"
MUTED = "#5a6475"
FAINT = "#8a93a3"
LINE = "#e0e4ea"
ACCENT = "#2c56c9"
TRACK = "#e9ecf1"
TONES = {
    "up": ("#16835a", "#e3f3ec"),
    "down": ("#c23b35", "#fbe8e7"),
    "even": ("#8a6d1c", "#f6efd9"),
    "noread": ("#5a6475", "#eef1f5"),
    "accent": (ACCENT, "#e6ecfb"),
}
FONT = "'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
MONO = "'IBM Plex Mono', ui-monospace, Menlo, Consolas, monospace"

_DARK_CSS = """
:root { color-scheme: light dark; supported-color-schemes: light dark; }
@media (prefers-color-scheme: dark) {
  .tc-bg { background: #0d1015 !important; }
  .tc-card { background: #151a21 !important; border-color: #262d38 !important; }
  .tc-box { background: #1a2029 !important; border-color: #262d38 !important; }
  .tc-ink { color: #e5e8ed !important; }
  .tc-muted { color: #9aa3b2 !important; }
  .tc-faint { color: #6d7788 !important; }
  .tc-accent { color: #7597f2 !important; }
  .tc-line { border-color: #262d38 !important; }
  .tc-track { background: #232a35 !important; }
  .tc-btn { background: #7597f2 !important; }
  .tc-btn a { color: #0d1015 !important; }
}
"""


@dataclass(frozen=True)
class Message:
    subject: str
    text: str
    html: str = ""


def esc(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def origin(url: str) -> str:
    """https://site.example from any link on that site ("" if it isn't one)."""
    parts = urlsplit(url or "")
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme in ("http", "https") and parts.netloc else ""


def p(html: str, *, muted: bool = False, size: int = 16, gap: int = 16) -> str:
    """A paragraph. `html` is already escaped/assembled by the caller."""
    colour, cls = (MUTED, "tc-muted") if muted else (INK, "tc-ink")
    return (
        f'<p class="{cls}" style="margin:0 0 {gap}px;font-family:{FONT};font-size:{size}px;'
        f'line-height:1.55;color:{colour};">{html}</p>'
    )


def heading(text: str) -> str:
    return (
        f'<h1 class="tc-ink" style="margin:0 0 16px;font-family:{FONT};font-size:24px;line-height:1.25;'
        f'font-weight:700;letter-spacing:-0.3px;color:{INK};">{esc(text)}</h1>'
    )


def button(url: str, label: str) -> str:
    """A 'bulletproof' button: a coloured table cell, so Outlook draws it too."""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 20px;">'
        f'<tr><td class="tc-btn" bgcolor="{ACCENT}" style="border-radius:10px;background:{ACCENT};">'
        f'<a href="{esc(url)}" target="_blank" style="display:inline-block;padding:13px 22px;font-family:{FONT};'
        f'font-size:16px;font-weight:600;line-height:1;color:#ffffff;text-decoration:none;border-radius:10px;">'
        f"{esc(label)}</a></td></tr></table>"
    )


def chip(text: str, tone: str) -> str:
    fg, bg = TONES.get(tone, TONES["noread"])
    return (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:99px;background:{bg};color:{fg};'
        f'font-family:{FONT};font-size:13px;font-weight:600;line-height:1.4;white-space:nowrap;">{esc(text)}</span>'
    )


def link(url: str, label: str) -> str:
    return f'<a class="tc-accent" href="{esc(url)}" target="_blank" style="color:{ACCENT};text-decoration:none;font-weight:500;">{esc(label)}</a>'


def details(rows: list[tuple[str, str]]) -> str:
    """Label / value lines in a soft box. Values are already-escaped HTML."""
    cells = "".join(
        f'<tr><td class="tc-muted" style="padding:6px 16px 6px 0;font-family:{FONT};font-size:14px;color:{MUTED};'
        f'vertical-align:top;white-space:nowrap;">{esc(label)}</td>'
        f'<td class="tc-ink" style="padding:6px 0;font-family:{FONT};font-size:14px;color:{INK};word-break:break-word;">{value}</td></tr>'
        for label, value in rows
    )
    return box(f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">{cells}</table>')


def box(inner: str, *, pad: int = 16, gap: int = 16) -> str:
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin:0 0 {gap}px;">'
        f'<tr><td class="tc-box" bgcolor="{SURFACE_2}" style="padding:{pad}px;background:{SURFACE_2};'
        f'border:1px solid {LINE};border-radius:12px;">{inner}</td></tr></table>'
    )


def lean_bar(prob: float | None, tone: str) -> str:
    """The app's lean bar in table cells: a track with a centre line, filled
    from the middle toward the side the Council leaned, as far as it leaned."""
    x = 50.0 if prob is None else max(0.0, min(1.0, (prob - 0.25) / 0.5)) * 100
    fill = TONES.get(tone, TONES["noread"])[0]

    def cell(width: float, colour: str, cls: str) -> str:
        if width <= 0.5:
            return ""
        return (
            f'<td class="{cls}" width="{width:.0f}%" height="8" bgcolor="{colour}" '
            f'style="width:{width:.0f}%;height:8px;background:{colour};font-size:0;line-height:0;">&nbsp;</td>'
        )

    def half(cells: str) -> str:
        return (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
            f'style="border-collapse:collapse;"><tr>{cells}</tr></table>'
        )

    left_fill = (50 - x) * 2 if x < 50 else 0
    right_fill = (x - 50) * 2 if x > 50 else 0
    left = half(cell(100 - left_fill, TRACK, "tc-track") + cell(left_fill, fill, ""))
    right = half(cell(right_fill, fill, "") + cell(100 - right_fill, TRACK, "tc-track"))
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        'style="border-collapse:collapse;margin:10px 0 10px;"><tr>'
        f'<td width="50%" style="width:50%;padding:0;">{left}</td>'
        f'<td width="2" height="14" bgcolor="{FAINT}" style="width:2px;height:14px;background:{FAINT};font-size:0;line-height:0;">&nbsp;</td>'
        f'<td width="50%" style="width:50%;padding:0;">{right}</td>'
        "</tr></table>"
    )


def page(*, site: str, preheader: str, body: str, footer: str) -> str:
    """The whole email: brand header, the card holding `body`, then `footer`
    (already-assembled HTML) under it."""
    logo = (
        f'<img src="{esc(site)}/icons/icon-192.png" width="32" height="32" alt="" '
        'style="display:block;width:32px;height:32px;border:0;border-radius:8px;">'
        if site else ""
    )
    brand_name = (
        f'<span class="tc-ink" style="font-family:{FONT};font-size:18px;font-weight:700;color:{INK};">'
        f'Ticker <span class="tc-accent" style="color:{ACCENT};">Council</span></span>'
    )
    brand = (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        + (f'<td style="padding-right:10px;vertical-align:middle;">{logo}</td>' if logo else "")
        + f'<td style="vertical-align:middle;">{brand_name}</td></tr></table>'
    )
    if site:
        brand = f'<a href="{esc(site)}" target="_blank" style="text-decoration:none;">{brand}</a>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>Ticker Council</title>
<style>{_DARK_CSS}</style>
</head>
<body class="tc-bg" style="margin:0;padding:0;background:{BG};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{esc(preheader)}&#8199;&#847;&#8199;&#847;&#8199;&#847;&#8199;&#847;&#8199;&#847;&#8199;&#847;</div>
<table role="presentation" class="tc-bg" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{BG}" style="background:{BG};">
<tr><td align="center" style="padding:28px 12px 36px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;">
<tr><td style="padding:0 8px 18px;">{brand}</td></tr>
<tr><td class="tc-card" bgcolor="{SURFACE}" style="background:{SURFACE};border:1px solid {LINE};border-radius:16px;padding:32px 28px;">
{body}
</td></tr>
<tr><td class="tc-faint" style="padding:20px 8px 0;font-family:{FONT};font-size:13px;line-height:1.6;color:{FAINT};">
{footer}
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


def footer(*lines: str) -> str:
    """Footer lines (already-escaped HTML), one paragraph each."""
    return "".join(f'<p style="margin:0 0 6px;">{line}</p>' for line in lines if line)


def footer_link(url: str, label: str) -> str:
    return f'<a class="tc-faint" href="{esc(url)}" target="_blank" style="color:{FAINT};text-decoration:underline;">{esc(label)}</a>'
