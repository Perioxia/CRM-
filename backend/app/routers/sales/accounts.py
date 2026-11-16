from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.core.user import User
from app.models.sales.account import Account
from app.models.sales.client import Client
from app.services.finance.gst import normalize_gstin
from app.services.sales.enrichment import apply_account as enrich_account
from app.utils.datetime_json import isoformat_utc
from app.services.sales.record_access import scope_clients
from app.utils.dependencies import apply_company_scope, ensure_company_access, get_active_team_id, get_current_user

router = APIRouter()


class AccountCreate(BaseModel):
    name: str
    website: Optional[str] = None
    phone: Optional[str] = None
    gstin: Optional[str] = None
    address: Optional[str] = None


class AccountPatch(BaseModel):
    name: Optional[str] = None
    website: Optional[str] = None
    phone: Optional[str] = None
    gstin: Optional[str] = None
    address: Optional[str] = None


def parse_gstin(value):
    if value is None:
        return None
    try:
        return normalize_gstin(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def get_account_or_404(db: Session, current_user: User, account_id: int) -> Account:
    row = (
        apply_company_scope(db.query(Account), Account, current_user)
        .filter(Account.id == account_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")
    ensure_company_access(row, current_user)
    return row


def visible_contacts(db: Session, current_user: User, active_team_id: Optional[int], account_ids: list[int]) -> dict[int, list[Client]]:
    """Each account's contacts that this user may see, by account id."""
    grouped: dict[int, list[Client]] = {account_id: [] for account_id in account_ids}
    if not account_ids:
        return grouped
    rows = (
        scope_clients(apply_company_scope(db.query(Client), Client, current_user), current_user, active_team_id)
        .filter(Client.account_id.in_(account_ids))
        .order_by(Client.name.asc())
        .all()
    )
    for row in rows:
        grouped[row.account_id].append(row)
    return grouped


def serialize_account(account: Account, contacts: list[Client]) -> dict:
    contact_rows = contacts
    return {
        "id": account.id,
        "name": account.name,
        "website": account.website,
        "phone": account.phone,
        "gstin": account.gstin,
        "address": account.address,
        "industry": account.industry,
        "linkedin_url": account.linkedin_url,
        "enriched_at": isoformat_utc(account.enriched_at),
        "enrichment_source": account.enrichment_source,
        "contact_count": len(contact_rows),
        "contacts": [{"id": c.id, "name": c.name, "email": c.email} for c in contact_rows],
        "created_at": account.created_at.isoformat() if account.created_at else None,
    }


@router.get("")
def list_accounts(
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    if current_user.company_id is None:
        raise HTTPException(status_code=403, detail="User must belong to a company")
    query = apply_company_scope(db.query(Account), Account, current_user)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(Account.name.ilike(pattern))
    rows = query.order_by(Account.name.asc()).all()
    contacts = visible_contacts(db, current_user, active_team_id, [a.id for a in rows])
    return {"items": [serialize_account(a, contacts[a.id]) for a in rows], "total": len(rows)}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_account(
    payload: AccountCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.company_id is None:
        raise HTTPException(status_code=403, detail="User must belong to a company")
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    account = Account(
        company_id=current_user.company_id,
        name=name,
        website=(payload.website or "").strip() or None,
        phone=(payload.phone or "").strip() or None,
        gstin=parse_gstin(payload.gstin),
        address=(payload.address or "").strip() or None,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return serialize_account(account, [])


@router.get("/{account_id:int}")
def get_account(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    account = get_account_or_404(db, current_user, account_id)
    return serialize_account(account, visible_contacts(db, current_user, active_team_id, [account.id])[account.id])


@router.post("/{account_id:int}/enrich")
def enrich_account_route(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    account = get_account_or_404(db, current_user, account_id)
    account = enrich_account(db, account)
    return serialize_account(account, visible_contacts(db, current_user, active_team_id, [account.id])[account.id])


@router.patch("/{account_id:int}")
def patch_account(
    account_id: int,
    payload: AccountPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    active_team_id: Optional[int] = Depends(get_active_team_id),
):
    account = get_account_or_404(db, current_user, account_id)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        account.name = name
    if "website" in data:
        account.website = (data["website"] or "").strip() or None
    if "phone" in data:
        account.phone = (data["phone"] or "").strip() or None
    if "gstin" in data:
        account.gstin = parse_gstin(data["gstin"])
    if "address" in data:
        account.address = (data["address"] or "").strip() or None
    db.commit()
    db.refresh(account)
    return serialize_account(account, visible_contacts(db, current_user, active_team_id, [account.id])[account.id])


@router.delete("/{account_id:int}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    account = get_account_or_404(db, current_user, account_id)
    linked = (
        apply_company_scope(db.query(Client.id), Client, current_user)
        .filter(Client.account_id == account.id)
        .first()
    )
    if linked:
        raise HTTPException(status_code=409, detail="Unlink contacts before deleting this account")
    db.delete(account)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
