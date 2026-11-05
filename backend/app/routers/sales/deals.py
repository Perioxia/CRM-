import json
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query, Response
from sqlalchemy.orm import Session

from sqlalchemy import or_

from app.database import get_db
from app.utils.dependencies import get_current_user, apply_company_scope, ensure_company_access, get_active_team_id, require_admin_or_md
from app.utils.audit import log_activity
from app.models.core.user import User
from app.models.core.team import Team
from app.models.core.team_membership import TeamMembership
from app.models.core.enums import DealStageType
from app.models.sales.deal import Deal
from app.models.sales.lead import Lead
from app.models.sales.client import Client
from app.models.sales.pipeline import Pipeline, PipelineStage
from app.services.sales.pipeline_seed import attach_default_stages, ensure_default_pipeline
from app.services.sales.custom_fields import get_values_map, set_values
from app.services.sales.lead_access import ensure_can_work_lead_id
from app.services.sales.deal_views import apply_deal_view
from app.services.sales.deal_next_activity import (
    NextActivityRequired,
    assert_next_activity_for_move,
    enrich_deal_activity_fields,
)
from app.services.sales.approvals import (
    ApprovalRequired,
    approve_deal,
    assert_deal_approved_for_close,
    refresh_deal_approval,
    reject_deal,
)
from app.models.core.enums import ApprovalStatus
from app.services.scoring.engine import score_entity
from app.services.scoring.recompute import active_rules, recompute_one
from app.services.predictions.convert import predict_deal
from app.services.sales.blueprint import (
    ALLOWED_REQUIRED_FIELDS,
    BlueprintError,
    assert_blueprint_move,
    parse_required_fields,
)
from app.schemas.sales.deal import (
    DealCreate, DealUpdate, DealStageUpdate, StageCreate, StageUpdate,
    PipelineCreate, PipelineUpdate,
)

router = APIRouter()

_VALID_STAGE_TYPES = {t.value for t in DealStageType}


def _role(user: User) -> str:
    r = getattr(user, "role", None)
    return str(getattr(r, "value", r)) if r is not None else ""


def _validate_ownership_refs(db: Session, current_user: User, *, assigned_to_id=None,
                              team_id=None, lead_id=None, client_id=None) -> None:
    """Ensure any provided ownership refs resolve within the caller's company."""
    checks = (
        ("assigned_to_id", assigned_to_id, User),
        ("team_id", team_id, Team),
        ("lead_id", lead_id, Lead),
        ("client_id", client_id, Client),
    )
    for field, value, model in checks:
        if value is None:
            continue
        found = apply_company_scope(db.query(model), model, current_user).filter(
            model.id == value
        ).first()
        if found is None:
            raise HTTPException(status_code=400, detail=f"{field} not found in your company")


def _apply_role_scope(db: Session, query, current_user: User, active_team_id: Optional[int]):
    """Mirror leads.py's role-based row scoping (see get_leads) for deals."""
    role = _role(current_user)
    if role == "sales":
        if active_team_id is not None:
            query = query.filter(
                Deal.team_id == active_team_id,
                or_(Deal.assigned_to_id == current_user.id, Deal.assigned_to_id.is_(None)),
            )
        else:
            # Fallback: no active team header/primary team set — still allow
            # "open to anyone" deals from any team the user belongs to.
            member_team_ids = [
                tm.team_id
                for tm in apply_company_scope(db.query(TeamMembership), TeamMembership, current_user)
                .filter(TeamMembership.user_id == current_user.id)
                .all()
            ]
            if member_team_ids:
                query = query.filter(
                    or_(
                        Deal.assigned_to_id == current_user.id,
                        (Deal.assigned_to_id.is_(None) & Deal.team_id.in_(member_team_ids)),
                    )
                )
            else:
                query = query.filter(Deal.assigned_to_id == current_user.id)
    elif role == "manager":
        if active_team_id is None:
            query = query.filter(False)
        else:
            query = query.filter(Deal.team_id == active_team_id)
    return query


