from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session, aliased

from app.database import get_db
from app.models.core.enums import MeetingStatus
from app.models.core.team_membership import TeamMembership
from app.models.core.user import User
from app.models.sales.lead import Lead
from app.models.sales.meeting import Meeting
from app.services.sales.activity_parents import (
    naive_utc_now,
    parse_iso_datetime,
    require_parent_in_company,
)
from app.services.sales.booking import (
    booking_host,
    get_or_create_settings,
    serialize_booking,
    set_booking_config,
)
from app.services.sales.calendar_sync import sync_meeting_outbound
from app.services.sales.lead_access import ensure_can_work_lead_id
from app.utils.audit import log_activity
from app.utils.datetime_json import isoformat_utc
from app.utils.dependencies import (
    apply_company_scope,
    ensure_company_access,
    get_active_team_id,
    get_current_user,
    require_admin_or_md,
)

router = APIRouter()

_VALID_STATUSES = {s.value for s in MeetingStatus}
_VALID_BUCKETS = {"today", "upcoming", "overdue", "completed", "cancelled"}
_EMPTY_SUMMARY = {
    "today": 0,
    "upcoming": 0,
    "overdue": 0,
    "completed_week": 0,
    "cancelled_week": 0,
    "total_scheduled": 0,
}


def _user_role_str(user: User) -> str:
    role = getattr(user, "role", None)
    if role is None:
        return ""
    return str(getattr(role, "value", role))


def _status_value(meeting: Meeting) -> str:
    raw = meeting.status
    return str(getattr(raw, "value", raw) or "").strip().lower()


def _naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _scoped_meetings_query(db: Session, current_user: User, active_team_id: Optional[int]):
    if _user_role_str(current_user) == "purchase":
        raise HTTPException(status_code=403, detail="Not allowed")
    query = apply_company_scope(db.query(Meeting), Meeting, current_user)
    role = _user_role_str(current_user)
    if role == "sales":
        return query.outerjoin(Lead, Meeting.lead_id == Lead.id).filter(
            or_(
                Meeting.created_by_id == current_user.id,
                Lead.assigned_to_id == current_user.id,
            )
        )
    if role == "manager":
        if active_team_id is None:
            return query.filter(False)
        member_ids = [
            uid
            for (uid,) in apply_company_scope(
                db.query(TeamMembership.user_id), TeamMembership, current_user
            )
            .filter(TeamMembership.team_id == active_team_id)
            .all()
        ]
        created_in_team = Meeting.created_by_id.in_(member_ids) if member_ids else False
        return query.outerjoin(Lead, Meeting.lead_id == Lead.id).filter(
            or_(Lead.team_id == active_team_id, created_in_team)
        )
    return query


def _normalize_bucket(raw: Optional[str]) -> Optional[str]:
    if raw is None or not str(raw).strip():
        return None
    value = str(raw).strip().lower()
    if value not in _VALID_BUCKETS:
        raise HTTPException(
            status_code=400,
            detail="Invalid bucket. Allowed: today, upcoming, overdue, completed, cancelled.",
        )
    return value


def _apply_bucket_filter(query, bucket: str):
    now = naive_utc_now()
    today = now.date()
    day_start = datetime(today.year, today.month, today.day)
    day_end = day_start + timedelta(days=1)
    scheduled = Meeting.status == MeetingStatus.SCHEDULED.value
    if bucket == "today":
        return query.filter(scheduled, Meeting.starts_at >= day_start, Meeting.starts_at < day_end)
    if bucket == "upcoming":
        return query.filter(scheduled, Meeting.starts_at > now)
    if bucket == "overdue":
        return query.filter(scheduled, Meeting.starts_at < now)
    if bucket == "completed":
        return query.filter(Meeting.status == MeetingStatus.COMPLETED.value)
    if bucket == "cancelled":
        return query.filter(Meeting.status == MeetingStatus.CANCELLED.value)
    return query


def _summarize_meetings(meetings) -> dict:
    now = naive_utc_now()
    week_ago = now - timedelta(days=7)
    today = now.date()
    counts = dict(_EMPTY_SUMMARY)
    for meeting in meetings:
        status = _status_value(meeting)
        starts = _naive_utc(meeting.starts_at)
        updated = _naive_utc(meeting.updated_at)
        if status == "scheduled":
            counts["total_scheduled"] += 1
            if starts is not None:
                if starts.date() == today:
                    counts["today"] += 1
                if starts > now:
                    counts["upcoming"] += 1
                elif starts < now:
                    counts["overdue"] += 1
        elif status == "completed" and updated is not None and updated >= week_ago:
            counts["completed_week"] += 1
        elif status == "cancelled" and updated is not None and updated >= week_ago:
            counts["cancelled_week"] += 1
    return counts


