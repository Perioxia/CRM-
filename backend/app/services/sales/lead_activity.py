"""Keep a lead's next-step and last-contact dates true, so lists and scoring can rely on them."""
from datetime import datetime, time
from typing import Optional

from sqlalchemy.orm import Session

from app.models.sales.follow_up import FollowUp
from app.models.sales.lead import Lead


def mark_lead_contacted(db: Session, lead_id: Optional[int]) -> None:
    if lead_id is not None:
        db.query(Lead).filter(Lead.id == lead_id).update(
            {Lead.last_contacted_at: datetime.utcnow()}, synchronize_session=False
        )


def mark_lead_responded(db: Session, lead_id: Optional[int]) -> None:
    if lead_id is not None:
        db.query(Lead).filter(Lead.id == lead_id).update(
            {Lead.last_response_at: datetime.utcnow()}, synchronize_session=False
        )


def refresh_next_follow_up(db: Session, lead_id: Optional[int]) -> None:
    """Set the lead's next step to its earliest pending follow-up, or clear it."""
    if lead_id is None:
        return
    db.flush()
    earliest = (
        db.query(FollowUp)
        .filter(FollowUp.lead_id == lead_id, FollowUp.status == "Pending")
        .order_by(FollowUp.scheduled_date.asc(), FollowUp.scheduled_time.asc())
        .first()
    )
    next_step = datetime.combine(earliest.scheduled_date, earliest.scheduled_time or time.min) if earliest else None
    db.query(Lead).filter(Lead.id == lead_id).update({Lead.next_follow_up: next_step}, synchronize_session=False)