def _ensure_deal_row_access(deal: Deal, current_user: User, active_team_id: Optional[int]) -> None:
    """Mirror leads.py get_lead's role check on by-id access."""
    role = _role(current_user)
    if role == "sales":
        own_or_open = (deal.assigned_to_id == current_user.id) or (
            deal.assigned_to_id is None and deal.team_id == active_team_id
        )
        if not own_or_open:
            raise HTTPException(status_code=403, detail="You do not have access to this deal")
    elif role == "manager":
        if active_team_id is None or deal.team_id != active_team_id:
            raise HTTPException(status_code=403, detail="You do not have access to this team's deal")


def _get_stage(db: Session, current_user: User, stage_id: int) -> Optional[PipelineStage]:
    return apply_company_scope(db.query(PipelineStage), PipelineStage, current_user).filter(
        PipelineStage.id == stage_id
    ).first()


def _effective_probability(deal: Deal, stage: PipelineStage) -> int:
    if deal.probability is not None:
        return deal.probability
    return stage.default_probability or 0


def _serialize_deal(deal: Deal, stage: PipelineStage, db: Session | None = None) -> dict:
    payload = {
        "id": deal.id,
        "title": deal.title,
        "amount": str(deal.amount) if deal.amount is not None else "0",
        "currency": deal.currency,
        "pipeline_id": deal.pipeline_id,
        "stage_id": deal.stage_id,
        "stage_name": stage.name if stage else None,
        "stage_type": stage.stage_type.value if stage else None,
        "probability": deal.probability,
        "effective_probability": _effective_probability(deal, stage) if stage else None,
        "expected_close": deal.expected_close.isoformat() if deal.expected_close else None,
        "closed_at": deal.closed_at.isoformat() if deal.closed_at else None,
        "lead_id": deal.lead_id,
        "client_id": deal.client_id,
        "assigned_to_id": deal.assigned_to_id,
        "source": deal.source,
        "score": deal.score,
        "approval_status": deal.approval_status,
        "created_at": deal.created_at.isoformat() if deal.created_at else None,
    }
    if db is not None:
        payload["custom_fields"] = get_values_map(db, deal.company_id, "deal", deal.id)
        payload.update(enrich_deal_activity_fields(db, deal, stage))
    return payload


@router.post("", status_code=status.HTTP_201_CREATED)
def create_deal(payload: DealCreate, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user),
                active_team_id: Optional[int] = Depends(get_active_team_id)):
    if payload.amount is not None and payload.amount < 0:
        raise HTTPException(status_code=400, detail="amount must be >= 0")
    if payload.probability is not None and not (0 <= payload.probability <= 100):
        raise HTTPException(status_code=400, detail="probability must be between 0 and 100")
    _validate_ownership_refs(
        db, current_user,
        assigned_to_id=payload.assigned_to_id, team_id=payload.team_id,
        lead_id=payload.lead_id, client_id=payload.client_id,
    )
    ensure_can_work_lead_id(db, current_user, active_team_id, payload.lead_id)

    pipeline_id = payload.pipeline_id
    stage_id = payload.stage_id
    if pipeline_id is not None and _get_pipeline(db, current_user, pipeline_id) is None:
        raise HTTPException(status_code=400, detail="pipeline_id not found in your company")
    if pipeline_id is None or stage_id is None:
        default_pipeline = ensure_default_pipeline(db, current_user.company_id)
        pipeline_id = pipeline_id or default_pipeline.id
        if stage_id is None:
            first_stage = apply_company_scope(db.query(PipelineStage), PipelineStage, current_user).filter(
                PipelineStage.pipeline_id == pipeline_id
            ).order_by(PipelineStage.position).first()
            if first_stage is None:
                raise HTTPException(status_code=400, detail="pipeline has no stages")
            stage_id = first_stage.id

    stage = _get_stage(db, current_user, stage_id)
    if stage is None or stage.pipeline_id != pipeline_id:
        raise HTTPException(status_code=400, detail="stage does not belong to pipeline")

    deal = Deal(
        company_id=current_user.company_id,
        title=payload.title,
        amount=payload.amount if payload.amount is not None else Decimal("0"),
        currency=payload.currency or "INR",
        pipeline_id=pipeline_id,
        stage_id=stage_id,
        probability=payload.probability,
        expected_close=payload.expected_close,
        lead_id=payload.lead_id,
        client_id=payload.client_id,
        assigned_to_id=payload.assigned_to_id,
        created_by_id=current_user.id,
        team_id=payload.team_id,
        source=payload.source,
    )
    db.add(deal)
    db.flush()
    refresh_deal_approval(db, deal=deal, amount=deal.amount, actor=current_user)
    log_activity(db, user=current_user, action="created", entity_type="deal",
                 entity_id=deal.id, entity_name=deal.title,
                 after=json.dumps({"amount": str(deal.amount), "stage_id": stage_id}))
    db.commit()
    db.refresh(deal)
    recompute_one(db, deal, "deal")
    db.commit()
    return _serialize_deal(deal, stage)


