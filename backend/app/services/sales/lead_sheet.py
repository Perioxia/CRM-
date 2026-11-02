"""Pull new leads from a Google Sheet shared as anyone with the link.

Google does not push cell edits to this app. Sync now, opening the leads
list, and the existing reminder run all fetch the published CSV and insert
only rows that classify as new (email, then phone).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.core.company_settings import CompanySettings
from app.models.core.enums import UserRole
from app.models.core.user import User
from app.services.sales.lead_import import insert_new_leads, preview_import

logger = logging.getLogger("app")

MAX_SHEET_BYTES = 2 * 1024 * 1024
_SHEET_SYNC_LOCK = 7301
_SHEET_ID = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
_SHARE_HINT = (
    "Could not read that sheet. Share it as Anyone with the link can view, then try again."
)


def parse_sheet_url(url: str) -> tuple[str, str | None]:
    raw = (url or "").strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host not in {"docs.google.com", "drive.google.com"}:
        raise ValueError("Paste a Google Sheets link (docs.google.com).")
    match = _SHEET_ID.search(parsed.path or "")
    if not match:
        raise ValueError("That link has no spreadsheet id.")
    query = parse_qs(parsed.query)
    fragment = parse_qs(parsed.fragment)
    gid_values = query.get("gid") or fragment.get("gid") or []
    gid = gid_values[0] if gid_values else None
    if gid is not None and not str(gid).isdigit():
        gid = None
    return match.group(1), gid


def export_csv_url(sheet_url: str) -> str:
    spreadsheet_id, gid = parse_sheet_url(sheet_url)
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv"
    if gid:
        url = f"{url}&gid={gid}"
    return url


def fetch_sheet_csv(sheet_url: str, client: httpx.Client | None = None) -> bytes:
    target = export_csv_url(sheet_url)
    owns_client = client is None
    http = client or httpx.Client(follow_redirects=True, timeout=20.0)
    try:
        response = http.get(target)
    finally:
        if owns_client:
            http.close()
    if response.status_code != 200:
        raise ValueError(_SHARE_HINT)
    body = response.content or b""
    if len(body) > MAX_SHEET_BYTES:
        raise ValueError("Sheet export is larger than 2 MB.")
    head = body[:200].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html") or b"<html" in head:
        raise ValueError(_SHARE_HINT)
    return body


def _settings(db: Session, company_id: int) -> CompanySettings:
    row = db.query(CompanySettings).filter(CompanySettings.company_id == company_id).first()
    if row is None:
        row = CompanySettings(company_id=company_id, company_name="Company Name")
        db.add(row)
        db.flush()
    return row


def serialize_sheet(row: CompanySettings | None) -> dict:
    if row is None or not row.lead_sheet_url:
        return {"url": None, "last_synced_at": None, "last_result": None}
    synced = row.lead_sheet_last_synced_at
    return {
        "url": row.lead_sheet_url,
        "last_synced_at": synced.isoformat() if synced else None,
        "last_result": row.lead_sheet_last_result,
    }


def save_sheet_url(db: Session, company_id: int, url: str | None) -> CompanySettings:
    row = _settings(db, company_id)
    cleaned = (url or "").strip()
    if not cleaned:
        row.lead_sheet_url = None
        row.lead_sheet_last_result = None
        return row
    parse_sheet_url(cleaned)
    row.lead_sheet_url = cleaned[:500]
    return row


def _stamp(row: CompanySettings, message: str) -> None:
    row.lead_sheet_last_synced_at = datetime.utcnow()
    row.lead_sheet_last_result = message[:255]


def _claim_sheet_sync(db: Session, company_id: int) -> bool:
    """Only one sync per company at a time, or both would add the same rows. Released on commit."""
    if db.get_bind().dialect.name != "postgresql":
        return True
    return bool(db.execute(
        text("SELECT pg_try_advisory_xact_lock(:scope, :company_id)"),
        {"scope": _SHEET_SYNC_LOCK, "company_id": company_id},
    ).scalar())


def sync_sheet(db: Session, *, user, team_id: int) -> dict:
    row = _settings(db, user.company_id)
    if not row.lead_sheet_url:
        raise ValueError("No Google Sheet is connected.")
    if not _claim_sheet_sync(db, user.company_id):
        return {
            "created": 0,
            "skipped_duplicate": 0,
            "skipped_invalid": 0,
            "last_result": row.lead_sheet_last_result,
        }
    try:
        contents = fetch_sheet_csv(row.lead_sheet_url)
        preview = preview_import(db, user.company_id, contents, None)
        created = insert_new_leads(
            db,
            user=user,
            team_id=team_id,
            preview=preview,
            default_source="Google Sheet",
        )
    except ValueError as exc:
        _stamp(row, str(exc))
        raise
    counts = preview["counts"]
    message = f"{len(created)} new, {counts['duplicate']} already in the CRM"
    if counts["invalid"]:
        message = f"{message}, {counts['invalid']} invalid"
    _stamp(row, message)
    return {
        "created": len(created),
        "skipped_duplicate": counts["duplicate"],
        "skipped_invalid": counts["invalid"],
        "last_result": row.lead_sheet_last_result,
    }


def _actor_for_company(db: Session, company_id: int) -> User | None:
    return (
        db.query(User)
        .filter(
            User.company_id == company_id,
            User.is_active.is_(True),
            User.role.in_([UserRole.ADMIN, UserRole.MD]),
        )
        .order_by(User.id.asc())
        .first()
    )


def sync_due_sheets(db: Session) -> int:
    """Pull every connected sheet. Failures stay on that company and do not stop the rest."""
    from app.routers.ops.imports import _team_for_import

    rows = (
        db.query(CompanySettings)
        .filter(CompanySettings.lead_sheet_url.isnot(None), CompanySettings.lead_sheet_url != "")
        .all()
    )
    created_total = 0
    for row in rows:
        actor = _actor_for_company(db, row.company_id)
        if actor is None:
            _stamp(row, "No admin or director to import the sheet as.")
            continue
        try:
            team_id = _team_for_import(db, actor, None)
            result = sync_sheet(db, user=actor, team_id=team_id)
            created_total += result["created"]
        except ValueError as exc:
            logger.info("Lead sheet sync skipped company_id=%s: %s", row.company_id, exc)
        except Exception:
            logger.exception("Lead sheet sync failed company_id=%s", row.company_id)
            _stamp(row, "Sync failed")
    return created_total
