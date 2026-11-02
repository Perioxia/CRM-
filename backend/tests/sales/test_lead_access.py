"""Who may see and work on a lead.

Every action runs as the owner first, so a block proves the rule and not a broken request.
"""
from unittest import mock

import pytest

from app.models import Lead
from app.models.sales.note import Note
from app.models.sales.whatsapp import WhatsAppMessage
from app.routers.ai.assistant_crm_actions import ai_list_leads

TOMORROW = "2030-01-01"
STARTS = "2030-01-01T10:00:00"
OUTSIDERS = ("rep_same_team", "rep_other_team", "mgr_other_team", "purchase", "other_company")


def _leaks(response):
    if response.status_code != 200:
        return False
    if response.request.url.path == "/api/search":
        return bool([r for r in response.json()["results"] if r["type"] == "lead"])
    return "SECRET" in response.text


def _seed_activity(api, db):
    lead_id = api.new_lead()
    assert api.call("owner", "POST", f"/api/leads/{lead_id}/notes", params={"content": "SECRETNOTE"}).status_code == 201
    assert api.call("owner", "POST", "/api/emails", json={
        "lead_id": lead_id, "to_email": "buyer@customer.co", "subject": "SECRETMAIL", "body": "hello",
    }).status_code == 201
    assert api.call("owner", "POST", "/api/calls", json={
        "lead_id": lead_id, "direction": "outbound", "notes": "SECRETCALL",
    }).status_code == 201
    db.add(WhatsAppMessage(
        company_id=api.org.company.id, lead_id=lead_id, to_phone="919000000001",
        direction="outbound", body="SECRETWHATSAPP", sent_by_id=api.org.owner.id,
    ))
    db.commit()
    return lead_id


def test_only_allowed_people_can_read_a_lead_and_its_activity(api, db):
    lead_id = _seed_activity(api, db)
    name = db.get(Lead, lead_id).name
    routes = [
        (f"/api/leads/{lead_id}", {}),
        (f"/api/leads/{lead_id}/notes", {}),
        (f"/api/timeline/lead/{lead_id}", {}),
        ("/api/emails", {"lead_id": lead_id}),
        ("/api/emails", {}),
        ("/api/calls", {"lead_id": lead_id}),
        ("/api/calls", {}),
        ("/api/whatsapp/messages", {"lead_id": lead_id}),
        ("/api/whatsapp/messages", {}),
        ("/api/search", {"q": name}),
        ("/api/export/leads", {}),
        ("/api/leads", {"limit": 500}),
    ]
    for path, params in routes:
        for allowed in ("owner", "mgr_same_team", "md"):
            response = api.call(allowed, "GET", path, params=params)
            assert response.status_code == 200, (allowed, path, params, response.text)
            assert _leaks(response), (allowed, path, params, "should see the data")
        for outsider in OUTSIDERS:
            response = api.call(outsider, "GET", path, params=params)
            assert not _leaks(response), (outsider, path, params, "must not see the data")


def test_score_and_duplicates_need_access_to_the_lead(api):
    lead_id = api.new_lead()
    for path, params in ((f"/api/leads/{lead_id}/score", {}), ("/api/leads/duplicates", {"lead_id": lead_id})):
        assert api.call("owner", "GET", path, params=params).status_code == 200
        assert api.call("mgr_same_team", "GET", path, params=params).status_code == 200
        for outsider in ("rep_same_team", "rep_other_team", "mgr_other_team", "purchase"):
            assert api.call(outsider, "GET", path, params=params).status_code == 403, (outsider, path)
        assert api.call("other_company", "GET", path, params=params).status_code == 404, path


