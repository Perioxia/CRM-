"""Keeping lead data correct: merge, permanent delete, deleted leads, duplicates, validation, status."""
import pytest

from app.models import Lead
from app.models.sales.call_log import CallLog
from app.models.sales.client import Client
from app.models.sales.custom_field import CustomFieldDef, CustomFieldValue
from app.models.sales.deal import Deal
from app.models.sales.email_log import EmailLog
from app.models.sales.lead_form import LeadForm
from app.models.sales.meeting import Meeting
from app.models.sales.note import Note
from app.models.sales.notification import Notification
from app.models.sales.task import Task
from app.models.sales.whatsapp import WhatsAppMessage
from app.routers.finance.export import build_csv_string
from app.utils.helpers import phone_variants
from app.utils.rate_limit import public_form_limiter

TOMORROW = "2030-01-01"
STARTS = "2030-01-01T10:00:00"
FORM = "dataform"


@pytest.fixture
def web_form(db, org):
    db.add(LeadForm(company_id=org.company.id, slug=FORM, name="f", is_active=True,
                    default_team_id=org.alpha.id, headline="h"))
    db.commit()
    public_form_limiter._buckets.clear()


def _submit(client, **body):
    public_form_limiter._buckets.clear()
    return client.post(f"/api/public/forms/{FORM}/submit", json=body)


def _link_activity(api, db, lead_id):
    org = api.org
    db.add(WhatsAppMessage(company_id=org.company.id, lead_id=lead_id, to_phone="919000000001",
                           direction="outbound", body="wa", sent_by_id=org.owner.id))
    db.commit()
    assert api.call("owner", "POST", "/api/calls", json={"lead_id": lead_id, "direction": "outbound"}).status_code == 201
    assert api.call("owner", "POST", "/api/meetings", json={
        "subject": "m", "starts_at": STARTS, "lead_id": lead_id,
    }).status_code == 201


# ---- merge ----

def test_merge_moves_every_linked_record(api, db):
    keep, source = api.new_lead(), api.new_lead()
    _link_activity(api, db, source)
    assert api.call("owner", "POST", f"/api/leads/{keep}/merge", json={"source_id": source}).status_code == 200
    db.expire_all()
    for model in (WhatsAppMessage, CallLog, Meeting):
        assert db.query(model).filter(model.lead_id == source).count() == 0, model
        assert db.query(model).filter(model.lead_id == keep).count() == 1, model


def test_merge_leaves_other_records_custom_fields_alone(api, db, org):
    keep, source = api.new_lead(), api.new_lead()
    client_field = CustomFieldDef(company_id=org.company.id, entity_type="client", name="GST",
                                  field_key="gst", field_type="text")
    lead_field = CustomFieldDef(company_id=org.company.id, entity_type="lead", name="Budget",
                                field_key="budget", field_type="text")
    db.add_all([client_field, lead_field])
    db.commit()
    db.add_all([
        CustomFieldValue(company_id=org.company.id, field_def_id=client_field.id, entity_id=source, value="CLIENT"),
        CustomFieldValue(company_id=org.company.id, field_def_id=lead_field.id, entity_id=source, value="LEAD"),
    ])
    db.commit()
    assert api.call("owner", "POST", f"/api/leads/{keep}/merge", json={"source_id": source}).status_code == 200
    db.expire_all()
    assert db.query(CustomFieldValue).filter_by(field_def_id=client_field.id).one().entity_id == source
    assert db.query(CustomFieldValue).filter_by(field_def_id=lead_field.id).one().entity_id == keep


# ---- permanent delete ----

@pytest.mark.parametrize("kind", ["plain", "call", "meeting", "email", "whatsapp", "deal", "tag", "note", "converted"])
def test_permanent_delete_works_with_linked_records(api, db, org, kind):
    lead_id = api.new_lead()
    if kind == "call":
        api.call("owner", "POST", "/api/calls", json={"lead_id": lead_id, "direction": "outbound"})
    elif kind == "meeting":
        api.call("owner", "POST", "/api/meetings", json={"subject": "m", "starts_at": STARTS, "lead_id": lead_id})
    elif kind == "email":
        api.call("owner", "POST", "/api/emails", json={
            "lead_id": lead_id, "to_email": "a@acme.co", "subject": "s", "body": "b",
        })
    elif kind == "whatsapp":
        db.add(WhatsAppMessage(company_id=org.company.id, lead_id=lead_id, to_phone="91", direction="outbound"))
        db.commit()
    elif kind == "deal":
        api.call("owner", "POST", "/api/deals", json={"title": "d", "lead_id": lead_id, "amount": 1})
    elif kind == "tag":
        api.call("owner", "PUT", f"/api/leads/{lead_id}", json={"tags": ["hot"]})
    elif kind == "note":
        api.call("owner", "POST", f"/api/leads/{lead_id}/notes", params={"content": "n"})
    elif kind == "converted":
        api.call("owner", "POST", f"/api/leads/{lead_id}/convert")
    assert api.call("md", "DELETE", f"/api/leads/{lead_id}").status_code == 200
    purged = api.call("md", "POST", f"/api/leads/{lead_id}/purge")
    assert purged.status_code == 200, (kind, purged.text)
    db.expire_all()
    assert db.get(Lead, lead_id) is None


