"""Report a problem: anyone signed in can send one from the Guide; it lands
on the admin page (Reports) and, when email is set up, in the owner's inbox."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from council import accounts, emailer, reports
from council.api import auth
from council.api.admin import _require_admin
from council.config import get_settings

log = logging.getLogger("council.reports")
router = APIRouter()

_PER_HOUR = 5


class _ReportRequest(BaseModel):
    kind: str = "broken"
    message: str
    page: str = ""
    run_id: str = ""
    ticker: str = ""


@router.post("/api/reports")
async def send_report(body: _ReportRequest, request: Request):
    settings = get_settings()
    user = getattr(request.state, "user", None)
    message = body.message.strip()
    if len(message) < 5:
        raise HTTPException(400, "Tell us a little about what happened.")
    if len(message) > reports.MAX_MESSAGE:
        raise HTTPException(400, f"Keep it under {reports.MAX_MESSAGE:,} characters.")
    kind = body.kind if body.kind in reports.KINDS else "other"
    user_id = user.id if user else None
    if reports.recent_count(settings.settings_db_path, user_id) >= _PER_HOUR:
        raise HTTPException(429, "You've sent a few reports in the last hour. Try again a bit later.")
    report_id = reports.create(
        settings.settings_db_path,
        user_id=user_id,
        name=user.name if user else "Owner",
        email=user.email if user else "",
        kind=kind,
        message=message,
        page=body.page.strip(),
        run_id=body.run_id.strip(),
        ticker=body.ticker.strip().upper(),
        user_agent=request.headers.get("user-agent", ""),
    )
    await _email_owner(request, user, kind, message, body)
    return {"ok": True, "id": report_id}


async def _email_owner(request: Request, user, kind: str, message: str, body: _ReportRequest) -> None:
    settings = get_settings()
    if not settings.resolved_auth_enabled or not emailer.email_configured(settings):
        return
    conn = auth._conn()
    try:
        owner = accounts.owner(conn)
    finally:
        conn.close()
    if owner is None or (user is not None and user.id == owner.id):
        return
    who = f"{user.name} ({user.email})" if user else "Someone"
    extra = "".join(
        f"\n{label}: {value}" for label, value in (("Ticker", body.ticker), ("Run", body.run_id), ("Page", body.page)) if value
    )
    text = (
        f"{who} reported a problem: {reports.KINDS[kind]}\n\n{message}\n{extra}\n\n"
        f"See all reports on the admin page:\n{auth.site_url(request)}/admin.html#reports\n"
    )
    try:
        await emailer.send_email(settings, owner.email, f"Problem report: {reports.KINDS[kind]}", text)
    except Exception as exc:  # noqa: BLE001 -- the report is saved either way
        log.warning("couldn't email the report: %s", exc)


@router.get("/api/admin/reports")
async def list_reports(request: Request, status: str = "open"):
    _require_admin(request)
    settings = get_settings()
    rows = reports.list_reports(settings.settings_db_path, status)
    return {
        "reports": [{**r, "kind_label": reports.KINDS.get(r["kind"], r["kind"])} for r in rows],
        "open_count": reports.open_count(settings.settings_db_path),
    }


class _ReportStatus(BaseModel):
    status: str


@router.post("/api/admin/reports/{report_id}")
async def set_report_status(report_id: int, body: _ReportStatus, request: Request):
    _require_admin(request)
    if body.status not in ("open", "resolved"):
        raise HTTPException(400, "status must be open or resolved")
    if not reports.set_status(get_settings().settings_db_path, report_id, body.status):
        raise HTTPException(404, "no such report")
    return {"ok": True}


def install_reports(app) -> None:
    app.include_router(router)
