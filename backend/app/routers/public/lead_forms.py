from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.core.company import Company
from app.models.core.enums import CompanyStatus, LeadStatus
from app.models.sales.lead import Lead
from app.models.sales.client import Client
from app.models.sales.lead_form import LeadForm
from app.models.sales.note import Note
from app.services.sales.cadence import enroll_lead_in_default_cadence
from app.services.sales.lead_fields import clean_lead_fields
from app.services.sales.territory import assign_lead_by_territory
from app.services.sales.workflow import run_workflows
from app.utils.helpers import normalize_email, normalize_phone, phone_variants
from app.utils.notify import send_notification
from app.utils.rate_limit import public_form_limiter

router = APIRouter()


class PublicSubmit(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    service_type: Optional[str] = None
    notes: Optional[str] = None
    website: Optional[str] = None


def _status_value(company: Company) -> str:
    raw = company.status
    return str(getattr(raw, "value", raw) or "").lower()


def _company_accepts_public_leads(company: Company) -> bool:
    value = _status_value(company)
    if value not in (CompanyStatus.ACTIVE.value, CompanyStatus.TRIAL.value):
        return False
    if value == CompanyStatus.TRIAL.value and company.trial_ends_at is not None:
        end = company.trial_ends_at
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if end < datetime.now(timezone.utc):
            return False
    return True


def _public_form(db: Session, slug: str) -> LeadForm:
    form = db.query(LeadForm).filter(LeadForm.slug == slug).first()
    if form is None or not form.is_active:
        raise HTTPException(status_code=404, detail="Form not found")
    company = db.query(Company).filter(Company.id == form.company_id).first()
    if company is None or not _company_accepts_public_leads(company):
        raise HTTPException(status_code=404, detail="Form not found")
    return form


def _same_contact(model, email: Optional[str], phone: Optional[str]):
    clauses = []
    if email:
        clauses.append(func.lower(model.email) == email)
    if phone:
        clauses.append(model.phone.in_(phone_variants(phone)))
    return or_(*clauses)


def _record_repeat_submit(db: Session, form: LeadForm, email: Optional[str], phone: Optional[str], notes: Optional[str]) -> bool:
    """Someone we already know filled in the form again: note it instead of adding a duplicate."""
    lead = db.query(Lead).filter(
        Lead.company_id == form.company_id, Lead.deleted_at.is_(None), _same_contact(Lead, email, phone)
    ).first()
    client = None
    if lead is None:
        client = db.query(Client).filter(Client.company_id == form.company_id, _same_contact(Client, email, phone)).first()
    record = lead or client
    if record is None:
        return False
    db.add(Note(
        company_id=form.company_id,
        content=f"Filled in the website form again. {notes or ''}".strip(),
        lead_id=lead.id if lead else None,
        client_id=client.id if client else None,
    ))
    if record.assigned_to_id:
        send_notification(
            db,
            record.assigned_to_id,
            title=f"Repeat website form: {record.name}",
            message=notes or "This contact filled in the website form again.",
            type="info",
            category="leads",
        )
    db.commit()
    return True


def ingest_public_lead(
    db: Session,
    form: LeadForm,
    *,
    name: Optional[str],
    phone: Optional[str],
    email: Optional[str],
    company: Optional[str] = None,
    service_type: Optional[str] = None,
    notes: Optional[str] = None,
    source: Optional[str] = None,
    website: Optional[str] = None,
) -> dict:
    if (website or "").strip():
        return {"ok": True}

    if not (name or "").strip() or not ((phone or "").strip() or (email or "").strip()):
        raise HTTPException(status_code=400, detail="name and phone or email are required")
    try:
        cleaned = clean_lead_fields({
            "name": name, "phone": phone, "email": email, "company": company,
            "service_type": service_type, "source": source,
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    name = cleaned["name"]
    phone = normalize_phone(cleaned.get("phone"))
    email = normalize_email(cleaned.get("email"))
    company = cleaned.get("company")
    service_type = cleaned.get("service_type")
    source = cleaned.get("source")
    notes = (notes or "").strip() or None

    if _record_repeat_submit(db, form, email, phone, notes):
        return {"ok": True}

    lead = Lead(
        company_id=form.company_id,
        name=name,
        email=email,
        phone=phone,
        company=company or None,
        source=source or form.default_source or "Website",
        service_type=service_type or None,
        notes=notes,
        status=LeadStatus.ACTIVE,
        assigned_to_id=None,
        created_by_id=None,
        team_id=form.default_team_id,
    )
    db.add(lead)
    db.flush()
    assign_lead_by_territory(db, lead)
    db.flush()
    enroll_lead_in_default_cadence(db, lead)
    run_workflows(db, "lead_created", lead=lead)
    db.commit()
    from app.services.sales.outbound_webhooks import emit_event
    emit_event(db, form.company_id, "lead.created", {"id": lead.id, "name": lead.name})
    return {"ok": True}


@router.get("/{slug}")
def get_public_form(slug: str, db: Session = Depends(get_db)):
    form = _public_form(db, slug)
    company = db.query(Company).filter(Company.id == form.company_id).first()
    return {
        "headline": form.headline,
        "company_name": company.name if company else None,
        "name": form.name,
    }


@router.post("/{slug}/submit", status_code=status.HTTP_201_CREATED)
def submit_public_form(slug: str, payload: PublicSubmit, request: Request, db: Session = Depends(get_db)):
    form = _public_form(db, slug)
    public_form_limiter.check(request, f"lead-form:{slug}", max_attempts=10, window_seconds=600)
    return ingest_public_lead(
        db,
        form,
        name=payload.name,
        phone=payload.phone,
        email=payload.email,
        company=payload.company,
        service_type=payload.service_type,
        notes=payload.notes,
        website=payload.website,
    )