def test_permanent_delete_removes_lead_only_activity_but_keeps_deals(api, db, org):
    lead_id = api.new_lead()
    _link_activity(api, db, lead_id)
    api.call("owner", "POST", "/api/emails", json={"lead_id": lead_id, "to_email": "a@acme.co", "subject": "s", "body": "b"})
    deal = api.call("owner", "POST", "/api/deals", json={"title": "KeepDeal", "lead_id": lead_id, "amount": 1}).json()["id"]
    api.call("md", "DELETE", f"/api/leads/{lead_id}")
    assert api.call("md", "POST", f"/api/leads/{lead_id}/purge").status_code == 200
    db.expire_all()
    for model in (WhatsAppMessage, CallLog, Meeting, EmailLog):
        assert db.query(model).filter(model.company_id == org.company.id).count() == 0, model
    assert db.get(Deal, deal).lead_id is None


# ---- deleted leads ----

def test_deleted_leads_leave_every_list_and_count(api, db):
    lead_id = api.new_lead()
    assert api.call("owner", "POST", "/api/follow-ups", json={"lead_id": lead_id, "scheduled_date": TOMORROW}).status_code == 201
    name = db.get(Lead, lead_id).name
    before = api.call("mgr_same_team", "GET", "/api/manager/dashboard").json()["metrics"]["total_team_leads"]
    assert api.call("owner", "DELETE", f"/api/leads/{lead_id}").status_code == 200
    for who, path in (
        ("md", "/api/md/leads"),
        ("mgr_same_team", "/api/manager/leads"),
        ("owner", "/api/tasks/list"),
        ("owner", "/api/follow-ups"),
        ("md", "/api/follow-ups"),
    ):
        response = api.call(who, "GET", path)
        assert response.status_code == 200, (who, path)
        assert name not in response.text, (who, path)
    assert api.call("mgr_same_team", "GET", "/api/manager/dashboard").json()["metrics"]["total_team_leads"] == before - 1
    assert api.call("owner", "GET", f"/api/timeline/lead/{lead_id}").status_code == 404


def test_no_new_work_on_a_deleted_lead(api):
    lead_id = api.new_lead()
    assert api.call("owner", "DELETE", f"/api/leads/{lead_id}").status_code == 200
    for path, body in (
        ("/api/tasks", {"title": "t", "lead_id": lead_id, "due_date": TOMORROW, "priority": "medium"}),
        ("/api/follow-ups", {"lead_id": lead_id, "scheduled_date": TOMORROW}),
        ("/api/calls", {"lead_id": lead_id, "direction": "outbound"}),
        ("/api/meetings", {"subject": "m", "starts_at": STARTS, "lead_id": lead_id}),
        ("/api/emails", {"lead_id": lead_id, "to_email": "a@acme.co", "subject": "s", "body": "b"}),
        ("/api/deals", {"title": "d", "lead_id": lead_id, "amount": 1}),
    ):
        response = api.call("owner", "POST", path, json=body)
        assert response.status_code in (400, 404), (path, response.status_code, response.text)


def test_restore_is_blocked_if_someone_else_now_has_that_contact(api):
    first = api.new_lead(email="same@acme.co", phone="9222222222")
    assert api.call("owner", "DELETE", f"/api/leads/{first}").status_code == 200
    api.new_lead(email="same@acme.co", phone="9333333333")
    blocked = api.call("owner", "POST", f"/api/leads/{first}/restore")
    assert blocked.status_code == 400, blocked.text


# ---- duplicates ----