@router.get("")
def list_deals(db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
               active_team_id: Optional[int] = Depends(get_active_team_id),
               pipeline_id: Optional[int] = Query(None), stage_id: Optional[int] = Query(None),
               assigned_to_id: Optional[int] = Query(None),
               view: Optional[str] = Query(None),
               sort: Optional[str] = Query(None),
               min_score: Optional[int] = Query(None),
               skip: int = 0, limit: int = 100):
    query = apply_company_scope(db.query(Deal), Deal, current_user)
    query = _apply_role_scope(db, query, current_user, active_team_id)
    if pipeline_id is not None:
        query = query.filter(Deal.pipeline_id == pipeline_id)
    if stage_id is not None:
        query = query.filter(Deal.stage_id == stage_id)
    if assigned_to_id is not None:
        query = query.filter(Deal.assigned_to_id == assigned_to_id)
    query = apply_deal_view(
        query, view, current_user.id, db=db, company_id=current_user.company_id,
    )
    if min_score is not None:
        query = query.filter(Deal.score >= min_score)
    from sqlalchemy import func as _sqlfunc
    order = _sqlfunc.coalesce(Deal.score, 0).desc() if sort == "score" else Deal.created_at.desc()

    total = query.count()
    deals = query.order_by(order).offset(skip).limit(limit).all()
    stage_ids = {d.stage_id for d in deals}
    stage_map = {
        s.id: s for s in apply_company_scope(db.query(PipelineStage), PipelineStage, current_user)
        .filter(PipelineStage.id.in_(stage_ids or [-1])).all()
    }
    return {
        "items": [_serialize_deal(d, stage_map.get(d.stage_id), db) for d in deals],
        "total": total, "skip": skip, "limit": limit,
    }


@router.get("/{deal_id:int}")
def get_deal(deal_id: int, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user),
             active_team_id: Optional[int] = Depends(get_active_team_id)):
    deal = apply_company_scope(db.query(Deal), Deal, current_user).filter(Deal.id == deal_id).first()
    if deal is None:
        raise HTTPException(status_code=404, detail="Deal not found")
    ensure_company_access(deal, current_user)
    _ensure_deal_row_access(deal, current_user, active_team_id)
    stage = _get_stage(db, current_user, deal.stage_id)
    return _serialize_deal(deal, stage, db)


