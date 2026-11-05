"""Per-user Gmail / Outlook mailbox: OAuth, send, and pull-sync."""
from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import getaddresses, parseaddr
from typing import Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.sales.client import Client
from app.models.sales.deal import Deal
from app.models.sales.email_log import EmailLog
from app.models.sales.lead import Lead
from app.services.sales.lead_activity import mark_lead_contacted, mark_lead_responded
from app.models.sales.mailbox import MailboxConnection
from app.services.auth.oauth import (
    GOOGLE_AUTH,
    GOOGLE_TOKEN,
    GOOGLE_USERINFO,
    MICROSOFT_AUTH,
    MICROSOFT_TOKEN,
    PROVIDERS,
    OAuthError,
    provider_enabled,
    providers_status,
)
from app.utils.security import create_access_token, decode_access_token
from app.utils.totp_crypto import decrypt_secret, encrypt_secret

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GOOGLE_MAIL_SCOPES = f"{GMAIL_SEND_SCOPE} {GMAIL_READ_SCOPE} openid email"
MICROSOFT_MAIL_SCOPES = "Mail.Send Mail.Read offline_access openid email"

GMAIL_SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GMAIL_LIST = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
GMAIL_GET = "https://gmail.googleapis.com/gmail/v1/users/me/messages/{id}"
GRAPH_SEND = "https://graph.microsoft.com/v1.0/me/sendMail"
GRAPH_LIST = "https://graph.microsoft.com/v1.0/me/messages"
GRAPH_ME = "https://graph.microsoft.com/v1.0/me"


@dataclass(frozen=True)
class MailboxTokens:
    refresh_token: str
    access_token: str
    expires_in: int
    email: str


@dataclass(frozen=True)
class MailboxMessage:
    provider_message_id: str
    from_email: str
    to_emails: list[str]
    subject: str
    body: str
    cc_emails: list[str] = field(default_factory=list)