def test_duplicates_hide_contact_details_of_leads_the_rep_cannot_see(api, db, org):
    lead_a = api.new_lead(phone="9111111111")
    lead_b = Lead(company_id=org.company.id, name="BravoCopy", status="Active", phone="9111111111",
                  team_id=org.alpha.id, assigned_to_id=org.rep_same_team.id)
    db.add(lead_b)
    db.commit()
    db.refresh(lead_b)
    seen_by_rep = api.call("rep_same_team", "GET", "/api/leads/duplicates", params={"lead_id": lead_b.id}).json()
    assert seen_by_rep["items"] == []
    seen_by_manager = api.call("mgr_same_team", "GET", "/api/leads/duplicates", params={"lead_id": lead_b.id}).json()
    assert [row["id"] for row in seen_by_manager["items"]] == [lead_a]


def _write_actions(api, db):
    """(label, action). Each action gets a fresh lead the owner holds, and runs as `who`."""
    def make_call(lead_id):
        return api.call("owner", "POST", "/api/calls", json={"lead_id": lead_id, "direction": "outbound"}).json()["id"]

    def make_meeting(lead_id):
        return api.call("owner", "POST", "/api/meetings", json={
            "subject": "m", "starts_at": STARTS, "lead_id": lead_id,
        }).json()["id"]

    def make_deal(_lead_id):
        return api.call("owner", "POST", "/api/deals", json={
            "title": "d", "amount": 1, "assigned_to_id": api.org.owner.id, "team_id": api.org.alpha.id,
        }).json()["id"]

    def restore(who, lead_id):
        api.call("owner", "DELETE", f"/api/leads/{lead_id}")
        return api.call(who, "POST", f"/api/leads/{lead_id}/restore")

    def open_whatsapp_session(lead_id):
        api.call("owner", "PUT", f"/api/leads/{lead_id}", json={"phone": "919000000001"})
        return lead_id

    return [
        ("edit", lambda who, l: api.call(who, "PUT", f"/api/leads/{l}", json={"company": "x"})),
        ("status", lambda who, l: api.call(who, "PATCH", f"/api/leads/{l}/status", json={"status": "Lost"})),
        ("add note", lambda who, l: api.call(who, "POST", f"/api/leads/{l}/notes", params={"content": "x"})),
        ("convert", lambda who, l: api.call(who, "POST", f"/api/leads/{l}/convert")),
        ("enrich", lambda who, l: api.call(who, "POST", f"/api/leads/{l}/enrich")),
        ("delete", lambda who, l: api.call(who, "DELETE", f"/api/leads/{l}")),
        ("merge", lambda who, l: api.call(who, "POST", f"/api/leads/{l}/merge", json={"source_id": api.new_lead()})),
        ("restore", restore),
        ("add task", lambda who, l: api.call(who, "POST", "/api/tasks", json={
            "title": "t", "lead_id": l, "due_date": TOMORROW, "priority": "medium",
        })),
        ("add follow-up", lambda who, l: api.call(who, "POST", "/api/follow-ups", json={
            "lead_id": l, "scheduled_date": TOMORROW,
        })),
        ("add meeting", lambda who, l: api.call(who, "POST", "/api/meetings", json={
            "subject": "m", "starts_at": STARTS, "lead_id": l,
        })),
        ("read meeting", lambda who, l: api.call(who, "GET", f"/api/meetings/{make_meeting(l)}")),
        ("edit meeting", lambda who, l: api.call(who, "PATCH", f"/api/meetings/{make_meeting(l)}", json={"subject": "z"})),
        ("delete meeting", lambda who, l: api.call(who, "DELETE", f"/api/meetings/{make_meeting(l)}")),
        ("log call", lambda who, l: api.call(who, "POST", "/api/calls", json={"lead_id": l, "direction": "outbound"})),
        ("read call", lambda who, l: api.call(who, "GET", f"/api/calls/{make_call(l)}")),
        ("edit call", lambda who, l: api.call(who, "PATCH", f"/api/calls/{make_call(l)}", json={"notes": "z"})),
        ("delete call", lambda who, l: api.call(who, "DELETE", f"/api/calls/{make_call(l)}")),
        ("send email", lambda who, l: api.call(who, "POST", "/api/emails", json={
            "lead_id": l, "to_email": "buyer@customer.co", "subject": "s", "body": "b",
        })),
        ("whatsapp", lambda who, l: api.call(who, "POST", "/api/whatsapp/session-send", json={
            "lead_id": open_whatsapp_session(l), "body": "hello",
        })),
        ("click to call", lambda who, l: api.call(who, "POST", "/api/telephony/click-to-call", json={
            "lead_id": l, "from_phone": "919000000009",
        })),
        ("create deal", lambda who, l: api.call(who, "POST", "/api/deals", json={
            "title": "d", "lead_id": l, "amount": 1,
        })),
        ("link deal", lambda who, l: api.call(who, "PATCH", f"/api/deals/{make_deal(l)}", json={"lead_id": l})),
        ("link client", lambda who, l: api.call(who, "POST", "/api/clients", json={
            "name": "c", "email": f"client{l}@customer.co", "converted_from_lead_id": l,
        })),
        ("list documents", lambda who, l: api.call(who, "GET", f"/api/documents/lead/{l}")),
    ]