@router.get("/{deal_id:int}/score")
def get_deal_score(deal_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    deal = apply_company_scope(db.query(Deal), Deal, current_user).filter(Deal.id == deal_id).first()
    if deal is None:
        raise HTTPException(status_code=404, detail="Deal not found")
    ensure_company_access(deal, current_user)
    result = score_entity(deal, active_rules(db, deal.company_id, "deal"))
    return {
        "score": result["total"],
        "score_updated_at": deal.score_updated_at.isoformat() if deal.score_updated_at else None,
        "breakdown": result["breakdown"],
    }


@router.get("/{deal_id:int}/prediction")
def get_deal_prediction(deal_id: int, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    deal = apply_company_scope(db.query(Deal), Deal, current_user).filter(Deal.id == deal_id).first()
    if deal is None:
        raise HTTPException(status_code=404, detail="Deal not found")
    ensure_company_access(deal, current_user)
    return predict_deal(db, deal)


@router.patch("/{deal_id:int}")
def update_deal(deal_id: int, payload: DealUpdate, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user),
                active_team_id: Optional[int] = Depends(get_active_team_id)):
    deal = apply_company_scope(db.query(Deal), Deal, current_user).filter(Deal.id == deal_id).first()
    if deal is None:
        raise HTTPException(status_code=404, detail="Deal not found")
    ensure_company_access(deal, current_user)
    _ensure_deal_row_access(deal, current_user, active_team_id)
    data = payload.model_dump(exclude_unset=True)
    custom_fields = data.pop("custom_fields", None)
    if "amount" in data and data["amount"] is not None and data["amount"] < 0:
        raise HTTPException(status_code=400, detail="amount must be >= 0")
    if "probability" in data and data["probability"] is not None and not (0 <= data["probability"] <= 100):
        raise HTTPException(status_code=400, detail="probability must be between 0 and 100")
    _validate_ownership_refs(
        db, current_user,
        assigned_to_id=data.get("assigned_to_id"), team_id=data.get("team_id"),
        lead_id=data.get("lead_id"), client_id=data.get("client_id"),
    )
    if data.get("lead_id") != deal.lead_id:
        ensure_can_work_lead_id(db, current_user, active_team_id, data.get("lead_id"))
    for field, value in data.items():
        setattr(deal, field, value)
    if "amount" in data:
        refresh_deal_approval(db, deal=deal, amount=deal.amount, actor=current_user)
    if "expected_close" in data:
        deal.due_reminded_at = None
    audit_after = {**data, "amount": str(data["amount"])} if data.get("amount") is not None else data
    log_activity(db, user=current_user, action="updated", entity_type="deal",
                 entity_id=deal.id, entity_name=deal.title, after=json.dumps(audit_after, default=str))
    if custom_fields is not None:
        set_values(db, current_user.company_id, "deal", deal.id, custom_fields)
    recompute_one(db, deal, "deal")
    db.commit()
    db.refresh(deal)
    stage = _get_stage(db, current_user, deal.stage_id)
    return _serialize_deal(deal, stage, db)


@router.delete("/{deal_id:int}")
def delete_deal(deal_id: int, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user),
                active_team_id: Optional[int] = Depends(get_active_team_id)):
    deal = apply_company_scope(db.query(Deal), Deal, current_user).filter(Deal.id == deal_id).first()
    if deal is None:
        raise HTTPException(status_code=404, detail="Deal not found")
    ensure_company_access(deal, current_user)
    _ensure_deal_row_access(deal, current_user, active_team_id)
    log_activity(db, user=current_user, action="deleted", entity_type="deal",
                 entity_id=deal.id, entity_name=deal.title)
    db.delete(deal)
    db.commit()
    return {"message": "Deal deleted"}


