import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database import get_db
from app.models.core.company_settings import CompanySettings
from app.models.core.team_membership import TeamMembership
from app.models.core.user import User
from app.models.sales.client import Client
from app.models.sales.lead import Lead
from app.models.sales.whatsapp import WhatsAppMessage, WhatsAppTemplate
from app.services.sales.whatsapp import (
    destination_msisdn,
    ingest_gupshup_inbound,
    params_for_record,
    parse_template_keys,
    post_gupshup_session_text,
    post_gupshup_template,
    session_open_until,
)
from app.services.sales.lead_access import ensure_can_work_lead, scope_activity_list
from app.services.sales.recycle import hide_deleted_lead_rows
from app.utils.datetime_json import isoformat_utc
from app.utils.dependencies import (
    apply_company_scope,
    ensure_company_access,
    get_active_team_id,
    get_current_user,
    require_admin_or_md,
)
from app.utils.helpers import normalize_phone

router = APIRouter()


class TemplateWrite(BaseModel):
    name: str
    provider_template_id: str
    language: Optional[str] = "en"
    body: Optional[str] = None
    variable_keys: Optional[list[str]] = None


class ConnectionWrite(BaseModel):
    api_key: Optional[str] = None
    source: Optional[str] = None
    cadence_template_id: Optional[int] = None


class SendWrite(BaseModel):
    template_id: int
    lead_id: Optional[int] = None
    client_id: Optional[int] = None
    params: Optional[list[str]] = None


class SessionSendWrite(BaseModel):
    body: str
    lead_id: Optional[int] = None
    client_id: Optional[int] = None


def _company_user(user: User) -> int:
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


def _parse_keys(raw) -> list[str]:
    return parse_template_keys(raw)