@pytest.mark.parametrize("stored,typed", [
    ("9876543210", "+91 98765 43210"),
    ("9876543210", "09876543210"),
    ("919876543210", "9876543210"),
    ("09876543210", "+919876543210"),
])
def test_same_indian_number_in_another_format_is_a_duplicate(api, stored, typed):
    api.new_lead(phone=stored)
    response = api.call("owner", "POST", "/api/leads", json={"name": "Dup", "phone": typed})
    assert response.status_code == 400, response.text


def test_phone_variants_leave_foreign_numbers_alone():
    assert phone_variants("+1 415 555 2671") == {"14155552671"}
    assert phone_variants("34") == {"34"}
    assert phone_variants("") == set()
    assert phone_variants(None) == set()
    assert phone_variants("+91 98765 43210") == {"9876543210", "09876543210", "919876543210"}


def test_edit_cannot_take_another_leads_number_in_another_format(api):
    api.new_lead(phone="9444444444")
    other = api.new_lead(phone="9555555555")
    assert api.call("owner", "PUT", f"/api/leads/{other}", json={"phone": "+91 94444 44444"}).status_code == 400


def test_csv_import_treats_another_format_as_a_duplicate(api, db):
    api.new_lead(phone="9666666666")
    response = api.call("owner", "POST", "/api/import/leads/text", json={
        "csv": "name,phone\nCsvDup,+91 96666 66666\n", "commit": True,
    })
    assert response.status_code == 200
    assert response.json()["created"] == 0
    assert response.json()["skipped_duplicate"] == 1


def test_duplicate_finder_does_not_treat_underscore_as_a_wildcard(api, db, org):
    lead_id = api.new_lead(email="a_b@acme.co")
    db.add(Lead(company_id=org.company.id, name="Other", status="Active", email="axb@acme.co",
                team_id=org.alpha.id, assigned_to_id=org.owner.id))
    db.commit()
    assert api.call("owner", "GET", "/api/leads/duplicates", params={"lead_id": lead_id}).json()["items"] == []


def test_website_form_cleans_phone_and_email(client, db, web_form):
    assert _submit(client, name="Web One", phone="+91 98888 77777", email="MIXED@Acme.CO").status_code == 201
    lead = db.query(Lead).filter(Lead.name == "Web One").one()
    assert lead.phone == "919888877777"
    assert lead.email == "mixed@acme.co"


def test_repeat_website_submit_adds_a_note_and_tells_the_owner(client, db, org, web_form):
    assert _submit(client, name="Repeat", phone="9777777777").status_code == 201
    lead = db.query(Lead).filter(Lead.name == "Repeat").one()
    owner_id = lead.assigned_to_id
    again = _submit(client, name="Repeat Again", phone="+91 97777 77777", notes="Still interested")
    assert again.status_code == 201
    db.expire_all()
    assert db.query(Lead).filter(Lead.company_id == org.company.id, Lead.name.like("Repeat%")).count() == 1
    note = db.query(Note).filter(Note.lead_id == lead.id).one()
    assert "Still interested" in note.content
    assert db.query(Notification).filter(
        Notification.user_id == owner_id, Notification.title.contains("website form")
    ).count() == 1


def test_website_form_for_an_existing_client_adds_a_client_note(client, db, org, web_form):
    db.add(Client(company_id=org.company.id, name="Known", phone="9111122222", assigned_to_id=org.owner.id))
    db.commit()
    assert _submit(client, name="Known Again", phone="9111122222", notes="Hello").status_code == 201
    db.expire_all()
    assert db.query(Lead).filter(Lead.name == "Known Again").count() == 0
    assert db.query(Note).filter(Note.client_id.isnot(None)).count() == 1


# ---- validation ----

@pytest.mark.parametrize("body", [
    {"name": ""},
    {"name": "   "},
    {"name": "n" * 256},
    {"name": "x", "email": "not-an-email"},
    {"name": "x", "email": "a" * 300 + "@acme.co"},
    {"name": "x", "phone": "abc"},
    {"name": "x", "phone": "9" * 51},
    {"name": "x", "company": "c" * 256},
    {"name": "x", "source": "s" * 101},
    {"name": "x", "service_type": "s" * 101},
])
def test_bad_lead_values_are_refused_with_a_message(api, org, body):
    response = api.call("owner", "POST", "/api/leads", json={"team_id": org.alpha.id} | body)
    assert response.status_code == 400, response.text
    assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize("body", [
    {"name": ""},
    {"name": "   "},
    {"email": "not-an-email"},
    {"phone": "abc"},
    {"company": "c" * 256},
    {"name": "n" * 256},
])
def test_bad_values_are_refused_on_edit_too(api, db, body):
    lead_id = api.new_lead()
    response = api.call("owner", "PUT", f"/api/leads/{lead_id}", json=body)
    assert response.status_code == 400, response.text
    db.expire_all()
    assert db.get(Lead, lead_id).name


