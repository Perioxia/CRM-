from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.core.user import User
from app.models.finance.invoice import Invoice, InvoiceItem
from app.models.finance.ledger import LedgerEntry
from app.models.ops.stock_item import StockItem
from app.models.sales.product import Product
from app.services.sales.price_books import resolve_product_unit_price
from app.services.finance.gst import line_tax
from app.utils.dependencies import apply_company_scope


@dataclass(frozen=True)
class ResolvedSaleLine:
    description: str
    quantity: int
    unit_price: Decimal
    hsn: Optional[str]
    tax_rate: Decimal
    line_amount: Decimal
    tax: float
    product_id: Optional[int]
    deduct_stock_item_id: Optional[int]


def _attr(item, name, default=None):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def resolve_sale_lines(
    db: Session,
    *,
    company_id: int,
    items: list,
    company_tax_rate: float,
    price_book_id: Optional[int] = None,
) -> list[ResolvedSaleLine]:
    resolved: list[ResolvedSaleLine] = []
    for item in items:
        product_id = _attr(item, "product_id")
        product = None
        if product_id is not None:
            product = (
                db.query(Product)
                .filter(Product.id == product_id, Product.company_id == company_id)
                .first()
            )
            if product is None:
                raise ValueError("product_id not found in your company")
            if not product.is_active:
                raise ValueError("product is inactive")

        req_description = str(_attr(item, "description") or "").strip()
        req_price = _attr(item, "unit_price")
        req_hsn = _attr(item, "hsn")
        if isinstance(req_hsn, str):
            req_hsn = req_hsn.strip() or None

        description = req_description or (product.name if product is not None else "")
        if product is not None:
            unit_price = resolve_product_unit_price(
                db,
                company_id=company_id,
                product=product,
                price_book_id=price_book_id,
                explicit_price=req_price,
            )
        else:
            unit_price = req_price
        hsn = req_hsn if req_hsn is not None else (product.hsn if product is not None else None)
        tax_rate = Decimal(str(product.tax_rate if product is not None else company_tax_rate))
        qty = int(_attr(item, "quantity") or 0)
        price = Decimal(str(unit_price or 0))
        amount = Decimal(qty) * price
        # Only physical goods deduct inventory. Services/subscriptions never do.
        legacy_stock = _attr(item, "stock_item_id")
        kind = (getattr(product, "kind", None) or "goods") if product is not None else "goods"
        if product is not None and kind == "goods" and product.stock_item_id is not None:
            deduct = product.stock_item_id
        elif product is None:
            deduct = legacy_stock
        else:
            deduct = None
        resolved.append(ResolvedSaleLine(
            description=description,
            quantity=qty,
            unit_price=price,
            hsn=hsn,
            tax_rate=tax_rate,
            line_amount=amount,
            tax=line_tax(amount, tax_rate),
            product_id=product.id if product is not None else None,
            deduct_stock_item_id=int(deduct) if deduct is not None else None,
        ))
    return resolved


def record_stock_out(db: Session, *, company_id: int, user_id: Optional[int], stock_item: StockItem,
                     quantity: int, amount, order_number: Optional[str], invoice_id: Optional[int]) -> None:
    """Stock Register row for a sale, in the same shape as the rows the Stock page writes.
    invoice_id lets the row follow the order when purchase gives it its final number."""
    db.add(LedgerEntry(
        company_id=company_id,
        ledger_slug="stock_register",
        data={
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "product": stock_item.name,
            "category": stock_item.category or "",
            "brand": "",
            "qty_in": 0,
            "qty_out": quantity,
            "purchase_rate": float(stock_item.unit_price or 0),
            "sale_rate": round(float(amount) / quantity, 2) if quantity else 0,
            "remarks": f"Sold on order {order_number}" if order_number else "Sold",
            "invoice_id": invoice_id,
        },
        created_by=user_id,
    ))


def deduct_stock(db: Session, current_user: User, lines: list[ResolvedSaleLine],
                 order_number: Optional[str] = None, invoice_id: Optional[int] = None) -> set[int]:
    requested: dict[int, int] = {}
    amounts: dict[int, Decimal] = {}
    for line in lines:
        if line.deduct_stock_item_id is None:
            continue
        requested[line.deduct_stock_item_id] = requested.get(line.deduct_stock_item_id, 0) + int(line.quantity or 0)
        amounts[line.deduct_stock_item_id] = amounts.get(line.deduct_stock_item_id, Decimal("0")) + Decimal(str(line.line_amount))
    if not requested:
        return set()
    rows = (
        apply_company_scope(db.query(StockItem), StockItem, current_user)
        .filter(StockItem.id.in_(requested.keys()))
        .with_for_update()
        .all()
    )
    stock_map = {s.id: s for s in rows}
    missing = [sid for sid in requested if sid not in stock_map]
    if missing:
        raise HTTPException(status_code=404, detail=f"Stock item(s) not found: {missing}")
    for sid, qty in requested.items():
        if int(stock_map[sid].quantity or 0) < qty:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient stock for '{stock_map[sid].name}'. Available: {stock_map[sid].quantity}, requested: {qty}",
            )
    low: set[int] = set()
    for sid, qty in requested.items():
        stock_item = stock_map[sid]
        stock_item.quantity = int(stock_item.quantity or 0) - qty
        stock_item.updated_by_id = current_user.id
        record_stock_out(db, company_id=stock_item.company_id, user_id=current_user.id, stock_item=stock_item,
                         quantity=qty, amount=amounts[sid], order_number=order_number, invoice_id=invoice_id)
        if int(stock_item.quantity or 0) <= int(stock_item.reorder_level or 0):
            low.add(sid)
    return low


def rename_stock_out(db: Session, company_id: int, invoice_id: int, order_number: str) -> None:
    """When a draft order gets its final number, its Stock Register rows show that number too."""
    rows = (
        db.query(LedgerEntry)
        .filter(
            LedgerEntry.company_id == company_id,
            LedgerEntry.ledger_slug == "stock_register",
            LedgerEntry.data["invoice_id"].as_integer() == invoice_id,
        )
        .all()
    )
    for row in rows:
        row.data = {**row.data, "remarks": f"Sold on order {order_number}"}


def return_stock(db: Session, invoice: Invoice, user_id: Optional[int]) -> None:
    """Put back what a rejected order took. Lines from before stock was recorded on them have nothing to return."""
    requested: dict[int, int] = {}
    for item in db.query(InvoiceItem).filter(InvoiceItem.invoice_id == invoice.id, InvoiceItem.stock_item_id.isnot(None)):
        requested[item.stock_item_id] = requested.get(item.stock_item_id, 0) + int(item.quantity or 0)
    if not requested:
        return
    rows = (
        db.query(StockItem)
        .filter(StockItem.company_id == invoice.company_id, StockItem.id.in_(requested.keys()))
        .with_for_update()
        .all()
    )
    for stock_item in rows:
        qty = requested[stock_item.id]
        stock_item.quantity = int(stock_item.quantity or 0) + qty
        stock_item.updated_by_id = user_id
        db.add(LedgerEntry(
            company_id=invoice.company_id,
            ledger_slug="stock_register",
            data={
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "product": stock_item.name,
                "category": stock_item.category or "",
                "brand": "",
                "qty_in": qty,
                "qty_out": 0,
                "purchase_rate": float(stock_item.unit_price or 0),
                "sale_rate": 0,
                "remarks": f"Returned: order {invoice.invoice_number} rejected",
                "invoice_id": invoice.id,
            },
            created_by=user_id,
        ))
