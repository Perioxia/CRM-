"""What the lead screens rely on: tabs, paging, search, dashboard numbers, dates, workload and territories."""
from datetime import date, timedelta
from unittest import mock

from app.models import Lead
from app.models.core.company_settings import CompanySettings
from app.models.sales.lead_form import LeadForm
from app.models.sales.note import Note
from app.models.sales.territory import Territory, TerritoryRule
from app.models.sales.whatsapp import WhatsAppMessage
from app.services.sales.territory import round_robin_sales_on_team
from app.utils.rate_limit import public_form_limiter
from tests.helpers.factories import create_client

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
LATER = (date.today() + timedelta(days=5)).isoformat()


def _add_leads(db, org, count, prefix, owner=None, team="alpha", status="Active"):
    db.add_all([
        Lead(company_id=org.company.id, name=f"{prefix}{i}", status=status,
             team_id=getattr(org, team).id, assigned_to_id=owner.id if owner else None)
        for i in range(count)
    ])
    db.commit()


def _row(api, who, lead_id):
    rows = api.call(who, "GET", "/api/leads", params={"limit": 500}).json()["items"]
    return next(row for row in rows if row["id"] == lead_id)


# ---- tabs, paging, search ----

def test_claimed_and_open_tabs_are_filtered_on_the_server(api, db, org):
    _add_leads(db, org, 3, "Mine", owner=org.owner)
    _add_leads(db, org, 2, "Bravo", owner=org.rep_same_team)
    _add_leads(db, org, 4, "Pool")
    def names(who, **params):
        body = api.call(who, "GET", "/api/leads", params=params).json()
        return body["total"], {row["name"][:4] for row in body["items"]}
    assert names("owner", claimed="true") == (3, {"Mine"})
    assert names("owner", unassigned="true") == (4, {"Pool"})
    assert names("owner") == (7, {"Mine", "Pool"})
    assert names("mgr_same_team", claimed="true") == (5, {"Mine", "Brav"})
    assert names("mgr_same_team", unassigned="true") == (4, {"Pool"})


def test_paging_returns_every_lead_exactly_once(api, db, org):
    _add_leads(db, org, 120, "Bulk")
    seen, skip = [], 0
    while True:
        body = api.call("mgr_same_team", "GET", "/api/leads", params={"unassigned": "true", "limit": 50, "skip": skip}).json()
        assert body["total"] == 120
        if not body["items"]:
            break
        seen += [row["id"] for row in body["items"]]
        skip += 50
    assert len(seen) == 120 and len(set(seen)) == 120


def test_search_finds_phone_and_treats_wildcards_as_text(api):
    wanted = api.new_lead(name="Asha Rao", phone="9812345678", email="asha@acme.co")
    api.new_lead(name="Other", phone="9000011111", email="other@acme.co")
    def ids(term):
        return [row["id"] for row in api.call("owner", "GET", "/api/leads", params={"search": term}).json()["items"]]
    assert ids("98123") == [wanted]
    assert ids("asha rao") == [wanted]
    assert ids("%") == []
    assert ids("_") == []


# ---- dashboard numbers ----

def test_rep_dashboard_counts_only_their_own_leads(api, db, org):
    _add_leads(db, org, 1, "Fresh", owner=org.owner, status="New")
    _add_leads(db, org, 1, "Working", owner=org.owner, status="Active")
    _add_leads(db, org, 1, "Called", owner=org.owner, status="Contacted")
    _add_leads(db, org, 1, "Gone", owner=org.owner, status="Lost")
    _add_leads(db, org, 1, "GoneClient", owner=org.owner, status="Lost Client")
    _add_leads(db, org, 1, "Won", owner=org.owner, status="Converted")
    _add_leads(db, org, 3, "Pool")
    _add_leads(db, org, 2, "Bravo", owner=org.rep_same_team)
    metrics = api.call("owner", "GET", "/api/leads/dashboard").json()["metrics"]
    assert metrics["total_leads"] == 6
    assert metrics["active_leads"] == 3
    assert metrics["lost_leads"] == 2
    assert metrics["closed_leads"] == 1