def _with_providers_mocked():
    return (
        mock.patch("app.routers.sales.whatsapp.post_gupshup_session_text", return_value=(True, "ok")),
        mock.patch("app.routers.sales.whatsapp.session_open_until", return_value="2030-01-01"),
        mock.patch("app.routers.sales.telephony.place_exotel_call", return_value="SID"),
    )


@pytest.mark.parametrize("allowed", ["owner", "mgr_same_team", "md"])
def test_allowed_people_can_work_on_the_lead(api, db, allowed):
    wa, session, call = _with_providers_mocked()
    with wa, session, call:
        for label, action in _write_actions(api, db):
            response = action(allowed, api.new_lead())
            assert response.status_code < 300, (allowed, label, response.status_code, response.text)


@pytest.mark.parametrize("outsider", OUTSIDERS)
def test_nobody_else_can_work_on_the_lead(api, db, outsider):
    wa, session, call = _with_providers_mocked()
    with wa as sent, session, call as dialled:
        for label, action in _write_actions(api, db):
            response = action(outsider, api.new_lead())
            assert response.status_code in (400, 403, 404), (outsider, label, response.status_code, response.text)
        assert not sent.called and not dialled.called


def test_a_deal_does_not_open_the_way_to_someone_elses_lead(api, db, org):
    lead_id = api.new_lead()
    own_lead = Lead(company_id=org.company.id, name="RepsOwn", status="Active",
                    team_id=org.alpha.id, assigned_to_id=org.rep_same_team.id)
    db.add(own_lead)
    db.commit()
    db.refresh(own_lead)
    deal = api.call("owner", "POST", "/api/deals", json={"title": "d", "lead_id": lead_id, "amount": 1})
    assert deal.status_code == 201, deal.text
    deal_id = deal.json()["id"]
    email = {"deal_id": deal_id, "subject": "s", "body": "b"}
    call = {"deal_id": deal_id, "from_phone": "919000000009"}
    with mock.patch("app.services.sales.crm_email.send_email", return_value=True) as mailed, \
            mock.patch("app.routers.sales.telephony.place_exotel_call", return_value="SID") as dialled:
        for outsider in OUTSIDERS:
            for path, body in (("/api/emails", email), ("/api/telephony/click-to-call", call)):
                response = api.call(outsider, "POST", path, json=body)
                assert response.status_code in (400, 403, 404), (outsider, path, response.status_code, response.text)
        assert api.call("rep_same_team", "POST", "/api/emails", json=email | {"lead_id": own_lead.id}).status_code == 403
        assert not mailed.called and not dialled.called
        for allowed in ("owner", "mgr_same_team", "md"):
            assert api.call(allowed, "POST", "/api/emails", json=email).status_code == 201, allowed
            assert api.call(allowed, "POST", "/api/telephony/click-to-call", json=call).status_code == 201, allowed
        assert mailed.call_count == 3 and dialled.call_count == 3


