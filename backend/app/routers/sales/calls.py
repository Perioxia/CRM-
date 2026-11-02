from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.core.enums import CallDirection
from app.models.core.user import User
from app.models.sales.call_log import CallLog
from app.services.sales.activity_parents import (
    naive_utc_now,
    parse_iso_datetime,
    require_parent_in_company,
)
from app.utils.audit import log_activity
from app.utils.datetime_json import isoformat_utc
from app.services.sales.lead_access import ensure_can_work_lead_id, scope_activity_list, scope_activity_rows
from app.utils.dependencies import apply_company_scope, ensure_company_access, get_active_team_id, get_current_user

router = APIRouter()

_VALID_DIRECTIONS = {d.value for d in CallDirection}


class CallCreate(BaseModel):
    direction: str
    duration_seconds: Optional[int] = None
    outcome: Optional[str] = None
    notes: Optional[str] = None
    logged_at: Optional[str] = None
    lead_id: Optional[int] = None
    client_id: Optional[int] = None
    deal_id: Optional[int] = None


class CallUpdate(BaseModel):
    direction: Optional[str] = None
    duration_seconds: Optional[int] = None
    outcome: Optional[str] = None
    notes: Optional[str] = None
    logged_at: Optional[str] = None
    lead_id: Optional[int] = None
    client_id: Optional[int] = None
    deal_id: Optional[int] = None


def _normalize_direction(raw: Optional[str], *, required: bool) -> Optional[str]:
    if raw is None or not str(raw).strip():
        if required:
            raise HTTPException(status_code=400, detail="direction is required")
        return None
    value = str(raw).strip().lower()
    if value not in _VALID_DIRECTIONS:
        raise HTTPException(
            status_code=400,
            detail="Invalid direction. Allowed: inbound, outbound.",
        )
    return value


def _validate_duration(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    if value < 0:
        raise HTTPException(status_code=400, detail="duration_seconds must be >= 0")
    return value


def _serialize(call: CallLog) -> dict:
    direction = call.direction
    if hasattr(direction, "value"):
        direction = direction.value
    return {
        "id": call.id,
        "company_id": call.company_id,
        "direction": direction,
        "duration_seconds": call.duration_seconds,
        "outcome": call.outcome,
        "notes": call.notes,
        "logged_at": isoformat_utc(call.logged_at),
        "lead_id": call.lead_id,
        "client_id": call.client_id,
        "deal_id": call.deal_id,
        "created_by_id": call.created_by_id,
        "provider": call.provider,
        "provider_call_id": call.provider_call_id,
        "from_phone": call.from_phone,
        "to_phone": call.to_phone,
        "created_at": isoformat_utc(call.created_at),
    }


def _get_or_404(db: Session, current_user, active_team_id: Optional[int], call_id: int) -> CallLog:
    query = apply_company_scope(db.query(CallLog), CallLog, current_user)
    query = scope_activity_rows(query, CallLog, CallLog.created_by_id, db, current_user, active_team_id)
    call = query.filter(CallLog.id == call_id).first()
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    ensure_company_access(call, current_user)
    return call


@router.post("", status_code=status.HTTP_201_CREATED)
def create_call(
    payload: CallCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    require_parent_in_company(
        db, current_user,
        lead_id=payload.lead_id, client_id=payload.client_id, deal_id=payload.deal_id,
    )
    ensure_can_work_lead_id(db, current_user, active_team_id, payload.lead_id)
    direction = _normalize_direction(payload.direction, required=True)
    duration = _validate_duration(payload.duration_seconds)
    logged_at = parse_iso_datetime(payload.logged_at, "logged_at") or naive_utc_now()
    call = CallLog(
        company_id=current_user.company_id,
        direction=direction,
        duration_seconds=duration,
        outcome=payload.outcome,
        notes=payload.notes,
        logged_at=logged_at,
        lead_id=payload.lead_id,
        client_id=payload.client_id,
        deal_id=payload.deal_id,
        created_by_id=current_user.id,
    )
    db.add(call)
    db.flush()
    log_activity(
        db, user=current_user, action="created", entity_type="call",
        entity_id=call.id, entity_name=direction,
    )
    db.commit()
    db.refresh(call)
    return _serialize(call)


@router.get("")
def list_calls(
    lead_id: Optional[int] = Query(None),
    client_id: Optional[int] = Query(None),
    deal_id: Optional[int] = Query(None),
    direction: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    query = apply_company_scope(db.query(CallLog), CallLog, current_user)
    query = scope_activity_list(query, CallLog, CallLog.created_by_id, lead_id, db, current_user, active_team_id)
    if client_id is not None:
        query = query.filter(CallLog.client_id == client_id)
    if deal_id is not None:
        query = query.filter(CallLog.deal_id == deal_id)
    if direction:
        query = query.filter(CallLog.direction == _normalize_direction(direction, required=True))
    total = query.count()
    rows = query.order_by(CallLog.logged_at.desc()).offset(skip).limit(limit).all()
    return {"items": [_serialize(c) for c in rows], "total": total}


@router.get("/{call_id:int}")
def get_call(
    call_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    return _serialize(_get_or_404(db, current_user, active_team_id, call_id))


@router.patch("/{call_id:int}")
def update_call(
    call_id: int,
    payload: CallUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    call = _get_or_404(db, current_user, active_team_id, call_id)
    data = payload.model_dump(exclude_unset=True)
    parent_keys = {"lead_id", "client_id", "deal_id"}
    if parent_keys & data.keys():
        require_parent_in_company(
            db, current_user,
            lead_id=data.get("lead_id", call.lead_id),
            client_id=data.get("client_id", call.client_id),
            deal_id=data.get("deal_id", call.deal_id),
        )
        if data.get("lead_id") != call.lead_id:
            ensure_can_work_lead_id(db, current_user, active_team_id, data.get("lead_id"))
    if "direction" in data:
        call.direction = _normalize_direction(data["direction"], required=True)
        data.pop("direction")
    if "duration_seconds" in data:
        call.duration_seconds = _validate_duration(data["duration_seconds"])
        data.pop("duration_seconds")
    if "logged_at" in data:
        parsed = parse_iso_datetime(data["logged_at"], "logged_at")
        if parsed is None:
            raise HTTPException(status_code=400, detail="logged_at is required")
        call.logged_at = parsed
        data.pop("logged_at")
    for field, value in data.items():
        setattr(call, field, value)
    log_activity(
        db, user=current_user, action="updated", entity_type="call",
        entity_id=call.id, entity_name=getattr(call.direction, "value", call.direction),
    )
    db.commit()
    db.refresh(call)
    return _serialize(call)


@router.delete("/{call_id:int}", status_code=status.HTTP_204_NO_CONTENT)
def delete_call(
    call_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    call = _get_or_404(db, current_user, active_team_id, call_id)
    log_activity(
        db, user=current_user, action="deleted", entity_type="call",
        entity_id=call.id, entity_name=getattr(call.direction, "value", call.direction),
    )
    db.delete(call)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