def test_a_claim_by_someone_else_does_not_change_my_dashboard(api, db, org):
    _add_leads(db, org, 1, "Mine", owner=org.owner)
    _add_leads(db, org, 1, "Pool")
    before = api.call("owner", "GET", "/api/leads/dashboard").json()["metrics"]["total_leads"]
    pool_id = db.query(Lead).filter(Lead.name == "Pool0").one().id
    assert api.call("rep_same_team", "POST", f"/api/leads/{pool_id}/claim").status_code == 200
    assert api.call("owner", "GET", "/api/leads/dashboard").json()["metrics"]["total_leads"] == before == 1


# ---- next step and last contact ----

def test_next_step_follows_the_earliest_pending_follow_up(api):
    lead_id = api.new_lead()
    assert _row(api, "owner", lead_id)["next_task"] is None
    later = api.call("owner", "POST", "/api/follow-ups", json={"lead_id": lead_id, "scheduled_date": LATER}).json()
    assert _row(api, "owner", lead_id)["next_task"].startswith(LATER)
    soon = api.call("owner", "POST", "/api/follow-ups", json={"lead_id": lead_id, "scheduled_date": TOMORROW}).json()
    assert _row(api, "owner", lead_id)["next_task"].startswith(TOMORROW)
    assert api.call("owner", "POST", f"/api/follow-ups/{soon['id']}/complete", json={"outcome": "done"}).status_code == 200
    assert _row(api, "owner", lead_id)["next_task"].startswith(LATER)
    assert api.call("owner", "DELETE", f"/api/follow-ups/{later['id']}").status_code == 200
    assert _row(api, "owner", lead_id)["next_task"] is None


def test_outbound_email_and_call_set_last_contacted(api):
    by_email = api.new_lead()
    with mock.patch("app.services.sales.crm_email.send_email", return_value=True):
        assert api.call("owner", "POST", "/api/emails", json={"lead_id": by_email, "subject": "s", "body": "b"}).status_code == 201
    assert _row(api, "owner", by_email)["last_contacted_at"]

    by_call = api.new_lead()
    assert api.call("owner", "POST", "/api/calls", json={"lead_id": by_call, "direction": "outbound"}).status_code == 201
    row = _row(api, "owner", by_call)
    assert row["last_contacted_at"] and not row["last_response_at"]


def test_failed_email_does_not_count_as_contact(api):
    lead_id = api.new_lead()
    with mock.patch("app.services.sales.crm_email.send_email", return_value=False):
        api.call("owner", "POST", "/api/emails", json={"lead_id": lead_id, "subject": "s", "body": "b"})
    assert _row(api, "owner", lead_id)["last_contacted_at"] is None


def test_inbound_call_and_whatsapp_set_last_response(api, client, db, org):
    by_call = api.new_lead()
    assert api.call("owner", "POST", "/api/calls", json={"lead_id": by_call, "direction": "inbound"}).status_code == 201
    assert _row(api, "owner", by_call)["last_response_at"]

    by_whatsapp = api.new_lead(phone="919000000077")
    source = db.query(CompanySettings).filter_by(company_id=org.company.id).one().whatsapp_source
    client.post("/api/whatsapp/webhook", params={"source": source}, json={
        "type": "message",
        "destination": source,
        "payload": {"id": "m1", "source": "919000000077", "type": "text", "payload": {"text": "hi"}},
    })
    db.expire_all()
    assert db.query(WhatsAppMessage).filter_by(lead_id=by_whatsapp, direction="inbound").count() == 1
    assert _row(api, "owner", by_whatsapp)["last_response_at"]


def test_whatsapp_send_sets_last_contacted(api, db, org):
    lead_id = api.new_lead(phone="919000000002")
    with mock.patch("app.routers.sales.whatsapp.post_gupshup_session_text", return_value=(True, "ok")), \
            mock.patch("app.routers.sales.whatsapp.session_open_until", return_value="2030-01-01"):
        assert api.call("owner", "POST", "/api/whatsapp/session-send", json={"lead_id": lead_id, "body": "hello"}).status_code == 201
    assert _row(api, "owner", lead_id)["last_contacted_at"]


