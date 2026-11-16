"""Every route that shows clients, invoices, quotes or sales orders applies the same rule as the Clients and
Orders pages: a rep sees their own, a manager their active team's, MD, admin and purchase the whole company."""
from datetime import date

import pytest

from app.models.finance.invoice import Invoice
from app.models.sales.account import Account
from app.models.sales.deal import Deal
from app.models.sales.quote import Quote
from app.models.sales.sales_order import SalesOrder
from app.services.sales.pipeline_seed import ensure_default_pipeline
from tests.helpers.factories import create_client

ROLES = ("owner", "rep_same_team", "rep_other_team", "mgr_same_team", "mgr_other_team", "md", "purchase")
EXPECTED = {  # which of the three clients (and the records on them) each role sees
    "owner": {"Own"},
    "rep_same_team": {"Same"},
    "rep_other_team": {"Other"},
    "mgr_same_team": {"Own", "Same"},
    "mgr_other_team": {"Other"},
    "md": {"Own", "Same", "Other"},
    "purchase": {"Own", "Same", "Other"},
}


@pytest.fixture
def books(db, org):
    account = Account(company_id=org.company.id, name="Shared Account")
    db.add(account)
    db.commit()
    people = {"Own": (org.owner, org.alpha), "Same": (org.rep_same_team, org.alpha), "Other": (org.rep_other_team, org.beta)}
    pipeline = ensure_default_pipeline(db, org.company.id)
    out = {"account": account, "clients": {}, "invoices": {}, "quotes": {}, "orders": {}, "deals": {}}
    for label, (rep, team) in people.items():
        customer = create_client(db, company_id=org.company.id, name=f"{label} Client", email=f"{label.lower()}@cust.co",
                                 assigned_to_id=rep.id, team_id=team.id, account_id=account.id)
        invoice = Invoice(company_id=org.company.id, invoice_number=f"INV-{label}", client_id=customer.id,
                          created_by_id=rep.id, total=1000, status="Draft", issued_date=date.today())
        quote = Quote(company_id=org.company.id, quote_number=f"QUO-{label}", client_id=customer.id,
                      created_by_id=rep.id, total=500)
        db.add_all([invoice, quote])
        db.commit()
        order = SalesOrder(company_id=org.company.id, order_number=f"SO-{label}", quote_id=quote.id,
                           client_id=customer.id, created_by_id=rep.id, total=500)
        deal = Deal(company_id=org.company.id, title=f"Deal-{label}", pipeline_id=pipeline.id,
                    stage_id=pipeline_first_stage(db, pipeline.id), assigned_to_id=rep.id, team_id=team.id)
        db.add_all([order, deal])
        db.commit()
        out["clients"][label] = customer
        out["invoices"][label] = invoice
        out["quotes"][label] = quote
        out["orders"][label] = order
        out["deals"][label] = deal
    return out


def pipeline_first_stage(db, pipeline_id):
    from app.models.sales.pipeline import PipelineStage
    return db.query(PipelineStage.id).filter(PipelineStage.pipeline_id == pipeline_id).order_by(PipelineStage.position).first()[0]


def _labels(text):
    return {label for label in ("Own", "Same", "Other") if f"{label} Client" in text or f"INV-{label}" in text}


@pytest.mark.parametrize("who", ROLES)
def test_search_shows_the_same_clients_and_invoices_as_the_pages(api, books, who):
    response = api.call(who, "GET", "/api/search", params={"q": "Client"})
    assert response.status_code == 200, response.text
    clients = {r["title"].split()[0] for r in response.json()["results"] if r["type"] == "client"}
    assert clients == EXPECTED[who]
    invoices = {r["title"].split("-")[1] for r in api.call(who, "GET", "/api/search", params={"q": "INV-"}).json()["results"]
                if r["type"] == "invoice"}
    assert invoices == EXPECTED[who]

    page_clients = {c["name"].split()[0] for c in api.call(who, "GET", "/api/clients").json()["items"]}
    assert clients == page_clients


@pytest.mark.parametrize("who", ROLES)
def test_csv_exports_follow_the_same_rule(api, books, who):
    clients = api.call(who, "GET", "/api/export/clients")
    assert clients.status_code == 200
    assert _labels(clients.text) == EXPECTED[who]
    invoices = api.call(who, "GET", "/api/export/invoices")
    assert invoices.status_code == 200
    assert {label for label in ("Own", "Same", "Other") if f"INV-{label}" in invoices.text} == EXPECTED[who]


@pytest.mark.parametrize("who", ROLES)
def test_churn_follows_the_same_rule(api, books, who):
    listed = api.call(who, "GET", "/api/predictions/churn")
    assert listed.status_code == 200, listed.text
    assert {row["client_name"].split()[0] for row in listed.json()["items"]} == EXPECTED[who]
    for label, customer in books["clients"].items():
        expected = 200 if label in EXPECTED[who] else 403
        assert api.call(who, "GET", f"/api/clients/{customer.id}/churn").status_code == expected, (who, label)


