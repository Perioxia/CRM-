"""MD, admin and purchase are not on a team. Opening the app must not quietly put them on one,
which used to add them to that team's chat and member list."""
import pytest

from app.models.core.team import Team
from app.models.core.team_membership import TeamMembership
from app.models.core.user import User
from app.models.sales.lead import Lead
from app.services.chat.service import _member_user_ids
from app.utils.rate_limit import auth_limiter
from tests.helpers.auth import create_active_user, login_user
from tests.helpers.factories import create_company


@pytest.fixture(autouse=True)
def _reset_auth_rate_limit():
    auth_limiter._buckets.clear()
    yield


@pytest.fixture
def company(db):
    company = create_company(db, name="Staff Co", company_code="STF")
    north = Team(company_id=company.id, name="North")
    south = Team(company_id=company.id, name="South")
    db.add_all([north, south])
    db.commit()
    people = {}
    for role in ("md", "admin", "purchase", "sales", "manager"):
        people[role] = create_active_user(db, email=f"{role}@stf.co", role=role, company_id=company.id, team_id=None)
    return company, north, south, people


def _memberships(db, user):
    db.expire_all()
    return db.query(TeamMembership).filter(TeamMembership.user_id == user.id).count()


@pytest.mark.parametrize("role", ["md", "admin"])
def test_md_and_admin_see_every_team_without_joining_one(client, db, company, role):
    _, north, south, people = company
    login_user(client, people[role].email)
    body = client.get("/api/teams/mine").json()
    assert {t["name"] for t in body["teams"]} == {"North", "South"}
    assert body["active_team_id"] is None
    assert _memberships(db, people[role]) == 0
    assert db.get(User, people[role].id).team_id is None


def test_purchase_gets_no_team_and_joins_none(client, db, company):
    _, _, _, people = company
    login_user(client, people["purchase"].email)
    body = client.get("/api/teams/mine").json()
    assert body["teams"] == [] and body["active_team_id"] is None
    assert _memberships(db, people["purchase"]) == 0


@pytest.mark.parametrize("role", ["sales", "manager"])
def test_reps_and_managers_still_join_the_first_team(client, db, company, role):
    _, north, _, people = company
    login_user(client, people[role].email)
    body = client.get("/api/teams/mine").json()
    assert body["active_team_id"] == north.id
    assert _memberships(db, people[role]) == 1
    assert db.get(User, people[role].id).team_id == north.id


def test_team_chat_members_are_only_the_team(client, db, company):
    co, north, _, people = company
    for role in ("md", "admin", "purchase", "sales", "manager"):
        client.headers.pop("Authorization", None)
        login_user(client, people[role].email)
        assert client.get("/api/teams/mine").status_code == 200
    assert _member_user_ids(db, co.id, north.id) == {people["sales"].id, people["manager"].id}


def test_md_creates_a_lead_on_the_team_picked_in_the_form(client, db, company):
    _, _, south, people = company
    login_user(client, people["md"].email)
    teams = client.get("/api/teams/mine").json()["teams"]
    picked = next(t["id"] for t in teams if t["name"] == "South")
    made = client.post("/api/leads", json={"name": "Picked Team Lead", "phone": "9000000001", "team_id": picked})
    assert made.status_code == 201, made.text
    assert db.get(Lead, made.json()["id"]).team_id == south.id
    assert _memberships(db, people["md"]) == 0


def test_md_import_without_a_team_still_lands_on_a_team(client, db, company):
    _, north, _, people = company
    login_user(client, people["md"].email)
    client.headers["X-Team-Id"] = str(north.id)  # what the screen sends after loading the team list
    csv = "Name,Phone\nImported Person,9000000002\n"
    done = client.post("/api/import/leads/commit", files={"file": ("leads.csv", csv.encode(), "text/csv")})
    assert done.status_code == 200, done.text
    lead = db.query(Lead).filter(Lead.name == "Imported Person").one()
    assert lead.team_id == north.id and lead.assigned_to_id is None
    assert _memberships(db, people["md"]) == 0