def test_edit_can_still_clear_email_and_phone(api, db):
    lead_id = api.new_lead()
    assert api.call("owner", "PUT", f"/api/leads/{lead_id}", json={"email": "", "phone": ""}).status_code == 200
    db.expire_all()
    lead = db.get(Lead, lead_id)
    assert lead.email is None and lead.phone is None


def test_values_are_trimmed(api, org):
    made = api.call("owner", "POST", "/api/leads", json={"name": "  Spaced  ", "team_id": org.alpha.id})
    assert made.status_code == 201
    assert made.json()["name"] == "Spaced"


@pytest.mark.parametrize("body", [
    {"name": "w", "phone": "9" * 60},
    {"name": "w", "email": "a" * 300 + "@acme.co"},
    {"name": "w", "phone": "91", "service_type": "s" * 150},
    {"name": "w", "phone": "abc"},
    {"name": "w", "email": "not-an-email"},
])
def test_bad_website_form_values_are_refused(client, web_form, body):
    assert _submit(client, **body).status_code == 400, body


def test_csv_import_marks_bad_rows_invalid_and_keeps_the_good_ones(api, db):
    long_name = "n" * 300
    response = api.call("mgr_same_team", "POST", "/api/import/leads/text", json={
        "csv": f"name,email,phone\nGoodRow,good@acme.co,\n{long_name},,\nBadMail,nope,\nBadPhone,,abc\n",
        "commit": True,
    })
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 1
    assert response.json()["skipped_invalid"] == 3
    assert db.query(Lead).filter(Lead.name == "GoodRow").count() == 1


# ---- status rules ----

def test_converting_by_edit_needs_an_owner_like_the_status_route(api, db, org):
    lead = Lead(company_id=org.company.id, name="NoOwner", status="Active", team_id=org.alpha.id)
    db.add(lead)
    db.commit()
    assert api.call("mgr_same_team", "PUT", f"/api/leads/{lead.id}", json={"status": "Converted"}).status_code == 400
    assert api.call("mgr_same_team", "PATCH", f"/api/leads/{lead.id}/status", json={"status": "Converted"}).status_code == 400
    db.expire_all()
    assert db.query(Client).filter(Client.converted_from_lead_id == lead.id).count() == 0


def test_converted_lead_cannot_change_status(api):
    lead_id = api.new_lead()
    assert api.call("owner", "POST", f"/api/leads/{lead_id}/convert").status_code == 200
    assert api.call("owner", "PUT", f"/api/leads/{lead_id}", json={"status": "Active"}).status_code == 400
    assert api.call("owner", "PATCH", f"/api/leads/{lead_id}/status", json={"status": "Lost"}).status_code == 400


def test_lost_lead_can_be_reopened(api):
    lead_id = api.new_lead()
    assert api.call("owner", "PUT", f"/api/leads/{lead_id}", json={"status": "Lost"}).status_code == 200
    assert api.call("owner", "PUT", f"/api/leads/{lead_id}", json={"status": "Active"}).status_code == 200


# ---- CSV export ----

@pytest.mark.parametrize("value", ["=HYPERLINK(\"http://evil\")", "+cmd", "-2+3", "@SUM(1)", "\t=1"])
def test_export_cannot_carry_a_live_formula(value):
    assert build_csv_string([[value]], ["h"]).splitlines()[1].lstrip('"').startswith("'")


@pytest.mark.parametrize("value", ["Ravi", "919876543210", "+91 98765 43210", "-", ""])
def test_export_leaves_normal_values_alone(value):
    assert "'" not in build_csv_string([[value]], ["h"])


def test_exported_lead_name_is_safe_end_to_end(api):
    api.new_lead(name="=1+1")
    assert "'=1+1" in api.call("md", "GET", "/api/export/leads").text


def test_follow_up_task_is_kept_for_the_owner_after_edits(api, db, org):
    lead_id = api.new_lead()
    api.call("owner", "PUT", f"/api/leads/{lead_id}", json={"company": "Acme"})
    assert db.query(Task).filter(Task.lead_id == lead_id).one().assigned_to_id == org.owner.id
