"""Sales order lifecycle: create from quote, convert to invoice."""
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.core.enums import InvoiceStatus, SalesOrderStatus
from app.models.finance.invoice import Invoice, InvoiceItem
from app.models.ops.stock_item import StockItem
from app.models.sales.product import Product
from app.models.sales.quote import Quote
from app.models.sales.sales_order import SalesOrder, SalesOrderItem
from app.services.sales.product_lines import ResolvedSaleLine, deduct_stock
from app.utils.notify import notify_role_users


def _actor(order: SalesOrder):
    return SimpleNamespace(id=order.created_by_id, company_id=order.company_id, role="sales")


def _notify_low_stock(db: Session, company_id: int, low_stock_alert_ids: set[int]) -> None:
    if not low_stock_alert_ids:
        return
    stock_rows = (
        db.query(StockItem)
        .filter(StockItem.company_id == company_id, StockItem.id.in_(low_stock_alert_ids))
        .all()
    )
    stock_map = {s.id: s for s in stock_rows}
    role_map = {
        "purchase": "/purchase/stock",
        "md": "/md/stock",
        "manager": "/manager/stock",
        "sales": "/sales/stock",
    }
    for stock_id in low_stock_alert_ids:
        stock_item = stock_map.get(stock_id)
        if stock_item is None:
            continue
        for target_role, link in role_map.items():
            notify_role_users(
                db,
                company_id=company_id,
                role=target_role,
                title=f"Low Stock: {stock_item.name}",
                message=f"Only {stock_item.quantity} {stock_item.unit}(s) remaining.",
                type="warning",
                link=link,
                category="inventory",
                dedupe_window_seconds=6 * 60 * 60,
                dedupe_match_message=False,
                skip_if_unread_duplicate=True,
            )


def create_sales_order_from_quote(db: Session, quote: Quote) -> SalesOrder:
    order = SalesOrder(
        company_id=quote.company_id,
        order_number=f"SO-{uuid4().hex[:8].upper()}",
        quote_id=quote.id,
        deal_id=quote.deal_id,
        client_id=quote.client_id,
        status=SalesOrderStatus.OPEN,
        subtotal=quote.subtotal,
        tax=quote.tax or 0,
        total=quote.total,
        notes=quote.notes,
        created_by_id=quote.created_by_id,
        cgst=quote.cgst,
        sgst=quote.sgst,
        igst=quote.igst,
        seller_gstin=quote.seller_gstin,
        buyer_gstin=quote.buyer_gstin,
        place_of_supply=quote.place_of_supply,
        tax_mode=quote.tax_mode,
    )
    db.add(order)
    db.flush()
    for item in quote.items:
        db.add(SalesOrderItem(
            company_id=quote.company_id,
            sales_order_id=order.id,
            description=item.description,
            quantity=item.quantity,
            unit_price=item.unit_price,
            total=item.total,
            product_id=item.product_id,
            hsn=item.hsn,
            tax_rate=item.tax_rate,
            tax=item.tax,
        ))
    quote.sales_order_id = order.id
    return order


def convert_sales_order_to_invoice(db: Session, order: SalesOrder) -> SalesOrder:
    status = order.status.value if hasattr(order.status, "value") else str(order.status)
    if status != SalesOrderStatus.OPEN.value:
        raise HTTPException(status_code=400, detail="Only open sales orders can be invoiced")
    if order.invoice_id is not None:
        raise HTTPException(status_code=400, detail="Sales order already invoiced")

    actor = _actor(order)
    invoice = Invoice(
        company_id=order.company_id,
        invoice_number=f"INV-{uuid4().hex[:8].upper()}",
        client_id=order.client_id,
        subtotal=order.subtotal,
        tax=order.tax or 0,
        discount=0,
        total=order.total,
        status=InvoiceStatus.PENDING,
        notes=order.notes,
        created_by_id=order.created_by_id,
        cgst=order.cgst,
        sgst=order.sgst,
        igst=order.igst,
        seller_gstin=order.seller_gstin,
        buyer_gstin=order.buyer_gstin,
        place_of_supply=order.place_of_supply,
        tax_mode=order.tax_mode,
    )
    db.add(invoice)
    db.flush()

    convert_lines: list[ResolvedSaleLine] = []
    for item in order.items:
        deduct_id = None
        if item.product_id:
            product = db.query(Product).filter(
                Product.id == item.product_id,
                Product.company_id == order.company_id,
            ).first()
            if product is not None:
                deduct_id = product.stock_item_id
        convert_lines.append(ResolvedSaleLine(
            description=item.description,
            quantity=item.quantity,
            unit_price=item.unit_price,
            hsn=item.hsn,
            tax_rate=item.tax_rate or 0,
            line_amount=item.total,
            tax=float(item.tax or 0),
            product_id=item.product_id,
            deduct_stock_item_id=deduct_id,
        ))
        db.add(InvoiceItem(
            company_id=order.company_id,
            invoice_id=invoice.id,
            description=item.description,
            quantity=item.quantity,
            unit_price=item.unit_price,
            total=item.total,
            product_id=item.product_id,
            stock_item_id=deduct_id,
            hsn=item.hsn,
            tax_rate=item.tax_rate,
            tax=item.tax,
        ))

    low_ids = deduct_stock(db, actor, convert_lines, order_number=invoice.invoice_number, invoice_id=invoice.id)
    _notify_low_stock(db, order.company_id, low_ids)

    order.invoice_id = invoice.id
    order.status = SalesOrderStatus.INVOICED

    quote = db.query(Quote).filter(Quote.id == order.quote_id).first()
    if quote is not None:
        quote.invoice_id = invoice.id

    db.commit()
    db.refresh(order)
    return order
