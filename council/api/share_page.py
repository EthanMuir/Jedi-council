"""The public page for a shared run (/s/<token>): the Council's call on
each term, the plain-English summary, how each seat leaned and, once
they're in, the scores. Self-contained (inline CSS, no JS) because the
visitor isn't signed in. Never shows who ran it."""
from __future__ import annotations

from datetime import datetime
from html import escape

from council.api.auth_pages import _EMU, _FAVICON
from council.api.run_views import direction, seat_leans, term_leans
from council.engine import price_target
from council.engine.horizons import TERMS

# Mirrors council/ui/js/app.js SEATS (the clean look's plain names).
SEAT_NAMES = {
    "technician": "Price Chart",
    "fundamentalist": "Financials",
    "analyst_ratings": "Analyst Targets",
    "estimate_scribe": "Earnings Estimates",
    "insider_reader": "Insider Trades",
    "senate_watcher": "Congress Trades",
    "catalyst_seer": "News",
    "structure_archivist": "SEC Filings",
    "flow_cartographer": "Institutional Holdings",
    "oracle_options": "Options Market",
    "macro_sage": "Economy",
    "cross_market": "Related Markets",
}
TERM_LABEL = {"short": "Next week", "medium": "Next 3 months", "long": "Next year"}
TERM_SHORT = {"short": "Week", "medium": "3 mo", "long": "Year"}
_ARROW = {"up": "▲", "down": "▼", "even": "●", "noread": "–"}


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


def _bar_pos(p: float) -> float:
    return max(0.0, min(1.0, (p - 0.25) / 0.5)) * 100


def _date_label(iso: str) -> str:
    try:
        when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso[:10]
    return f"{when:%b} {when.day}, {when.year}"


def _headline(run: dict, leans: dict) -> str:
    synth = run.get("synthesis") or {}
    text = synth.get("plain_headline") or synth.get("headline")
    if text:
        return text
    return "; ".join(f"{TERM_LABEL[t]}: {lean_words(leans.get(t)).lower()}" for t in TERMS if t in leans) + "."


def _score(run: dict, term: str) -> str:
    res = (run["terms"].get(term) or {}).get("resolution")
    if not res or res.get("direction_correct") is None:
        return ""
    move = res.get("realised_move_pct")
    moved = f" · price {move:+.1f}%" if move is not None else ""
    if res["direction_correct"]:
        return f'<div class="score right">Scored: right{moved}</div>'
    return f'<div class="score wrong">Scored: wrong{moved}</div>'


def _price(v: float, like: float | None = None) -> str:
    """Whole dollars from $100 up, cents below; `like` keeps a line's prices
    in step (a $95-$108 range reads "$95-$108", not "$95.00-$108")."""
    return f"${v:,.0f}" if (like if like is not None else v) >= 100 else f"${v:,.2f}"


def _target(run: dict, term: str) -> str:
    """The term's price target and likely range (price_target.py), and once
    scored, where the price ended."""
    target = ((run.get("synthesis") or {}).get("terms") or {}).get(term, {}).get("price_target")
    if not target:
        return ""
    res = (run["terms"].get(term) or {}).get("resolution") or {}
    ended = price_target.final_price(target, res.get("realised_move_pct"))
    landed = price_target.landed_in_range(target, res.get("realised_move_pct"))
    end_line = (
        f'<span>Ended at {_price(ended, target["target"])}: {"inside" if landed else "outside"} the range</span>' if ended is not None else ""
    )
    return (
        f'<div class="target"><span>Price target <b>about {_price(target["target"])}</b></span>'
        f'<span>{target["chance_pct"]}% chance {_price(target["low"], target["target"])}–{_price(target["high"], target["target"])}</span>'
        f'<span>Then {_price(target["price_now"], target["target"])}</span>{end_line}</div>'
    )


def _term_row(run: dict, term: str, p: float | None) -> str:
    dir_ = direction(p)
    if p is None:
        bar = '<div class="track"><span class="mid"></span></div>'
    else:
        x = _bar_pos(p)
        left, width = min(x, 50), abs(x - 50)
        bar = (
            f'<div class="track"><span class="mid"></span>'
            f'<span class="fill {dir_}" style="left:{left:.1f}%;width:{width:.1f}%"></span>'
            f'<span class="knob {dir_}" style="left:{x:.1f}%"></span></div>'
        )
    synth = run.get("synthesis") or {}
    note = synth.get(f"plain_{term}") or synth.get(term) or ""
    return f"""
      <div class="term">
        <div class="term-top"><b>{TERM_LABEL[term]}</b><span class="word {dir_}">{escape(lean_words(p))}</span></div>
        {bar}
        <div class="scale"><span>◀ Down</span><span>Even</span><span>Up ▶</span></div>
        {f'<p class="note">{escape(note)}</p>' if note else ''}
        {_score(run, term)}
        {_target(run, term) if p is not None else ''}
      </div>"""