@router.patch("/{deal_id:int}/stage")
def move_deal_stage(deal_id: int, payload: DealStageUpdate, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user),
                    active_team_id: Optional[int] = Depends(get_active_team_id)):
    deal = apply_company_scope(db.query(Deal), Deal, current_user).filter(Deal.id == deal_id).first()
    if deal is None:
        raise HTTPException(status_code=404, detail="Deal not found")
    ensure_company_access(deal, current_user)
    _ensure_deal_row_access(deal, current_user, active_team_id)
    stage = _get_stage(db, current_user, payload.stage_id)
    if stage is None or stage.pipeline_id != deal.pipeline_id:
        raise HTTPException(status_code=400, detail="stage does not belong to deal's pipeline")

    pipeline = _get_pipeline(db, current_user, deal.pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    current_stage = _get_stage(db, current_user, deal.stage_id)
    stages = apply_company_scope(db.query(PipelineStage), PipelineStage, current_user).filter(
        PipelineStage.pipeline_id == deal.pipeline_id
    ).all()
    try:
        assert_blueprint_move(
            deal=deal, pipeline=pipeline, current_stage=current_stage,
            target_stage=stage, stages=stages,
        )
    except BlueprintError as err:
        if err.missing_fields:
            raise HTTPException(
                status_code=400,
                detail={"message": err.message, "missing_fields": err.missing_fields},
            )
        raise HTTPException(status_code=400, detail=err.message)

    try:
        assert_next_activity_for_move(
            db, deal=deal, current_stage=current_stage, target_stage=stage,
        )
    except NextActivityRequired as err:
        raise HTTPException(status_code=400, detail=str(err))

    try:
        assert_deal_approved_for_close(deal, stage)
    except ApprovalRequired as err:
        raise HTTPException(status_code=400, detail=str(err))

    before_stage = deal.stage_id
    deal.stage_id = stage.id
    if stage.stage_type in (DealStageType.WON, DealStageType.LOST):
        deal.closed_at = deal.closed_at or datetime.utcnow()
    else:
        deal.closed_at = None
    log_activity(db, user=current_user, action="stage_changed", entity_type="deal",
                 entity_id=deal.id, entity_name=deal.title,
                 before=json.dumps({"stage_id": before_stage}), after=json.dumps({"stage_id": stage.id}))
    recompute_one(db, deal, "deal")
    db.commit()
    db.refresh(deal)
    from app.services.sales.outbound_webhooks import emit_event
    emit_event(db, current_user.company_id, "deal.stage_changed", {
        "id": deal.id,
        "title": deal.title,
        "stage_id": stage.id,
        "stage_name": stage.name,
        "previous_stage_id": before_stage,
    })
    return _serialize_deal(deal, stage, db)


@router.post("/{deal_id:int}/approve")
def approve_deal_route(
    deal_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_md),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    deal = apply_company_scope(db.query(Deal), Deal, current_user).filter(Deal.id == deal_id).first()
    if deal is None:
        raise HTTPException(status_code=404, detail="Deal not found")
    ensure_company_access(deal, current_user)
    if deal.approval_status != ApprovalStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="Deal is not pending approval")
    deal = approve_deal(db, deal, current_user)
    stage = _get_stage(db, current_user, deal.stage_id)
    return _serialize_deal(deal, stage, db)


@router.post("/{deal_id:int}/reject")
def reject_deal_route(
    deal_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_md),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    deal = apply_company_scope(db.query(Deal), Deal, current_user).filter(Deal.id == deal_id).first()
    if deal is None:
        raise HTTPException(status_code=404, detail="Deal not found")
    ensure_company_access(deal, current_user)
    if deal.approval_status != ApprovalStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="Deal is not pending approval")
    deal = reject_deal(db, deal, current_user)
    stage = _get_stage(db, current_user, deal.stage_id)
    return _serialize_deal(deal, stage, db)


@router.get("/board")
def deal_board(db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
               active_team_id: Optional[int] = Depends(get_active_team_id),
               pipeline_id: Optional[int] = Query(None),
               view: Optional[str] = Query(None)):
    if pipeline_id is None:
        pipeline = ensure_default_pipeline(db, current_user.company_id)
        pipeline_id = pipeline.id
    else:
        pipeline = _get_pipeline(db, current_user, pipeline_id)
        if pipeline is None:
            raise HTTPException(status_code=404, detail="Pipeline not found")

    stages = apply_company_scope(db.query(PipelineStage), PipelineStage, current_user).filter(
        PipelineStage.pipeline_id == pipeline_id
    ).order_by(PipelineStage.position).all()

    deals_q = apply_company_scope(db.query(Deal), Deal, current_user).filter(Deal.pipeline_id == pipeline_id)
    deals_q = _apply_role_scope(db, deals_q, current_user, active_team_id)
    deals_q = apply_deal_view(
        deals_q, view, current_user.id, db=db, company_id=current_user.company_id,
    )
    deals = deals_q.all()

    by_stage = {s.id: [] for s in stages}
    for d in deals:
        by_stage.setdefault(d.stage_id, []).append(d)

    open_forecast = Decimal("0")
    won_value = Decimal("0")
    stage_blocks = []
    for s in stages:
        s_deals = by_stage.get(s.id, [])
        stage_total = sum((d.amount or Decimal("0")) for d in s_deals) or Decimal("0")
        weighted = Decimal("0")
        for d in s_deals:
            amt = d.amount or Decimal("0")
            if s.stage_type == DealStageType.OPEN:
                eff = d.probability if d.probability is not None else (s.default_probability or 0)
                weighted += amt * Decimal(eff) / Decimal(100)
            elif s.stage_type == DealStageType.WON:
                won_value += amt
        if s.stage_type == DealStageType.OPEN:
            open_forecast += weighted
        stage_blocks.append({
            "stage_id": s.id, "name": s.name, "stage_type": s.stage_type.value,
            "stage_total": str(stage_total.quantize(Decimal("0.01"))),
            "weighted_value": str(weighted.quantize(Decimal("0.01"))),
            "required_fields": parse_required_fields(s.required_fields),
            "deals": [_serialize_deal(d, s, db) for d in s_deals],
        })

    return {
        "pipeline_id": pipeline_id,
        "pipeline_name": pipeline.name,
        "stages": stage_blocks,
        "open_forecast": str(open_forecast.quantize(Decimal("0.01"))),
        "won_value": str(won_value.quantize(Decimal("0.01"))),
    }