def test_rep_can_view_an_open_lead_but_must_claim_before_working(api, db, org):
    lead = Lead(company_id=org.company.id, name="OpenLead", status="Active", team_id=org.alpha.id)
    db.add(lead)
    db.commit()
    db.refresh(lead)
    rep = "rep_same_team"
    assert api.call(rep, "GET", f"/api/leads/{lead.id}").status_code == 200
    assert api.call(rep, "GET", f"/api/timeline/lead/{lead.id}").status_code == 200
    assert api.call(rep, "GET", "/api/emails", params={"lead_id": lead.id}).status_code == 200
    assert api.call(rep, "POST", f"/api/leads/{lead.id}/notes", params={"content": "x"}).status_code == 403
    task = {"title": "t", "lead_id": lead.id, "due_date": TOMORROW, "priority": "medium"}
    assert api.call(rep, "POST", "/api/tasks", json=task).status_code == 403
    assert api.call(rep, "POST", f"/api/leads/{lead.id}/claim").status_code == 200
    assert api.call(rep, "POST", f"/api/leads/{lead.id}/notes", params={"content": "x"}).status_code == 201
    assert api.call(rep, "POST", "/api/tasks", json=task).status_code == 201
    assert api.call("rep_other_team", "GET", f"/api/leads/{lead.id}").status_code == 403


def test_reassign_is_for_managers_not_reps(api, org):
    lead_id = api.new_lead()
    body = {"assigned_to_id": org.rep_same_team.id}
    for who in ("owner", "rep_same_team", "rep_other_team", "mgr_other_team", "purchase"):
        assert api.call(who, "PUT", f"/api/leads/{lead_id}", json=body).status_code == 403, who
    assert api.call("md", "PUT", f"/api/leads/{lead_id}", json=body).status_code == 200


def test_search_links_open_the_lead_and_skip_deleted_leads(api, db):
    lead_id = api.new_lead()
    name = db.get(Lead, lead_id).name
    hit = api.call("owner", "GET", "/api/search", params={"q": name}).json()["results"][0]
    assert hit["url"] == f"/sales/leads/{lead_id}"
    hit = api.call("md", "GET", "/api/search", params={"q": name}).json()["results"][0]
    assert hit["url"] == f"/md/leads/{lead_id}"
    api.call("owner", "DELETE", f"/api/leads/{lead_id}")
    for who in ("owner", "md"):
        assert not _leaks(api.call(who, "GET", "/api/search", params={"q": name})), who
        assert name not in api.call(who, "GET", "/api/export/leads").text, who


def test_purchase_has_no_lead_access(api, db):
    lead_id = _seed_activity(api, db)
    assert api.call("purchase", "GET", "/api/leads").json()["items"] == []
    assert api.call("purchase", "GET", f"/api/leads/{lead_id}").status_code == 403
    assert api.call("purchase", "GET", "/api/leads/trash").json()["items"] == []
    assert api.call("purchase", "GET", "/api/leads/dashboard").json()["metrics"]["total_leads"] == 0
    assert api.call("purchase", "POST", "/api/leads", json={"name": "x"}).status_code == 403
    assert api.call("purchase", "POST", "/api/import/leads/text", json={
        "csv": "name,email\nBuyerImport,b@customer.co\n", "commit": True,
    }).status_code == 403
    assert "SECRET" not in api.call("purchase", "GET", "/api/tasks/list").text
    assert "SECRET" not in api.call("purchase", "GET", "/api/follow-ups").text


def test_ai_assistant_gives_purchase_no_leads(api, db, org):
    api.new_lead()
    assert ai_list_leads(db, org.purchase, 20, None, None).get("leads", []) == []


