"""Opening the chat list must not repeat work for every team the user is on."""
from sqlalchemy import event

from app.models.core.team import Team
from app.models.core.team_membership import TeamMembership
from app.models.core.user import User
from app.services.chat import service


def _team(db, org, name, *roles):
    team = Team(company_id=org["a"].id, name=name)
    db.add(team)
    db.flush()
    for role in roles:
        db.add(TeamMembership(company_id=org["a"].id, team_id=team.id, user_id=org["users"][role].id))
    db.commit()
    return team


def _queries_for_list(db, user_id):
    me = db.get(User, user_id)
    service.list_conversations(db, me, 0, 50)
    seen = []

    def count(*_):
        seen.append(1)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", count)
    try:
        result = service.list_conversations(db, me, 0, 50)
    finally:
        event.remove(bind, "before_cursor_execute", count)
    return len(seen), result


def test_each_extra_team_adds_at_most_three_queries(org, db):
    manager = org["users"]["manager"].id
    _team(db, org, "Alpha", "manager", "sales")
    one_team, result = _queries_for_list(db, manager)
    assert [c["team"]["name"] for c in result["items"]] == ["Alpha"]

    _team(db, org, "Beta", "manager", "md")
    _team(db, org, "Gamma", "manager")
    three_teams, result = _queries_for_list(db, manager)
    assert sorted(c["team"]["name"] for c in result["items"]) == ["Alpha", "Beta", "Gamma"]

    assert (three_teams - one_team) / 2 <= 3, (one_team, three_teams)