def normalize_email(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def extract_emails(*raw_values: str) -> set[str]:
    found: set[str] = set()
    for raw in raw_values:
        if not raw:
            continue
        for _, addr in getaddresses([raw]):
            n = normalize_email(addr)
            if n and "@" in n:
                found.add(n)
        n = normalize_email(parseaddr(raw)[1] or raw)
        if n and "@" in n:
            found.add(n)
    return found


def mailbox_redirect_uri(provider: str) -> str:
    base = (settings.PUBLIC_API_URL or "").rstrip("/")
    return f"{base}/api/mailbox/oauth/{provider}/callback"


def make_mailbox_state(provider: str, user_id: int) -> str:
    return create_access_token(
        data={"provider": provider, "nonce": secrets.token_urlsafe(16), "user_id": user_id},
        expires_delta=timedelta(minutes=10),
        audience="mailbox_oauth_state",
    )


def parse_mailbox_state(state: str, expected_provider: str) -> int:
    payload = decode_access_token(state, audience="mailbox_oauth_state")
    if payload is None:
        raise OAuthError("denied")
    if payload.get("provider") != expected_provider:
        raise OAuthError("denied")
    user_id = payload.get("user_id")
    if not user_id:
        raise OAuthError("denied")
    return int(user_id)


def mailbox_authorization_url(provider: str, user_id: int) -> str:
    if provider not in PROVIDERS:
        raise OAuthError("provider")
    if not provider_enabled(provider):
        raise OAuthError("provider")

    state = make_mailbox_state(provider, user_id)
    if provider == "google":
        params = {
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "redirect_uri": mailbox_redirect_uri(provider),
            "response_type": "code",
            "scope": GOOGLE_MAIL_SCOPES,
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{GOOGLE_AUTH}?{urlencode(params)}"

    tenant = settings.MICROSOFT_OAUTH_TENANT or "common"
    params = {
        "client_id": settings.MICROSOFT_OAUTH_CLIENT_ID,
        "redirect_uri": mailbox_redirect_uri(provider),
        "response_type": "code",
        "scope": MICROSOFT_MAIL_SCOPES,
        "state": state,
        "response_mode": "query",
    }
    return f"{MICROSOFT_AUTH.format(tenant=tenant)}?{urlencode(params)}"


def _google_tokens(code: str) -> MailboxTokens:
    with httpx.Client(timeout=20.0) as client:
        token_res = client.post(
            GOOGLE_TOKEN,
            data={
                "code": code,
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "redirect_uri": mailbox_redirect_uri("google"),
                "grant_type": "authorization_code",
            },
        )
        if token_res.status_code >= 400:
            raise OAuthError("denied")
        data = token_res.json()
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        if not access or not refresh:
            raise OAuthError("denied")
        info = client.get(GOOGLE_USERINFO, headers={"Authorization": f"Bearer {access}"})
        if info.status_code >= 400:
            raise OAuthError("denied")
        email = normalize_email(info.json().get("email"))
        if not email:
            raise OAuthError("denied")
        return MailboxTokens(
            refresh_token=refresh,
            access_token=access,
            expires_in=int(data.get("expires_in") or 3600),
            email=email,
        )


def _microsoft_tokens(code: str) -> MailboxTokens:
    tenant = settings.MICROSOFT_OAUTH_TENANT or "common"
    with httpx.Client(timeout=20.0) as client:
        token_res = client.post(
            MICROSOFT_TOKEN.format(tenant=tenant),
            data={
                "code": code,
                "client_id": settings.MICROSOFT_OAUTH_CLIENT_ID,
                "client_secret": settings.MICROSOFT_OAUTH_CLIENT_SECRET,
                "redirect_uri": mailbox_redirect_uri("microsoft"),
                "grant_type": "authorization_code",
            },
        )
        if token_res.status_code >= 400:
            raise OAuthError("denied")
        data = token_res.json()
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        if not access or not refresh:
            raise OAuthError("denied")
        me = client.get(
            GRAPH_ME,
            params={"$select": "mail,userPrincipalName"},
            headers={"Authorization": f"Bearer {access}"},
        )
        if me.status_code >= 400:
            raise OAuthError("denied")
        profile = me.json()
        email = normalize_email(profile.get("mail") or profile.get("userPrincipalName"))
        if not email:
            raise OAuthError("denied")
        return MailboxTokens(
            refresh_token=refresh,
            access_token=access,
            expires_in=int(data.get("expires_in") or 3600),
            email=email,
        )


def exchange_mailbox_code(provider: str, code: str) -> MailboxTokens:
    if provider == "google":
        return _google_tokens(code)
    if provider == "microsoft":
        return _microsoft_tokens(code)
    raise OAuthError("provider")


def upsert_connection(db: Session, *, user_id: int, company_id: int, provider: str, tokens: MailboxTokens) -> MailboxConnection:
    expires = datetime.now(timezone.utc) + timedelta(seconds=max(tokens.expires_in - 60, 0))
    row = db.query(MailboxConnection).filter(MailboxConnection.user_id == user_id).first()
    if row is None:
        row = MailboxConnection(user_id=user_id, company_id=company_id)
        db.add(row)
    row.company_id = company_id
    row.provider = provider
    row.email = tokens.email
    row.refresh_token_encrypted = encrypt_secret(tokens.refresh_token)
    row.access_token_encrypted = encrypt_secret(tokens.access_token)
    row.access_token_expires_at = expires
    row.status = "active"
    row.error_message = None
    db.commit()
    db.refresh(row)
    return row


def serialize_mailbox(row: Optional[MailboxConnection]) -> dict:
    return {
        "connected": row is not None,
        "provider": row.provider if row else None,
        "email": row.email if row else None,
        "status": row.status if row else None,
        "last_synced_at": row.last_synced_at.isoformat() if row and row.last_synced_at else None,
        "providers": providers_status(),
    }


def get_user_mailbox(db: Session, user_id: int) -> Optional[MailboxConnection]:
    return db.query(MailboxConnection).filter(MailboxConnection.user_id == user_id).first()


def delete_user_mailbox(db: Session, user_id: int) -> None:
    row = get_user_mailbox(db, user_id)
    if row is None:
        return
    db.delete(row)
    db.commit()


def _refresh_access_token(connection: MailboxConnection) -> str:
    refresh = decrypt_secret(connection.refresh_token_encrypted)
    if connection.provider == "google":
        with httpx.Client(timeout=20.0) as client:
            res = client.post(
                GOOGLE_TOKEN,
                data={
                    "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                    "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                    "refresh_token": refresh,
                    "grant_type": "refresh_token",
                },
            )
        if res.status_code >= 400:
            raise OAuthError("denied")
        data = res.json()
        access = data.get("access_token")
        if not access:
            raise OAuthError("denied")
        return access

    tenant = settings.MICROSOFT_OAUTH_TENANT or "common"
    with httpx.Client(timeout=20.0) as client:
        res = client.post(
            MICROSOFT_TOKEN.format(tenant=tenant),
            data={
                "client_id": settings.MICROSOFT_OAUTH_CLIENT_ID,
                "client_secret": settings.MICROSOFT_OAUTH_CLIENT_SECRET,
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
        )
    if res.status_code >= 400:
        raise OAuthError("denied")
    data = res.json()
    access = data.get("access_token")
    if not access:
        raise OAuthError("denied")
    return access


def access_token_for(db: Session, connection: MailboxConnection) -> str:
    now = datetime.now(timezone.utc)
    expires = connection.access_token_expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if connection.access_token_encrypted and expires and expires > now:
        return decrypt_secret(connection.access_token_encrypted)
    access = _refresh_access_token(connection)
    connection.access_token_encrypted = encrypt_secret(access)
    connection.access_token_expires_at = now + timedelta(minutes=50)
    connection.status = "active"
    connection.error_message = None
    db.commit()
    return access


def send_via_mailbox(connection: MailboxConnection, *, to_email: str, subject: str, body: str, db: Optional[Session] = None) -> str:
    """Send an HTML body and return the provider message id. Tests patch this."""
    if db is not None:
        access = access_token_for(db, connection)
    elif connection.access_token_encrypted:
        access = decrypt_secret(connection.access_token_encrypted)
    else:
        raise RuntimeError("mailbox has no access token")
    if connection.provider == "google":
        return _gmail_send(access, from_email=connection.email, to_email=to_email, subject=subject, body=body)
    if connection.provider == "microsoft":
        return _graph_send(access, to_email=to_email, subject=subject, body=body)
    raise RuntimeError("unknown mailbox provider")


def _gmail_send(access: str, *, from_email: str, to_email: str, subject: str, body: str) -> str:
    msg = MIMEText(body, "html", "utf-8")
    msg["To"] = to_email
    msg["From"] = from_email
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")
    with httpx.Client(timeout=20.0) as client:
        res = client.post(GMAIL_SEND, json={"raw": raw}, headers={"Authorization": f"Bearer {access}"})
    if res.status_code >= 400:
        raise RuntimeError("gmail send failed")
    return str(res.json().get("id") or "")


def _graph_send(access: str, *, to_email: str, subject: str, body: str) -> str:
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body},
            "toRecipients": [{"emailAddress": {"address": to_email}}],
        },
        "saveToSentItems": True,
    }
    with httpx.Client(timeout=20.0) as client:
        res = client.post(GRAPH_SEND, json=payload, headers={"Authorization": f"Bearer {access}"})
    if res.status_code >= 400:
        raise RuntimeError("graph send failed")
    return res.headers.get("x-ms-request-id") or f"graph-{secrets.token_hex(8)}"