def _get_pipeline(db: Session, current_user: User, pipeline_id: int) -> Optional[Pipeline]:
    return apply_company_scope(db.query(Pipeline), Pipeline, current_user).filter(
        Pipeline.id == pipeline_id
    ).first()


def _serialize_pipeline(p: Pipeline) -> dict:
    return {
        "id": p.id, "name": p.name, "is_default": bool(p.is_default), "is_active": bool(p.is_active),
        "blueprint_enabled": bool(getattr(p, "blueprint_enabled", False)),
    }


def _store_required_fields(keys: list[str]) -> str | None:
    for key in keys:
        if key not in ALLOWED_REQUIRED_FIELDS:
            raise HTTPException(status_code=400, detail=f"invalid required field: {key}")
    return json.dumps(keys) if keys else None


def _clear_other_defaults(db: Session, current_user: User, keep_id: int) -> None:
    others = apply_company_scope(db.query(Pipeline), Pipeline, current_user).filter(
        Pipeline.id != keep_id, Pipeline.is_default == True  # noqa: E712
    ).all()
    for row in others:
        row.is_default = False


def _require_admin_or_md(current_user: User):
    if _role(current_user) not in ("admin", "md"):
        raise HTTPException(status_code=403, detail="Only admin or MD can configure stages")


def _serialize_stage(s: PipelineStage) -> dict:
    return {
        "id": s.id, "pipeline_id": s.pipeline_id, "name": s.name,
        "position": s.position,
        "stage_type": s.stage_type.value if hasattr(s.stage_type, "value") else s.stage_type,
        "default_probability": s.default_probability, "is_active": s.is_active,
        "required_fields": parse_required_fields(s.required_fields),
    }