def _serialize_template(row: WhatsAppTemplate) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "language": row.language,
        "provider_template_id": row.provider_template_id,
        "body": row.body,
        "variable_keys": _parse_keys(row.variable_keys),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _serialize_message(row: WhatsAppMessage) -> dict:
    return {
        "id": row.id,
        "template_id": row.template_id,
        "lead_id": row.lead_id,
        "client_id": row.client_id,
        "to_phone": row.to_phone,
        "from_phone": getattr(row, "from_phone", None),
        "direction": getattr(row, "direction", None) or "outbound",
        "body": getattr(row, "body", None),
        "status": row.status,
        "error": row.error,
        "session_expires_at": row.session_expires_at.isoformat() if getattr(row, "session_expires_at", None) else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _get_template(db: Session, user: User, template_id: int) -> WhatsAppTemplate:
    row = apply_company_scope(db.query(WhatsAppTemplate), WhatsAppTemplate, user).filter(
        WhatsAppTemplate.id == template_id
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    ensure_company_access(row, user)
    return row


_THREAD_LIST_ROLES = frozenset({"sales", "manager", "md", "admin"})
_THREAD_COMPANY_ROLES = frozenset({"md", "admin"})


def _user_role_str(user: User) -> str:
    role = getattr(user, "role", None)
    if role is None:
        return ""
    return str(getattr(role, "value", role))


def _latest_thread_ids(db: Session, current_user: User):
    def grouped(filters, group_col):
        q = apply_company_scope(
            db.query(func.max(WhatsAppMessage.id)),
            WhatsAppMessage,
            current_user,
        )
        for predicate in filters:
            q = q.filter(predicate)
        return q.group_by(group_col)

    return grouped(
        [WhatsAppMessage.lead_id.isnot(None)],
        WhatsAppMessage.lead_id,
    ).union_all(
        grouped(
            [WhatsAppMessage.lead_id.is_(None), WhatsAppMessage.client_id.isnot(None)],
            WhatsAppMessage.client_id,
        ),
        grouped(
            [WhatsAppMessage.lead_id.is_(None), WhatsAppMessage.client_id.is_(None)],
            WhatsAppMessage.to_phone,
        ),
    )


def _scoped_threads_query(db: Session, current_user: User, active_team_id: Optional[int]):
    role = _user_role_str(current_user)
    if role not in _THREAD_LIST_ROLES:
        raise HTTPException(status_code=403, detail="Not allowed")
    query = (
        apply_company_scope(db.query(WhatsAppMessage, Lead, Client), WhatsAppMessage, current_user)
        .outerjoin(Lead, WhatsAppMessage.lead_id == Lead.id)
        .outerjoin(Client, WhatsAppMessage.client_id == Client.id)
        .filter(WhatsAppMessage.id.in_(_latest_thread_ids(db, current_user)))
    )
    query = hide_deleted_lead_rows(query, WhatsAppMessage.lead_id)
    if role == "sales":
        return query.filter(
            or_(
                WhatsAppMessage.sent_by_id == current_user.id,
                Lead.assigned_to_id == current_user.id,
                and_(
                    WhatsAppMessage.lead_id.is_(None),
                    Client.assigned_to_id == current_user.id,
                ),
            )
        )
    if role == "manager":
        if active_team_id is None:
            return query.filter(False)
        member_ids = [
            uid
            for (uid,) in apply_company_scope(
                db.query(TeamMembership.user_id), TeamMembership, current_user
            )
            .filter(TeamMembership.team_id == active_team_id)
            .all()
        ]
        sent_in_team = WhatsAppMessage.sent_by_id.in_(member_ids) if member_ids else False
        return query.filter(
            or_(
                and_(
                    WhatsAppMessage.lead_id.isnot(None),
                    or_(Lead.team_id == active_team_id, sent_in_team),
                ),
                and_(
                    WhatsAppMessage.lead_id.is_(None),
                    WhatsAppMessage.client_id.isnot(None),
                    or_(Client.team_id == active_team_id, sent_in_team),
                ),
            )
        )
    if role in _THREAD_COMPANY_ROLES:
        return query
    raise HTTPException(status_code=403, detail="Not allowed")


def _serialize_thread(row: WhatsAppMessage, lead: Optional[Lead], client: Optional[Client]) -> dict:
    if row.lead_id is not None:
        name = lead.name if lead is not None else None
        phone = lead.phone if lead is not None else row.to_phone
    elif row.client_id is not None:
        name = client.name if client is not None else None
        phone = client.phone if client is not None else row.to_phone
    else:
        name = row.to_phone
        phone = row.to_phone
    direction = getattr(row, "direction", None) or "outbound"
    return {
        "lead_id": row.lead_id,
        "client_id": row.client_id,
        "name": name,
        "phone": phone,
        "last_direction": direction,
        "last_body": getattr(row, "body", None),
        "last_at": isoformat_utc(row.created_at),
        "unanswered": direction == "inbound",
    }


@router.get("/threads")
def list_threads(
    unanswered: bool = Query(False),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    _company_user(current_user)
    query = _scoped_threads_query(db, current_user, active_team_id)
    if unanswered:
        query = query.filter(WhatsAppMessage.direction == "inbound")
    total = query.with_entities(WhatsAppMessage.id).order_by(None).count()
    rows = query.order_by(WhatsAppMessage.id.desc()).offset(skip).limit(limit).all()
    return {
        "items": [_serialize_thread(message, lead, client) for message, lead, client in rows],
        "total": total,
    }


@router.get("/connection")
def get_connection(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_md),
):
    company_id = _company_user(current_user)
    settings = db.query(CompanySettings).filter(CompanySettings.company_id == company_id).first()
    key = getattr(settings, "whatsapp_api_key", None) if settings else None
    source = getattr(settings, "whatsapp_source", None) if settings else None
    return {
        "configured": bool(key and source),
        "source": source,
        "cadence_template_id": getattr(settings, "whatsapp_cadence_template_id", None) if settings else None,
    }


@router.put("/connection")
def put_connection(
    body: ConnectionWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_md),
):
    company_id = _company_user(current_user)
    settings = _settings(db, company_id)
    if body.api_key is not None:
        cleaned = (body.api_key or "").strip()
        settings.whatsapp_api_key = cleaned or None
    if body.source is not None:
        settings.whatsapp_source = normalize_phone(body.source)
    if "cadence_template_id" in body.model_fields_set:
        settings.whatsapp_cadence_template_id = body.cadence_template_id or None
    db.commit()
    return {
        "configured": bool(settings.whatsapp_api_key and settings.whatsapp_source),
        "source": settings.whatsapp_source,
        "cadence_template_id": settings.whatsapp_cadence_template_id,
    }


@router.get("/templates")
def list_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _company_user(current_user)
    rows = (
        apply_company_scope(db.query(WhatsAppTemplate), WhatsAppTemplate, current_user)
        .order_by(WhatsAppTemplate.id.desc())
        .all()
    )
    return {"items": [_serialize_template(r) for r in rows], "total": len(rows)}


@router.post("/templates", status_code=status.HTTP_201_CREATED)
def create_template(
    body: TemplateWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_md),
):
    company_id = _company_user(current_user)
    name = (body.name or "").strip()
    provider_id = (body.provider_template_id or "").strip()
    if not name or len(name) > 100:
        raise HTTPException(status_code=400, detail="name is required (max 100 characters)")
    if not provider_id or len(provider_id) > 120:
        raise HTTPException(status_code=400, detail="provider_template_id is required")
    row = WhatsAppTemplate(
        company_id=company_id,
        name=name,
        language=(body.language or "en").strip() or "en",
        provider_template_id=provider_id,
        body=(body.body or "").strip() or None,
        variable_keys=json.dumps(body.variable_keys or []),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_template(row)


@router.get("/templates/{template_id}")
def get_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _serialize_template(_get_template(db, current_user, template_id))


@router.delete("/templates/{template_id}")
def delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_md),
):
    row = _get_template(db, current_user, template_id)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/messages")
