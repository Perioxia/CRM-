from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.sales.campaign import EmailCampaign, EmailCampaignRecipient
from app.models.sales.client import Client
from app.models.sales.lead import Lead
from app.services.sales.crm_email import deliver_and_log

AUDIENCES = frozenset({"leads", "clients"})
MAX_RECIPIENTS = 50


def _now():
    return datetime.now(timezone.utc)


def serialize_recipient(row: EmailCampaignRecipient) -> dict:
    return {
        "id": row.id,
        "to_email": row.to_email,
        "lead_id": row.lead_id,
        "client_id": row.client_id,
        "email_log_id": row.email_log_id,
        "status": row.status,
    }


def serialize_campaign(row: EmailCampaign, *, include_recipients: bool = False) -> dict:
    data = {
        "id": row.id,
        "name": row.name,
        "subject": row.subject,
        "body": row.body,
        "audience": row.audience,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
    }
    if include_recipients:
        data["recipients"] = [serialize_recipient(r) for r in row.recipients]
    return data


def list_campaigns(db: Session, company_id: int) -> list[EmailCampaign]:
    return (
        db.query(EmailCampaign)
        .filter(EmailCampaign.company_id == company_id)
        .order_by(EmailCampaign.id.desc())
        .all()
    )


def get_campaign(db: Session, company_id: int, campaign_id: int) -> EmailCampaign:
    row = (
        db.query(EmailCampaign)
        .filter(EmailCampaign.company_id == company_id, EmailCampaign.id == campaign_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return row


def create_campaign(
    db: Session,
    company_id: int,
    *,
    name: str,
    subject: str,
    body: str,
    audience: str,
    created_by_id: int | None,
) -> EmailCampaign:
    name = (name or "").strip()
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not name or len(name) > 200:
        raise HTTPException(status_code=400, detail="name is required (max 200 characters)")
    if not subject or len(subject) > 200:
        raise HTTPException(status_code=400, detail="subject is required (max 200 characters)")
    if not body or len(body) > 20000:
        raise HTTPException(status_code=400, detail="body is required")
    if audience not in AUDIENCES:
        raise HTTPException(status_code=400, detail="audience must be leads or clients")
    row = EmailCampaign(
        company_id=company_id,
        name=name,
        subject=subject,
        body=body,
        audience=audience,
        status="draft",
        created_by_id=created_by_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_campaign(db: Session, row: EmailCampaign) -> None:
    db.delete(row)
    db.commit()


def _audience_rows(db: Session, company_id: int, audience: str):
    if audience == "leads":
        return (
            db.query(Lead)
            .filter(Lead.company_id == company_id, Lead.deleted_at.is_(None))
            .order_by(Lead.id.asc())
            .all()
        )
    return db.query(Client).filter(Client.company_id == company_id).order_by(Client.id.asc()).all()


def _targets(db: Session, company_id: int, audience: str):
    skipped = []
    eligible = []
    seen = set()
    for rec in _audience_rows(db, company_id, audience):
        email = (rec.email or "").strip()
        lead_id = rec.id if audience == "leads" else None
        client_id = rec.id if audience == "clients" else None
        if not email:
            skipped.append({
                "to_email": "",
                "lead_id": lead_id,
                "client_id": client_id,
            })
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        eligible.append({
            "to_email": email,
            "lead_id": lead_id,
            "client_id": client_id,
        })
    return eligible, skipped


def send_campaign(db: Session, company_id: int, campaign: EmailCampaign, sent_by_id: int | None):
    if campaign.status != "draft":
        raise HTTPException(status_code=400, detail="Campaign already sent")
    eligible, skipped = _targets(db, company_id, campaign.audience)
    if not eligible:
        raise HTTPException(status_code=400, detail="No recipients with email")
    if len(eligible) > MAX_RECIPIENTS:
        raise HTTPException(status_code=400, detail=f"Audience exceeds {MAX_RECIPIENTS} recipients")

    sent = 0
    failed = 0
    for item in skipped:
        db.add(EmailCampaignRecipient(
            company_id=company_id,
            campaign_id=campaign.id,
            to_email=item["to_email"] or "",
            lead_id=item["lead_id"],
            client_id=item["client_id"],
            status="skipped",
        ))
    for item in eligible:
        log = deliver_and_log(
            db,
            company_id=company_id,
            sent_by_id=sent_by_id,
            to_email=item["to_email"],
            subject=campaign.subject,
            body=campaign.body,
            lead_id=item["lead_id"],
            client_id=item["client_id"],
        )
        status = "sent" if getattr(log, "status", None) == "sent" else "failed"
        if status == "sent":
            sent += 1
        else:
            failed += 1
        db.add(EmailCampaignRecipient(
            company_id=company_id,
            campaign_id=campaign.id,
            to_email=item["to_email"],
            lead_id=item["lead_id"],
            client_id=item["client_id"],
            email_log_id=getattr(log, "id", None),
            status=status,
        ))
    campaign.status = "sent"
    campaign.sent_at = _now()
    db.commit()
    db.refresh(campaign)
    return {
        "sent": sent,
        "failed": failed,
        "skipped": len(skipped),
        "campaign": serialize_campaign(campaign, include_recipients=True),
    }
