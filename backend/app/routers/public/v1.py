from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.core.company_settings import CompanySettings
from app.models.core.enums import LeadStatus
from app.models.finance.invoice import Invoice, InvoiceItem
from app.models.ops.stock_item import StockItem
from app.models.sales.client import Client
from app.models.sales.deal import Deal
from app.models.sales.lead import Lead
from app.models.sales.pipeline import Pipeline, PipelineStage
from app.services.sales.api_keys import ApiPrincipal
from app.services.sales.pipeline_seed import ensure_default_pipeline
from app.services.finance.gst import compute_gst, normalize_gstin
from app.services.sales.lead_fields import clean_lead_fields
from app.utils.helpers import normalize_email, normalize_phone
from app.utils.api_principal import get_api_principal, require_api_write

router = APIRouter()


def _page(skip: int, limit: int):
    if skip < 0:
        raise HTTPException(status_code=400, detail="skip must be >= 0")
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    return skip, limit


def _lead_json(lead: Lead) -> dict:
    status_val = lead.status.value if hasattr(lead.status, "value") else lead.status
    return {
        "id": lead.id,
        "name": lead.name,
        "email": lead.email,
        "phone": lead.phone,
        "company": lead.company,
        "status": status_val,
        "source": lead.source,
        "service_type": lead.service_type,
        "notes": lead.notes,
        "assigned_to_id": lead.assigned_to_id,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
    }


def _client_json(row: Client) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "email": row.email,
        "phone": row.phone,
        "company": row.company,
        "address": row.address,
        "gstin": row.gstin,
        "assigned_to_id": row.assigned_to_id,
    }


def _deal_json(deal: Deal, stage: Optional[PipelineStage] = None) -> dict:
    return {
        "id": deal.id,
        "title": deal.title,
        "amount": str(deal.amount) if deal.amount is not None else "0",
        "currency": deal.currency,
        "pipeline_id": deal.pipeline_id,
        "stage_id": deal.stage_id,
        "stage_name": stage.name if stage else None,
        "probability": deal.probability,
        "expected_close": deal.expected_close.isoformat() if deal.expected_close else None,
        "lead_id": deal.lead_id,
        "client_id": deal.client_id,
        "assigned_to_id": deal.assigned_to_id,
    }


def _money(value) -> float:
    if value is None:
        return 0.0
    return float(value)


def _invoice_json(inv: Invoice, items: Optional[list] = None) -> dict:
    body = {
        "id": inv.id,
        "invoice_number": inv.invoice_number,
        "client_id": inv.client_id,
        "subtotal": _money(inv.subtotal),
        "tax": _money(inv.tax),
        "cgst": _money(inv.cgst),
        "sgst": _money(inv.sgst),
        "igst": _money(inv.igst),
        "tax_mode": inv.tax_mode,
        "seller_gstin": inv.seller_gstin,
        "buyer_gstin": inv.buyer_gstin,
        "place_of_supply": inv.place_of_supply,
        "discount": _money(inv.discount),
        "total": _money(inv.total),
        "status": inv.status.value if hasattr(inv.status, "value") else inv.status,
        "issued_date": inv.issued_date.isoformat() if inv.issued_date else None,
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "notes": inv.notes,
    }
    if items is not None:
        body["items"] = [
            {
                "id": it.id,
                "description": it.description,
                "quantity": it.quantity,
                "unit_price": _money(it.unit_price),
                "total": _money(it.total),
                "hsn": it.hsn,
            }
            for it in items
        ]
    return body


def _company_lead(db, company_id, lead_id) -> Lead:
    lead = db.query(Lead).filter(
        Lead.id == lead_id, Lead.company_id == company_id, Lead.deleted_at.is_(None)
    ).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Not found")
    return lead


