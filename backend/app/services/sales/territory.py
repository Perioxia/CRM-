from sqlalchemy import func

from app.models.core.enums import LeadStatus
from sqlalchemy.orm import Session, joinedload

from app.models.core.team_membership import TeamMembership
from app.models.core.user import User
from app.models.sales.lead import Lead
from app.models.sales.territory import Territory
from app.services.sales.lead_ownership import notify_lead_assigned

MATCH_FIELDS = frozenset({"service_type", "source"})
_FINISHED_STATUSES = (LeadStatus.CONVERTED, LeadStatus.LOST, LeadStatus.LOST_CLIENT)


def round_robin_sales_on_team(db: Session, *, company_id: int, team_id: int) -> int | None:
    """Return user_id with lowest lead load among active sales on team, or None."""
    candidates = (
        db.query(User)
        .join(TeamMembership, TeamMembership.user_id == User.id)
        .filter(
            User.company_id == company_id,
            User.role == "sales",
            User.is_active == True,
            TeamMembership.team_id == team_id,
            TeamMembership.company_id == company_id,
        )
        .all()
    )
    return _user_id_with_lowest_load(db, company_id, candidates)


def assign_lead_by_territory(db: Session, lead: Lead) -> None:
    if lead.assigned_to_id or lead.team_id:
        return
    territories = (
        db.query(Territory)
        .options(joinedload(Territory.rules))
        .filter(
            Territory.company_id == lead.company_id,
            Territory.is_active == True,
        )
        .order_by(Territory.priority.asc(), Territory.id.asc())
        .all()
    )
    territory = next((t for t in territories if _territory_matches_lead(t, lead)), None)
    if territory is None:
        return
    lead.team_id = territory.team_id
    user_id = round_robin_sales_on_team(db, company_id=lead.company_id, team_id=territory.team_id)
    if user_id is not None:
        lead.assigned_to_id = user_id
        notify_lead_assigned(db, lead, user_id, "A new lead was assigned to you.")


def _territory_matches_lead(territory: Territory, lead: Lead) -> bool:
    return any(_rule_matches_lead(rule, lead) for rule in territory.rules)


def _rule_matches_lead(rule, lead: Lead) -> bool:
    if rule.match_field not in MATCH_FIELDS:
        return False
    lead_value = str(getattr(lead, rule.match_field) or "").strip().lower()
    if not lead_value:
        return False
    return lead_value == rule.match_value.strip().lower()


def _user_id_with_lowest_load(db: Session, company_id: int, candidates: list[User]) -> int | None:
    if not candidates:
        return None
    open_leads = dict(
        db.query(Lead.assigned_to_id, func.count(Lead.id))
        .filter(
            Lead.company_id == company_id,
            Lead.assigned_to_id.in_([user.id for user in candidates]),
            Lead.deleted_at.is_(None),
            Lead.status.notin_(_FINISHED_STATUSES),
        )
        .group_by(Lead.assigned_to_id)
        .all()
    )
    return min(candidates, key=lambda user: (open_leads.get(user.id, 0), user.id)).id
