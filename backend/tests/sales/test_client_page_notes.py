"""A client's page must keep opening after notes are added, for everyone allowed to see it."""
from app.models.sales.audit import AuditLog
from tests.helpers.factories import create_client


def _client_on_alpha(db, org):
    return create_client(db, company_id=org.company.id, name="Noted Customer",
                         assigned_to_id=org.owner.id, team_id=org.alpha.id)


def test_client_page_opens_after_a_note_for_owner_manager_and_md(api, db, org):
    customer = _client_on_alpha(db, org)
    assert api.call("owner", "POST", f"/api/clients/{customer.id}/notes", json={"content": "Called about phase 2"}).status_code == 201
    assert api.call("mgr_same_team", "POST", f"/api/clients/{customer.id}/notes", json={"content": "Approved discount"}).status_code == 201

    for who in ("owner", "mgr_same_team", "md"):
        page = api.call(who, "GET", f"/api/clients/{customer.id}")
        assert page.status_code == 200, (who, page.text)
        notes = {note["content"]: note["author"] for note in page.json()["notes"]}
        assert notes == {"Called about phase 2": "owner", "Approved discount": "mgrsame"}


def test_client_note_without_a_known_author_shows_system(api, db, org):
    from app.models.sales.note import Note

    customer = _client_on_alpha(db, org)
    db.add(Note(company_id=org.company.id, client_id=customer.id, content="Imported note"))
    db.commit()
    page = api.call("owner", "GET", f"/api/clients/{customer.id}")
    assert page.status_code == 200, page.text
    assert page.json()["notes"][0]["author"] == "System"


def test_client_note_text_is_kept_in_the_audit_log(api, db, org):
    customer = _client_on_alpha(db, org)
    api.call("owner", "POST", f"/api/clients/{customer.id}/notes", json={"content": "Wants 30 days credit"})
    entry = db.query(AuditLog).filter(AuditLog.action == "note_added", AuditLog.entity_id == str(customer.id)).one()
    assert entry.after_value == "Wants 30 days credit"


def test_others_still_cannot_open_the_client(api, db, org):
    customer = _client_on_alpha(db, org)
    api.call("owner", "POST", f"/api/clients/{customer.id}/notes", json={"content": "Private"})
    assert api.call("rep_same_team", "GET", f"/api/clients/{customer.id}").status_code == 403
    assert api.call("mgr_other_team", "GET", f"/api/clients/{customer.id}").status_code == 403
    assert api.call("other_company", "GET", f"/api/clients/{customer.id}").status_code == 404
