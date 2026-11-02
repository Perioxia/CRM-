"""Who owns a lead: creating, importing, claiming, assigning, and giving it back."""
import threading
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import Lead
from app.models.core.team import Team
from app.models.sales.lead_form import LeadForm
from app.models.sales.notification import Notification
from app.models.sales.task import Task
from app.routers.ai.assistant_crm_actions import ai_claim_lead
from app.utils.rate_limit import public_form_limiter
from app.utils.security import crm_access_token
from tests.conftest import PG_TEST_URL, TestingSessionLocal
from tests.sales.conftest import _user


def _open_lead(db, org, name="OpenLead", team="alpha"):
    team_id = getattr(org, team).id if team else None
    lead = Lead(company_id=org.company.id, name=name, status="Active", team_id=team_id)
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def _lead(db, lead_id):
    db.expire_all()
    return db.get(Lead, lead_id)


def _in_list(api, who, lead_id, **params):
    rows = api.call(who, "GET", "/api/leads", params={"limit": 500, **params}).json()["items"]
    return any(row["id"] == lead_id for row in rows)


def _notified(db, user, text):
    db.expire_all()
    return db.query(Notification).filter(
        Notification.user_id == user.id, Notification.title.contains(text)
    ).count()


def _create(api, who, **body):
    return api.call(who, "POST", "/api/leads", json={"team_id": api.org.alpha.id} | body)


# ---- "Open to Anyone" ----

@pytest.mark.parametrize("creator", ["mgr_same_team", "md"])
def test_open_to_anyone_stays_open_until_claimed(api, db, org, creator):
    made = _create(api, creator, name="LeaveOpen")
    assert made.status_code == 201, made.text
    assert made.json()["assigned_to_id"] is None
    lead_id = made.json()["id"]
    assert _in_list(api, "owner", lead_id, unassigned="true")
    assert _in_list(api, "rep_same_team", lead_id, unassigned="true")
    assert not _in_list(api, "rep_other_team", lead_id)
    task = db.query(Task).filter(Task.lead_id == lead_id).one()
    assert task.assigned_to_id is None

    assert api.call("owner", "POST", f"/api/leads/{lead_id}/claim").status_code == 200
    assert not _in_list(api, "rep_same_team", lead_id)
    assert api.call("rep_same_team", "GET", f"/api/leads/{lead_id}").status_code == 403
    assert api.call("rep_same_team", "POST", f"/api/leads/{lead_id}/claim").status_code == 400
    db.expire_all()
    assert db.query(Task).filter(Task.lead_id == lead_id).one().assigned_to_id == org.owner.id


def test_lead_for_a_chosen_rep_goes_to_that_rep_and_tells_them(api, db, org):
    made = _create(api, "mgr_same_team", name="ForYou", assigned_to_id=org.owner.id)
    assert made.status_code == 201
    assert made.json()["assigned_to_id"] == org.owner.id
    assert _notified(db, org.owner, "ForYou") == 1
    assert not _in_list(api, "rep_same_team", made.json()["id"])


def test_rep_lead_belongs_to_the_rep_and_stays_private(api, org):
    made = _create(api, "owner", name="MineOnly")
    assert made.json()["assigned_to_id"] == org.owner.id
    assert not _in_list(api, "rep_same_team", made.json()["id"])


def test_rep_cannot_create_a_lead_for_someone_else(api, org):
    assert _create(api, "owner", name="Pushed", assigned_to_id=org.rep_same_team.id).status_code == 403


def test_website_lead_is_auto_assigned_and_the_rep_is_told(client, api, db, org):
    db.add(LeadForm(company_id=org.company.id, slug="claimform", name="f", is_active=True,
                    default_team_id=org.alpha.id, headline="h"))
    db.commit()
    public_form_limiter._buckets.clear()
    sent = client.post("/api/public/forms/claimform/submit", json={"name": "FromWeb", "phone": "919000000001"})
    assert sent.status_code == 201, sent.text
    lead = db.query(Lead).filter(Lead.name == "FromWeb").one()
    assert lead.assigned_to_id in (org.owner.id, org.rep_same_team.id)
    owner = org.owner if lead.assigned_to_id == org.owner.id else org.rep_same_team
    assert _notified(db, owner, "FromWeb") == 1


# ---- imports ----

def _import(api, who, csv):
    return api.call(who, "POST", "/api/import/leads/text", json={"csv": csv, "commit": True})


def test_rep_import_belongs_to_the_rep(api, db, org):
    assert _import(api, "owner", "name,email\nRepCsv,repcsv@acme.co\n").status_code == 200
    lead = db.query(Lead).filter(Lead.name == "RepCsv").one()
    assert lead.assigned_to_id == org.owner.id
    assert not _in_list(api, "rep_same_team", lead.id)


