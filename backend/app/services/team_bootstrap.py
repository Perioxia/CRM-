"""Ensure company users have at least one team for scoped CRM actions."""
from sqlalchemy.orm import Session

from app.models.core.team import Team
from app.models.core.team_membership import TeamMembership
from app.models.core.user import User
from app.services.sales.lead_access import user_role
from app.utils.dependencies import apply_company_scope


def _membership_teams(db: Session, user: User) -> list[Team]:
    return (
        apply_company_scope(db.query(Team), Team, user)
        .join(TeamMembership, TeamMembership.team_id == Team.id)
        .filter(TeamMembership.user_id == user.id)
        .order_by(Team.name.asc())
        .all()
    )


def ensure_user_teams(db: Session, user: User) -> list[Team]:
    """
    Return teams the user may act as.

    Reps and managers work inside a team, so one with no team yet joins the company's first
    team (created if the company has none) and CSV import and the team switcher just work.
    MD and admin work across the company: they see every team without joining any, so they
    never land in a team's chat or member list. Purchase works on no team.
    """
    if user.company_id is None:
        return []

    role = user_role(user)
    if role in ("md", "admin"):
        return apply_company_scope(db.query(Team), Team, user).order_by(Team.name.asc()).all()
    if role not in ("sales", "manager"):
        return []

    teams = _membership_teams(db, user)
    if teams:
        return teams

    company_teams = (
        apply_company_scope(db.query(Team), Team, user).order_by(Team.id.asc()).all()
    )
    if company_teams:
        team = company_teams[0]
        _ensure_membership(db, user, team)
        if user.team_id is None:
            user.team_id = team.id
            db.commit()
            db.refresh(user)
        return company_teams

    team = Team(company_id=user.company_id, name="Default")
    db.add(team)
    db.flush()
    _ensure_membership(db, user, team)
    user.team_id = team.id
    db.commit()
    db.refresh(user)
    return [team]


def _ensure_membership(db: Session, user: User, team: Team) -> None:
    exists = (
        db.query(TeamMembership)
        .filter(
            TeamMembership.team_id == team.id,
            TeamMembership.user_id == user.id,
        )
        .first()
    )
    if exists:
        return
    db.add(
        TeamMembership(
            company_id=user.company_id,
            team_id=team.id,
            user_id=user.id,
        )
    )
    db.flush()
