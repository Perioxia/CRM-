"""
CSV Export API Endpoints
Provides CSV download for leads, clients, and invoices.
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
import csv
import re
import io
from datetime import datetime

from app.database import get_db
from app.utils.dependencies import get_current_user, apply_company_scope, get_active_team_id
from app.services.sales.lead_access import scope_visible_leads
from app.models.core.user import User
from app.models.sales.lead import Lead
from app.models.sales.client import Client
from app.models.finance.invoice import Invoice

router = APIRouter()

_PLAIN_NUMBER = re.compile(r"[+\-]?[\d\s().\-]*")


def _safe_cell(value):
    """Spreadsheets run a cell starting with these as a formula, and outsiders can set a lead's name."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r") and not _PLAIN_NUMBER.fullmatch(value):
        return f"'{value}"
    return value


def make_csv_response(rows: list, headers: list, filename: str):
    """Create a StreamingResponse for CSV download."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_safe_cell(cell) for cell in row])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


def build_csv_string(rows: list, headers: list) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_safe_cell(cell) for cell in row])
    return output.getvalue()


@router.get("/leads")
def export_leads(
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Export leads as CSV"""
    query = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None))
    query = scope_visible_leads(query, db, current_user, active_team_id)
    if status:
        query = query.filter(Lead.status == status)
    leads = query.order_by(Lead.created_at.desc()).all()

    headers = ["ID", "Name", "Email", "Phone", "Company", "Status", "Source", "Service Type", "Created At"]
    rows = [
        [l.id, l.name, l.email, l.phone, l.company, l.status, l.source, l.service_type,
         l.created_at.strftime("%Y-%m-%d %H:%M") if l.created_at else ""]
        for l in leads
    ]
    return make_csv_response(rows, headers, f"leads_export_{datetime.now().strftime('%Y%m%d')}.csv")


@router.get("/clients")
def export_clients(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Export clients as CSV"""
    query = apply_company_scope(db.query(Client), Client, current_user)
    clients = query.order_by(Client.created_at.desc()).all()

    headers = ["ID", "Name", "Email", "Phone", "Company", "Address", "Created At"]
    rows = [
        [c.id, c.name, c.email, c.phone, c.company, c.address,
         c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else ""]
        for c in clients
    ]
    return make_csv_response(rows, headers, f"clients_export_{datetime.now().strftime('%Y%m%d')}.csv")


@router.get("/invoices")
def export_invoices(
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Export invoices as CSV"""
    query = apply_company_scope(db.query(Invoice), Invoice, current_user)
    if status:
        query = query.filter(Invoice.status == status)
    invoices = query.order_by(Invoice.created_at.desc()).all()

    # Resolve client names
    client_map = {}
    client_ids = set(i.client_id for i in invoices if i.client_id)
    if client_ids:
        for c in apply_company_scope(db.query(Client), Client, current_user).filter(Client.id.in_(client_ids)).all():
            client_map[c.id] = c.name

    headers = ["Invoice #", "Client", "Subtotal", "Tax", "Total", "Status", "Issued Date", "Due Date", "Paid Date"]
    rows = [
        [i.invoice_number, client_map.get(i.client_id, "N/A"),
         f"{i.subtotal:.2f}" if i.subtotal else "0.00",
         f"{i.tax:.2f}" if i.tax else "0.00",
         f"{i.total:.2f}" if i.total else "0.00",
         i.status,
         i.issued_date.strftime("%Y-%m-%d") if i.issued_date else "",
         i.due_date.strftime("%Y-%m-%d") if i.due_date else "",
         i.paid_date.strftime("%Y-%m-%d") if i.paid_date else ""]
        for i in invoices
    ]
    return make_csv_response(rows, headers, f"invoices_export_{datetime.now().strftime('%Y%m%d')}.csv")