def _company_client(db, company_id, client_id) -> Client:
    row = db.query(Client).filter(Client.id == client_id, Client.company_id == company_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return row


def _company_deal(db, company_id, deal_id) -> Deal:
    deal = db.query(Deal).filter(Deal.id == deal_id, Deal.company_id == company_id).first()
    if deal is None:
        raise HTTPException(status_code=404, detail="Not found")
    return deal


def _company_invoice(db, company_id, invoice_id) -> Invoice:
    inv = db.query(Invoice).filter(Invoice.id == invoice_id, Invoice.company_id == company_id).first()
    if inv is None:
        raise HTTPException(status_code=404, detail="Not found")
    return inv


def _fk_in_company(db, model, company_id, value, field: str):
    if value is None:
        return
    found = db.query(model).filter(model.id == value, model.company_id == company_id).first()
    if found is None:
        raise HTTPException(status_code=400, detail=f"{field} not found in your company")


class LeadWrite(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    source: Optional[str] = None
    service_type: Optional[str] = None
    notes: Optional[str] = None


class LeadPatch(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    status: Optional[LeadStatus] = None
    source: Optional[str] = None
    service_type: Optional[str] = None
    notes: Optional[str] = None


class ClientWrite(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    address: Optional[str] = None
    gstin: Optional[str] = None


class ClientPatch(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    address: Optional[str] = None
    gstin: Optional[str] = None


class DealWrite(BaseModel):
    title: str
    amount: Decimal = Decimal("0")
    currency: str = "INR"
    pipeline_id: Optional[int] = None
    stage_id: Optional[int] = None
    probability: Optional[int] = None
    expected_close: Optional[date] = None
    lead_id: Optional[int] = None
    client_id: Optional[int] = None


class DealPatch(BaseModel):
    title: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    probability: Optional[int] = None
    expected_close: Optional[date] = None
    stage_id: Optional[int] = None
    lead_id: Optional[int] = None
    client_id: Optional[int] = None


class InvoiceItemWrite(BaseModel):
    description: str
    quantity: int = 1
    unit_price: float = 0.0
    stock_item_id: Optional[int] = None
    hsn: Optional[str] = None


class InvoiceWrite(BaseModel):
    client_id: int
    items: list[InvoiceItemWrite]
    invoice_number: Optional[str] = None
    issued_date: Optional[date] = None
    due_date: Optional[date] = None
    due_days: Optional[int] = None
    notes: Optional[str] = None
    tax: Optional[float] = None
    discount: Optional[float] = None


@router.get("/leads")
def list_leads(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: Session = Depends(get_db),
    principal: ApiPrincipal = Depends(get_api_principal),
):
    skip, limit = _page(skip, limit)
    q = db.query(Lead).filter(Lead.company_id == principal.company_id, Lead.deleted_at.is_(None))
    total = q.count()
    rows = q.order_by(Lead.created_at.desc()).offset(skip).limit(limit).all()
    return {"items": [_lead_json(r) for r in rows], "total": total, "skip": skip, "limit": limit}


@router.get("/leads/{lead_id}")
def get_lead(lead_id: int, db: Session = Depends(get_db), principal: ApiPrincipal = Depends(get_api_principal)):
    return _lead_json(_company_lead(db, principal.company_id, lead_id))


def _clean_lead_body(values: dict) -> dict:
    try:
        return clean_lead_fields(values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/leads", status_code=status.HTTP_201_CREATED)
def create_lead(body: LeadWrite, db: Session = Depends(get_db), principal: ApiPrincipal = Depends(require_api_write)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    cleaned = _clean_lead_body(body.model_dump())
    lead = Lead(
        company_id=principal.company_id,
        name=cleaned["name"],
        email=normalize_email(cleaned.get("email")),
        phone=normalize_phone(cleaned.get("phone")),
        company=cleaned.get("company"),
        source=cleaned.get("source"),
        service_type=cleaned.get("service_type"),
        notes=body.notes,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return _lead_json(lead)


@router.patch("/leads/{lead_id}")
def patch_lead(
    lead_id: int,
    body: LeadPatch,
    db: Session = Depends(get_db),
    principal: ApiPrincipal = Depends(require_api_write),
):
    lead = _company_lead(db, principal.company_id, lead_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data and not (data["name"] or "").strip():
        raise HTTPException(status_code=400, detail="name is required")
    data |= _clean_lead_body(data)
    if "email" in data:
        data["email"] = normalize_email(data["email"])
    if "phone" in data:
        data["phone"] = normalize_phone(data["phone"])
    for field, value in data.items():
        setattr(lead, field, value)
    db.commit()
    db.refresh(lead)
    return _lead_json(lead)


@router.get("/clients")
def list_clients(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: Session = Depends(get_db),
    principal: ApiPrincipal = Depends(get_api_principal),
):
    skip, limit = _page(skip, limit)
    q = db.query(Client).filter(Client.company_id == principal.company_id)
    total = q.count()
    rows = q.order_by(Client.created_at.desc()).offset(skip).limit(limit).all()
    return {"items": [_client_json(r) for r in rows], "total": total, "skip": skip, "limit": limit}


@router.get("/clients/{client_id}")
def get_client(client_id: int, db: Session = Depends(get_db), principal: ApiPrincipal = Depends(get_api_principal)):
    return _client_json(_company_client(db, principal.company_id, client_id))


@router.post("/clients", status_code=status.HTTP_201_CREATED)
def create_client(body: ClientWrite, db: Session = Depends(get_db), principal: ApiPrincipal = Depends(require_api_write)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        gstin = normalize_gstin(body.gstin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    row = Client(
        company_id=principal.company_id,
        name=name,
        email=body.email,
        phone=body.phone,
        company=body.company,
        address=body.address,
        gstin=gstin,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _client_json(row)


@router.patch("/clients/{client_id}")
def patch_client(
    client_id: int,
    body: ClientPatch,
    db: Session = Depends(get_db),
    principal: ApiPrincipal = Depends(require_api_write),
):
    row = _company_client(db, principal.company_id, client_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data and not (data["name"] or "").strip():
        raise HTTPException(status_code=400, detail="name is required")
    if "gstin" in data:
        try:
            data["gstin"] = normalize_gstin(data["gstin"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    for field, value in data.items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return _client_json(row)


def _resolve_pipeline(db, company_id, pipeline_id, stage_id):
    if pipeline_id is not None:
        pipe = db.query(Pipeline).filter(Pipeline.id == pipeline_id, Pipeline.company_id == company_id).first()
        if pipe is None:
            raise HTTPException(status_code=400, detail="pipeline_id not found in your company")
    if pipeline_id is None or stage_id is None:
        default_pipeline = ensure_default_pipeline(db, company_id)
        pipeline_id = pipeline_id or default_pipeline.id
        if stage_id is None:
            first = (
                db.query(PipelineStage)
                .filter(PipelineStage.company_id == company_id, PipelineStage.pipeline_id == pipeline_id)
                .order_by(PipelineStage.position)
                .first()
            )
            if first is None:
                raise HTTPException(status_code=400, detail="pipeline has no stages")
            stage_id = first.id
    stage = db.query(PipelineStage).filter(
        PipelineStage.id == stage_id, PipelineStage.company_id == company_id
    ).first()
    if stage is None or stage.pipeline_id != pipeline_id:
        raise HTTPException(status_code=400, detail="stage does not belong to pipeline")
    return pipeline_id, stage


@router.get("/deals")
def list_deals(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: Session = Depends(get_db),
    principal: ApiPrincipal = Depends(get_api_principal),
):
    skip, limit = _page(skip, limit)
    q = db.query(Deal).filter(Deal.company_id == principal.company_id)
    total = q.count()
    rows = q.order_by(Deal.created_at.desc()).offset(skip).limit(limit).all()
    stage_ids = {d.stage_id for d in rows}
    stages = {
        s.id: s
        for s in db.query(PipelineStage).filter(PipelineStage.id.in_(stage_ids or [-1])).all()
    }
    return {
        "items": [_deal_json(d, stages.get(d.stage_id)) for d in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get("/deals/{deal_id}")
def get_deal(deal_id: int, db: Session = Depends(get_db), principal: ApiPrincipal = Depends(get_api_principal)):
    deal = _company_deal(db, principal.company_id, deal_id)
    stage = db.query(PipelineStage).filter(PipelineStage.id == deal.stage_id).first()
    return _deal_json(deal, stage)


@router.post("/deals", status_code=status.HTTP_201_CREATED)
def create_deal(body: DealWrite, db: Session = Depends(get_db), principal: ApiPrincipal = Depends(require_api_write)):
    if body.amount is not None and body.amount < 0:
        raise HTTPException(status_code=400, detail="amount must be >= 0")
    if body.probability is not None and not (0 <= body.probability <= 100):
        raise HTTPException(status_code=400, detail="probability must be between 0 and 100")
    _fk_in_company(db, Lead, principal.company_id, body.lead_id, "lead_id")
    _fk_in_company(db, Client, principal.company_id, body.client_id, "client_id")
    pipeline_id, stage = _resolve_pipeline(db, principal.company_id, body.pipeline_id, body.stage_id)
    deal = Deal(
        company_id=principal.company_id,
        title=body.title,
        amount=body.amount if body.amount is not None else Decimal("0"),
        currency=body.currency or "INR",
        pipeline_id=pipeline_id,
        stage_id=stage.id,
        probability=body.probability,
        expected_close=body.expected_close,
        lead_id=body.lead_id,
        client_id=body.client_id,
    )
    db.add(deal)
    db.commit()
    db.refresh(deal)
    return _deal_json(deal, stage)


@router.patch("/deals/{deal_id}")
def patch_deal(
    deal_id: int,
    body: DealPatch,
    db: Session = Depends(get_db),
    principal: ApiPrincipal = Depends(require_api_write),
):
    deal = _company_deal(db, principal.company_id, deal_id)
    data = body.model_dump(exclude_unset=True)
    if "amount" in data and data["amount"] is not None and data["amount"] < 0:
        raise HTTPException(status_code=400, detail="amount must be >= 0")
    if "probability" in data and data["probability"] is not None and not (0 <= data["probability"] <= 100):
        raise HTTPException(status_code=400, detail="probability must be between 0 and 100")
    _fk_in_company(db, Lead, principal.company_id, data.get("lead_id"), "lead_id")
    _fk_in_company(db, Client, principal.company_id, data.get("client_id"), "client_id")
    stage = db.query(PipelineStage).filter(PipelineStage.id == deal.stage_id).first()
    if "stage_id" in data and data["stage_id"] is not None:
        stage_row = db.query(PipelineStage).filter(
            PipelineStage.id == data["stage_id"],
            PipelineStage.company_id == principal.company_id,
            PipelineStage.pipeline_id == deal.pipeline_id,
        ).first()
        if stage_row is None:
            raise HTTPException(status_code=400, detail="stage does not belong to pipeline")
        stage = stage_row
    for field, value in data.items():
        setattr(deal, field, value)
    db.commit()
    db.refresh(deal)
    return _deal_json(deal, stage)


@router.get("/invoices")
def list_invoices(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: Session = Depends(get_db),
    principal: ApiPrincipal = Depends(get_api_principal),
):
    skip, limit = _page(skip, limit)
    q = db.query(Invoice).filter(Invoice.company_id == principal.company_id)
    total = q.count()
    rows = q.order_by(Invoice.created_at.desc()).offset(skip).limit(limit).all()
    return {"items": [_invoice_json(r) for r in rows], "total": total, "skip": skip, "limit": limit}


@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: int, db: Session = Depends(get_db), principal: ApiPrincipal = Depends(get_api_principal)):
    inv = _company_invoice(db, principal.company_id, invoice_id)
    items = db.query(InvoiceItem).filter(InvoiceItem.invoice_id == inv.id).all()
    return _invoice_json(inv, items)


@router.post("/invoices", status_code=status.HTTP_201_CREATED)
def create_invoice(
    body: InvoiceWrite,
    db: Session = Depends(get_db),
    principal: ApiPrincipal = Depends(require_api_write),
):
    if not body.items:
        raise HTTPException(status_code=400, detail="At least one line item is required")
    if body.tax is not None and body.tax < 0:
        raise HTTPException(status_code=400, detail="tax must be >= 0")
    if body.discount is not None and body.discount < 0:
        raise HTTPException(status_code=400, detail="discount must be >= 0")
    if body.due_days is not None and body.due_days < 0:
        raise HTTPException(status_code=400, detail="due_days must be >= 0")
    client_row = db.query(Client).filter(
        Client.id == body.client_id, Client.company_id == principal.company_id
    ).first()
    if client_row is None:
        raise HTTPException(status_code=400, detail="client_id not found in your company")
    for item in body.items:
        if (item.quantity or 0) <= 0:
            raise HTTPException(status_code=400, detail="Quantity must be greater than zero for all line items")
        if (item.unit_price or 0) < 0:
            raise HTTPException(status_code=400, detail="Unit price cannot be negative")
        _fk_in_company(db, StockItem, principal.company_id, item.stock_item_id, "stock_item_id")

    settings = db.query(CompanySettings).filter(CompanySettings.company_id == principal.company_id).first()
    tax_rate = 18.0
    if settings:
        tax_rate = getattr(settings, "tax_rate", 18.0) or 18.0

    if body.invoice_number:
        invoice_number = body.invoice_number.strip()
        existing = db.query(Invoice).filter(
            Invoice.company_id == principal.company_id,
            Invoice.invoice_number == invoice_number,
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Invoice number already exists for this company")
    else:
        invoice_number = f"DRAFT-{uuid4().hex[:8].upper()}"

    subtotal = 0.0
    for it in body.items:
        subtotal += (it.quantity or 0) * (it.unit_price or 0)
    try:
        gst = compute_gst(
            subtotal=subtotal,
            rate_percent=tax_rate,
            seller_gstin=getattr(settings, "gst_number", None) if settings else None,
            buyer_gstin=getattr(client_row, "gstin", None),
            tax_override=body.tax,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    tax = gst.tax
    discount_val = round(body.discount, 2) if body.discount else 0.0
    total = subtotal + tax - discount_val
    issued = body.issued_date or datetime.now(timezone.utc).date()
    due = body.due_date
    if not due and body.due_days is not None:
        due = issued + timedelta(days=body.due_days)

    requested_stock: dict[int, int] = {}
    for it in body.items:
        if it.stock_item_id is not None:
            requested_stock[it.stock_item_id] = requested_stock.get(it.stock_item_id, 0) + int(it.quantity or 0)
    stock_map = {}
    if requested_stock:
        rows = (
            db.query(StockItem)
            .filter(StockItem.company_id == principal.company_id, StockItem.id.in_(requested_stock.keys()))
            .with_for_update()
            .all()
        )
        stock_map = {s.id: s for s in rows}
        for sid, qty in requested_stock.items():
            if sid not in stock_map:
                raise HTTPException(status_code=400, detail="stock_item_id not found in your company")
            if int(stock_map[sid].quantity or 0) < qty:
                raise HTTPException(status_code=400, detail="Insufficient stock")
            stock_map[sid].quantity = int(stock_map[sid].quantity or 0) - qty

    invoice = Invoice(
        company_id=principal.company_id,
        invoice_number=invoice_number,
        client_id=body.client_id,
        subtotal=subtotal,
        tax=tax,
        discount=discount_val,
        total=total,
        status="Draft",
        issued_date=issued,
        due_date=due,
        notes=body.notes,
        created_by_id=None,
        cgst=gst.cgst,
        sgst=gst.sgst,
        igst=gst.igst,
        seller_gstin=gst.seller_gstin,
        buyer_gstin=gst.buyer_gstin,
        place_of_supply=gst.place_of_supply,
        tax_mode=gst.tax_mode,
    )
    db.add(invoice)
    db.flush()
    for it in body.items:
        line_total = (it.quantity or 0) * (it.unit_price or 0)
        db.add(InvoiceItem(
            company_id=principal.company_id,
            invoice_id=invoice.id,
            description=it.description,
            quantity=it.quantity or 1,
            unit_price=it.unit_price or 0.0,
            total=line_total,
            hsn=(it.hsn or "").strip() or None,
        ))
    db.commit()
    db.refresh(invoice)
    items = db.query(InvoiceItem).filter(InvoiceItem.invoice_id == invoice.id).all()
    return _invoice_json(invoice, items)