def _header_map(payload: dict) -> dict[str, str]:
    headers = {}
    for item in payload.get("payload", {}).get("headers") or []:
        name = (item.get("name") or "").lower()
        headers[name] = item.get("value") or ""
    return headers


def _gmail_body(payload: dict) -> str:
    snippet = payload.get("snippet") or ""
    parts = [payload.get("payload") or {}]
    texts = []
    while parts:
        part = parts.pop()
        mime = (part.get("mimeType") or "")
        data = (part.get("body") or {}).get("data")
        if data and mime.startswith("text/plain"):
            pad = "=" * (-len(data) % 4)
            texts.append(base64.urlsafe_b64decode(data + pad).decode("utf-8", errors="replace"))
        parts.extend(part.get("parts") or [])
    return (texts[0] if texts else snippet)[:20000]


def list_mailbox_messages(connection: MailboxConnection, db: Optional[Session] = None) -> list[MailboxMessage]:
    """Pull recent messages. Tests patch this."""
    if db is not None:
        access = access_token_for(db, connection)
    elif connection.access_token_encrypted:
        access = decrypt_secret(connection.access_token_encrypted)
    else:
        return []
    if connection.provider == "google":
        return _gmail_list(access)
    if connection.provider == "microsoft":
        return _graph_list(access)
    return []