def test_trash_only_lists_what_the_user_can_restore(api):
    lead_id = api.new_lead()
    api.call("owner", "DELETE", f"/api/leads/{lead_id}")
    assert [row["id"] for row in api.call("owner", "GET", "/api/leads/trash").json()["items"]] == [lead_id]
    assert api.call("rep_same_team", "GET", "/api/leads/trash").json()["items"] == []


def _import(api, who, csv):
    return api.call(who, "POST", "/api/import/leads/text", json={"csv": csv, "commit": True})


def test_rep_cannot_see_or_undo_someone_elses_import(api, db):
    assert _import(api, "mgr_same_team", "name,email\nImpOne,i1@customer.co\n").status_code == 200
    assert api.call("rep_same_team", "GET", "/api/import/last").json()["batch"] is None
    assert api.call("rep_same_team", "POST", "/api/import/undo").status_code == 400
    assert db.query(Lead).filter(Lead.name == "ImpOne", Lead.deleted_at.is_(None)).count() == 1


def test_rep_can_undo_their_own_import(api, db):
    assert _import(api, "owner", "name,email\nImpOwn,own@customer.co\n").status_code == 200
    assert api.call("owner", "POST", "/api/import/undo").status_code == 200
    db.expire_all()
    assert db.query(Lead).filter(Lead.name == "ImpOwn", Lead.deleted_at.is_(None)).count() == 0


def test_undo_skips_leads_someone_claimed_or_converted(api, db):
    csv = "name,email\nKeepClaim,k1@customer.co\nKeepConvert,k2@customer.co\nDropMe,k3@customer.co\n"
    assert _import(api, "mgr_same_team", csv).status_code == 200
    claimed = db.query(Lead).filter(Lead.name == "KeepClaim").one().id
    converted = db.query(Lead).filter(Lead.name == "KeepConvert").one().id
    dropped = db.query(Lead).filter(Lead.name == "DropMe").one().id
    assert api.call("owner", "POST", f"/api/leads/{claimed}/claim").status_code == 200
    assert api.call("mgr_same_team", "POST", f"/api/leads/{converted}/convert").status_code == 200
    undone = api.call("mgr_same_team", "POST", "/api/import/undo")
    assert undone.status_code == 200, undone.text
    assert undone.json()["removed"] == 1
    assert undone.json()["skipped"] == 2
    db.expire_all()
    assert db.get(Lead, claimed).deleted_at is None
    assert db.get(Lead, converted).deleted_at is None
    assert db.get(Lead, dropped).deleted_at is not None


def test_manager_can_only_undo_their_own_teams_imports(api, db):
    assert _import(api, "rep_other_team", "name,email\nBetaImp,beta@customer.co\n").status_code == 200
    assert api.call("mgr_same_team", "POST", "/api/import/undo").status_code == 400
    db.expire_all()
    assert db.query(Lead).filter(Lead.name == "BetaImp", Lead.deleted_at.is_(None)).count() == 1
    assert api.call("mgr_other_team", "POST", "/api/import/undo").status_code == 200


def test_client_cannot_be_linked_to_another_companys_lead(api):
    lead_id = api.new_lead()
    body = {"name": "c", "email": "foreign@customer.co", "converted_from_lead_id": lead_id}
    assert api.call("other_company", "POST", "/api/clients", json=body).status_code == 400


def test_editing_a_missing_lead_returns_404(api):
    lead_id = api.new_lead()
    assert api.call("owner", "PUT", "/api/leads/99999999", json={"company": "x"}).status_code == 404
    assert api.call("other_company", "PUT", f"/api/leads/{lead_id}", json={"company": "x"}).status_code == 404


def test_notes_stay_on_the_lead_after_the_owner_writes_them(api, db):
    lead_id = api.new_lead()
    api.call("owner", "POST", f"/api/leads/{lead_id}/notes", params={"content": "kept"})
    assert db.query(Note).filter(Note.lead_id == lead_id).count() == 1
