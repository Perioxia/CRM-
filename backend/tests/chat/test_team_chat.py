"""Team rooms: one chat per team, open only to current members."""
import pytest
from fastapi import HTTPException

from app.models.core.team import Team
from app.models.core.team_membership import TeamMembership
from app.models.core.user import User
from app.services.chat import calls
from tests.chat.conftest import As


def _team(db, org, name, *roles, primary=()):
    team = Team(company_id=org["a"].id, name=name)
    db.add(team)
    db.flush()
    for role in roles:
        user = org["users"][role]
        db.add(TeamMembership(company_id=org["a"].id, team_id=team.id, user_id=user.id))
    for role in primary:
        org["users"][role].team_id = team.id
    db.commit()
    db.refresh(team)
    return team


def test_each_team_has_one_room_for_its_members(org, db):
    team = _team(db, org, "Alpha", "sales", "manager")
    sales = As("sales@a.test")
    manager = As("manager@a.test")

    opened = sales.post("/api/chat/conversations/team", json={"team_id": team.id})
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["kind"] == "team"
    assert body["team"]["name"] == "Alpha"
    assert body["team"]["member_count"] == 2
    assert body["other_user"] is None
    assert {m["id"] for m in body["members"]} == {org["users"]["sales"].id, org["users"]["manager"].id}

    again = manager.post("/api/chat/conversations/team", json={"team_id": team.id})
    assert again.status_code == 200
    assert again.json()["id"] == body["id"]

    assert sales.send(body["id"], "stand-up").status_code == 201
    messages = manager.get(f"/api/chat/conversations/{body['id']}/messages")
    assert messages.status_code == 200
    assert [m["body"] for m in messages.json()["items"]] == ["stand-up"]

    listed = sales.get("/api/chat/conversations").json()
    rooms = [item for item in listed["items"] if item["kind"] == "team"]
    assert [item["id"] for item in rooms] == [body["id"]]


def test_outsider_and_other_company_cannot_open_the_room(org, db):
    team = _team(db, org, "Alpha", "sales", "manager")
    conv_id = As("sales@a.test").post("/api/chat/conversations/team", json={"team_id": team.id}).json()["id"]
    As("sales@a.test").send(conv_id, "private")

    for email in ("md@a.test", "admin@b.test"):
        caller = As(email)
        assert caller.post("/api/chat/conversations/team", json={"team_id": team.id}).status_code == 404
        assert caller.get(f"/api/chat/conversations/{conv_id}").status_code == 404
        assert caller.get(f"/api/chat/conversations/{conv_id}/messages").status_code == 404
        assert caller.send(conv_id, "nope").status_code == 404
        assert caller.post(f"/api/chat/conversations/{conv_id}/read", json={"seq": 1}).status_code == 404
        assert all(item["id"] != conv_id for item in caller.get("/api/chat/conversations").json()["items"])


def test_leaving_the_team_removes_access(org, db):
    team = _team(db, org, "Alpha", "sales", "manager")
    sales = As("sales@a.test")
    conv_id = sales.post("/api/chat/conversations/team", json={"team_id": team.id}).json()["id"]
    assert sales.send(conv_id, "still here").status_code == 201

    db.query(TeamMembership).filter(
        TeamMembership.team_id == team.id,
        TeamMembership.user_id == org["users"]["sales"].id,
    ).delete()
    db.commit()

    assert sales.get(f"/api/chat/conversations/{conv_id}/messages").status_code == 404
    assert sales.send(conv_id, "gone").status_code == 404
    assert all(item["id"] != conv_id for item in sales.get("/api/chat/conversations").json()["items"])

    manager = As("manager@a.test")
    history = manager.get(f"/api/chat/conversations/{conv_id}/messages").json()
    assert [m["body"] for m in history["items"]] == ["still here"]
    summary = manager.get(f"/api/chat/conversations/{conv_id}").json()
    assert summary["team"]["member_count"] == 1
    assert [m["id"] for m in summary["members"]] == [org["users"]["manager"].id]


def test_primary_team_id_counts_without_a_membership_row(org, db):
    team = _team(db, org, "Floor", "sales", primary=("purchase",))
    purchase = As("purchase@a.test")
    opened = purchase.post("/api/chat/conversations/team", json={"team_id": team.id})
    assert opened.status_code == 200, opened.text
    assert {m["id"] for m in opened.json()["members"]} == {
        org["users"]["sales"].id,
        org["users"]["purchase"].id,
    }


def test_direct_chats_stay_separate_from_the_team_room(org, db):
    team = _team(db, org, "Alpha", "sales", "manager")
    sales = As("sales@a.test")
    room_id = sales.post("/api/chat/conversations/team", json={"team_id": team.id}).json()["id"]
    direct_id = sales.start(org["users"]["manager"].id).json()["id"]
    assert room_id != direct_id
    assert sales.send(room_id, "room").status_code == 201
    assert sales.send(direct_id, "dm").status_code == 201
    room = sales.get(f"/api/chat/conversations/{room_id}/messages").json()
    direct = sales.get(f"/api/chat/conversations/{direct_id}/messages").json()
    assert [m["body"] for m in room["items"]] == ["room"]
    assert [m["body"] for m in direct["items"]] == ["dm"]


def test_voice_calls_are_rejected_in_a_team_room(org, db):
    team = _team(db, org, "Alpha", "sales", "manager")
    conv_id = As("sales@a.test").post("/api/chat/conversations/team", json={"team_id": team.id}).json()["id"]
    caller = db.query(User).filter(User.id == org["users"]["sales"].id).one()
    with pytest.raises(HTTPException) as exc:
        calls.create_call(db, caller, conv_id)
    assert exc.value.status_code == 409
