"""Who may see and work on a lead. Every route that touches a lead uses these rules.

Sales see their own leads and unclaimed leads on their team, and work only on their own.
Managers see and work on their active team. MD and admin see and work on the whole company.
Purchase has no lead access.
"""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import and_, false, or_
from sqlalchemy.orm import Query, Session

from app.models.core.team_membership import TeamMembership
from app.models.core.user import User
from app.models.sales.client import Client
from app.models.sales.deal import Deal
from app.models.sales.lead import Lead
from app.utils.dependencies import apply_company_scope


def user_role(user: User) -> str:
    role = getattr(user, "role", None)
    return "" if role is None else str(getattr(role, "value", role))


def member_team_ids(db: Session, user: User) -> list[int]:
    rows = (
        apply_company_scope(db.query(TeamMembership.team_id), TeamMembership, user)
        .filter(TeamMembership.user_id == user.id)
        .all()
    )
    return [team_id for (team_id,) in rows]


def scope_visible_leads(query: Query, db: Session, user: User, active_team_id: Optional[int]) -> Query:
    """Limit a Lead query to the leads this user may see."""
    role = user_role(user)
    if role == "purchase":
        return query.filter(false())
    if role == "manager":
        return query.filter(false() if active_team_id is None else Lead.team_id == active_team_id)
    if role != "sales":
        return query
    if active_team_id is not None:
        open_on_team = or_(Lead.team_id == active_team_id, Lead.team_id.is_(None))
    else:
        teams = member_team_ids(db, user)
        open_on_team = or_(Lead.team_id.in_(teams), Lead.team_id.is_(None)) if teams else Lead.team_id.is_(None)
    return query.filter(or_(Lead.assigned_to_id == user.id, and_(Lead.assigned_to_id.is_(None), open_on_team)))


def scope_workable_leads(query: Query, user: User, active_team_id: Optional[int]) -> Query:
    """Limit a Lead query to the leads this user may work on."""
    role = user_role(user)
    if role == "purchase":
        return query.filter(false())
    if role == "manager":
        return query.filter(false() if active_team_id is None else Lead.team_id == active_team_id)
    if role == "sales":
        return query.filter(Lead.assigned_to_id == user.id)
    return query


def can_view_lead(lead: Lead, user: User, active_team_id: Optional[int], db: Session) -> bool:
    role = user_role(user)
    if role == "purchase":
        return False
    if role == "manager":
        return active_team_id is not None and lead.team_id == active_team_id
    if role != "sales":
        return True
    if lead.assigned_to_id == user.id:
        return True
    if lead.assigned_to_id is not None:
        return False
    if lead.team_id is None:
        return True
    if active_team_id is not None:
        return lead.team_id == active_team_id
    return lead.team_id in member_team_ids(db, user)


def can_work_lead(lead: Lead, user: User, active_team_id: Optional[int]) -> bool:
    role = user_role(user)
    if role == "purchase":
        return False
    if role == "manager":
        return active_team_id is not None and lead.team_id == active_team_id
    if role == "sales":
        return lead.assigned_to_id == user.id
    return True


def ensure_can_view_lead(
    lead: Lead,
    user: User,
    active_team_id: Optional[int],
    db: Session,
    detail: str = "You do not have access to this lead",
) -> None:
    if not can_view_lead(lead, user, active_team_id, db):
        raise HTTPException(status_code=403, detail=detail)


def ensure_can_work_lead(
    lead: Lead,
    user: User,
    active_team_id: Optional[int],
    detail: str = "You can only work on leads you own",
) -> None:
    if not can_work_lead(lead, user, active_team_id):
        raise HTTPException(status_code=403, detail=detail)


def ensure_can_work_lead_id(
    db: Session,
    user: User,
    active_team_id: Optional[int],
    lead_id: Optional[int],
) -> None:
    """For routes that link a record to a lead. A lead outside the company is left to the caller's own check."""
    if lead_id is None:
        return
    lead = apply_company_scope(db.query(Lead), Lead, user).filter(Lead.id == lead_id).first()
    if lead is not None:
        ensure_can_work_lead(lead, user, active_team_id)


def scope_activity_list(
    query: Query,
    model,
    owner_column,
    lead_id: Optional[int],
    db: Session,
    user: User,
    active_team_id: Optional[int],
) -> Query:
    """Rows for one lead need access to that lead. Any other list is limited row by row."""
    if lead_id is None:
        return scope_activity_rows(query, model, owner_column, db, user, active_team_id)
    lead = apply_company_scope(db.query(Lead), Lead, user).filter(Lead.id == lead_id).first()
    if lead is not None:
        ensure_can_view_lead(lead, user, active_team_id, db)
    return query.filter(model.lead_id == lead_id)


def scope_activity_rows(
    query: Query,
    model,
    owner_column,
    db: Session,
    user: User,
    active_team_id: Optional[int],
) -> Query:
    """Limit emails, calls or WhatsApp messages to the ones this user wrote or whose lead, client or deal they hold."""
    role = user_role(user)
    if role == "purchase":
        return query.filter(false())
    if role not in ("sales", "manager"):
        return query
    if role == "manager" and active_team_id is None:
        return query.filter(false())

    query = query.outerjoin(Lead, model.lead_id == Lead.id).outerjoin(Client, model.client_id == Client.id)
    deal_id = getattr(model, "deal_id", None)
    if deal_id is not None:
        query = query.outerjoin(Deal, deal_id == Deal.id)

    if role == "sales":
        allowed = [owner_column == user.id, Lead.assigned_to_id == user.id, Client.assigned_to_id == user.id]
        if deal_id is not None:
            allowed.append(Deal.assigned_to_id == user.id)
            if active_team_id is not None:
                allowed.append(and_(Deal.assigned_to_id.is_(None), Deal.team_id == active_team_id))
        return query.filter(or_(*allowed))

    members = (
        apply_company_scope(db.query(TeamMembership.user_id), TeamMembership, user)
        .filter(TeamMembership.team_id == active_team_id)
    )
    allowed = [Lead.team_id == active_team_id, Client.team_id == active_team_id, owner_column.in_(members)]
    if deal_id is not None:
        allowed.append(Deal.team_id == active_team_id)
    return query.filter(or_(*allowed))
