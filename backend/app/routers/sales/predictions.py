import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.core.user import User
from app.models.sales.prediction import PredictionModel
from app.services.predictions.convert import train_convert, CONVERT_KIND
from app.models.sales.client import Client
from app.services.predictions.churn_service import ranked_churn
from app.services.sales.record_access import scope_clients
from app.utils.dependencies import (
    apply_company_scope,
    get_active_team_id,
    get_current_user,
    require_admin_or_md,
)

router = APIRouter()

_KINDS = {"deal_convert"}


class TrainIn(BaseModel):
    kind: str = "deal_convert"


@router.post("/train")
def train(payload: TrainIn, db: Session = Depends(get_db),
          current_user: User = Depends(require_admin_or_md)):
    if payload.kind not in _KINDS:
        raise HTTPException(status_code=400, detail="Unknown model kind")
    model = train_convert(db, current_user.company_id)
    params = json.loads(model.params or "{}")
    return {
        "kind": model.kind,
        "sample_count": model.sample_count,
        "base_rate": model.base_rate,
        "model": params.get("model", "fallback"),
        "weights": params.get("features", {}),
        "trained_at": model.trained_at.isoformat() if model.trained_at else None,
    }


@router.get("/models")
def list_models(db: Session = Depends(get_db),
                current_user: User = Depends(require_admin_or_md)):
    rows = apply_company_scope(db.query(PredictionModel), PredictionModel, current_user).all()
    return {"items": [
        {
            "id": m.id, "kind": m.kind, "sample_count": m.sample_count,
            "base_rate": m.base_rate,
            "model": json.loads(m.params or "{}").get("model", "fallback"),
            "trained_at": m.trained_at.isoformat() if m.trained_at else None,
        }
        for m in rows
    ]}


@router.get("/churn")
def churn_list(band: Optional[str] = Query(None),
               db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user),
               active_team_id: Optional[int] = Depends(get_active_team_id)):
    if current_user.company_id is None:
        raise HTTPException(status_code=403, detail="User must belong to a company")
    clients = scope_clients(
        db.query(Client).filter(Client.company_id == current_user.company_id), current_user, active_team_id
    ).all()
    rows = ranked_churn(db, current_user.company_id, clients)
    if band:
        rows = [r for r in rows if r["band"] == band]
    return {"items": rows, "total": len(rows)}
