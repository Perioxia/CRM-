from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.core.user import User
from app.models.sales.client import Client
from app.models.sales.deal import Deal
from app.models.sales.email_log import EmailLog
from app.models.sales.lead import Lead
from app.services.sales.crm_email import deliver_and_log, serialize_email
from app.services.sales.mailbox import get_user_mailbox, maybe_autosync
from app.services.sales.lead_access import ensure_can_work_lead, scope_activity_list, scope_activity_rows
from app.utils.dependencies import apply_company_scope, ensure_company_access, get_active_team_id, get_current_user

router = APIRouter()


class EmailCreate(BaseModel):
    subject: str
    body: str
    to_email: Optional[EmailStr] = None
    lead_id: Optional[int] = None
    client_id: Optional[int] = None
    deal_id: Optional[int] = None


def _get_log(db: Session, current_user: User, active_team_id: Optional[int], email_id: int) -> EmailLog:
    query = apply_company_scope(db.query(EmailLog), EmailLog, current_user)
    query = scope_activity_rows(query, EmailLog, EmailLog.sent_by_id, db, current_user, active_team_id)
    row = query.filter(EmailLog.id == email_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Email not found")
    ensure_company_access(row, current_user)
    return row


@router.post("", status_code=status.HTTP_201_CREATED)
def create_email(
    payload: EmailCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    if current_user.company_id is None:
        raise HTTPException(status_code=403, detail="User must belong to a company")
    subject = (payload.subject or "").strip()
    body = (payload.body or "").strip()
    if not subject or len(subject) > 200:
        raise HTTPException(status_code=400, detail="subject is required (max 200 characters)")
    if not body or len(body) > 20000:
        raise HTTPException(status_code=400, detail="body is required")

    lead = None
    client = None
    deal = None
    to_email = str(payload.to_email) if payload.to_email else None
    if payload.lead_id is not None:
        lead = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.id == payload.lead_id).first()
        if lead is None:
            raise HTTPException(status_code=400, detail="lead_id not found in your company")
        ensure_can_work_lead(lead, current_user, active_team_id)
        to_email = to_email or (lead.email or "").strip() or None
    if payload.client_id is not None:
        client = apply_company_scope(db.query(Client), Client, current_user).filter(Client.id == payload.client_id).first()
        if client is None:
            raise HTTPException(status_code=400, detail="client_id not found in your company")
        to_email = to_email or (client.email or "").strip() or None
    if payload.deal_id is not None:
        deal = apply_company_scope(db.query(Deal), Deal, current_user).filter(Deal.id == payload.deal_id).first()
        if deal is None:
            raise HTTPException(status_code=400, detail="deal_id not found in your company")
        if lead is None and deal.lead_id:
            lead = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.id == deal.lead_id).first()
        if client is None and deal.client_id:
            client = apply_company_scope(db.query(Client), Client, current_user).filter(Client.id == deal.client_id).first()
        to_email = to_email or (lead.email if lead else None) or (client.email if client else None)
        to_email = (str(to_email).strip() if to_email else None) or None
    if not to_email:
        raise HTTPException(status_code=400, detail="to_email is required (or a lead/client with an email)")

    row = deliver_and_log(
        db,
        company_id=current_user.company_id,
        sent_by_id=current_user.id,
        to_email=to_email,
        subject=subject,
        body=body,
        lead_id=lead.id if lead else None,
        client_id=client.id if client else None,
        deal_id=deal.id if deal else None,
    )
    return serialize_email(row)


@router.get("")
def list_emails(
    lead_id: Optional[int] = Query(None),
    client_id: Optional[int] = Query(None),
    deal_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    maybe_autosync(db, get_user_mailbox(db, current_user.id))
    query = apply_company_scope(db.query(EmailLog), EmailLog, current_user)
    query = scope_activity_list(query, EmailLog, EmailLog.sent_by_id, lead_id, db, current_user, active_team_id)
    if client_id is not None:
        query = query.filter(EmailLog.client_id == client_id)
    if deal_id is not None:
        query = query.filter(EmailLog.deal_id == deal_id)
    rows = query.order_by(EmailLog.id.desc()).limit(100).all()
    return {"items": [serialize_email(r) for r in rows], "total": len(rows)}


@router.get("/{email_id:int}")
def get_email(
    email_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    return serialize_email(_get_log(db, current_user, active_team_id, email_id))
