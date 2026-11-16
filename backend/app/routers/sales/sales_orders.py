from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.core.user import User
from app.models.sales.sales_order import SalesOrder
from app.services.sales.record_access import ensure_can_view_client_record, scope_client_records
from app.services.sales.sales_order_lifecycle import convert_sales_order_to_invoice
from app.utils.dependencies import apply_company_scope, ensure_company_access, get_active_team_id, get_current_user

router = APIRouter()


def _money(value) -> str:
    return str(Decimal(value or 0).quantize(Decimal("0.01")))


def _serialize(order: SalesOrder) -> dict:
    return {
        "id": order.id,
        "order_number": order.order_number,
        "quote_id": order.quote_id,
        "deal_id": order.deal_id,
        "client_id": order.client_id,
        "status": order.status.value if hasattr(order.status, "value") else order.status,
        "subtotal": _money(order.subtotal),
        "tax": _money(order.tax),
        "total": _money(order.total),
        "cgst": _money(order.cgst),
        "sgst": _money(order.sgst),
        "igst": _money(order.igst),
        "tax_mode": order.tax_mode,
        "seller_gstin": order.seller_gstin,
        "buyer_gstin": order.buyer_gstin,
        "place_of_supply": order.place_of_supply,
        "notes": order.notes,
        "invoice_id": order.invoice_id,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "items": [
            {
                "description": it.description,
                "quantity": it.quantity,
                "unit_price": _money(it.unit_price),
                "total": _money(it.total),
                "product_id": it.product_id,
                "hsn": it.hsn,
                "tax_rate": _money(it.tax_rate),
                "tax": _money(it.tax),
            }
            for it in order.items
        ],
    }


def _get_order(db: Session, current_user: User, order_id: int, active_team_id: Optional[int]) -> SalesOrder:
    order = apply_company_scope(db.query(SalesOrder), SalesOrder, current_user).filter(
        SalesOrder.id == order_id
    ).first()
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    ensure_company_access(order, current_user)
    ensure_can_view_client_record(db, order, current_user, active_team_id, detail="You do not have access to this sales order")
    return order


@router.get("")
def list_sales_orders(
    deal_id: Optional[int] = Query(None),
    client_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    query = scope_client_records(
        apply_company_scope(db.query(SalesOrder), SalesOrder, current_user), SalesOrder, current_user, active_team_id
    )
    if deal_id is not None:
        query = query.filter(SalesOrder.deal_id == deal_id)
    if client_id is not None:
        query = query.filter(SalesOrder.client_id == client_id)
    if status is not None:
        query = query.filter(SalesOrder.status == status)
    orders = query.order_by(SalesOrder.created_at.desc()).all()
    return {"items": [_serialize(o) for o in orders], "total": len(orders)}


@router.get("/{order_id:int}")
def get_sales_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    return _serialize(_get_order(db, current_user, order_id, active_team_id))


@router.post("/{order_id:int}/invoice")
def invoice_sales_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    order = _get_order(db, current_user, order_id, active_team_id)
    order = convert_sales_order_to_invoice(db, order)
    return _serialize(order)
