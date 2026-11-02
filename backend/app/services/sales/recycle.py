from datetime import datetime
import os

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, Query

from app.models.core.user import User
from app.models.ops.document import Document
from app.models.sales.call_log import CallLog
from app.models.sales.campaign import EmailCampaignRecipient
from app.models.sales.client import Client
from app.models.sales.custom_field import CustomFieldDef, CustomFieldValue
from app.models.sales.deal import Deal
from app.models.sales.email_log import EmailLog
from app.models.sales.lead import Lead
from app.models.sales.meeting import Meeting
from app.models.sales.tag import LeadTag
from app.models.sales.whatsapp import WhatsAppMessage
from app.utils.dependencies import apply_company_scope, ensure_company_access


def not_deleted(query: Query) -> Query:
    return query.filter(Lead.deleted_at.is_(None))


def hide_deleted_lead_rows(query: Query, lead_id_column) -> Query:
    """Leave out tasks, follow-ups, messages and so on that belong to a lead in the trash."""
    deleted = select(Lead.id).where(Lead.deleted_at.isnot(None))
    return query.filter(or_(lead_id_column.is_(None), lead_id_column.notin_(deleted)))


def live_lead_query(db: Session, current_user: User) -> Query:
    return not_deleted(apply_company_scope(db.query(Lead), Lead, current_user))


def get_live_lead(db: Session, current_user: User, lead_id: int) -> Lead:
    lead = live_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    ensure_company_access(lead, current_user)
    return lead


def get_trashed_lead(db: Session, current_user: User, lead_id: int) -> Lead:
    lead = (
        apply_company_scope(db.query(Lead), Lead, current_user)
        .filter(Lead.id == lead_id, Lead.deleted_at.isnot(None))
        .first()
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    ensure_company_access(lead, current_user)
    return lead


def soft_delete_lead(lead: Lead) -> None:
    lead.deleted_at = datetime.utcnow()


def restore_lead(lead: Lead) -> None:
    lead.deleted_at = None


def purge_lead(db: Session, lead: Lead) -> None:
    """Delete a lead for good. Activity that belongs only to this lead goes with it; deals and clients stay."""
    for model in (CallLog, Meeting, EmailLog, WhatsAppMessage):
        rows = db.query(model).filter(model.lead_id == lead.id)
        deal_id = getattr(model, "deal_id", None)
        kept = or_(model.client_id.isnot(None), deal_id.isnot(None)) if deal_id is not None else model.client_id.isnot(None)
        rows.filter(kept).update({model.lead_id: None}, synchronize_session=False)
        db.query(model).filter(model.lead_id == lead.id).delete(synchronize_session=False)
    db.query(EmailCampaignRecipient).filter(EmailCampaignRecipient.lead_id == lead.id).update(
        {EmailCampaignRecipient.lead_id: None}, synchronize_session=False
    )
    db.query(Deal).filter(Deal.lead_id == lead.id).update({Deal.lead_id: None}, synchronize_session=False)
    db.query(Client).filter(Client.converted_from_lead_id == lead.id).update(
        {Client.converted_from_lead_id: None}, synchronize_session=False
    )
    db.query(LeadTag).filter(LeadTag.lead_id == lead.id).delete(synchronize_session=False)
    lead_fields = select(CustomFieldDef.id).where(
        CustomFieldDef.company_id == lead.company_id, CustomFieldDef.entity_type == "lead"
    )
    db.query(CustomFieldValue).filter(
        CustomFieldValue.entity_id == lead.id, CustomFieldValue.field_def_id.in_(lead_fields)
    ).delete(synchronize_session=False)
    for document in db.query(Document).filter(Document.lead_id == lead.id).all():
        if document.file_path and os.path.exists(document.file_path):
            os.remove(document.file_path)
        db.delete(document)
    db.delete(lead)