def _gmail_list(access: str) -> list[MailboxMessage]:
    headers = {"Authorization": f"Bearer {access}"}
    out: list[MailboxMessage] = []
    with httpx.Client(timeout=20.0) as client:
        listed = client.get(GMAIL_LIST, params={"maxResults": 50, "q": "newer_than:7d"}, headers=headers)
        if listed.status_code >= 400:
            return []
        for item in (listed.json().get("messages") or [])[:50]:
            mid = item.get("id")
            if not mid:
                continue
            got = client.get(GMAIL_GET.format(id=mid), params={"format": "full"}, headers=headers)
            if got.status_code >= 400:
                continue
            data = got.json()
            h = _header_map(data)
            from_email = normalize_email(parseaddr(h.get("from", ""))[1])
            to_emails = sorted(extract_emails(h.get("to", "")))
            cc_emails = sorted(extract_emails(h.get("cc", "")))
            out.append(MailboxMessage(
                provider_message_id=str(mid),
                from_email=from_email,
                to_emails=to_emails,
                cc_emails=cc_emails,
                subject=(h.get("subject") or "(no subject)")[:255],
                body=_gmail_body(data),
            ))
    return out


def _graph_list(access: str) -> list[MailboxMessage]:
    headers = {"Authorization": f"Bearer {access}"}
    out: list[MailboxMessage] = []
    with httpx.Client(timeout=20.0) as client:
        res = client.get(
            GRAPH_LIST,
            params={
                "$top": 50,
                "$select": "id,subject,bodyPreview,from,toRecipients,ccRecipients,isDraft",
                "$orderby": "receivedDateTime desc",
            },
            headers=headers,
        )
        if res.status_code >= 400:
            return []
        for item in res.json().get("value") or []:
            if item.get("isDraft"):
                continue
            from_email = normalize_email(
                ((item.get("from") or {}).get("emailAddress") or {}).get("address")
            )
            to_emails = [
                normalize_email((r.get("emailAddress") or {}).get("address"))
                for r in (item.get("toRecipients") or [])
            ]
            cc_emails = [
                normalize_email((r.get("emailAddress") or {}).get("address"))
                for r in (item.get("ccRecipients") or [])
            ]
            out.append(MailboxMessage(
                provider_message_id=str(item.get("id")),
                from_email=from_email,
                to_emails=[e for e in to_emails if e],
                cc_emails=[e for e in cc_emails if e],
                subject=(item.get("subject") or "(no subject)")[:255],
                body=(item.get("bodyPreview") or "")[:20000],
            ))
    return out


