"""Small screen fixes: Purchase's approval list, the team switcher, Purchase search links,
and the manager and MD dashboards counting the same leads."""
from datetime import date, datetime, timedelta

from app.models import Lead
from app.models.core.team import Team
from app.models.finance.invoice import Invoice
from app.models.core.user import User
from tests.helpers.factories import create_client


def _invoice(db, org, client, number, status):
    row = Invoice(company_id=org.company.id, invoice_number=number, client_id=client.id, created_by_id=org.owner.id,
                  total=100, status=status, issued_date=date.today())
    db.add(row)
    db.commit()
    return row


def test_purchase_dashboard_lists_orders_waiting_for_approval(api, db, org):
    customer = create_client(db, company_id=org.company.id, name="Waiting Co", assigned_to_id=org.owner.id, team_id=org.alpha.id)
    waiting = _invoice(db, org, customer, "DRAFT-WAIT", "Draft")
    _invoice(db, org, customer, "INV-DONE", "Pending")
    queue = api.call("purchase", "GET", "/api/purchase/dashboard").json()["approval_queue"]
    assert [row["id"] for row in queue] == [waiting.id]


def test_a_rep_joined_to_the_first_team_sees_only_that_team(api, db, org):
    newcomer = User(email="new@acc.test", full_name="Newcomer", hashed_password="unused", role="sales",
                    company_id=org.company.id, status="active", is_active=True)
    db.add(newcomer)
    db.commit()
    body = api.call(newcomer, "GET", "/api/teams/mine").json()
    first = db.query(Team).filter(Team.company_id == org.company.id).order_by(Team.id).first()
    assert [t["id"] for t in body["teams"]] == [first.id]
    assert body["active_team_id"] == first.id


def test_purchase_client_search_opens_their_invoice_list(api, db, org):
    create_client(db, company_id=org.company.id, name="Mehra & Sons", assigned_to_id=org.owner.id, team_id=org.alpha.id)
    results = api.call("purchase", "GET", "/api/search", params={"q": "Mehra"}).json()["results"]
    client = next(r for r in results if r["type"] == "client")
    assert client["url"] == "/purchase/invoices?search=Mehra%20%26%20Sons"


def _lead(db, org, name, status, owner=None, days_old=0):
    row = Lead(company_id=org.company.id, name=name, status=status, team_id=org.alpha.id,
               assigned_to_id=owner.id if owner else None)
    db.add(row)
    db.commit()
    if days_old:
        row.created_at = datetime.utcnow() - timedelta(days=days_old)
        db.commit()
    return row


def test_manager_counts_imported_new_leads_as_active(api, db, org):
    for name, status in (("Imported", "New"), ("Typed", "Active"), ("Called", "Contacted"), ("Won", "Converted"), ("Gone", "Lost")):
        _lead(db, org, name, status, owner=org.owner)
    members = api.call("mgr_same_team", "GET", "/api/manager/dashboard").json()["team_members"]
    owner = next(m for m in members if m["id"] == org.owner.id)
    assert (owner["leads_active"], owner["leads_converted"]) == (3, 1)
    detail = api.call("mgr_same_team", "GET", f"/api/manager/monitoring/{org.owner.id}").json()
    assert detail["current_week"]["leads_contacted"] == 3
    assert {lead["name"] for lead in detail["active_leads"]} == {"Imported", "Typed", "Called"}


def test_md_funnel_first_stage_counts_new_and_active(api, db, org):
    _lead(db, org, "Imported", "New")
    _lead(db, org, "Typed", "Active")
    _lead(db, org, "Called", "Contacted")
    stages = {s["stage"]: s["count"] for s in api.call("md", "GET", "/api/md/dashboard").json()["pipelineSummary"]["stageDistribution"]}
    assert (stages["New"], stages["Contacted"]) == (2, 1)
    funnel = {f["name"]: f["value"] for f in api.call("md", "GET", "/api/md/leads").json()["funnel"]}
    assert (funnel["New"], funnel["Contacted"]) == (2, 1)