@router.get("/pipelines")
def list_pipelines(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_default_pipeline(db, current_user.company_id)
    pipelines = apply_company_scope(db.query(Pipeline), Pipeline, current_user).order_by(Pipeline.id).all()
    return {"items": [_serialize_pipeline(p) for p in pipelines]}


@router.post("/pipelines", status_code=status.HTTP_201_CREATED)
def create_pipeline(
    payload: PipelineCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin_or_md(current_user)
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    ensure_default_pipeline(db, current_user.company_id)
    pipeline = Pipeline(
        company_id=current_user.company_id,
        name=name,
        is_default=bool(payload.is_default),
        is_active=True,
    )
    db.add(pipeline)
    db.flush()
    attach_default_stages(db, current_user.company_id, pipeline.id)
    if pipeline.is_default:
        _clear_other_defaults(db, current_user, pipeline.id)
    db.commit()
    db.refresh(pipeline)
    return _serialize_pipeline(pipeline)


@router.patch("/pipelines/{pipeline_id:int}")
def update_pipeline(
    pipeline_id: int,
    payload: PipelineUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin_or_md(current_user)
    pipeline = _get_pipeline(db, current_user, pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        pipeline.name = name
        data.pop("name")
    if data.get("is_default") is False and pipeline.is_default:
        other_default = apply_company_scope(db.query(Pipeline), Pipeline, current_user).filter(
            Pipeline.id != pipeline.id, Pipeline.is_default == True  # noqa: E712
        ).first()
        if other_default is None:
            raise HTTPException(status_code=400, detail="cannot unset the last default pipeline")
    if "is_active" in data and data["is_active"] is not None:
        pipeline.is_active = data["is_active"]
    if data.get("is_default") is True:
        pipeline.is_default = True
        _clear_other_defaults(db, current_user, pipeline.id)
    elif data.get("is_default") is False:
        pipeline.is_default = False
    if "blueprint_enabled" in data and data["blueprint_enabled"] is not None:
        pipeline.blueprint_enabled = data["blueprint_enabled"]
    db.commit()
    db.refresh(pipeline)
    return _serialize_pipeline(pipeline)


@router.delete("/pipelines/{pipeline_id:int}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pipeline(
    pipeline_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin_or_md(current_user)
    pipeline = _get_pipeline(db, current_user, pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    if pipeline.is_default:
        raise HTTPException(status_code=400, detail="cannot delete the default pipeline")
    deal_count = apply_company_scope(db.query(Deal), Deal, current_user).filter(
        Deal.pipeline_id == pipeline.id
    ).count()
    if deal_count:
        raise HTTPException(status_code=400, detail="pipeline has deals")
    db.query(PipelineStage).filter(PipelineStage.pipeline_id == pipeline.id).delete()
    db.delete(pipeline)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/stages")
def list_stages(db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
                pipeline_id: Optional[int] = Query(None)):
    q = apply_company_scope(db.query(PipelineStage), PipelineStage, current_user)
    if pipeline_id is not None:
        q = q.filter(PipelineStage.pipeline_id == pipeline_id)
    stages = q.order_by(PipelineStage.pipeline_id, PipelineStage.position).all()
    return {"items": [_serialize_stage(s) for s in stages]}


@router.post("/stages", status_code=status.HTTP_201_CREATED)
def create_stage(payload: StageCreate, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    _require_admin_or_md(current_user)
    pipeline = apply_company_scope(db.query(Pipeline), Pipeline, current_user).filter(
        Pipeline.id == payload.pipeline_id
    ).first()
    if pipeline is None:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    if not (0 <= payload.default_probability <= 100):
        raise HTTPException(status_code=400, detail="default_probability must be 0..100")
    if payload.stage_type is not None and payload.stage_type not in _VALID_STAGE_TYPES:
        raise HTTPException(status_code=400, detail="stage_type must be one of: open, won, lost")
    required_fields = None
    if payload.required_fields is not None:
        required_fields = _store_required_fields(payload.required_fields)
    stage = PipelineStage(
        company_id=current_user.company_id, pipeline_id=pipeline.id, name=payload.name,
        position=payload.position, stage_type=payload.stage_type,
        default_probability=payload.default_probability,
        required_fields=required_fields,
    )
    db.add(stage)
    db.commit()
    db.refresh(stage)
    return _serialize_stage(stage)


@router.patch("/stages/{stage_id:int}")
def update_stage(stage_id: int, payload: StageUpdate, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    _require_admin_or_md(current_user)
    stage = apply_company_scope(db.query(PipelineStage), PipelineStage, current_user).filter(
        PipelineStage.id == stage_id
    ).first()
    if stage is None:
        raise HTTPException(status_code=404, detail="Stage not found")
    data = payload.model_dump(exclude_unset=True)
    if "default_probability" in data and data["default_probability"] is not None and not (0 <= data["default_probability"] <= 100):
        raise HTTPException(status_code=400, detail="default_probability must be 0..100")
    if "stage_type" in data and data["stage_type"] is not None and data["stage_type"] not in _VALID_STAGE_TYPES:
        raise HTTPException(status_code=400, detail="stage_type must be one of: open, won, lost")
    if "required_fields" in data:
        stage.required_fields = _store_required_fields(data.pop("required_fields") or [])
    for field, value in data.items():
        setattr(stage, field, value)
    db.commit()
    db.refresh(stage)
    return _serialize_stage(stage)
