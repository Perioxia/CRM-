"""Track and undo CSV import batches."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.core.enums import LeadStatus
from app.models.finance.invoice import Invoice
from app.models.sales.client import Client
from app.models.sales.deal import Deal
from app.models.sales.import_batch import ImportBatch, ImportBatchItem
from app.models.sales.lead import Lead
from app.models.sales.quote import Quote
from app.models.sales.sales_order import SalesOrder
from app.models.sales.task import Task


def record_batch(
    db: Session,
    *,
    company_id: int,
    entity_type: str,
    entity_ids: list[int],
    created_by_id: int | None,
) -> ImportBatch:
    batch = ImportBatch(
        company_id=company_id,
        entity_type=entity_type,
        created_by_id=created_by_id,
    )
    db.add(batch)
    db.flush()
    for entity_id in entity_ids:
        db.add(ImportBatchItem(batch_id=batch.id, entity_id=entity_id))
    db.flush()
    return batch


def last_batch(db: Session, company_id: int, created_by_ids: list[int] | None = None) -> ImportBatch | None:
    """Newest batch not yet undone. created_by_ids limits it to those importers; None means anyone."""
    query = db.query(ImportBatch).filter(
        ImportBatch.company_id == company_id,
        ImportBatch.undone_at.is_(None),
    )
    if created_by_ids is not None:
        query = query.filter(ImportBatch.created_by_id.in_(created_by_ids))
    return query.order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc()).first()


def serialize_batch(batch: ImportBatch | None) -> dict | None:
    if batch is None:
        return None
    return {
        "id": batch.id,
        "entity_type": batch.entity_type,
        "item_count": len(batch.items),
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
    }


def _client_in_use(db: Session, client_id: int) -> bool:
    if db.query(Invoice.id).filter(Invoice.client_id == client_id).first():
        return True
    if db.query(Quote.id).filter(Quote.client_id == client_id).first():
        return True
    if db.query(Deal.id).filter(Deal.client_id == client_id).first():
        return True
    if db.query(SalesOrder.id).filter(SalesOrder.client_id == client_id).first():
        return True
    return False


def _deal_in_use(db: Session, deal_id: int) -> bool:
    if db.query(Quote.id).filter(Quote.deal_id == deal_id).first():
        return True
    if db.query(SalesOrder.id).filter(SalesOrder.deal_id == deal_id).first():
        return True
    if db.query(Task.id).filter(Task.deal_id == deal_id).first():
        return True
    return False


def _lead_was_taken_over(lead: Lead, batch: ImportBatch) -> bool:
    """Someone else now holds this lead, or it became a client: undo must not delete their work."""
    status = getattr(lead.status, "value", lead.status)
    if status == LeadStatus.CONVERTED.value:
        return True
    return lead.assigned_to_id is not None and lead.assigned_to_id != batch.created_by_id


def undo_last_batch(db: Session, company_id: int, created_by_ids: list[int] | None = None) -> dict:
    batch = last_batch(db, company_id, created_by_ids)
    if batch is None:
        raise ValueError("No import batch to undo")

    removed = 0
    skipped = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for item in batch.items:
        if batch.entity_type == "lead":
            lead = db.query(Lead).filter(
                Lead.id == item.entity_id,
                Lead.company_id == company_id,
            ).first()
            if lead is None or lead.deleted_at is not None or _lead_was_taken_over(lead, batch):
                skipped += 1
                continue
            lead.deleted_at = now
            removed += 1
        elif batch.entity_type == "client":
            row = db.query(Client).filter(
                Client.id == item.entity_id,
                Client.company_id == company_id,
            ).first()
            if row is None:
                skipped += 1
                continue
            if _client_in_use(db, row.id):
                skipped += 1
                continue
            db.delete(row)
            removed += 1
        elif batch.entity_type == "deal":
            deal = db.query(Deal).filter(
                Deal.id == item.entity_id,
                Deal.company_id == company_id,
            ).first()
            if deal is None:
                skipped += 1
                continue
            if _deal_in_use(db, deal.id):
                skipped += 1
                continue
            db.delete(deal)
            removed += 1
        else:
            skipped += 1

    batch.undone_at = now
    db.commit()
    return {
        "entity_type": batch.entity_type,
        "removed": removed,
        "skipped": skipped,
        "batch_id": batch.id,
    }
