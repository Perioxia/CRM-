"""Changing who owns a lead: claiming, assigning, reassigning, and going back to the pool."""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.core.enums import LeadStatus, TaskStatus
from app.models.core.team_membership import TeamMembership
from app.models.core.user import User
from app.models.sales.lead import Lead
from app.models.sales.task import Task
from app.services.sales.lead_access import lead_page_path, user_role
from app.utils.dependencies import apply_company_scope
from app.utils.notify import send_notification


def user_on_team(db: Session, user: User, team_id: Optional[int]) -> bool:
    if team_id is None:
        return False
    if user.team_id == team_id:
        return True
    return (
        db.query(TeamMembership.id)
        .filter(TeamMembership.user_id == user.id, TeamMembership.team_id == team_id)
        .first()
        is not None
    )


def users_home_team(db: Session, user: User) -> Optional[int]:
    if user.team_id is not None:
        return user.team_id
    row = (
        db.query(TeamMembership.team_id)
        .filter(TeamMembership.user_id == user.id)
        .order_by(TeamMembership.id.desc())
        .first()
    )
    return row[0] if row else None


def ensure_assignable(db: Session, actor: User, assignee_id: int) -> User:
    """A lead may only go to an active person in the company who can work on leads."""
    assignee = apply_company_scope(db.query(User), User, actor).filter(User.id == assignee_id).first()
    if assignee is None:
        raise HTTPException(status_code=400, detail="User not found in this company")
    if not assignee.is_active:
        raise HTTPException(status_code=400, detail="This user is no longer active")
    if user_role(assignee) == "purchase":
        raise HTTPException(status_code=400, detail="Leads cannot be assigned to purchase users")
    return assignee


def lead_is_converted(lead: Lead) -> bool:
    return getattr(lead.status, "value", lead.status) == LeadStatus.CONVERTED.value


def manager_may_move_lead(db: Session, lead: Lead) -> bool:
    """A manager can't take a lead a rep created, unless that lead's owner has left."""
    if not lead.assigned_to_id or not lead.created_by_id:
        return True
    creator = db.query(User).filter(User.id == lead.created_by_id).first()
    if creator is None or user_role(creator) != "sales":
        return True
    owner = db.query(User).filter(User.id == lead.assigned_to_id).first()
    return owner is None or not owner.is_active


def notify_lead_assigned(db: Session, lead: Lead, owner_id: int, message: str, reassigned: bool = False) -> None:
    owner = db.query(User).filter(User.id == owner_id).first()
    if owner is None:
        return
    send_notification(
        db,
        owner.id,
        title=f"Lead {'Reassigned' if reassigned else 'Assigned'}: {lead.name}",
        message=message,
        type="info",
        link=lead_page_path(owner, lead.id),
        category="leads",
    )


def _hand_over_open_tasks(db: Session, lead: Lead, from_owner_id: Optional[int], to_owner_id: Optional[int]) -> None:
    owners = [Task.assigned_to_id.is_(None)]
    if from_owner_id is not None:
        owners.append(Task.assigned_to_id == from_owner_id)
    db.query(Task).filter(
        Task.lead_id == lead.id,
        Task.status != TaskStatus.COMPLETED,
        or_(*owners),
    ).update({Task.assigned_to_id: to_owner_id}, synchronize_session=False)


def _take_open_lead(db: Session, lead: Lead, owner_id: int) -> bool:
    """One statement, so two people acting together can never both get the lead."""
    db.flush()
    updated = db.query(Lead).filter(
        Lead.id == lead.id,
        Lead.assigned_to_id.is_(None),
        Lead.deleted_at.is_(None),
    ).update({Lead.assigned_to_id: owner_id}, synchronize_session=False)
    if not updated:
        return False
    _hand_over_open_tasks(db, lead, None, owner_id)
    db.refresh(lead, ["assigned_to_id"])
    return True


def claim_open_lead(db: Session, lead: Lead, user: User) -> bool:
    return _take_open_lead(db, lead, user.id)


def assign_open_lead(db: Session, lead: Lead, assignee: User, actor: User) -> bool:
    if not _take_open_lead(db, lead, assignee.id):
        return False
    _keep_lead_on_owners_team(db, lead, assignee)
    if assignee.id != actor.id:
        notify_lead_assigned(db, lead, assignee.id, f"{actor.full_name} assigned you a new lead.")
    return True


def _keep_lead_on_owners_team(db: Session, lead: Lead, owner: User) -> None:
    if user_on_team(db, owner, lead.team_id):
        return
    home = users_home_team(db, owner)
    if home is not None:
        lead.team_id = home


def move_lead_to_owner(db: Session, lead: Lead, new_owner: User, actor: User) -> None:
    previous_id = lead.assigned_to_id
    if previous_id == new_owner.id:
        return
    lead.assigned_to_id = new_owner.id
    _keep_lead_on_owners_team(db, lead, new_owner)
    _hand_over_open_tasks(db, lead, previous_id, new_owner.id)
    if new_owner.id != actor.id:
        notify_lead_assigned(
            db, lead, new_owner.id, f"{actor.full_name} assigned you a new lead.", reassigned=bool(previous_id)
        )
    if previous_id and previous_id != actor.id:
        _notify_previous_owner(db, lead, previous_id, f"{actor.full_name} moved this lead to {new_owner.full_name}.")


def return_lead_to_pool(db: Session, lead: Lead, actor: User) -> None:
    previous_id = lead.assigned_to_id
    lead.assigned_to_id = None
    if previous_id is not None:
        db.query(Task).filter(
            Task.lead_id == lead.id,
            Task.status != TaskStatus.COMPLETED,
            Task.assigned_to_id == previous_id,
        ).update({Task.assigned_to_id: None}, synchronize_session=False)
        if previous_id != actor.id:
            _notify_previous_owner(db, lead, previous_id, f"{actor.full_name} sent this lead back to the pool.")


def _notify_previous_owner(db: Session, lead: Lead, owner_id: int, message: str) -> None:
    send_notification(db, owner_id, title=f"Lead Moved: {lead.name}", message=message, type="info", category="leads")