def match_crm_ids(db: Session, company_id: int, addresses: set[str], mailbox_email: str) -> dict:
    wanted = {normalize_email(a) for a in addresses if a and normalize_email(a) != normalize_email(mailbox_email)}
    lead_id = None
    client_id = None
    deal_id = None
    if not wanted:
        return {"lead_id": None, "client_id": None, "deal_id": None}

    lead = (
        db.query(Lead)
        .filter(
            Lead.company_id == company_id,
            Lead.deleted_at.is_(None),
            sa_func.lower(Lead.email).in_(wanted),
        )
        .order_by(Lead.id.asc())
        .first()
    )
    if lead:
        lead_id = lead.id
    client = (
        db.query(Client)
        .filter(Client.company_id == company_id, sa_func.lower(Client.email).in_(wanted))
        .order_by(Client.id.asc())
        .first()
    )
    if client:
        client_id = client.id
    deal_q = db.query(Deal).filter(Deal.company_id == company_id)
    if lead_id:
        deal = deal_q.filter(Deal.lead_id == lead_id).order_by(Deal.id.asc()).first()
        if deal:
            deal_id = deal.id
    if deal_id is None and client_id:
        deal = deal_q.filter(Deal.client_id == client_id).order_by(Deal.id.asc()).first()
        if deal:
            deal_id = deal.id
    return {"lead_id": lead_id, "client_id": client_id, "deal_id": deal_id}


def import_message(db: Session, connection: MailboxConnection, message: MailboxMessage) -> bool:
    if not message.provider_message_id:
        return False
    exists = (
        db.query(EmailLog)
        .filter(
            EmailLog.company_id == connection.company_id,
            EmailLog.provider == connection.provider,
            EmailLog.provider_message_id == message.provider_message_id,
        )
        .first()
    )
    if exists:
        return False

    addresses = extract_emails(message.from_email) | set(message.to_emails) | set(message.cc_emails)
    matched = match_crm_ids(db, connection.company_id, addresses, connection.email)
    if not matched["lead_id"] and not matched["client_id"] and not matched["deal_id"]:
        return False

    mailbox = normalize_email(connection.email)
    direction = "outbound" if normalize_email(message.from_email) == mailbox else "inbound"
    to_email = message.to_emails[0] if message.to_emails else (message.from_email or mailbox)
    row = EmailLog(
        company_id=connection.company_id,
        lead_id=matched["lead_id"],
        client_id=matched["client_id"],
        deal_id=matched["deal_id"],
        to_email=to_email,
        from_email=message.from_email or None,
        subject=message.subject or "(no subject)",
        body=message.body or "",
        status="sent",
        direction=direction,
        provider=connection.provider,
        provider_message_id=message.provider_message_id,
        sent_by_id=connection.user_id if direction == "outbound" else None,
    )
    db.add(row)
    if direction == "outbound":
        mark_lead_contacted(db, matched["lead_id"])
    else:
        mark_lead_responded(db, matched["lead_id"])
    db.commit()
    return True


def sync_mailbox(db: Session, connection: MailboxConnection) -> int:
    imported = 0
    try:
        messages = list_mailbox_messages(connection, db)
        for msg in messages:
            if import_message(db, connection, msg):
                imported += 1
        connection.last_synced_at = datetime.now(timezone.utc)
        connection.status = "active"
        connection.error_message = None
        db.commit()
    except Exception as exc:  # noqa: BLE001 — persist last error, do not crash list
        connection.status = "error"
        connection.error_message = str(exc)[:500]
        db.commit()
        raise
    return imported


def maybe_autosync(db: Session, connection: Optional[MailboxConnection]) -> None:
    if connection is None or connection.status != "active":
        return
    last = connection.last_synced_at
    now = datetime.now(timezone.utc)
    if last is not None:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if now - last < timedelta(seconds=60):
            return
    try:
        sync_mailbox(db, connection)
    except Exception:
        return
