from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date, timedelta, timezone

from app.database import get_db
from app.services.sales.lead_access import ensure_can_work_lead
from app.services.sales.recycle import hide_deleted_lead_rows
from app.utils.dependencies import get_current_user, apply_company_scope, ensure_company_access, get_active_team_id
from app.models.core.user import User
from app.models.core.team_membership import TeamMembership
from app.models.sales.follow_up import FollowUp
from app.models.sales.lead import Lead
from app.schemas.sales import FollowUpCreate, FollowUpListResponse
from app.schemas.admin import MessageResponse

router = APIRouter()


def _role_scoped_lead_ids(db: Session, current_user: User, active_team_id: Optional[int]):
    """None means no extra filter (admin/md). Empty list means see nothing."""
    query = apply_company_scope(db.query(Lead.id), Lead, current_user).filter(Lead.deleted_at.is_(None))
    if current_user.role == "sales":
        if active_team_id is not None:
            query = query.filter(
                Lead.team_id == active_team_id,
                or_(Lead.assigned_to_id == current_user.id, Lead.assigned_to_id.is_(None)),
            )
        else:
            query = query.filter(Lead.assigned_to_id == current_user.id)
        return [row.id for row in query.all()]
    if current_user.role == "manager":
        if active_team_id is None:
            return []
        return [row.id for row in query.filter(Lead.team_id == active_team_id).all()]
    if current_user.role == "purchase":
        return []
    return None


def _apply_follow_up_role_scope(query, db: Session, current_user: User, active_team_id: Optional[int]):
    query = hide_deleted_lead_rows(query, FollowUp.lead_id)
    lead_ids = _role_scoped_lead_ids(db, current_user, active_team_id)
    if lead_ids is None:
        return query
    if not lead_ids:
        return query.filter(False)
    return query.filter(FollowUp.lead_id.in_(lead_ids))


def _assert_follow_up_lead_access(db: Session, fu: FollowUp, current_user: User, active_team_id: Optional[int], detail: str):
    lead_ids = _role_scoped_lead_ids(db, current_user, active_team_id)
    if lead_ids is None:
        return
    if fu.lead_id not in lead_ids:
        raise HTTPException(status_code=403, detail=detail)


class FollowUpUpdateBody(BaseModel):
    scheduled_date: Optional[str] = None
    scheduled_time: Optional[str] = None
    notes: Optional[str] = None


class FollowUpCompleteBody(BaseModel):
    outcome: str


class FollowUpRescheduleBody(BaseModel):
    new_date: str
    new_time: Optional[str] = None
    reason: Optional[str] = None


def _parse_ymd_date(value: str, field_name: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name} format. Expected YYYY-MM-DD.")


def _parse_hm_time(value: str, field_name: str):
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name} format. Expected HH:MM (24-hour).")


# ===============================
# Follow-ups Endpoints
# ===============================

@router.get("", response_model=FollowUpListResponse)
def list_follow_ups(
    status: Optional[str] = Query(None, description="Pending, Completed, Cancelled"),
    lead_id: Optional[int] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """List follow-ups for the current user (paginated)."""
    query = apply_company_scope(db.query(FollowUp), FollowUp, current_user)
    query = _apply_follow_up_role_scope(query, db, current_user, active_team_id)
    if status:
        query = query.filter(FollowUp.status == status)
    if lead_id:
        query = query.filter(FollowUp.lead_id == lead_id)
    total = query.count()
    follow_ups = query.order_by(FollowUp.scheduled_date.asc()).offset(skip).limit(limit).all()
    lead_ids = {fu.lead_id for fu in follow_ups if fu.lead_id}
    leads = {l.id: l for l in apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.id.in_(lead_ids)).all()} if lead_ids else {}
    
    result = []
    for fu in follow_ups:
        lead = leads.get(fu.lead_id)
        lead_name = f"{lead.name} - {lead.company}" if lead else "Unknown Lead"
        result.append({
            "id": fu.id,
            "lead_id": fu.lead_id,
            "lead_name": lead_name,
            "scheduled_date": fu.scheduled_date.strftime("%Y-%m-%d") if fu.scheduled_date else None,
            "scheduled_time": fu.scheduled_time.strftime("%I:%M %p") if fu.scheduled_time else None,
            "status": fu.status,
            "notes": fu.notes,
            "channel": fu.channel,
        })
    return {"items": result, "total": total, "skip": skip, "limit": limit}