# ---- auto-assign workload ----

def test_auto_assign_counts_only_open_work(db, org):
    _add_leads(db, org, 5, "Closed", owner=org.owner, status="Converted")
    _add_leads(db, org, 2, "Lost", owner=org.owner, status="Lost")
    _add_leads(db, org, 2, "Busy", owner=org.rep_same_team, status="Active")
    picked = round_robin_sales_on_team(db, company_id=org.company.id, team_id=org.alpha.id)
    assert picked == org.owner.id


def test_auto_assign_ignores_deleted_leads(api, db, org):
    _add_leads(db, org, 2, "Busy", owner=org.owner)
    _add_leads(db, org, 1, "Other", owner=org.rep_same_team)
    for lead in db.query(Lead).filter(Lead.name.like("Busy%")).all():
        api.call("owner", "DELETE", f"/api/leads/{lead.id}")
    db.expire_all()
    assert round_robin_sales_on_team(db, company_id=org.company.id, team_id=org.alpha.id) == org.owner.id


# ---- territories ----

def _territory(db, org, field, value, team="beta"):
    territory = Territory(company_id=org.company.id, team_id=getattr(org, team).id, name="T", priority=1, is_active=True)
    db.add(territory)
    db.commit()
    db.add(TerritoryRule(company_id=org.company.id, territory_id=territory.id, match_field=field, match_value=value))
    db.commit()


def test_website_form_set_to_no_team_uses_territory_rules(client, db, org):
    db.add(LeadForm(company_id=org.company.id, slug="terrform", name="f", is_active=True,
                    default_team_id=None, headline="h"))
    db.commit()
    public_form_limiter._buckets.clear()
    _territory(db, org, "service_type", "Solar")
    assert client.post("/api/public/forms/terrform/submit", json={
        "name": "SolarLead", "phone": "9000000001", "service_type": "Solar",
    }).status_code == 201
    lead = db.query(Lead).filter(Lead.name == "SolarLead").one()
    assert lead.team_id == org.beta.id
    assert lead.assigned_to_id == org.rep_other_team.id


def test_team_chosen_by_a_person_is_not_overridden(api, db, org):
    _territory(db, org, "source", "Referral")
    made = api.call("md", "POST", "/api/leads", json={"name": "Chosen", "source": "Referral", "team_id": org.alpha.id})
    assert made.status_code == 201
    assert made.json()["team_id"] == org.alpha.id


# ---- notes stay out of the URL ----

def test_notes_can_be_sent_in_the_body(api, db):
    lead_id = api.new_lead()
    assert api.call("owner", "POST", f"/api/leads/{lead_id}/notes", json={"content": "  In the body  "}).status_code == 201
    assert db.query(Note).filter(Note.lead_id == lead_id).one().content == "In the body"


def test_older_app_versions_can_still_send_notes_in_the_query(api, db):
    lead_id = api.new_lead()
    assert api.call("owner", "POST", f"/api/leads/{lead_id}/notes", params={"content": "In the query"}).status_code == 201
    assert db.query(Note).filter(Note.lead_id == lead_id).one().content == "In the query"


def test_an_empty_note_is_refused(api):
    lead_id = api.new_lead()
    assert api.call("owner", "POST", f"/api/leads/{lead_id}/notes", json={"content": "   "}).status_code == 400
    assert api.call("owner", "POST", f"/api/leads/{lead_id}/notes").status_code == 400


def test_client_notes_can_be_sent_in_the_body(api, db, org):
    customer = create_client(db, company_id=org.company.id, name="Cust", assigned_to_id=org.owner.id, team_id=org.alpha.id)
    assert api.call("owner", "POST", f"/api/clients/{customer.id}/notes", json={"content": "Hello"}).status_code == 201
    assert db.query(Note).filter(Note.client_id == customer.id).one().content == "Hello"