def test_manager_import_goes_to_the_pool(api, db):
    assert _import(api, "mgr_same_team", "name,email\nMgrCsv,mgrcsv@acme.co\n").status_code == 200
    lead = db.query(Lead).filter(Lead.name == "MgrCsv").one()
    assert lead.assigned_to_id is None
    assert _in_list(api, "owner", lead.id, unassigned="true")


def test_google_sheet_leads_go_to_the_pool(api, db):
    assert api.call("md", "PUT", "/api/import/sheets", json={
        "url": "https://docs.google.com/spreadsheets/d/abc/edit",
    }).status_code == 200
    with mock.patch("app.services.sales.lead_sheet.fetch_sheet_csv", return_value=b"name,email\nSheetRow,sr@acme.co\n"):
        assert api.call("md", "POST", "/api/import/sheets/sync").status_code == 200
    assert db.query(Lead).filter(Lead.name == "SheetRow").one().assigned_to_id is None


# ---- claiming ----

def test_rep_with_no_team_cannot_claim_a_teams_lead(api, db, org):
    no_team = _user(db, "noteam", "sales", org.company)
    lead = _open_lead(db, org)
    assert api.call(no_team, "POST", f"/api/leads/{lead.id}/claim").status_code == 403


def test_rep_cannot_claim_another_teams_lead(api, db, org):
    lead = _open_lead(db, org)
    assert api.call("rep_other_team", "POST", f"/api/leads/{lead.id}/claim").status_code == 403


def test_ai_claim_follows_the_same_rules(api, db, org):
    other_team = _open_lead(db, org, "AiOther", team="beta")
    assert ai_claim_lead(db, org.owner, other_team.id, None).get("ok") is False
    assert _lead(db, other_team.id).assigned_to_id is None

    deleted = _open_lead(db, org, "AiDeleted")
    assert api.call("mgr_same_team", "DELETE", f"/api/leads/{deleted.id}").status_code == 200
    assert ai_claim_lead(db, org.owner, deleted.id, None).get("ok") is False

    open_lead = _open_lead(db, org, "AiOpen")
    assert ai_claim_lead(db, org.owner, open_lead.id, None) == {"lead_id": open_lead.id, "claimed_by": org.owner.id}
    assert ai_claim_lead(db, org.rep_same_team, open_lead.id, None).get("ok") is False
    assert _lead(db, open_lead.id).assigned_to_id == org.owner.id


# ---- assigning from the pool ----

def test_pool_assign_cannot_take_a_lead_someone_just_claimed(api, db, org):
    lead = _open_lead(db, org)
    assert api.call("owner", "POST", f"/api/leads/{lead.id}/claim").status_code == 200
    late = api.call("md", "PUT", f"/api/leads/{lead.id}", json={
        "assigned_to_id": org.rep_same_team.id, "only_if_unassigned": True,
    })
    assert late.status_code == 409, late.text
    assert _lead(db, lead.id).assigned_to_id == org.owner.id


def test_pool_assign_works_on_an_open_lead(api, db, org):
    lead = _open_lead(db, org)
    done = api.call("md", "PUT", f"/api/leads/{lead.id}", json={
        "assigned_to_id": org.owner.id, "only_if_unassigned": True,
    })
    assert done.status_code == 200, done.text
    assert _lead(db, lead.id).assigned_to_id == org.owner.id
    assert _notified(db, org.owner, lead.name) == 1


# ---- reassigning ----

def test_reassign_moves_open_tasks_and_tells_both_people(api, db, org):
    lead_id = api.new_lead()
    done_task = Task(company_id=org.company.id, title="Done", lead_id=lead_id,
                     assigned_to_id=org.owner.id, status="Completed")
    db.add(done_task)
    db.commit()
    moved = api.call("md", "PUT", f"/api/leads/{lead_id}", json={"assigned_to_id": org.rep_same_team.id})
    assert moved.status_code == 200, moved.text
    db.expire_all()
    owners = {t.title: t.assigned_to_id for t in db.query(Task).filter(Task.lead_id == lead_id)}
    assert owners["Done"] == org.owner.id
    assert [uid for title, uid in owners.items() if title != "Done"] == [org.rep_same_team.id]
    name = _lead(db, lead_id).name
    assert _notified(db, org.rep_same_team, name) == 1
    assert _notified(db, org.owner, name) == 1


