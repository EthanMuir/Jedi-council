"""Sends the few emails accounts need -- a new sign-up alert to the owner,
"you're approved", password-reset links -- through Resend
(https://resend.com). Without RESEND_API_KEY and EMAIL_FROM it sends
nothing and says so in the server log; the admin page covers the gap (it
lists pending sign-ups, and can make a reset link to pass on by hand)."""
from __future__ import annotations

import logging

import httpx

from council.config import Settings

log = logging.getLogger("council.email")
_RESEND_URL = "https://api.resend.com/emails"


def email_configured(settings: Settings) -> bool:
    return bool(settings.resend_api_key and settings.email_from)


async def send_email(settings: Settings, to: str, subject: str, text: str) -> bool:
    """True if Resend accepted it. Never raises: a failed email must not
    break sign-up or approval, which still work without one."""
    if not email_configured(settings):
        log.info("email not configured; not sending %r to %s", subject, to)
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _RESEND_URL,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json={"from": settings.email_from, "to": [to], "subject": subject, "text": text},
            )
        if resp.status_code >= 300:
            log.warning("Resend refused %r to %s: %s %s", subject, to, resp.status_code, resp.text[:300])
            return False
        return True
    except httpx.HTTPError as exc:
        log.warning("couldn't reach Resend for %r to %s: %s", subject, to, exc)
        return False


def signup_alert(name: str, email: str, admin_url: str) -> tuple[str, str]:
    return (
        f"New sign-up: {name}",
        f"{name} ({email}) asked for a Ticker Council account.\n\n"
        f"Approve or decline it on the admin page:\n{admin_url}\n",
    )


def approved_message(name: str, site_url: str) -> tuple[str, str]:
    return (
        "Your Ticker Council account is ready",
        f"Hi {name},\n\nYour account has been approved. Sign in here:\n{site_url}\n\n"
        "To get started, add an AI key under Settings -> API keys (a free Google Gemini key works). "
        "Until then, runs use sample answers.\n",
    )


def reset_message(name: str, link: str, hours: int) -> tuple[str, str]:
    return (
        "Reset your Ticker Council password",
        f"Hi {name},\n\nUse this link to choose a new password. It works once, for the next "
        f"{hours} hour{'s' if hours != 1 else ''}:\n{link}\n\n"
        "If you didn't ask for this, you can ignore this email.\n",
    )
