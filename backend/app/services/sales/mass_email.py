from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.sales.client import Client
from app.models.sales.lead import Lead
from app.models.sales.mass_email import MassEmailBlast
from app.services.sales.crm_email import deliver_and_log

MAX_RECIPIENTS = 25
MAX_PER_DAY = 100
AUDIENCES = frozenset({"leads", "clients"})


def serialize_blast(row: MassEmailBlast) -> dict:
    return {
        "id": row.id,
        "subject": row.subject,
        "audience": row.audience,
        "sent_count": row.sent_count,
        "failed_count": row.failed_count,
        "skipped_count": row.skipped_count,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
    }


def _utc_now() -> datetime:
    """sent_at has no time zone, so it holds UTC written by us, never the database session's local time."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sent_today(db: Session, company_id: int) -> int:
    start = _utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
    total = (
        db.query(func.coalesce(func.sum(MassEmailBlast.sent_count), 0))
        .filter(
            MassEmailBlast.company_id == company_id,
            MassEmailBlast.sent_at >= start,
            MassEmailBlast.sent_at < start + timedelta(days=1),
        )
        .scalar()
    )
    return int(total or 0)


def remaining_today(db: Session, company_id: int) -> int:
    return max(0, MAX_PER_DAY - sent_today(db, company_id))


def list_blasts(db: Session, company_id: int) -> list[MassEmailBlast]:
    return (
        db.query(MassEmailBlast)
        .filter(MassEmailBlast.company_id == company_id)
        .order_by(MassEmailBlast.id.desc())
        .all()
    )


def _dedupe(rows, *, kind: str):
    skipped = 0
    eligible = []
    seen = set()
    for rec in rows:
        email = (rec.email or "").strip()
        if not email:
            skipped += 1
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        eligible.append({
            "to_email": email,
            "lead_id": rec.id if kind == "lead" else None,
            "client_id": rec.id if kind == "client" else None,
        })
    return eligible, skipped


def _from_audience(db: Session, company_id: int, audience: str):
    if audience == "leads":
        rows = (
            db.query(Lead)
            .filter(Lead.company_id == company_id, Lead.deleted_at.is_(None))
            .order_by(Lead.id.asc())
            .all()
        )
        return _dedupe(rows, kind="lead")
    rows = (
        db.query(Client)
        .filter(Client.company_id == company_id)
        .order_by(Client.id.asc())
        .all()
    )
    return _dedupe(rows, kind="client")


def _from_ids(db: Session, company_id: int, *, lead_ids, client_ids):
    if lead_ids is not None:
        ids = list(lead_ids)
        rows = (
            db.query(Lead)
            .filter(Lead.company_id == company_id, Lead.id.in_(ids), Lead.deleted_at.is_(None))
            .all()
        )
        if len(rows) != len(set(ids)):
            raise HTTPException(status_code=400, detail="lead_ids not found in your company")
        return _dedupe(rows, kind="lead")
    ids = list(client_ids)
    rows = (
        db.query(Client)
        .filter(Client.company_id == company_id, Client.id.in_(ids))
        .all()
    )
    if len(rows) != len(set(ids)):
        raise HTTPException(status_code=400, detail="client_ids not found in your company")
    return _dedupe(rows, kind="client")


def send_mass_email(
    db: Session,
    company_id: int,
    *,
    subject: str,
    body: str,
    audience: str | None,
    lead_ids: list[int] | None,
    client_ids: list[int] | None,
    sent_by_id: int | None,
):
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not subject or len(subject) > 200:
        raise HTTPException(status_code=400, detail="subject is required (max 200 characters)")
    if not body or len(body) > 20000:
        raise HTTPException(status_code=400, detail="body is required")

    has_audience = bool(audience)
    has_leads = lead_ids is not None
    has_clients = client_ids is not None
    chosen = sum([has_audience, has_leads, has_clients])
    if chosen != 1:
        raise HTTPException(status_code=400, detail="Provide audience or lead_ids or client_ids")
    if has_audience and audience not in AUDIENCES:
        raise HTTPException(status_code=400, detail="audience must be leads or clients")
    if has_leads and not lead_ids:
        raise HTTPException(status_code=400, detail="lead_ids is empty")
    if has_clients and not client_ids:
        raise HTTPException(status_code=400, detail="client_ids is empty")

    left = remaining_today(db, company_id)
    if left <= 0:
        raise HTTPException(status_code=400, detail="Daily mass-email cap reached")

    if has_audience:
        eligible, skipped = _from_audience(db, company_id, audience)
        kind = audience
    else:
        eligible, skipped = _from_ids(
            db, company_id, lead_ids=lead_ids, client_ids=client_ids,
        )
        kind = "ids"

    if not eligible:
        raise HTTPException(status_code=400, detail="No recipients with email")
    if len(eligible) > MAX_RECIPIENTS:
        raise HTTPException(status_code=400, detail=f"Blast exceeds {MAX_RECIPIENTS} recipients")
    if len(eligible) > left:
        raise HTTPException(status_code=400, detail="Blast exceeds remaining daily cap")

    sent = 0
    failed = 0
    for item in eligible:
        log = deliver_and_log(
            db,
            company_id=company_id,
            sent_by_id=sent_by_id,
            to_email=item["to_email"],
            subject=subject,
            body=body,
            lead_id=item["lead_id"],
            client_id=item["client_id"],
        )
        if getattr(log, "status", None) == "sent":
            sent += 1
        else:
            failed += 1

    row = MassEmailBlast(
        company_id=company_id,
        subject=subject,
        audience=kind,
        sent_by_id=sent_by_id,
        sent_count=sent,
        failed_count=failed,
        skipped_count=skipped,
        sent_at=_utc_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
        "remaining_today": remaining_today(db, company_id),
        "blast": serialize_blast(row),
    }