def test_reassign_to_another_team_moves_the_lead_to_that_team(api, db, org):
    lead_id = api.new_lead()
    assert api.call("md", "PUT", f"/api/leads/{lead_id}", json={
        "assigned_to_id": org.rep_other_team.id,
    }).status_code == 200
    assert _lead(db, lead_id).team_id == org.beta.id
    assert api.call("mgr_other_team", "GET", f"/api/leads/{lead_id}").status_code == 200
    assert api.call("mgr_same_team", "GET", f"/api/leads/{lead_id}").status_code == 403


def test_manager_cannot_reassign_a_rep_created_lead_unless_the_rep_is_inactive(api, db, org):
    lead_id = api.new_lead()
    body = {"assigned_to_id": org.rep_same_team.id}
    assert api.call("mgr_same_team", "PUT", f"/api/leads/{lead_id}", json=body).status_code == 403
    org.owner.is_active = False
    db.commit()
    assert api.call("mgr_same_team", "PUT", f"/api/leads/{lead_id}", json=body).status_code == 200


def test_manager_route_for_reassign_follows_the_same_rules(api, db, org):
    lead_id = api.new_lead()
    path = f"/api/manager/leads/{lead_id}/reassign"
    params = {"new_assignee_id": org.rep_same_team.id}
    assert api.call("mgr_same_team", "POST", path, params=params).status_code == 403
    made = _create(api, "mgr_same_team", name="MgrMade", assigned_to_id=org.owner.id).json()["id"]
    moved = api.call("mgr_same_team", "POST", f"/api/manager/leads/{made}/reassign", params=params)
    assert moved.status_code == 200, moved.text
    db.expire_all()
    assert db.query(Task).filter(Task.lead_id == made).one().assigned_to_id == org.rep_same_team.id


def test_leads_cannot_go_to_inactive_or_purchase_users(api, db, org):
    gone = _user(db, "gone", "sales", org.company, org.alpha)
    gone.is_active = False
    db.commit()
    for target in (gone, org.purchase):
        assert _create(api, "md", name=f"To{target.id}", assigned_to_id=target.id).status_code == 400, target.email
        lead_id = api.new_lead()
        assert api.call("md", "PUT", f"/api/leads/{lead_id}", json={"assigned_to_id": target.id}).status_code == 400
    for params in ({}, {"team_id": org.alpha.id}):
        members = api.call("md", "GET", "/api/leads/team-members", params=params).json()["members"]
        assert gone.id not in {m["id"] for m in members}, params


def test_multi_team_manager_assigns_by_the_leads_team(api, db, org):
    from app.models.core.team_membership import TeamMembership

    db.add(TeamMembership(company_id=org.company.id, team_id=org.beta.id, user_id=org.mgr_same_team.id))
    db.commit()
    right = api.call("mgr_same_team", "POST", "/api/leads", json={
        "name": "BetaForBeta", "team_id": org.beta.id, "assigned_to_id": org.rep_other_team.id,
    })
    assert right.status_code == 201, right.text
    wrong = api.call("mgr_same_team", "POST", "/api/leads", json={
        "name": "BetaForAlpha", "team_id": org.beta.id, "assigned_to_id": org.owner.id,
    })
    assert wrong.status_code == 403, wrong.text


def test_md_cannot_create_a_lead_whose_owner_is_on_another_team(api, org):
    wrong = api.call("md", "POST", "/api/leads", json={
        "name": "Mismatch", "team_id": org.alpha.id, "assigned_to_id": org.rep_other_team.id,
    })
    assert wrong.status_code == 403, wrong.text
    no_team_given = api.call("md", "POST", "/api/leads", json={
        "name": "FollowsRep", "assigned_to_id": org.rep_other_team.id,
    })
    assert no_team_given.status_code == 201, no_team_given.text
    assert no_team_given.json()["team_id"] == org.beta.id


# ---- back to the pool ----

def test_manager_can_send_a_wrong_claim_back_to_the_pool(api, db, org):
    lead_id = _create(api, "mgr_same_team", name="WrongClaim").json()["id"]
    assert api.call("owner", "POST", f"/api/leads/{lead_id}/claim").status_code == 200
    for blocked in ("owner", "rep_same_team", "mgr_other_team", "purchase"):
        assert api.call(blocked, "POST", f"/api/leads/{lead_id}/release").status_code == 403, blocked
    released = api.call("mgr_same_team", "POST", f"/api/leads/{lead_id}/release")
    assert released.status_code == 200, released.text
    assert _lead(db, lead_id).assigned_to_id is None
    assert _in_list(api, "rep_same_team", lead_id, unassigned="true")
    assert _notified(db, org.owner, "WrongClaim") >= 1
    db.expire_all()
    assert db.query(Task).filter(Task.lead_id == lead_id).one().assigned_to_id is None
    assert api.call("rep_same_team", "POST", f"/api/leads/{lead_id}/claim").status_code == 200