@router.get("/today")
def get_todays_follow_ups(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Get follow-ups scheduled for today"""
    today = datetime.now(timezone.utc).date()
    fu_query = apply_company_scope(db.query(FollowUp), FollowUp, current_user)
    fu_query = _apply_follow_up_role_scope(fu_query, db, current_user, active_team_id)
    follow_ups = fu_query.filter(
        FollowUp.scheduled_date == today,
        FollowUp.status == "Pending"
    ).all()
    
    lead_ids = {fu.lead_id for fu in follow_ups if fu.lead_id}
    leads = {l.id: l for l in apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.id.in_(lead_ids)).all()} if lead_ids else {}
    
    result = []
    for fu in follow_ups:
        lead = leads.get(fu.lead_id)
        result.append({
            "id": fu.id,
            "lead_name": lead.name if lead else "Unknown",
            "company": lead.company if lead else "",
            "time": fu.scheduled_time.strftime("%I:%M %p") if fu.scheduled_time else "No time set",
            "notes": fu.notes,
            "status": fu.status
        })
    
    return {
        "date": today.strftime("%Y-%m-%d"),
        "follow_ups": result,
        "total": len(result)
    }


@router.get("/overdue")
def get_overdue_follow_ups(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Get overdue follow-ups"""
    today = datetime.now(timezone.utc).date()
    fu_query = apply_company_scope(db.query(FollowUp), FollowUp, current_user)
    fu_query = _apply_follow_up_role_scope(fu_query, db, current_user, active_team_id)
    follow_ups = fu_query.filter(
        FollowUp.scheduled_date < today,
        FollowUp.status == "Pending"
    ).all()
    
    lead_ids = {fu.lead_id for fu in follow_ups if fu.lead_id}
    leads = {l.id: l for l in apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.id.in_(lead_ids)).all()} if lead_ids else {}
    
    result = []
    for fu in follow_ups:
        lead = leads.get(fu.lead_id)
        days_overdue = (today - fu.scheduled_date).days if fu.scheduled_date else 0
        result.append({
            "id": fu.id,
            "lead_name": lead.name if lead else "Unknown",
            "company": lead.company if lead else "",
            "scheduled_date": fu.scheduled_date.strftime("%Y-%m-%d") if fu.scheduled_date else None,
            "days_overdue": days_overdue,
            "notes": fu.notes
        })
    
    return {
        "follow_ups": result,
        "total": len(result)
    }


