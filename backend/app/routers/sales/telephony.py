"""Exotel click-to-call and webhook."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database import get_db
from app.models.core.company_settings import CompanySettings
from app.models.core.user import User
from app.models.sales.call_log import CallLog
from app.models.sales.client import Client
from app.models.sales.deal import Deal
from app.models.sales.lead import Lead
from app.routers.sales.calls import _serialize as serialize_call
from app.services.sales.activity_parents import naive_utc_now, require_parent_in_company
from app.services.sales.telephony import (
    apply_webhook,
    place_exotel_call,
    resolve_to_phone,
    save_token,
    serialize_connection,
    telephony_configured,
    webhook_url,
)
from app.utils.audit import log_activity
from app.services.sales.lead_access import ensure_can_work_lead_id
from app.utils.dependencies import apply_company_scope, get_active_team_id, get_current_user, require_admin_or_md
from app.utils.helpers import normalize_phone

router = APIRouter()


class ConnectionWrite(BaseModel):
    sid: Optional[str] = None
    api_key: Optional[str] = None
    api_token: Optional[str] = None
    subdomain: Optional[str] = None
    caller_id: Optional[str] = None


class ClickToCallWrite(BaseModel):
    lead_id: Optional[int] = None
    client_id: Optional[int] = None
    deal_id: Optional[int] = None
    to_phone: Optional[str] = None
    from_phone: Optional[str] = None


def _company_id(user: User) -> int:
    if user.company_id is None:
        raise HTTPException(status_code=403, detail="User must belong to a company")
    return user.company_id


def _settings(db: Session, company_id: int) -> CompanySettings:
    row = db.query(CompanySettings).filter(CompanySettings.company_id == company_id).first()
    if row is None:
        row = CompanySettings(company_id=company_id, company_name="Company")
        db.add(row)
        db.flush()
    return row


@router.get("/connection")
def get_connection(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    row = db.query(CompanySettings).filter(CompanySettings.company_id == company_id).first()
    return serialize_connection(row, has_agent_phone=bool(normalize_phone(current_user.phone)))


@router.put("/connection")
def put_connection(
    payload: ConnectionWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_md),
):
    company_id = _company_id(current_user)
    row = _settings(db, company_id)
    if payload.sid is not None:
        row.exotel_sid = payload.sid.strip() or None
    if payload.api_key is not None:
        row.exotel_api_key = payload.api_key.strip() or None
    if payload.subdomain is not None:
        row.exotel_subdomain = payload.subdomain.strip() or None
    if payload.caller_id is not None:
        row.exotel_caller_id = payload.caller_id.strip() or None
    if payload.api_token:
        save_token(row, payload.api_token.strip())
    if row.exotel_api_key is None and row.exotel_sid:
        row.exotel_api_key = row.exotel_sid
    db.commit()
    db.refresh(row)
    return serialize_connection(row, has_agent_phone=bool(normalize_phone(current_user.phone)))


@router.post("/click-to-call", status_code=status.HTTP_201_CREATED)
def click_to_call(
    payload: ClickToCallWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    company_id = _company_id(current_user)
    row = db.query(CompanySettings).filter(CompanySettings.company_id == company_id).first()
    if not telephony_configured(row):
        raise HTTPException(status_code=400, detail="Telephony is not configured")

    require_parent_in_company(
        db, current_user,
        lead_id=payload.lead_id, client_id=payload.client_id, deal_id=payload.deal_id,
    )
    ensure_can_work_lead_id(db, current_user, active_team_id, payload.lead_id)
    lead = client = deal = None
    if payload.lead_id is not None:
        lead = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.id == payload.lead_id).first()
    if payload.client_id is not None:
        client = apply_company_scope(db.query(Client), Client, current_user).filter(Client.id == payload.client_id).first()
    if payload.deal_id is not None:
        deal = apply_company_scope(db.query(Deal), Deal, current_user).filter(Deal.id == payload.deal_id).first()

    to_phone = resolve_to_phone(
        db, company_id=company_id, lead=lead, client=client, deal=deal, to_phone=payload.to_phone,
    )
    from_phone = normalize_phone(payload.from_phone) or normalize_phone(current_user.phone)
    if not to_phone:
        raise HTTPException(status_code=400, detail="Destination phone is required")
    if not from_phone:
        raise HTTPException(status_code=400, detail="Agent phone is required (set your user phone)")

    try:
        sid = place_exotel_call(
            row, from_phone=from_phone, to_phone=to_phone, status_callback_url=webhook_url(),
        )
    except Exception:
        raise HTTPException(status_code=502, detail="Could not place call")

    call = CallLog(
        company_id=company_id,
        direction="outbound",
        duration_seconds=None,
        outcome="initiated",
        notes=None,
        logged_at=naive_utc_now(),
        lead_id=payload.lead_id,
        client_id=payload.client_id,
        deal_id=payload.deal_id,
        created_by_id=current_user.id,
        provider="exotel",
        provider_call_id=sid,
        from_phone=from_phone,
        to_phone=to_phone,
    )
    db.add(call)
    db.flush()
    log_activity(
        db, user=current_user, action="created", entity_type="call",
        entity_id=call.id, entity_name="outbound",
    )
    db.commit()
    db.refresh(call)
    return serialize_call(call)


@router.post("/exotel/webhook", status_code=204)
async def exotel_webhook(request: Request, db: Session = Depends(get_db)):
    call_sid = status_val = duration = None
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        payload = await request.json()
        if isinstance(payload, dict):
            call_sid = payload.get("CallSid") or payload.get("Sid")
            status_val = payload.get("Status") or payload.get("CallStatus")
            duration = payload.get("DialCallDuration") or payload.get("Duration")
    else:
        form = await request.form()
        call_sid = form.get("CallSid") or form.get("Sid")
        status_val = form.get("Status") or form.get("CallStatus")
        duration = form.get("DialCallDuration") or form.get("Duration")
    await run_in_threadpool(apply_webhook, db, call_sid=str(call_sid or ""), status=status_val, duration=duration)
    return None