def test_back_to_pool_limits(api, db, org):
    rep_made = api.new_lead()
    assert api.call("mgr_same_team", "POST", f"/api/leads/{rep_made}/release").status_code == 403
    assert api.call("md", "POST", f"/api/leads/{rep_made}/release").status_code == 200
    assert api.call("md", "POST", f"/api/leads/{rep_made}/release").status_code == 400
    converted = api.new_lead()
    assert api.call("owner", "POST", f"/api/leads/{converted}/convert").status_code == 200
    assert api.call("md", "POST", f"/api/leads/{converted}/release").status_code == 400


# ---- leads with no team ----

def test_open_leads_with_no_team_are_only_for_md(api, db, org):
    lead = _open_lead(db, org, "NoTeam", team=None)
    for rep in ("owner", "rep_other_team"):
        assert not _in_list(api, rep, lead.id), rep
        assert api.call(rep, "GET", f"/api/leads/{lead.id}").status_code == 403, rep
        assert api.call(rep, "POST", f"/api/leads/{lead.id}/claim").status_code == 403, rep
    assert not _in_list(api, "mgr_same_team", lead.id)
    assert _in_list(api, "md", lead.id, unassigned="true")
    assert api.call("md", "PUT", f"/api/leads/{lead.id}", json={"assigned_to_id": org.owner.id}).status_code == 200
    assert _lead(db, lead.id).team_id == org.alpha.id


def test_deleting_a_team_does_not_show_its_open_leads_to_every_rep(api, db, org):
    doomed = Team(company_id=org.company.id, name="Doomed")
    db.add(doomed)
    db.commit()
    lead = Lead(company_id=org.company.id, name="Orphan", status="Active", team_id=doomed.id)
    db.add(lead)
    db.commit()
    lead_id = lead.id
    lead.team_id = None
    db.commit()
    assert not _in_list(api, "rep_other_team", lead_id)
    assert _in_list(api, "md", lead_id)


# ---- real concurrency needs a real database ----

needs_postgres = pytest.mark.skipif(not PG_TEST_URL, reason="CRM_PG_TEST_URL not set")


@pytest.fixture
def separate_sessions(db_engine):
    """Every request gets its own database session, the way the real app does."""
    def own_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = own_session
    yield
    app.dependency_overrides.pop(get_db, None)


def _at_once(*calls):
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def run(index, call):
        barrier.wait()
        results[index] = call()

    threads = [threading.Thread(target=run, args=(i, c)) for i, c in enumerate(calls)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return results


def _headers(user):
    return {"Authorization": f"Bearer {crm_access_token(user)}", "X-Team-Id": str(user.team_id)}


@needs_postgres
def test_two_reps_claiming_together_get_exactly_one_winner(org, db, separate_sessions):
    client_a = TestClient(app, raise_server_exceptions=False)
    client_b = TestClient(app, raise_server_exceptions=False)
    headers_a, headers_b = _headers(org.owner), _headers(org.rep_same_team)
    for round_number in range(25):
        lead = _open_lead(db, org, f"Race{round_number}")
        lead_id = lead.id
        db.commit()
        codes = _at_once(
            lambda: client_a.post(f"/api/leads/{lead_id}/claim", headers=headers_a).status_code,
            lambda: client_b.post(f"/api/leads/{lead_id}/claim", headers=headers_b).status_code,
        )
        assert sorted(codes) == [200, 400], (round_number, codes)


@needs_postgres
def test_two_sheet_syncs_together_do_not_double_the_leads(org, db, api, separate_sessions):
    assert api.call("md", "PUT", "/api/import/sheets", json={
        "url": "https://docs.google.com/spreadsheets/d/abc/edit",
    }).status_code == 200
    db.commit()
    csv = b"name,email\nRowOne,row1@acme.co\nRowTwo,row2@acme.co\n"
    client_a = TestClient(app, raise_server_exceptions=False)
    client_b = TestClient(app, raise_server_exceptions=False)
    headers = _headers(org.md) | {"X-Team-Id": str(org.alpha.id)}

    def slow_fetch(*_args, **_kwargs):
        threading.Event().wait(0.5)
        return csv

    with mock.patch("app.services.sales.lead_sheet.fetch_sheet_csv", side_effect=slow_fetch):
        codes = _at_once(
            lambda: client_a.post("/api/import/sheets/sync", headers=headers).status_code,
            lambda: client_b.post("/api/import/sheets/sync", headers=headers).status_code,
        )
    assert codes == [200, 200]
    db.expire_all()
    assert db.query(Lead).filter(Lead.email.in_(["row1@acme.co", "row2@acme.co"])).count() == 2
