"""Exotel click-to-call."""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.models.core.company_settings import CompanySettings
from app.models.sales.call_log import CallLog
from app.models.sales.client import Client
from app.models.sales.deal import Deal
from app.models.sales.lead import Lead
from app.utils.helpers import normalize_phone
from app.utils.totp_crypto import decrypt_secret, encrypt_secret


def telephony_configured(row: Optional[CompanySettings]) -> bool:
    if row is None:
        return False
    token = (row.exotel_api_token_encrypted or "").strip()
    sid = (row.exotel_sid or "").strip()
    caller = (row.exotel_caller_id or "").strip()
    return bool(sid and token and caller)


def serialize_connection(row: Optional[CompanySettings], *, has_agent_phone: bool) -> dict:
    from app.services.sales.twilio_voice import serialize_twilio, twilio_configured

    exotel_ok = telephony_configured(row)
    twilio_ok = twilio_configured(row)
    if exotel_ok and twilio_ok:
        provider = "both"
    elif twilio_ok:
        provider = "twilio"
    elif exotel_ok:
        provider = "exotel"
    else:
        provider = None
    return {
        "configured": exotel_ok,
        "caller_id": (row.exotel_caller_id if row else None) or None,
        "sid": (row.exotel_sid if row and exotel_ok else None),
        "subdomain": (row.exotel_subdomain if row else None) or "api.exotel.com",
        "has_agent_phone": has_agent_phone,
        "provider": provider,
        "twilio": serialize_twilio(row),
    }


def save_token(row: CompanySettings, api_token: str) -> None:
    row.exotel_api_token_encrypted = encrypt_secret(api_token)


def resolve_to_phone(
    db: Session,
    *,
    company_id: int,
    lead: Optional[Lead],
    client: Optional[Client],
    deal: Optional[Deal],
    to_phone: Optional[str],
) -> Optional[str]:
    if to_phone:
        return normalize_phone(to_phone)
    if lead and lead.phone:
        return normalize_phone(lead.phone)
    if client and client.phone:
        return normalize_phone(client.phone)
    if deal:
        if deal.client_id:
            linked = (
                db.query(Client)
                .filter(Client.id == deal.client_id, Client.company_id == company_id)
                .first()
            )
            if linked and linked.phone:
                return normalize_phone(linked.phone)
        if deal.lead_id:
            linked_lead = (
                db.query(Lead)
                .filter(Lead.id == deal.lead_id, Lead.company_id == company_id)
                .first()
            )
            if linked_lead and linked_lead.phone:
                return normalize_phone(linked_lead.phone)
    return None


def webhook_url() -> str:
    base = (app_settings.PUBLIC_API_URL or "").rstrip("/")
    return f"{base}/api/telephony/exotel/webhook"


def place_exotel_call(
    row: CompanySettings, *, from_phone: str, to_phone: str, status_callback_url: str
) -> str:
    sid = (row.exotel_sid or "").strip()
    api_key = (row.exotel_api_key or sid).strip()
    token = decrypt_secret(row.exotel_api_token_encrypted)
    subdomain = (row.exotel_subdomain or "api.exotel.com").strip()
    caller_id = (row.exotel_caller_id or "").strip()
    host = subdomain.replace("https://", "").replace("http://", "").rstrip("/")
    url = f"https://{host}/v1/Accounts/{quote(sid)}/Calls/connect.json"
    data = {
        "From": from_phone,
        "To": to_phone,
        "CallerId": caller_id,
        "CallType": "trans",
        "StatusCallback": status_callback_url,
    }
    with httpx.Client(timeout=20.0) as client:
        res = client.post(url, data=data, auth=(api_key, token))
    if res.status_code >= 400:
        raise RuntimeError("exotel connect failed")
    payload = res.json() if res.content else {}
    call = payload.get("Call") if isinstance(payload, dict) else None
    sid_out = None
    if isinstance(call, dict):
        sid_out = call.get("Sid") or call.get("sid")
    if not sid_out and isinstance(payload, dict):
        sid_out = payload.get("Sid")
    if not sid_out:
        raise RuntimeError("exotel connect returned no Sid")
    return str(sid_out)


def apply_webhook(db: Session, *, call_sid: str, status: Optional[str], duration: Optional[str]) -> None:
    if not call_sid:
        return
    row = db.query(CallLog).filter(CallLog.provider_call_id == str(call_sid)).first()
    if row is None:
        return
    if duration is not None and str(duration).strip() != "":
        try:
            seconds = int(float(str(duration).strip()))
            if seconds >= 0:
                row.duration_seconds = seconds
        except (TypeError, ValueError):
            pass
    if status:
        row.outcome = str(status).strip().lower()[:255]
    db.commit()
