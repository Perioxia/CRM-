"""
Global Search API
Searches across leads, clients, and invoices simultaneously.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.database import get_db
from app.utils.dependencies import get_current_user, apply_company_scope, get_active_team_id
from app.models.core.user import User
from app.models.sales.lead import Lead
from app.models.sales.client import Client
from app.models.finance.invoice import Invoice
from app.services.sales.lead_access import scope_visible_leads, user_role

router = APIRouter()


@router.get("")
def global_search(
    q: str = Query(..., min_length=1, description="Search query"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Search across leads, clients, and invoices."""
    term = f"%{q}%"
    results = []
    role = user_role(current_user)
    lead_home = role if role in ("sales", "manager", "md") else "md"

    # Search leads
    lead_q = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None))
    lead_q = scope_visible_leads(lead_q, db, current_user, active_team_id)
    leads = lead_q.filter(
        or_(Lead.name.ilike(term), Lead.email.ilike(term), Lead.company.ilike(term))
    ).limit(5).all()
    for l in leads:
        results.append({
            "type": "lead",
            "id": l.id,
            "title": l.name,
            "subtitle": l.company or l.email or "",
            "status": l.status,
            "url": f"/{lead_home}/leads/{l.id}"
        })

    # Search clients
    client_q = apply_company_scope(db.query(Client), Client, current_user)
    clients = client_q.filter(
        or_(Client.name.ilike(term), Client.email.ilike(term), Client.company.ilike(term))
    ).limit(5).all()
    for c in clients:
        results.append({
            "type": "client",
            "id": c.id,
            "title": c.name,
            "subtitle": c.company or c.email or "",
            "status": "Active",
            "url": f"/{role}/clients/{c.id}" if role in ('sales', 'manager') else f"/md/clients"
        })

    # Search invoices
    inv_q = apply_company_scope(db.query(Invoice), Invoice, current_user)
    invoices = inv_q.filter(
        Invoice.invoice_number.ilike(term)
    ).limit(5).all()
    for inv in invoices:
        results.append({
            "type": "invoice",
            "id": inv.id,
            "title": inv.invoice_number,
            "subtitle": f"₹{inv.total:,.2f}" if inv.total else "₹0.00",
            "status": inv.status,
            "url": f"/{role}/invoices"
        })

    return {"results": results, "total": len(results), "query": q}