@pytest.mark.parametrize("who", ROLES)
def test_client_and_invoice_timelines_follow_the_same_rule(api, books, who):
    for label in ("Own", "Same", "Other"):
        expected = 200 if label in EXPECTED[who] else 403
        client_id = books["clients"][label].id
        invoice_id = books["invoices"][label].id
        assert api.call(who, "GET", f"/api/timeline/client/{client_id}").status_code == expected, (who, "client", label)
        assert api.call(who, "GET", f"/api/timeline/invoice/{invoice_id}").status_code == expected, (who, "invoice", label)


def test_deal_timeline_follows_the_deal_board(api, books):
    own_deal = books["deals"]["Own"].id
    assert api.call("owner", "GET", f"/api/timeline/deal/{own_deal}").status_code == 200
    assert api.call("mgr_same_team", "GET", f"/api/timeline/deal/{own_deal}").status_code == 200
    assert api.call("md", "GET", f"/api/timeline/deal/{own_deal}").status_code == 200
    assert api.call("rep_same_team", "GET", f"/api/timeline/deal/{own_deal}").status_code == 403
    assert api.call("rep_other_team", "GET", f"/api/timeline/deal/{own_deal}").status_code == 403
    assert api.call("mgr_other_team", "GET", f"/api/timeline/deal/{own_deal}").status_code == 403


@pytest.mark.parametrize("who", ROLES)
def test_quotes_and_sales_orders_follow_the_same_rule(api, books, who):
    quotes = {q["quote_number"].split("-")[1] for q in api.call(who, "GET", "/api/quotes").json()["items"]}
    assert quotes == EXPECTED[who]
    orders = {o["order_number"].split("-")[1] for o in api.call(who, "GET", "/api/sales-orders").json()["items"]}
    assert orders == EXPECTED[who]
    for label in ("Own", "Same", "Other"):
        expected = 200 if label in EXPECTED[who] else 403
        quote_id = books["quotes"][label].id
        assert api.call(who, "GET", f"/api/quotes/{quote_id}").status_code == expected, (who, "quote", label)
        assert api.call(who, "GET", f"/api/quotes/{quote_id}/pdf").status_code == expected, (who, "pdf", label)
        assert api.call(who, "GET", f"/api/sales-orders/{books['orders'][label].id}").status_code == expected, (who, "order", label)


def test_others_cannot_act_on_a_quote_or_order_they_cannot_see(api, books):
    quote_id = books["quotes"]["Own"].id
    for path in (f"/api/quotes/{quote_id}/share", f"/api/quotes/{quote_id}/accept", f"/api/quotes/{quote_id}/reject",
                 f"/api/sales-orders/{books['orders']['Own'].id}/invoice"):
        assert api.call("rep_other_team", "POST", path).status_code == 403, path
        assert api.call("rep_same_team", "POST", path).status_code == 403, path
    assert api.call("rep_other_team", "DELETE", f"/api/quotes/{quote_id}/share").status_code == 403


def test_a_quote_can_only_be_made_for_a_client_you_can_see(api, books):
    body = {"title": "Phase 2", "items": [{"description": "Wiring", "quantity": 1, "unit_price": 100}]}
    own = books["clients"]["Own"].id
    other = books["clients"]["Other"].id
    assert api.call("owner", "POST", "/api/quotes", json=body | {"client_id": other}).status_code == 403
    assert api.call("mgr_same_team", "POST", "/api/quotes", json=body | {"client_id": other}).status_code == 403
    assert api.call("owner", "POST", "/api/quotes", json=body | {"client_id": own}).status_code == 201
    assert api.call("mgr_same_team", "POST", "/api/quotes", json=body | {"client_id": own}).status_code == 201
    assert api.call("md", "POST", "/api/quotes", json=body | {"client_id": other}).status_code == 201


@pytest.mark.parametrize("who", ROLES)
def test_accounts_list_only_the_contacts_you_can_see(api, books, who):
    account_id = books["account"].id
    listed = api.call(who, "GET", "/api/accounts").json()["items"]
    row = next(a for a in listed if a["id"] == account_id)
    assert {c["name"].split()[0] for c in row["contacts"]} == EXPECTED[who]
    assert row["contact_count"] == len(EXPECTED[who])
    detail = api.call(who, "GET", f"/api/accounts/{account_id}").json()
    assert {c["name"].split()[0] for c in detail["contacts"]} == EXPECTED[who]


def test_search_results_link_to_pages_the_role_can_open(api, books):
    own_client = books["clients"]["Own"].id
    own_invoice = books["invoices"]["Own"].id

    def urls(who, q):
        return {r["type"]: r["url"] for r in api.call(who, "GET", "/api/search", params={"q": q}).json()["results"]}

    assert urls("owner", "Own Client")["client"] == f"/sales/clients/{own_client}"
    assert urls("owner", "INV-Own")["invoice"] == f"/sales/invoices/{own_invoice}"
    assert urls("mgr_same_team", "Own Client")["client"] == f"/manager/clients/{own_client}"
    assert urls("mgr_same_team", "INV-Own")["invoice"] == f"/manager/invoices/{own_invoice}"
    assert urls("md", "Own Client")["client"] == f"/md/clients/{own_client}"
    assert urls("md", "INV-Own")["invoice"] == f"/md/invoices/{own_invoice}"
    assert urls("purchase", "INV-Own")["invoice"] == f"/purchase/invoices/{own_invoice}"
