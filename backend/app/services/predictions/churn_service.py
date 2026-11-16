from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.finance.invoice import Invoice
from app.services.predictions.churn import churn_score


def _invoice_dates(db: Session, company_id: int, client_id: int) -> list:
    rows = (
        db.query(Invoice.created_at, Invoice.paid_date)
        .filter(Invoice.company_id == company_id, Invoice.client_id == client_id)
        .all()
    )
    dates = []
    for created_at, paid_date in rows:
        dates.append(paid_date or created_at)
    return [d for d in dates if d is not None]


def churn_for_client(db: Session, company_id: int, client) -> dict:
    now = datetime.now(timezone.utc)
    result = churn_score(_invoice_dates(db, company_id, client.id), now)
    result["client_id"] = client.id
    result["client_name"] = client.name
    return result


def ranked_churn(db: Session, company_id: int, clients: list | None = None) -> list:
    from app.models.sales.client import Client
    if clients is None:
        clients = db.query(Client).filter(Client.company_id == company_id).all()
    scored = [churn_for_client(db, company_id, c) for c in clients]
    scored = [s for s in scored if s["risk"] is not None]
    scored.sort(key=lambda s: s["risk"], reverse=True)
    return scored