def list_messages(
    lead_id: Optional[int] = Query(None),
    client_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    _company_user(current_user)
    query = apply_company_scope(db.query(WhatsAppMessage), WhatsAppMessage, current_user)
    query = scope_activity_list(
        query, WhatsAppMessage, WhatsAppMessage.sent_by_id, lead_id, db, current_user, active_team_id
    )
    if client_id is not None:
        query = query.filter(WhatsAppMessage.client_id == client_id)
    rows = query.order_by(WhatsAppMessage.id.desc()).limit(100).all()
    expires = session_open_until(
        db,
        company_id=current_user.company_id,
        lead_id=lead_id,
        client_id=client_id,
    )
    return {
        "items": [_serialize_message(r) for r in rows],
        "total": len(rows),
        "session_open": expires is not None,
        "session_expires_at": expires.isoformat() if expires else None,
    }


@router.post("/send", status_code=status.HTTP_201_CREATED)
def send_template(
    body: SendWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    company_id = _company_user(current_user)
    template = _get_template(db, current_user, body.template_id)
    if body.lead_id is None and body.client_id is None:
        raise HTTPException(status_code=400, detail="lead_id or client_id is required")

    record = None
    phone = None
    lead_id = None
    client_id = None
    if body.lead_id is not None:
        record = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.id == body.lead_id).first()
        if record is None:
            raise HTTPException(status_code=404, detail="Not found")
        ensure_company_access(record, current_user)
        ensure_can_work_lead(record, current_user, active_team_id)
        phone = record.phone
        lead_id = record.id
    if body.client_id is not None:
        record = apply_company_scope(db.query(Client), Client, current_user).filter(Client.id == body.client_id).first()
        if record is None:
            raise HTTPException(status_code=404, detail="Not found")
        ensure_company_access(record, current_user)
        phone = record.phone
        client_id = record.id

    settings = db.query(CompanySettings).filter(CompanySettings.company_id == company_id).first()
    api_key = getattr(settings, "whatsapp_api_key", None) if settings else None
    source = getattr(settings, "whatsapp_source", None) if settings else None
    if not api_key or not source:
        raise HTTPException(status_code=400, detail="WhatsApp is not configured for this company")

    try:
        destination = destination_msisdn(phone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    keys = _parse_keys(template.variable_keys)
    params = body.params if body.params is not None else params_for_record(keys, record)
    ok, snippet = post_gupshup_template(
        api_key=api_key,
        source=source,
        destination=destination,
        template_id=template.provider_template_id,
        params=params,
    )
    row = WhatsAppMessage(
        company_id=company_id,
        template_id=template.id,
        lead_id=lead_id,
        client_id=client_id,
        to_phone=destination,
        direction="outbound",
        status="sent" if ok else "failed",
        error=None if ok else snippet,
        sent_by_id=current_user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_message(row)


@router.post("/webhook", status_code=status.HTTP_204_NO_CONTENT)
async def gupshup_webhook(request: Request, db: Session = Depends(get_db)):
    query_source = request.query_params.get("source") or request.query_params.get("destination")
    body: dict = {}
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        payload = await request.json()
        if isinstance(payload, dict):
            body = payload
    else:
        form = await request.form()
        raw = form.get("payload") or form.get("message")
        if isinstance(raw, str) and raw.strip().startswith("{"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    body = parsed
            except json.JSONDecodeError:
                body = {"type": form.get("type"), "text": raw, "source": form.get("source")}
        else:
            body = {k: form.get(k) for k in form.keys()}
            if query_source is None:
                query_source = form.get("destination") or form.get("app")
    await run_in_threadpool(ingest_gupshup_inbound, db, body, query_source=query_source)
    return None


@router.post("/session-send", status_code=status.HTTP_201_CREATED)
def session_send(
    body: SessionSendWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    company_id = _company_user(current_user)
    text = (body.body or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="body is required")
    if body.lead_id is None and body.client_id is None:
        raise HTTPException(status_code=400, detail="lead_id or client_id is required")

    record = None
    lead_id = None
    client_id = None
    phone = None
    if body.lead_id is not None:
        record = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.id == body.lead_id).first()
        if record is None:
            raise HTTPException(status_code=404, detail="Not found")
        ensure_company_access(record, current_user)
        ensure_can_work_lead(record, current_user, active_team_id)
        lead_id = record.id
        phone = record.phone
    if body.client_id is not None:
        record = apply_company_scope(db.query(Client), Client, current_user).filter(Client.id == body.client_id).first()
        if record is None:
            raise HTTPException(status_code=404, detail="Not found")
        ensure_company_access(record, current_user)
        client_id = record.id
        phone = record.phone

    expires = session_open_until(db, company_id=company_id, lead_id=lead_id, client_id=client_id)
    if expires is None:
        raise HTTPException(status_code=400, detail="WhatsApp session window is closed")

    settings = db.query(CompanySettings).filter(CompanySettings.company_id == company_id).first()
    api_key = getattr(settings, "whatsapp_api_key", None) if settings else None
    source = getattr(settings, "whatsapp_source", None) if settings else None
    if not api_key or not source:
        raise HTTPException(status_code=400, detail="WhatsApp is not configured for this company")
    try:
        destination = destination_msisdn(phone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    ok, snippet = post_gupshup_session_text(
        api_key=api_key,
        source=source,
        destination=destination,
        text=text,
    )
    row = WhatsAppMessage(
        company_id=company_id,
        lead_id=lead_id,
        client_id=client_id,
        to_phone=destination,
        direction="outbound",
        body=text,
        status="sent" if ok else "failed",
        error=None if ok else snippet,
        sent_by_id=current_user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_message(row)
