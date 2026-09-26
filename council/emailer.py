"""Sends the few emails accounts need -- a new sign-up alert to the owner,
"you're approved", password-reset links, problem reports -- through Resend
(https://resend.com). Without RESEND_API_KEY and EMAIL_FROM it sends
nothing and says so in the server log; the admin page covers the gap (it
lists pending sign-ups, and can make a reset link to pass on by hand).
The layout they share lives in email_design.py."""
from __future__ import annotations

import logging

import httpx

from council import email_design as d
from council.config import Settings
from council.email_design import Message

log = logging.getLogger("council.email")
_RESEND_URL = "https://api.resend.com/emails"


def email_configured(settings: Settings) -> bool:
    return bool(settings.resend_api_key and settings.email_from)


async def send_email(settings: Settings, to: str, subject: str, text: str, html: str = "") -> bool:
    """True if Resend accepted it. Never raises: a failed email must not
    break sign-up or approval, which still work without one. `html` is the
    designed version; `text` always goes too, for mail apps that want it."""
    if not email_configured(settings):
        log.info("email not configured; not sending %r to %s", subject, to)
        return False
    payload = {"from": settings.email_from, "to": [to], "subject": subject, "text": text}
    if html:
        payload["html"] = html
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _RESEND_URL,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json=payload,
            )
        if resp.status_code >= 300:
            log.warning("Resend refused %r to %s: %s %s", subject, to, resp.status_code, resp.text[:300])
            return False
        return True
    except httpx.HTTPError as exc:
        log.warning("couldn't reach Resend for %r to %s: %s", subject, to, exc)
        return False


async def send_message(settings: Settings, to: str, message: Message) -> bool:
    return await send_email(settings, to, message.subject, message.text, message.html)


def signup_alert(name: str, email: str, admin_url: str) -> Message:
    subject = f"New sign-up: {name}"
    text = (
        f"{name} ({email}) asked for a Ticker Council account.\n\n"
        f"Approve or decline it on the admin page:\n{admin_url}\n"
    )
    body = (
        d.heading("New sign-up waiting")
        + d.p(f"{d.esc(name)} asked for a Ticker Council account.")
        + d.details([("Name", d.esc(name)), ("Email", d.esc(email))])
        + d.button(admin_url, "Review on the admin page")
    )
    html = d.page(
        site=d.origin(admin_url),
        preheader=f"{name} ({email}) is waiting for approval.",
        body=body,
        footer=d.footer("You get these because you own this Ticker Council site."),
    )
    return Message(subject, text, html)


def approved_message(name: str, site_url: str) -> Message:
    subject = "Your Ticker Council account is ready"
    text = (
        f"Hi {name},\n\nYour account has been approved. Sign in here:\n{site_url}\n\n"
        "The setup guide walks you through adding an AI key (a free Google Gemini key works, no card needed). "
        "Until then, runs use sample answers.\n"
    )
    step = (
        '<tr><td class="tc-accent" style="padding:0 12px 12px 0;vertical-align:top;font-family:{font};'
        'font-size:15px;font-weight:700;color:{accent};">{n}</td>'
        '<td class="tc-ink" style="padding:0 0 12px;font-family:{font};font-size:15px;line-height:1.5;color:{ink};">{t}</td></tr>'
    )
    steps = "".join(
        step.format(font=d.FONT, accent=d.ACCENT, ink=d.INK, n=n, t=t)
        for n, t in (
            (1, "<b>Sign in</b> with the button above."),
            (2, "<b>Add an AI key.</b> The setup guide walks you through it. A free Google Gemini key works, "
                "no card needed. Until then, runs use sample answers."),
            (3, "<b>Run your first stock.</b> Twelve AI seats each read a different slice of the data and "
                "vote on where it goes next week, over the next 3 months and over the next year."),
        )
    )
    body = (
        d.heading("You're in")
        + d.p(f"Hi {d.esc(name)}, your Ticker Council account has been approved.")
        + d.button(site_url, "Sign in")
        + d.p("<b>What happens next</b>", gap=12)
        + f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">{steps}</table>'
    )
    html = d.page(
        site=d.origin(site_url),
        preheader="Your account has been approved. Sign in and run your first stock.",
        body=body,
        footer=d.footer(
            "You're getting this because you asked for a Ticker Council account.",
            "Ticker Council's calls are not financial advice.",
        ),
    )
    return Message(subject, text, html)


def reset_message(name: str, link: str, hours: int) -> Message:
    subject = "Reset your Ticker Council password"
    lasts = f"{hours} hour{'s' if hours != 1 else ''}"
    text = (
        f"Hi {name},\n\nUse this link to choose a new password. It works once, for the next "
        f"{lasts}:\n{link}\n\n"
        "If you didn't ask for this, you can ignore this email.\n"
    )
    body = (
        d.heading("Reset your password")
        + d.p(f"Hi {d.esc(name)}, use the button below to choose a new password. "
              f"The link works once, for the next {lasts}.")
        + d.button(link, "Choose a new password")
        + d.p("Didn't ask for this? You can ignore this email. Your password won't change.", muted=True, size=14)
        + d.p(f'Button not working? Paste this link into your browser:<br>'
              f'<span style="font-family:{d.MONO};font-size:12px;word-break:break-all;">{d.esc(link)}</span>',
              muted=True, size=14, gap=0)
    )
    html = d.page(
        site=d.origin(link),
        preheader=f"Choose a new password. The link works for the next {lasts}.",
        body=body,
        footer=d.footer("You're getting this because someone asked to reset the password for this account."),
    )
    return Message(subject, text, html)


def report_message(who: str, kind_label: str, message: str, extras: list[tuple[str, str]], reports_url: str) -> Message:
    """A problem report, for the owner. `who` is "Name (email)" or "Someone"."""
    subject = f"Problem report: {kind_label}"
    extra = "".join(f"\n{label}: {value}" for label, value in extras)
    text = (
        f"{who} reported a problem: {kind_label}\n\n{message}\n{extra}\n\n"
        f"See all reports on the admin page:\n{reports_url}\n"
    )
    quoted = (
        f'<div class="tc-ink" style="font-family:{d.FONT};font-size:15px;line-height:1.55;color:{d.INK};'
        f'white-space:pre-wrap;word-break:break-word;">{d.esc(message)}</div>'
    )
    body = (
        d.heading("Problem report")
        + d.p(f"{d.chip(kind_label, 'down')}", gap=12)
        + d.p(f"From {d.esc(who)}", muted=True, size=14, gap=12)
        + d.box(quoted)
        + (d.details([(label, d.esc(value)) for label, value in extras]) if extras else "")
        + d.button(reports_url, "Open reports")
    )
    html = d.page(
        site=d.origin(reports_url),
        preheader=f"{who}: {message[:90]}",
        body=body,
        footer=d.footer("You get these because you own this Ticker Council site."),
    )
    return Message(subject, text, html)
