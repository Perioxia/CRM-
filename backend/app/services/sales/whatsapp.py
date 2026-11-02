import json
from datetime import datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.models.core.company_settings import CompanySettings
from app.models.sales.client import Client
from app.models.sales.lead import Lead
from app.services.sales.lead_activity import mark_lead_responded
from app.models.sales.whatsapp import WhatsAppMessage, WhatsAppTemplate
from app.utils.helpers import normalize_phone

GUPSHUP_TEMPLATE_URL = "https://api.gupshup.io/wa/api/v1/template/msg"
GUPSHUP_MSG_URL = "https://api.gupshup.io/wa/api/v1/msg"
SESSION_HOURS = 24


def destination_msisdn(value: Optional[str]) -> str:
    digits = normalize_phone(value)
    if not digits:
        raise ValueError("A phone number is required")
    if len(digits) == 10:
        return "91" + digits
    if digits.startswith("0") and len(digits) == 11:
        return "91" + digits[1:]
    if digits.startswith("91") and len(digits) >= 12:
        return digits
    if len(digits) < 10:
        raise ValueError("Phone number is too short")
    return digits


def params_for_record(variable_keys: list[str], record) -> list[str]:
    out = []
    for key in variable_keys or []:
        alias = "name" if key in ("name", "lead_name", "client_name") else key
        value = getattr(record, alias, None)
        if value is None and alias == "name":
            value = getattr(record, "name", None)
        out.append("" if value is None else str(value))
    return out


def post_gupshup_template(*, api_key: str, source: str, destination: str, template_id: str, params: list[str]) -> tuple[bool, str]:
    response = httpx.post(
        GUPSHUP_TEMPLATE_URL,
        headers={"apikey": api_key, "Content-Type": "application/x-www-form-urlencoded"},
        data={
            "source": source,
            "destination": destination,
            "template": json.dumps({"id": template_id, "params": params}),
        },
        timeout=20.0,
    )
    snippet = (response.text or "")[:500]
    if response.status_code >= 400:
        return False, snippet or f"HTTP {response.status_code}"
    return True, snippet


def post_gupshup_session_text(*, api_key: str, source: str, destination: str, text: str) -> tuple[bool, str]:
    response = httpx.post(
        GUPSHUP_MSG_URL,
        headers={"apikey": api_key, "Content-Type": "application/x-www-form-urlencoded"},
        data={
            "source": source,
            "destination": destination,
            "message": json.dumps({"type": "text", "text": text}),
        },
        timeout=20.0,
    )
    snippet = (response.text or "")[:500]
    if response.status_code >= 400:
        return False, snippet or f"HTTP {response.status_code}"
    return True, snippet


def parse_template_keys(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(x) for x in data]


def phone_match_key(value: Optional[str]) -> Optional[str]:
    try:
        return destination_msisdn(value)
    except ValueError:
        digits = normalize_phone(value)
        if not digits:
            return None
        if len(digits) >= 10:
            last10 = digits[-10:]
            return "91" + last10
        return digits


def _sources_equal(stored: Optional[str], incoming: Optional[str]) -> bool:
    a = phone_match_key(stored)
    b = phone_match_key(incoming)
    if a and b:
        return a == b
    sa = normalize_phone(stored)
    sb = normalize_phone(incoming)
    return bool(sa and sb and sa == sb)


def resolve_inbound_company(db: Session, destination: Optional[str]) -> Optional[CompanySettings]:
    dest = (destination or "").strip() or None
    configured = (
        db.query(CompanySettings)
        .filter(CompanySettings.whatsapp_source.isnot(None), CompanySettings.whatsapp_source != "")
        .all()
    )
    if dest:
        for row in configured:
            if _sources_equal(row.whatsapp_source, dest):
                return row
        return None
    if len(configured) == 1:
        return configured[0]
    return None


def match_lead_or_client(db: Session, company_id: int, phone: Optional[str]):
    key = phone_match_key(phone)
    if not key:
        return None, None
    leads = db.query(Lead).filter(Lead.company_id == company_id).all()
    for lead in leads:
        if getattr(lead, "deleted_at", None) is not None:
            continue
        if phone_match_key(lead.phone) == key:
            return lead, None
    clients = db.query(Client).filter(Client.company_id == company_id).all()
    for client in clients:
        if phone_match_key(client.phone) == key:
            return None, client
    return None, None


def extract_gupshup_inbound(body: dict, query_source: Optional[str] = None) -> dict:
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
    inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    text = inner.get("text") or payload.get("text") or inner.get("caption") or body.get("text")
    destination = (
        query_source
        or body.get("destination")
        or payload.get("destination")
        or inner.get("destination")
    )
    source = payload.get("source") or (payload.get("sender") or {}).get("phone") or body.get("source")
    msg_id = payload.get("id") or payload.get("gsId") or body.get("id")
    event_type = body.get("type") or payload.get("type")
    return {
        "event_type": event_type,
        "destination": destination,
        "source": source,
        "text": "" if text is None else str(text),
        "provider_message_id": None if msg_id is None else str(msg_id),
    }