def _seat_grid(run: dict) -> str:
    leans = seat_leans(run)
    cells = []
    for seat_id, name in SEAT_NAMES.items():
        terms = leans.get(seat_id)
        if not terms:
            continue
        chips = "".join(
            f'<span class="chip {direction(terms.get(t))}" title="{TERM_LABEL[t]}: {escape(lean_words(terms.get(t)))}">'
            f'{TERM_SHORT[t]} {_ARROW[direction(terms.get(t))]}</span>'
            for t in TERMS if t in terms
        )
        cells.append(f'<div class="seat"><b>{escape(name)}</b><div class="chips">{chips}</div></div>')
    return "".join(cells)


_STYLE = """
  :root {
    color-scheme: light;
    --bg: #fbfbfd; --surface: #fff; --surface-2: #f5f5f7; --ink: #1d1d1f; --muted: #6e6e73; --faint: #86868b;
    --line: rgba(0,0,0,.08); --accent: #0071e3; --up: #1f9d55; --up-soft: #e3f5e9; --down: #d93025;
    --down-soft: #fde8e8; --even: #a07b12; --even-soft: #f7efd9; --track: #ececf0;
    --shadow: 0 20px 60px -20px rgba(0,0,0,.18), 0 0 0 1px rgba(0,0,0,.04);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      color-scheme: dark;
      --bg: #0b0d12; --surface: #151a21; --surface-2: #1b2029; --ink: #eef0f4; --muted: #9aa3b2; --faint: #6d7788;
      --line: rgba(255,255,255,.08); --accent: #7597f2; --up: #3ddc97; --up-soft: rgba(61,220,151,.1);
      --down: #ff5c7a; --down-soft: rgba(255,92,122,.1); --even: #e6c35c; --even-soft: rgba(230,195,92,.1);
      --track: rgba(255,255,255,.08); --shadow: 0 0 0 1px rgba(255,255,255,.08);
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink); line-height: 1.5; -webkit-font-smoothing: antialiased;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", Inter, "Segoe UI", Roboto, sans-serif; }
  a { color: var(--accent); text-decoration: none; }
  .wrap { max-width: 760px; margin: 0 auto; padding-inline: 16px; }
  header { border-bottom: 1px solid var(--line); }
  header .wrap { display: flex; align-items: center; gap: 12px; height: 56px; }
  .brand { font-weight: 700; font-size: 16px; color: var(--ink); display: inline-flex; align-items: center; gap: 7px; }
  .brand .logo { width: 22px; height: 22px; color: var(--accent); margin-top: -3px; }
  .brand span { color: var(--accent); }
  .btn { display: inline-flex; align-items: center; justify-content: center; border-radius: 999px; padding: 9px 16px;
    font-weight: 600; font-size: 14px; background: var(--accent); color: #fff; white-space: nowrap; }
  header .btn { margin-left: auto; }
  main { padding-block: 28px 40px; }
  .eyebrow { font-size: 13px; color: var(--muted); margin: 0 0 6px; }
  h1 { font-size: clamp(34px, 8vw, 48px); letter-spacing: -.03em; line-height: 1.05; margin: 0 0 10px; }
  .headline { font-size: 19px; margin: 0 0 22px; text-wrap: pretty; }
  .card { background: var(--surface); border-radius: 22px; box-shadow: var(--shadow); padding: 20px; margin-bottom: 16px; }
  .card h2 { font-size: 17px; margin: 0 0 12px; }
  .term { padding: 14px 0; border-top: 1px solid var(--line); }
  .term:first-of-type { border-top: 0; padding-top: 0; }
  .term-top { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
  .word { font-weight: 700; }
  .word.up { color: var(--up); } .word.down { color: var(--down); } .word.even { color: var(--even); } .word.noread { color: var(--faint); }
  .track { position: relative; height: 10px; border-radius: 99px; background: var(--track); }
  .mid { position: absolute; left: 50%; top: -4px; bottom: -4px; width: 2px; margin-left: -1px; background: var(--line); }
  .fill { position: absolute; top: 0; bottom: 0; border-radius: 99px; }
  .fill.up { background: var(--up); } .fill.down { background: var(--down); }
  .knob { position: absolute; top: 50%; width: 18px; height: 18px; margin: -9px 0 0 -9px; border-radius: 50%;
    background: #fff; border: 3px solid var(--faint); box-shadow: 0 1px 4px rgba(0,0,0,.25); }
  .knob.up { border-color: var(--up); } .knob.down { border-color: var(--down); } .knob.even { border-color: var(--even); }
  .scale { display: flex; justify-content: space-between; font-size: 11px; color: var(--faint); margin-top: 6px; }
  .note { font-size: 15px; color: var(--muted); margin: 10px 0 0; }
  .score { display: inline-block; margin-top: 10px; font-size: 13px; font-weight: 600; border-radius: 99px; padding: 3px 10px; }
  .score.right { color: var(--up); background: var(--up-soft); }
  .score.wrong { color: var(--down); background: var(--down-soft); }
  h1 .company { font-size: 17px; font-weight: 500; letter-spacing: 0; color: var(--muted); }
  .target { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-top: 10px; font-size: 13px; color: var(--muted); }
  .target b { color: var(--ink); }
  .seats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
  .seat { background: var(--surface-2); border-radius: 14px; padding: 10px 12px; }
  .seat b { display: block; font-size: 14px; margin-bottom: 6px; }
  .chips { display: flex; gap: 4px; flex-wrap: wrap; }
  .chip { font-size: 11.5px; font-weight: 600; border-radius: 99px; padding: 2px 8px; background: var(--surface); color: var(--faint); }
  .chip.up { color: var(--up); background: var(--up-soft); } .chip.down { color: var(--down); background: var(--down-soft); }
  .chip.even { color: var(--even); background: var(--even-soft); }
  .cta { text-align: center; padding: 28px 20px; }
  .cta h2 { font-size: 24px; letter-spacing: -.02em; margin: 0 0 6px; }
  .cta p { color: var(--muted); margin: 0 0 16px; }
  .cta .btn { padding: 12px 22px; font-size: 16px; }
  footer { color: var(--faint); font-size: 12.5px; padding: 0 0 36px; }
  @media (min-width: 640px) { .seats { grid-template-columns: repeat(3, minmax(0, 1fr)); } .card { padding: 24px; } }
"""