class MeetingCreate(BaseModel):
    subject: str
    starts_at: str
    ends_at: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    lead_id: Optional[int] = None
    client_id: Optional[int] = None
    deal_id: Optional[int] = None


class BookingConfigPatch(BaseModel):
    slug: Optional[str] = None
    host_user_id: Optional[int] = None


class MeetingUpdate(BaseModel):
    subject: Optional[str] = None
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    lead_id: Optional[int] = None
    client_id: Optional[int] = None
    deal_id: Optional[int] = None


def _normalize_status(raw: Optional[str], *, required: bool) -> Optional[str]:
    if raw is None or not str(raw).strip():
        if required:
            raise HTTPException(status_code=400, detail="status is required")
        return None
    value = str(raw).strip().lower()
    if value not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Invalid status. Allowed: scheduled, completed, cancelled.",
        )
    return value


def _assert_time_order(starts_at, ends_at):
    if starts_at is not None and ends_at is not None and ends_at <= starts_at:
        raise HTTPException(status_code=400, detail="ends_at must be after starts_at")


def _serialize(meeting: Meeting, lead_name: Optional[str] = None) -> dict:
    status_val = meeting.status
    if hasattr(status_val, "value"):
        status_val = status_val.value
    return {
        "id": meeting.id,
        "company_id": meeting.company_id,
        "subject": meeting.subject,
        "starts_at": isoformat_utc(meeting.starts_at),
        "ends_at": isoformat_utc(meeting.ends_at),
        "location": meeting.location,
        "notes": meeting.notes,
        "status": status_val,
        "lead_id": meeting.lead_id,
        "lead_name": lead_name,
        "client_id": meeting.client_id,
        "deal_id": meeting.deal_id,
        "created_by_id": meeting.created_by_id,
        "calendar_event_id": meeting.calendar_event_id,
        "calendar_synced": bool(meeting.calendar_event_id),
        "conference_url": meeting.conference_url,
        "created_at": isoformat_utc(meeting.created_at),
        "updated_at": isoformat_utc(meeting.updated_at),
    }


def _get_or_404(db: Session, current_user, active_team_id: Optional[int], meeting_id: int) -> Meeting:
    meeting = _scoped_meetings_query(db, current_user, active_team_id).filter(
        Meeting.id == meeting_id
    ).first()
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    ensure_company_access(meeting, current_user)
    return meeting


def _company_id(user: User) -> int:
    if user.company_id is None:
        raise HTTPException(status_code=403, detail="User must belong to a company")
    return user.company_id