@router.get("/{follow_up_id}")
def get_follow_up(
    follow_up_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Get follow-up details by ID"""
    fu = db.query(FollowUp).filter(FollowUp.id == follow_up_id).first()
    if not fu:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    ensure_company_access(fu, current_user)
    _assert_follow_up_lead_access(db, fu, current_user, active_team_id, "You do not have access to this follow-up")

    lead = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.id == fu.lead_id).first()
    
    return {
        "id": fu.id,
        "lead_id": fu.lead_id,
        "lead_name": f"{lead.name} - {lead.company}" if lead else "Unknown",
        "scheduled_date": fu.scheduled_date.strftime("%Y-%m-%d") if fu.scheduled_date else None,
        "scheduled_time": fu.scheduled_time.strftime("%I:%M %p") if fu.scheduled_time else None,
        "status": fu.status,
        "notes": fu.notes,
        "outcome": fu.outcome,
        "channel": fu.channel,
    }


@router.post("", status_code=201)
def create_follow_up(
    body: FollowUpCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Create a new follow-up"""
    lead = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.id == body.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    ensure_can_work_lead(lead, current_user, active_team_id, "You cannot create follow-ups for this lead")
    if current_user.role == "sales" and active_team_id is not None and lead.team_id != active_team_id:
        raise HTTPException(status_code=403, detail="You can only create follow-ups in your active team")
    
    new_fu = FollowUp(
        company_id=lead.company_id,
        lead_id=body.lead_id,
        scheduled_date=_parse_ymd_date(body.scheduled_date, "scheduled_date"),
        scheduled_time=_parse_hm_time(body.scheduled_time, "scheduled_time") if body.scheduled_time else None,
        notes=body.notes,
        status="Pending"
    )
    
    db.add(new_fu)
    db.commit()
    db.refresh(new_fu)
    
    return {
        "id": new_fu.id,
        "lead_id": new_fu.lead_id,
        "scheduled_date": new_fu.scheduled_date.strftime("%Y-%m-%d"),
        "status": new_fu.status,
        "message": "Follow-up created successfully"
    }


@router.put("/{follow_up_id}")
def update_follow_up(
    follow_up_id: int,
    body: FollowUpUpdateBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Update follow-up details"""
    fu = db.query(FollowUp).filter(FollowUp.id == follow_up_id).first()
    if not fu:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    ensure_company_access(fu, current_user)
    _assert_follow_up_lead_access(db, fu, current_user, active_team_id, "You cannot edit this follow-up")

    if body.scheduled_date is not None:
        fu.scheduled_date = _parse_ymd_date(body.scheduled_date, "scheduled_date")
    if body.scheduled_time is not None:
        fu.scheduled_time = _parse_hm_time(body.scheduled_time, "scheduled_time")
    if body.notes is not None:
        fu.notes = body.notes
    
    db.commit()
    
    return {"message": f"Follow-up {follow_up_id} updated"}


@router.post("/{follow_up_id}/complete")
def complete_follow_up(
    follow_up_id: int,
    body: FollowUpCompleteBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Mark follow-up as completed"""
    fu = db.query(FollowUp).filter(FollowUp.id == follow_up_id).first()
    if not fu:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    ensure_company_access(fu, current_user)
    _assert_follow_up_lead_access(db, fu, current_user, active_team_id, "You cannot complete this follow-up")

    fu.status = "Completed"
    fu.outcome = body.outcome
    fu.completed_at = datetime.now(timezone.utc)
    
    db.commit()
    
    return {
        "message": f"Follow-up {follow_up_id} marked as completed",
        "outcome": body.outcome,
        "completed_at": fu.completed_at.isoformat()
    }


@router.post("/{follow_up_id}/reschedule")
def reschedule_follow_up(
    follow_up_id: int,
    body: FollowUpRescheduleBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Reschedule a follow-up"""
    fu = db.query(FollowUp).filter(FollowUp.id == follow_up_id).first()
    if not fu:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    ensure_company_access(fu, current_user)
    _assert_follow_up_lead_access(db, fu, current_user, active_team_id, "You cannot reschedule this follow-up")

    fu.scheduled_date = _parse_ymd_date(body.new_date, "new_date")
    if body.new_time:
        fu.scheduled_time = _parse_hm_time(body.new_time, "new_time")
    if body.reason:
        fu.notes = f"{fu.notes or ''}\n[Rescheduled: {body.reason}]"
    
    db.commit()
    
    return {
        "message": f"Follow-up {follow_up_id} rescheduled",
        "new_date": body.new_date,
        "new_time": body.new_time
    }


@router.delete("/{follow_up_id}", response_model=MessageResponse)
def delete_follow_up(
    follow_up_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    """Delete a follow-up"""
    fu = db.query(FollowUp).filter(FollowUp.id == follow_up_id).first()
    if not fu:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    ensure_company_access(fu, current_user)
    _assert_follow_up_lead_access(db, fu, current_user, active_team_id, "You can only delete follow-ups for leads you can access")

    db.delete(fu)
    db.commit()
    
    return {"message": f"Follow-up {follow_up_id} deleted"}
