"""Twilio Voice softphone: tokens, TwiML, inbound routing, status updates."""
from __future__ import annotations

from typing import Optional
from xml.sax.saxutils import escape

from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.models.core.company_settings import CompanySettings
from app.models.core.user import User
from app.models.sales.call_log import CallLog
from app.models.sales.client import Client
from app.models.sales.lead import Lead
from app.services.sales.activity_parents import naive_utc_now
from app.utils.helpers import normalize_phone, phone_variants
from app.utils.totp_crypto import decrypt_secret, encrypt_secret


def client_identity(user_id: int) -> str:
    return f"user:{int(user_id)}"


def parse_client_identity(identity: Optional[str]) -> Optional[int]:
    if not identity:
        return None
    value = str(identity).strip()
    if value.startswith("client:"):
        value = value[len("client:") :]
    if not value.startswith("user:"):
        return None
    try:
        return int(value.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        return None


def to_e164(phone: Optional[str]) -> Optional[str]:
    digits = normalize_phone(phone)
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10:
        digits = f"91{digits}"
    elif len(digits) == 11 and digits.startswith("0"):
        digits = f"91{digits[1:]}"
    return f"+{digits}"


def twilio_configured(row: Optional[CompanySettings]) -> bool:
    if row is None:
        return False
    if not (
        (row.twilio_account_sid or "").strip()
        and (row.twilio_auth_token_encrypted or "").strip()
        and (row.twilio_api_key_sid or "").strip()
        and (row.twilio_api_key_secret_encrypted or "").strip()
        and (row.twilio_twiml_app_sid or "").strip()
        and (row.twilio_phone_number or "").strip()
    ):
        return False
    # Secrets are Fernet-encrypted with SECRET_KEY. Non-empty blobs from another
    # environment (e.g. local CRM writing to shared Neon) must not count as configured.
    try:
        return bool(auth_token(row).strip() and api_key_secret(row).strip())
    except Exception:
        return False


def save_auth_token(row: CompanySettings, token: str) -> None:
    row.twilio_auth_token_encrypted = encrypt_secret(token)


def save_api_key_secret(row: CompanySettings, secret: str) -> None:
    row.twilio_api_key_secret_encrypted = encrypt_secret(secret)


def auth_token(row: CompanySettings) -> str:
    return decrypt_secret(row.twilio_auth_token_encrypted)


def api_key_secret(row: CompanySettings) -> str:
    return decrypt_secret(row.twilio_api_key_secret_encrypted)


def serialize_twilio(row: Optional[CompanySettings]) -> dict:
    configured = twilio_configured(row)
    if row is None or row.twilio_ring_mobile_default is None:
        ring_default = True
    else:
        ring_default = bool(row.twilio_ring_mobile_default)
    return {
        "configured": configured,
        "account_sid": (row.twilio_account_sid if row and configured else None),
        "api_key_sid": (row.twilio_api_key_sid if row and configured else None),
        "twiml_app_sid": (row.twilio_twiml_app_sid if row and configured else None),
        "phone_number": (row.twilio_phone_number if row else None) or None,
        "default_user_id": (row.twilio_default_user_id if row else None),
        "ring_mobile_default": ring_default,
    }


def webhook_base() -> str:
    return (app_settings.PUBLIC_API_URL or "").rstrip("/")


def voice_url() -> str:
    return f"{webhook_base()}/api/telephony/twilio/voice"


def inbound_url() -> str:
    return f"{webhook_base()}/api/telephony/twilio/inbound"


def status_url() -> str:
    return f"{webhook_base()}/api/telephony/twilio/status"


def mint_access_token(row: CompanySettings, *, user_id: int, ttl_seconds: int = 3600) -> str:
    from twilio.jwt.access_token import AccessToken
    from twilio.jwt.access_token.grants import VoiceGrant

    token = AccessToken(
        row.twilio_account_sid.strip(),
        row.twilio_api_key_sid.strip(),
        api_key_secret(row),
        identity=client_identity(user_id),
        ttl=ttl_seconds,
    )
    grant = VoiceGrant(
        outgoing_application_sid=row.twilio_twiml_app_sid.strip(),
        incoming_allow=True,
    )
    token.add_grant(grant)
    return token.to_jwt()


def validate_request_signature(
    row: CompanySettings,
    *,
    url: str,
    params: dict,
    signature: Optional[str],
    extra_urls: Optional[list[str]] = None,
) -> bool:
    if not signature:
        return False
    from twilio.request_validator import RequestValidator

    validator = RequestValidator(auth_token(row))
    candidates: list[str] = []
    for candidate in [url, *(extra_urls or [])]:
        if not candidate:
            continue
        if candidate not in candidates:
            candidates.append(candidate)
        # Trailing-slash variants trip signature checks behind ngrok/proxies.
        trimmed = candidate.rstrip("/")
        if trimmed and trimmed not in candidates:
            candidates.append(trimmed)
    return any(validator.validate(candidate, params, signature) for candidate in candidates)


def gateway_error_twiml(message: str = "We could not complete your call.") -> str:
    return _say_twiml(message)


def resolve_caller_id(row: CompanySettings, user: User) -> str:
    personal = to_e164(user.twilio_phone_number)
    if personal:
        return personal
    company = to_e164(row.twilio_phone_number)
    if not company:
        raise RuntimeError("Twilio phone number is not configured")
    return company


def ring_mobile_for(user: User, row: CompanySettings) -> bool:
    if user.twilio_ring_mobile is not None:
        return bool(user.twilio_ring_mobile)
    if row.twilio_ring_mobile_default is None:
        return True
    return bool(row.twilio_ring_mobile_default)


def find_settings_by_twilio_number(db: Session, to_phone: Optional[str]) -> Optional[CompanySettings]:
    variants = phone_variants(to_phone)
    if not variants:
        return None
    rows = db.query(CompanySettings).filter(CompanySettings.twilio_phone_number.isnot(None)).all()
    for row in rows:
        if phone_variants(row.twilio_phone_number) & variants:
            return row
    return None


def find_user_by_twilio_number(db: Session, *, company_id: int, to_phone: Optional[str]) -> Optional[User]:
    variants = phone_variants(to_phone)
    if not variants:
        return None
    users = (
        db.query(User)
        .filter(User.company_id == company_id, User.twilio_phone_number.isnot(None), User.is_active.is_(True))
        .all()
    )
    for user in users:
        if phone_variants(user.twilio_phone_number) & variants:
            return user
    return None


def find_lead_by_phone(db: Session, *, company_id: int, from_phone: Optional[str]) -> Optional[Lead]:
    variants = phone_variants(from_phone)
    if not variants:
        return None
    leads = db.query(Lead).filter(Lead.company_id == company_id, Lead.phone.isnot(None)).all()
    for lead in leads:
        if phone_variants(lead.phone) & variants:
            return lead
    return None


def find_client_by_phone(db: Session, *, company_id: int, from_phone: Optional[str]) -> Optional[Client]:
    variants = phone_variants(from_phone)
    if not variants:
        return None
    clients = db.query(Client).filter(Client.company_id == company_id, Client.phone.isnot(None)).all()
    for client in clients:
        if phone_variants(client.phone) & variants:
            return client
    return None


def pick_inbound_agent(
    db: Session,
    *,
    row: CompanySettings,
    to_phone: Optional[str],
    from_phone: Optional[str],
) -> tuple[User, Optional[Lead], Optional[Client]]:
    company_id = row.company_id
    lead = find_lead_by_phone(db, company_id=company_id, from_phone=from_phone)
    client = None if lead else find_client_by_phone(db, company_id=company_id, from_phone=from_phone)

    agent: Optional[User] = None
    if lead and lead.assigned_to_id:
        agent = (
            db.query(User)
            .filter(User.id == lead.assigned_to_id, User.company_id == company_id, User.is_active.is_(True))
            .first()
        )
    if agent is None and client and client.assigned_to_id:
        agent = (
            db.query(User)
            .filter(User.id == client.assigned_to_id, User.company_id == company_id, User.is_active.is_(True))
            .first()
        )
    if agent is None:
        agent = find_user_by_twilio_number(db, company_id=company_id, to_phone=to_phone)
    if agent is None and row.twilio_default_user_id:
        agent = (
            db.query(User)
            .filter(
                User.id == row.twilio_default_user_id,
                User.company_id == company_id,
                User.is_active.is_(True),
            )
            .first()
        )
    if agent is None:
        raise LookupError("No agent available for inbound call")
    return agent, lead, client


def _say_twiml(message: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Say>{escape(message)}</Say><Hangup/></Response>'


def outbound_dial_twiml(*, to_phone: str, caller_id: str, status_callback: str) -> str:
    to_e = escape(to_e164(to_phone) or to_phone)
    from_e = escape(to_e164(caller_id) or caller_id)
    cb = escape(status_callback)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Dial callerId="{from_e}" answerOnBridge="true" '
        f'statusCallback="{cb}" statusCallbackEvent="initiated ringing answered completed" '
        'statusCallbackMethod="POST">'
        f"<Number>{to_e}</Number>"
        "</Dial>"
        "</Response>"
    )


def inbound_dial_twiml(
    *,
    agent: User,
    row: CompanySettings,
    status_callback: str,
) -> str:
    identity = escape(client_identity(agent.id))
    cb = escape(status_callback)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<Response>",
        f'<Dial timeout="30" answerOnBridge="true" '
        f'statusCallback="{cb}" statusCallbackEvent="initiated ringing answered completed" '
        'statusCallbackMethod="POST">',
        f"<Client>{identity}</Client>",
    ]
    if ring_mobile_for(agent, row):
        mobile = to_e164(agent.phone)
        if mobile:
            parts.append(f"<Number>{escape(mobile)}</Number>")
    parts.extend(["</Dial>", "</Response>"])
    return "".join(parts)


