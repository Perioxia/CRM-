"""Exotel click-to-call and Twilio Voice softphone."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
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
from app.services.sales.lead_access import ensure_can_work_lead_id
from app.services.sales.lead_activity import mark_lead_contacted
from app.services.sales.telephony import (
    apply_webhook,
    place_exotel_call,
    resolve_to_phone,
    save_token,
    serialize_connection,
    telephony_configured,
    webhook_url,
)
from app.services.sales import twilio_voice
from app.utils.audit import log_activity
from app.utils.dependencies import apply_company_scope, get_active_team_id, get_current_user, require_admin_or_md
from app.utils.helpers import normalize_phone

router = APIRouter()


class ConnectionWrite(BaseModel):
    sid: Optional[str] = None
    api_key: Optional[str] = None
    api_token: Optional[str] = None
    subdomain: Optional[str] = None
    caller_id: Optional[str] = None
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[str] = None
    twilio_api_key_sid: Optional[str] = None
    twilio_api_key_secret: Optional[str] = None
    twilio_twiml_app_sid: Optional[str] = None
    twilio_phone_number: Optional[str] = None
    twilio_default_user_id: Optional[int] = None
    twilio_ring_mobile_default: Optional[bool] = None


class ClickToCallWrite(BaseModel):
    lead_id: Optional[int] = None
    client_id: Optional[int] = None
    deal_id: Optional[int] = None
    to_phone: Optional[str] = None
    from_phone: Optional[str] = None


class TwilioCallWrite(BaseModel):
    lead_id: Optional[int] = None
    client_id: Optional[int] = None
    deal_id: Optional[int] = None
    to_phone: Optional[str] = None


class MeTelephonyWrite(BaseModel):
    twilio_phone_number: Optional[str] = None
    twilio_ring_mobile: Optional[bool] = None


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


def _form_dict(form) -> dict:
    return {key: str(form.get(key)) for key in form.keys()}


def _request_url(request: Request) -> str:
    # Prefer PUBLIC_API_URL so signature validation matches the URL Twilio posted to.
    base = (twilio_voice.webhook_base() or str(request.base_url).rstrip("/")).rstrip("/")
    path = request.url.path
    return f"{base}{path}"


def _signature_urls(request: Request) -> list[str]:
    """Public + proxied URL shapes Twilio may have signed (ngrok / reverse proxies)."""
    primary = _request_url(request)
    urls = [primary]
    if request.url.query:
        urls.append(f"{primary}?{request.url.query}")
    forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "https").split(",")[0].strip()
    if forwarded_host:
        built = f"{forwarded_proto}://{forwarded_host}{request.url.path}"
        urls.append(built)
        if request.url.query:
            urls.append(f"{built}?{request.url.query}")
    urls.append(str(request.url))
    # Dedupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _voice_sig_ok(row, request: Request, params: dict) -> bool:
    signature = request.headers.get("X-Twilio-Signature")
    urls = _signature_urls(request)
    return twilio_voice.validate_request_signature(
        row, url=urls[0], params=params, signature=signature, extra_urls=urls[1:],
    )


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

    if payload.twilio_account_sid is not None:
        row.twilio_account_sid = payload.twilio_account_sid.strip() or None
    if payload.twilio_api_key_sid is not None:
        row.twilio_api_key_sid = payload.twilio_api_key_sid.strip() or None
    if payload.twilio_twiml_app_sid is not None:
        row.twilio_twiml_app_sid = payload.twilio_twiml_app_sid.strip() or None
    if payload.twilio_phone_number is not None:
        row.twilio_phone_number = normalize_phone(payload.twilio_phone_number)
    if payload.twilio_default_user_id is not None:
        if payload.twilio_default_user_id <= 0:
            row.twilio_default_user_id = None
        else:
            agent = (
                apply_company_scope(db.query(User), User, current_user)
                .filter(User.id == payload.twilio_default_user_id)
                .first()
            )
            if agent is None:
                raise HTTPException(status_code=400, detail="Default Twilio user not found")
            row.twilio_default_user_id = agent.id
    if payload.twilio_ring_mobile_default is not None:
        row.twilio_ring_mobile_default = 1 if payload.twilio_ring_mobile_default else 0
    if payload.twilio_auth_token:
        twilio_voice.save_auth_token(row, payload.twilio_auth_token.strip())
    if payload.twilio_api_key_secret:
        twilio_voice.save_api_key_secret(row, payload.twilio_api_key_secret.strip())

    db.commit()
    db.refresh(row)
    return serialize_connection(row, has_agent_phone=bool(normalize_phone(current_user.phone)))


@router.get("/me")
def get_me_telephony(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    row = db.query(CompanySettings).filter(CompanySettings.company_id == company_id).first()
    ring_default = True if row is None or row.twilio_ring_mobile_default is None else bool(row.twilio_ring_mobile_default)
    ring = ring_default if current_user.twilio_ring_mobile is None else bool(current_user.twilio_ring_mobile)
    return {
        "phone": current_user.phone,
        "twilio_phone_number": current_user.twilio_phone_number,
        "twilio_ring_mobile": ring,
        "twilio_ring_mobile_override": current_user.twilio_ring_mobile,
        "twilio_configured": twilio_voice.twilio_configured(row),
        "company_twilio_number": (row.twilio_phone_number if row else None),
    }


@router.put("/me")
def put_me_telephony(
    payload: MeTelephonyWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _company_id(current_user)
    user = db.query(User).filter(User.id == current_user.id).one()
    if payload.twilio_phone_number is not None:
        user.twilio_phone_number = normalize_phone(payload.twilio_phone_number)
    if payload.twilio_ring_mobile is not None:
        user.twilio_ring_mobile = 1 if payload.twilio_ring_mobile else 0
    db.commit()
    db.refresh(user)
    row = db.query(CompanySettings).filter(CompanySettings.company_id == user.company_id).first()
    ring_default = True if row is None or row.twilio_ring_mobile_default is None else bool(row.twilio_ring_mobile_default)
    ring = ring_default if user.twilio_ring_mobile is None else bool(user.twilio_ring_mobile)
    return {
        "phone": user.phone,
        "twilio_phone_number": user.twilio_phone_number,
        "twilio_ring_mobile": ring,
        "twilio_ring_mobile_override": user.twilio_ring_mobile,
        "twilio_configured": twilio_voice.twilio_configured(row),
        "company_twilio_number": (row.twilio_phone_number if row else None),
    }


@router.post("/twilio/token")
def twilio_token(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    row = db.query(CompanySettings).filter(CompanySettings.company_id == company_id).first()
    if not twilio_voice.twilio_configured(row):
        raise HTTPException(status_code=400, detail="Twilio is not configured")
    try:
        token = twilio_voice.mint_access_token(row, user_id=current_user.id)
    except Exception:
        raise HTTPException(status_code=502, detail="Could not mint Twilio token")
    return {
        "token": token,
        "identity": twilio_voice.client_identity(current_user.id),
        "from_phone": twilio_voice.resolve_caller_id(row, current_user),
    }


@router.post("/twilio/calls", status_code=status.HTTP_201_CREATED)
def twilio_create_call(
    payload: TwilioCallWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    company_id = _company_id(current_user)
    row = db.query(CompanySettings).filter(CompanySettings.company_id == company_id).first()
    if not twilio_voice.twilio_configured(row):
        raise HTTPException(status_code=400, detail="Twilio is not configured")

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
        if deal is not None:
            ensure_can_work_lead_id(db, current_user, active_team_id, deal.lead_id)

    to_phone = resolve_to_phone(
        db, company_id=company_id, lead=lead, client=client, deal=deal, to_phone=payload.to_phone,
    )
    if not to_phone:
        raise HTTPException(status_code=400, detail="Destination phone is required")
    try:
        from_phone = twilio_voice.resolve_caller_id(row, current_user)
    except RuntimeError:
        raise HTTPException(status_code=400, detail="Twilio caller ID is not configured")

    now = naive_utc_now()
    call = CallLog(
        company_id=company_id,
        direction="outbound",
        duration_seconds=None,
        outcome="initiated",
        notes=None,
        logged_at=now,
        started_at=now,
        lead_id=payload.lead_id,
        client_id=payload.client_id,
        deal_id=payload.deal_id,
        created_by_id=current_user.id,
        provider="twilio",
        provider_call_id=None,
        from_phone=normalize_phone(from_phone),
        to_phone=to_phone,
    )
    db.add(call)
    db.flush()
    mark_lead_contacted(db, payload.lead_id)
    log_activity(
        db, user=current_user, action="created", entity_type="call",
        entity_id=call.id, entity_name="outbound",
    )
    db.commit()
    db.refresh(call)
    body = serialize_call(call)
    body["to_e164"] = twilio_voice.to_e164(to_phone)
    body["from_e164"] = from_phone
    return body


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
        if deal is not None:
            ensure_can_work_lead_id(db, current_user, active_team_id, deal.lead_id)

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
    mark_lead_contacted(db, payload.lead_id)
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


def _company_settings_for_account(db: Session, account_sid: Optional[str]) -> Optional[CompanySettings]:
    if not account_sid:
        return None
    return (
        db.query(CompanySettings)
        .filter(CompanySettings.twilio_account_sid == str(account_sid).strip())
        .first()
    )


@router.post("/twilio/voice")
async def twilio_voice_webhook(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    params = _form_dict(form)
    account_sid = params.get("AccountSid")
    row = _company_settings_for_account(db, account_sid)
    if row is None or not twilio_voice.twilio_configured(row):
        # Always return TwiML — JSON/403 makes Twilio Client fail with 31005.
        return Response(content=twilio_voice.no_agent_twiml(), media_type="application/xml", status_code=200)

    if not _voice_sig_ok(row, request, params):
        import logging
        logging.getLogger("app").warning(
            "twilio voice signature mismatch public_url=%s path=%s",
            twilio_voice.webhook_base(),
            request.url.path,
        )
        return Response(
            content=twilio_voice.gateway_error_twiml(
                "Call setup failed signature check. Check PUBLIC_API_URL and TwiML App Voice URL."
            ),
            media_type="application/xml",
            status_code=200,
        )

    to_phone = params.get("To") or params.get("to")
    call_log_id = params.get("CallLogId") or params.get("call_log_id")
    caller_id = params.get("From") or params.get("Caller")
    # Outbound from browser Client: To is the destination number; Caller is client:user:N
    identity_user_id = twilio_voice.parse_client_identity(caller_id)
    if identity_user_id is not None and call_log_id:
        call = db.query(CallLog).filter(CallLog.id == int(call_log_id), CallLog.company_id == row.company_id).first()
        if call is None:
            return Response(content=twilio_voice.no_agent_twiml(), media_type="application/xml")
        if params.get("CallSid") and not call.provider_call_id:
            call.provider_call_id = str(params.get("CallSid"))[:64]
            db.commit()
        to_phone = call.to_phone or to_phone
        try:
            user = db.query(User).filter(User.id == identity_user_id, User.company_id == row.company_id).one()
            from_phone = twilio_voice.resolve_caller_id(row, user)
        except Exception:
            from_phone = twilio_voice.to_e164(row.twilio_phone_number) or row.twilio_phone_number
        dial_to = twilio_voice.to_e164(to_phone) or to_phone
        xml = twilio_voice.outbound_dial_twiml(
            to_phone=dial_to,
            caller_id=from_phone,
            status_callback=f"{twilio_voice.status_url()}?call_log_id={call.id}",
        )
        return Response(content=xml, media_type="application/xml")

    if not to_phone:
        return Response(content=twilio_voice.no_agent_twiml(), media_type="application/xml")
    from_phone = twilio_voice.to_e164(row.twilio_phone_number) or row.twilio_phone_number
    xml = twilio_voice.outbound_dial_twiml(
        to_phone=twilio_voice.to_e164(to_phone) or to_phone,
        caller_id=from_phone,
        status_callback=twilio_voice.status_url(),
    )
    return Response(content=xml, media_type="application/xml")


@router.post("/twilio/inbound")
async def twilio_inbound_webhook(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    params = _form_dict(form)
    to_phone = params.get("To") or params.get("Called")
    from_phone = params.get("From") or params.get("Caller")
    account_sid = params.get("AccountSid")

    row = twilio_voice.find_settings_by_twilio_number(db, to_phone)
    if row is None:
        row = _company_settings_for_account(db, account_sid)
    if row is None or not twilio_voice.twilio_configured(row):
        return Response(content=twilio_voice.no_agent_twiml(), media_type="application/xml")

    if not _voice_sig_ok(row, request, params):
        import logging
        logging.getLogger("app").warning("twilio inbound signature mismatch")
        return Response(
            content=twilio_voice.gateway_error_twiml("Inbound call could not be authenticated."),
            media_type="application/xml",
            status_code=200,
        )

    try:
        agent, lead, client = twilio_voice.pick_inbound_agent(
            db, row=row, to_phone=to_phone, from_phone=from_phone,
        )
    except LookupError:
        return Response(content=twilio_voice.no_agent_twiml(), media_type="application/xml")

    now = naive_utc_now()
    call = CallLog(
        company_id=row.company_id,
        direction="inbound",
        duration_seconds=None,
        outcome="ringing",
        notes=None,
        logged_at=now,
        started_at=now,
        lead_id=lead.id if lead else None,
        client_id=client.id if client else None,
        deal_id=None,
        created_by_id=agent.id,
        provider="twilio",
        provider_call_id=(params.get("CallSid") or None),
        from_phone=normalize_phone(from_phone),
        to_phone=normalize_phone(to_phone) or normalize_phone(row.twilio_phone_number),
    )
    db.add(call)
    db.commit()
    db.refresh(call)

    xml = twilio_voice.inbound_dial_twiml(
        agent=agent,
        row=row,
        status_callback=f"{twilio_voice.status_url()}?call_log_id={call.id}",
    )
    return Response(content=xml, media_type="application/xml")


@router.post("/twilio/status", status_code=204)
async def twilio_status_webhook(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    params = _form_dict(form)
    account_sid = params.get("AccountSid")
    call_log_id = request.query_params.get("call_log_id") or params.get("call_log_id")
    row = _company_settings_for_account(db, account_sid)
    if row is None and call_log_id:
        call = db.query(CallLog).filter(CallLog.id == int(call_log_id)).first()
        if call is not None:
            row = db.query(CompanySettings).filter(CompanySettings.company_id == call.company_id).first()
    if row is None or not twilio_voice.twilio_configured(row):
        return None

    if not _voice_sig_ok(row, request, params):
        # Status callbacks should not 403 — Twilio retries and Client surfaces 31005.
        import logging
        logging.getLogger("app").warning("twilio status signature mismatch")
        return None

    await run_in_threadpool(
        twilio_voice.apply_twilio_status,
        db,
        call_sid=params.get("CallSid"),
        parent_call_sid=params.get("ParentCallSid"),
        call_status=params.get("CallStatus") or params.get("DialCallStatus"),
        call_duration=params.get("CallDuration") or params.get("DialCallDuration"),
        call_log_id=int(call_log_id) if call_log_id else None,
    )
    return None
