from typing import Optional

from sqlalchemy.orm import Session

from app.models.sales.email_log import EmailLog
from app.models.sales.mailbox import MailboxConnection
from app.services.sales.lead_activity import mark_lead_contacted
from app.services.sales.email_tracking import build_outbound_html, mint_token, tracking_base
from app.services.sales.mailbox import get_user_mailbox, send_via_mailbox
from app.utils.email_service import send_email


def serialize_email(row: EmailLog) -> dict:
    return {
        "id": row.id,
        "lead_id": row.lead_id,
        "client_id": row.client_id,
        "deal_id": row.deal_id,
        "to_email": row.to_email,
        "from_email": row.from_email,
        "subject": row.subject,
        "body": row.body,
        "status": row.status,
        "direction": row.direction,
        "provider": row.provider,
        "provider_message_id": row.provider_message_id,
        "sent_by_id": row.sent_by_id,
        "open_count": int(row.open_count or 0),
        "click_count": int(row.click_count or 0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def deliver_and_log(
    db: Session,
    *,
    company_id: int,
    sent_by_id: Optional[int],
    to_email: str,
    subject: str,
    body: str,
    lead_id: Optional[int] = None,
    client_id: Optional[int] = None,
    deal_id: Optional[int] = None,
) -> EmailLog:
    mailbox: Optional[MailboxConnection] = None
    if sent_by_id is not None:
        mailbox = get_user_mailbox(db, sent_by_id)
        if mailbox is not None and mailbox.company_id != company_id:
            mailbox = None

    provider = "smtp"
    from_email = None
    provider_message_id = None
    ok = False

    base = tracking_base()
    open_raw = open_hash = click_raw = click_hash = None
    if base:
        open_raw, open_hash = mint_token()
        click_raw, click_hash = mint_token()
    outbound_html = build_outbound_html(
        body, base=base, open_raw=open_raw, click_raw=click_raw, click_hash=click_hash
    )

    if mailbox is not None and mailbox.status == "active":
        provider = mailbox.provider
        from_email = mailbox.email
        try:
            provider_message_id = send_via_mailbox(
                mailbox, to_email=to_email, subject=subject, body=outbound_html, db=db
            ) or None
            ok = True
        except Exception:
            ok = False
    else:
        ok = send_email(to_email, subject, outbound_html)

    row = EmailLog(
        company_id=company_id,
        lead_id=lead_id,
        client_id=client_id,
        deal_id=deal_id,
        to_email=to_email,
        from_email=from_email,
        subject=subject,
        body=body,
        status="sent" if ok else "failed",
        direction="outbound",
        provider=provider,
        provider_message_id=provider_message_id,
        sent_by_id=sent_by_id,
        open_token_hash=open_hash,
        click_token_hash=click_hash,
    )
    db.add(row)
    if ok:
        mark_lead_contacted(db, lead_id)
    db.commit()
    db.refresh(row)
    return row
