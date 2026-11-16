"""Who may see a client and the money records on it: invoices, quotes and sales orders.

The same rule the Clients and Orders pages already apply, kept in one place for every other route.
Sales see the clients assigned to them and the records they created or that belong to those clients.
Managers see their active team's clients and the records their team created or that belong to those
clients. MD, admin and purchase see the whole company.
"""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import false, or_, select
from sqlalchemy.orm import Query, Session

from app.models.core.team_membership import TeamMembership
from app.models.core.user import User
from app.models.sales.client import Client
from app.services.sales.lead_access import user_role
from app.utils.dependencies import apply_company_scope

_LIMITED = ("sales", "manager")


def _team_member_ids(user: User, team_id: int):
    return select(TeamMembership.user_id).where(
        TeamMembership.company_id == user.company_id,
        TeamMembership.team_id == team_id,
    )


def _visible_client_ids(user: User, active_team_id: Optional[int]):
    if user_role(user) == "sales":
        return select(Client.id).where(Client.company_id == user.company_id, Client.assigned_to_id == user.id)
    return select(Client.id).where(Client.company_id == user.company_id, Client.team_id == active_team_id)


def scope_clients(query: Query, user: User, active_team_id: Optional[int]) -> Query:
    role = user_role(user)
    if role == "sales":
        return query.filter(Client.assigned_to_id == user.id)
    if role == "manager":
        return query.filter(false() if active_team_id is None else Client.team_id == active_team_id)
    return query


def can_view_client(client: Client, user: User, active_team_id: Optional[int]) -> bool:
    role = user_role(user)
    if role == "sales":
        return client.assigned_to_id == user.id
    if role == "manager":
        return active_team_id is not None and client.team_id == active_team_id
    return True


def ensure_can_view_client(client: Client, user: User, active_team_id: Optional[int]) -> None:
    if not can_view_client(client, user, active_team_id):
        raise HTTPException(status_code=403, detail="You do not have access to this client")


def scope_client_records(query: Query, model, user: User, active_team_id: Optional[int],
                         include_client: bool = True) -> Query:
    """Invoices, quotes or sales orders: by who created them, and (except invoices) by whose client they are for."""
    role = user_role(user)
    if role not in _LIMITED:
        return query
    if role == "manager" and active_team_id is None:
        return query.filter(false())
    if role == "sales":
        created = model.created_by_id == user.id
    else:
        created = model.created_by_id.in_(_team_member_ids(user, active_team_id))
    if not include_client:
        return query.filter(created)
    return query.filter(or_(created, model.client_id.in_(_visible_client_ids(user, active_team_id))))


def can_view_client_record(db: Session, record, user: User, active_team_id: Optional[int],
                           include_client: bool = True) -> bool:
    model = type(record)
    query = apply_company_scope(db.query(model.id), model, user).filter(model.id == record.id)
    return scope_client_records(query, model, user, active_team_id, include_client).first() is not None


def ensure_can_view_client_record(db: Session, record, user: User, active_team_id: Optional[int],
                                  include_client: bool = True, detail: str = "You do not have access to this record") -> None:
    if not can_view_client_record(db, record, user, active_team_id, include_client):
        raise HTTPException(status_code=403, detail=detail)