def _page(title: str, head_extra: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
{_FAVICON}
<title>{escape(title)}</title>
{head_extra}
<style>{_STYLE}</style>
</head>
<body>
{body}
</body>
</html>"""


def _header(signed_in: bool) -> str:
    cta = (
        '<a class="btn" href="/index.html">Open the app</a>'
        if signed_in else '<a class="btn" href="/welcome">Try Ticker Council</a>'
    )
    return f'<header><div class="wrap"><a class="brand" href="/welcome">{_EMU}Ticker <span>Council</span></a>{cta}</div></header>'


def render(run: dict, base_url: str, url: str, signed_in: bool = False) -> str:
    ticker = run["ticker"]
    company = run.get("company") or (run.get("synthesis") or {}).get("company") or ""
    named = f"{ticker} ({company})" if company else ticker
    image_alt = f"{named}: the Council's lean for the next week, 3 months and year"
    leans = term_leans(run)
    headline = _headline(run, leans)
    when = _date_label(run["created_at"])
    title = f"{named}: what twelve AIs think · Ticker Council"
    description = f"Twelve AI analysts debated {named} on {when}. {headline}"
    head = f"""<meta name="description" content="{escape(description)}" />
<meta property="og:type" content="article" />
<meta property="og:site_name" content="Ticker Council" />
<meta property="og:title" content="{escape(f'{named}: twelve AIs debated it. Here is the verdict.')}" />
<meta property="og:description" content="{escape(headline)}" />
<meta property="og:url" content="{escape(url)}" />
<meta property="og:image" content="{escape(url)}/card.png" />
<meta property="og:image:width" content="1200" />
<meta property="og:image:height" content="630" />
<meta property="og:image:alt" content="{escape(image_alt)}" />
<meta name="twitter:image" content="{escape(url)}/card.png" />
<meta name="twitter:card" content="summary_large_image" />
<meta name="robots" content="noindex" />"""
    terms = "".join(_term_row(run, t, leans.get(t)) for t in TERMS if t in leans)
    seats = _seat_grid(run)
    sample = run.get("run_mode") == "sample"
    body = f"""{_header(signed_in)}
<main class="wrap">
  <p class="eyebrow">The Council's verdict · {escape(when)}{' · sample answers, not a real run' if sample else ''}</p>
  <h1>{escape(ticker)}{f' <span class="company">{escape(company)}</span>' if company else ''}</h1>
  <p class="headline">{escape(headline)}</p>
  <section class="card" aria-label="The Council's position">
    <h2>Which way it leans</h2>
    {terms}
  </section>
  {f'<section class="card" aria-label="Seats"><h2>How each seat leaned</h2><div class="seats">{seats}</div></section>' if seats else ''}
  <section class="card cta">
    <h2>Twelve AIs debate every stock.</h2>
    <p>Each reads a different kind of data, they argue it out, and every call is scored afterwards.</p>
    <a class="btn" href="{'/index.html' if signed_in else '/welcome'}">{'Run your own' if signed_in else 'Request access'}</a>
  </section>
</main>
<footer><div class="wrap">Not financial advice. A research tool: its calls can be wrong, and past accuracy doesn't guarantee future results. <a href="/terms">Terms</a> · <a href="/privacy">Privacy</a></div></footer>"""
    return _page(title, head, body)


def missing_page() -> str:
    body = f"""{_header(False)}
<main class="wrap">
  <h1>Link not found</h1>
  <p class="headline">This shared result doesn't exist, or whoever shared it has stopped sharing it.</p>
  <a class="btn" href="/welcome">See what Ticker Council is</a>
</main>"""
    return _page("Link not found · Ticker Council", "", body)