def ingest_gupshup_inbound(db: Session, body: dict, query_source: Optional[str] = None) -> None:
    parsed = extract_gupshup_inbound(body or {}, query_source=query_source)
    if parsed["event_type"] not in (None, "message"):
        return
    if parsed["event_type"] is None and not parsed["source"] and not parsed["text"]:
        return
    settings = resolve_inbound_company(db, parsed["destination"])
    if settings is None:
        return
    company_id = settings.company_id
    provider_id = parsed["provider_message_id"]
    if provider_id:
        existing = (
            db.query(WhatsAppMessage)
            .filter(
                WhatsAppMessage.company_id == company_id,
                WhatsAppMessage.provider_message_id == provider_id,
            )
            .first()
        )
        if existing is not None:
            return
    lead, client = match_lead_or_client(db, company_id, parsed["source"])
    now = datetime.utcnow()
    try:
        from_phone = destination_msisdn(parsed["source"]) if parsed["source"] else (parsed["source"] or "")
    except ValueError:
        from_phone = normalize_phone(parsed["source"]) or (parsed["source"] or "")
    to_phone = settings.whatsapp_source or from_phone
    db.add(
        WhatsAppMessage(
            company_id=company_id,
            lead_id=lead.id if lead is not None else None,
            client_id=client.id if client is not None else None,
            to_phone=to_phone,
            from_phone=from_phone or None,
            direction="inbound",
            body=parsed["text"] or None,
            provider_message_id=provider_id,
            session_expires_at=now + timedelta(hours=SESSION_HOURS),
            status="received",
        )
    )
    mark_lead_responded(db, lead.id if lead is not None else None)
    db.commit()


def session_open_until(
    db: Session,
    *,
    company_id: int,
    lead_id: Optional[int] = None,
    client_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    now = now or datetime.utcnow()
    query = db.query(WhatsAppMessage).filter(
        WhatsAppMessage.company_id == company_id,
        WhatsAppMessage.direction == "inbound",
        WhatsAppMessage.session_expires_at.isnot(None),
        WhatsAppMessage.session_expires_at > now,
    )
    if lead_id is not None:
        query = query.filter(WhatsAppMessage.lead_id == lead_id)
    if client_id is not None:
        query = query.filter(WhatsAppMessage.client_id == client_id)
    row = query.order_by(WhatsAppMessage.session_expires_at.desc()).first()
    return row.session_expires_at if row is not None else None


def send_template_to_record(
    db: Session,
    *,
    company_id: int,
    template: WhatsAppTemplate,
    record,
    lead_id: Optional[int],
    client_id: Optional[int],
    sent_by_id: Optional[int] = None,
    params: Optional[list[str]] = None,
) -> WhatsAppMessage:
    settings = db.query(CompanySettings).filter(CompanySettings.company_id == company_id).first()
    api_key = getattr(settings, "whatsapp_api_key", None) if settings else None
    source = getattr(settings, "whatsapp_source", None) if settings else None
    if not api_key or not source:
        raise ValueError("WhatsApp is not configured for this company")
    destination = destination_msisdn(getattr(record, "phone", None))
    keys = parse_template_keys(template.variable_keys)
    resolved = params if params is not None else params_for_record(keys, record)
    ok, snippet = post_gupshup_template(
        api_key=api_key,
        source=source,
        destination=destination,
        template_id=template.provider_template_id,
        params=resolved,
    )
    row = WhatsAppMessage(
        company_id=company_id,
        template_id=template.id,
        lead_id=lead_id,
        client_id=client_id,
        to_phone=destination,
        direction="outbound",
        body=template.body,
        status="sent" if ok else "failed",
        error=None if ok else snippet,
        sent_by_id=sent_by_id,
    )
    db.add(row)
    db.flush()
    return row


def send_cadence_whatsapp(db: Session, follow_up) -> bool:
    settings = db.query(CompanySettings).filter(CompanySettings.company_id == follow_up.company_id).first()
    template_id = getattr(settings, "whatsapp_cadence_template_id", None) if settings else None
    if not template_id:
        return False
    template = (
        db.query(WhatsAppTemplate)
        .filter(WhatsAppTemplate.id == template_id, WhatsAppTemplate.company_id == follow_up.company_id)
        .first()
    )
    lead = follow_up.lead
    if template is None or lead is None:
        return False
    try:
        send_template_to_record(
            db,
            company_id=follow_up.company_id,
            template=template,
            record=lead,
            lead_id=lead.id,
            client_id=None,
        )
    except ValueError:
        return False
    return True
