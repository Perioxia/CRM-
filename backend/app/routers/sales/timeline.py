"""
Activity Timeline API
Projected feed for lead/client/deal; audit-only for invoice/task/user.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.utils.dependencies import get_current_user, apply_company_scope, get_active_team_id
from app.models.core.user import User
from app.models.sales.audit import AuditLog
from app.services.sales.activity_timeline import build_activity_feed, require_timeline_parent
from app.services.sales.lead_access import ensure_can_view_lead
from app.utils.datetime_json import isoformat_utc

router = APIRouter()

_AUDIT_ONLY = ("invoice", "task", "user")
_FEED = ("lead", "client", "deal")


@router.get("/{entity_type}/{entity_id}")
def get_timeline(
    entity_type: str,
    entity_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    if entity_type == "lead":
        lead = require_timeline_parent(db, current_user, "lead", entity_id)
        ensure_can_view_lead(lead, current_user, active_team_id, db)
    if entity_type in _FEED:
        return build_activity_feed(db, current_user, entity_type, entity_id, skip, limit)

    if entity_type not in _AUDIT_ONLY:
        raise HTTPException(status_code=400, detail="Invalid entity type")

    query = apply_company_scope(db.query(AuditLog), AuditLog, current_user).filter(
        AuditLog.entity_type == entity_type,
        AuditLog.entity_id == str(entity_id)
    )

    total = query.count()
    events = query.order_by(AuditLog.timestamp.desc()).offset(skip).limit(limit).all()

    return {
        "events": [
            {
                "id": e.id,
                "action": e.action,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "entity_name": e.entity_name,
                "admin_name": e.admin_name,
                "before_value": e.before_value,
                "after_value": e.after_value,
                "timestamp": isoformat_utc(e.timestamp)
            }
            for e in events
        ],
        "total": total
    }