def no_agent_twiml() -> str:
    return _say_twiml("Sorry, no one is available to take your call right now.")


def apply_twilio_status(
    db: Session,
    *,
    call_sid: Optional[str],
    parent_call_sid: Optional[str],
    call_status: Optional[str],
    call_duration: Optional[str],
    call_log_id: Optional[int] = None,
) -> None:
    row: Optional[CallLog] = None
    if call_log_id is not None:
        row = db.query(CallLog).filter(CallLog.id == int(call_log_id)).first()
    if row is None and call_sid:
        row = db.query(CallLog).filter(CallLog.provider_call_id == str(call_sid)).first()
    if row is None and parent_call_sid:
        row = db.query(CallLog).filter(CallLog.provider_call_id == str(parent_call_sid)).first()
    if row is None:
        return

    if call_sid and not row.provider_call_id:
        row.provider_call_id = str(call_sid)[:64]
    elif call_sid and row.provider_call_id != str(call_sid) and parent_call_sid == row.provider_call_id:
        # Keep the parent SID as the stable id; duration still applies.
        pass

    status = (call_status or "").strip().lower()
    if status:
        row.outcome = status[:255]
        now = naive_utc_now()
        if status in {"in-progress", "answered", "ringing"} and row.started_at is None:
            row.started_at = now
        if status in {"completed", "busy", "failed", "no-answer", "canceled", "cancelled"}:
            row.ended_at = now
            if row.started_at is None:
                row.started_at = now

    if call_duration is not None and str(call_duration).strip() != "":
        try:
            seconds = int(float(str(call_duration).strip()))
            if seconds >= 0:
                row.duration_seconds = seconds
        except (TypeError, ValueError):
            pass
    db.commit()