@router.get("/booking")
def read_booking_config(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    row = get_or_create_settings(db, company_id)
    host = booking_host(db, company_id, row.booking_host_user_id)
    return serialize_booking(row, host)


@router.patch("/booking")
def update_booking_config(
    payload: BookingConfigPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_md),
):
    data = payload.model_dump(exclude_unset=True)
    row, host = set_booking_config(
        db,
        _company_id(current_user),
        slug=data.get("slug"),
        slug_provided="slug" in data,
        host_user_id=data.get("host_user_id"),
        host_provided="host_user_id" in data,
    )
    return serialize_booking(row, host)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_meeting(
    payload: MeetingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    require_parent_in_company(
        db, current_user,
        lead_id=payload.lead_id, client_id=payload.client_id, deal_id=payload.deal_id,
    )
    ensure_can_work_lead_id(db, current_user, active_team_id, payload.lead_id)
    starts_at = parse_iso_datetime(payload.starts_at, "starts_at")
    if starts_at is None:
        raise HTTPException(status_code=400, detail="starts_at is required")
    ends_at = parse_iso_datetime(payload.ends_at, "ends_at")
    _assert_time_order(starts_at, ends_at)
    status_val = _normalize_status(payload.status, required=False) or MeetingStatus.SCHEDULED.value
    if not (payload.subject or "").strip():
        raise HTTPException(status_code=400, detail="subject is required")
    meeting = Meeting(
        company_id=current_user.company_id,
        subject=payload.subject.strip(),
        starts_at=starts_at,
        ends_at=ends_at,
        location=(payload.location or None),
        notes=payload.notes,
        status=status_val,
        lead_id=payload.lead_id,
        client_id=payload.client_id,
        deal_id=payload.deal_id,
        created_by_id=current_user.id,
    )
    db.add(meeting)
    db.flush()
    log_activity(
        db, user=current_user, action="created", entity_type="meeting",
        entity_id=meeting.id, entity_name=meeting.subject,
    )
    db.commit()
    db.refresh(meeting)
    sync_meeting_outbound(db, current_user, meeting)
    db.refresh(meeting)
    return _serialize(meeting)


@router.get("")
def list_meetings(
    lead_id: Optional[int] = Query(None),
    client_id: Optional[int] = Query(None),
    deal_id: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    bucket: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    query = _scoped_meetings_query(db, current_user, active_team_id)
    if lead_id is not None:
        query = query.filter(Meeting.lead_id == lead_id)
    if client_id is not None:
        query = query.filter(Meeting.client_id == client_id)
    if deal_id is not None:
        query = query.filter(Meeting.deal_id == deal_id)
    if status_filter:
        query = query.filter(Meeting.status == _normalize_status(status_filter, required=True))
    bucket_val = _normalize_bucket(bucket)
    if bucket_val:
        query = _apply_bucket_filter(query, bucket_val)
    total = query.count()
    LeadForName = aliased(Lead)
    rows = (
        query.outerjoin(LeadForName, Meeting.lead_id == LeadForName.id)
        .add_columns(LeadForName.name)
        .order_by(Meeting.starts_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return {
        "items": [_serialize(meeting, lead_name=name) for meeting, name in rows],
        "total": total,
    }


@router.get("/summary")
def meetings_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    query = _scoped_meetings_query(db, current_user, active_team_id)
    return _summarize_meetings(query.all())


@router.get("/{meeting_id:int}")
def get_meeting(
    meeting_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    return _serialize(_get_or_404(db, current_user, active_team_id, meeting_id))


@router.patch("/{meeting_id:int}")
def update_meeting(
    meeting_id: int,
    payload: MeetingUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    meeting = _get_or_404(db, current_user, active_team_id, meeting_id)
    data = payload.model_dump(exclude_unset=True)
    parent_keys = {"lead_id", "client_id", "deal_id"}
    if parent_keys & data.keys():
        require_parent_in_company(
            db, current_user,
            lead_id=data.get("lead_id", meeting.lead_id),
            client_id=data.get("client_id", meeting.client_id),
            deal_id=data.get("deal_id", meeting.deal_id),
        )
        if data.get("lead_id") != meeting.lead_id:
            ensure_can_work_lead_id(db, current_user, active_team_id, data.get("lead_id"))
    if "starts_at" in data:
        parsed = parse_iso_datetime(data["starts_at"], "starts_at")
        if parsed is None:
            raise HTTPException(status_code=400, detail="starts_at is required")
        meeting.starts_at = parsed
        data.pop("starts_at")
    if "ends_at" in data:
        meeting.ends_at = parse_iso_datetime(data["ends_at"], "ends_at")
        data.pop("ends_at")
    if "status" in data:
        meeting.status = _normalize_status(data["status"], required=True)
        data.pop("status")
    if "subject" in data:
        subject = (data["subject"] or "").strip()
        if not subject:
            raise HTTPException(status_code=400, detail="subject is required")
        meeting.subject = subject
        data.pop("subject")
    for field, value in data.items():
        setattr(meeting, field, value)
    _assert_time_order(meeting.starts_at, meeting.ends_at)
    meeting.updated_at = naive_utc_now()
    log_activity(
        db, user=current_user, action="updated", entity_type="meeting",
        entity_id=meeting.id, entity_name=meeting.subject,
    )
    db.commit()
    db.refresh(meeting)
    sync_meeting_outbound(db, current_user, meeting)
    db.refresh(meeting)
    return _serialize(meeting)


@router.delete("/{meeting_id:int}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meeting(
    meeting_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    meeting = _get_or_404(db, current_user, active_team_id, meeting_id)
    sync_meeting_outbound(db, current_user, meeting, deleted=True)
    log_activity(
        db, user=current_user, action="deleted", entity_type="meeting",
        entity_id=meeting.id, entity_name=meeting.subject,
    )
    db.delete(meeting)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
