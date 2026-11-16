"""Phase 7.10 — deal amount + quote discount approvals."""
import pytest

from app.models.core.enums import ApprovalStatus
from app.models.sales.deal import Deal
from app.models.sales.quote import Quote
from app.utils.rate_limit import auth_limiter
from tests.helpers.auth import create_active_user, login_user
from tests.helpers.factories import create_client, create_company


@pytest.fixture(autouse=True)
def _reset_auth():
    auth_limiter._buckets.clear()
    yield


def _company(db, code="AP1"):
    return create_company(db, name=f"Co {code}", company_code=code)


def _set_thresholds(client, *, deal_amount=None, discount_pct=None):
    payload = {}
    if deal_amount is not None:
        payload["deal_approval_amount_threshold"] = deal_amount
    if discount_pct is not None:
        payload["discount_approval_percent_threshold"] = discount_pct
    return client.put("/api/settings/approvals", json=payload)


def test_high_deal_amount_pending_blocks_won(client, db):
    company = _company(db, "AP2")
    admin = create_active_user(db, email="admin@ap2.com", role="admin", company_id=company.id)
    sales = create_active_user(db, email="sales@ap2.com", role="sales", company_id=company.id)
    login_user(client, admin.email)
    _set_thresholds(client, deal_amount="50000")
    login_user(client, sales.email)
    deal = client.post("/api/deals", json={"title": "Big", "amount": "75000"}).json()
    assert deal["approval_status"] == ApprovalStatus.PENDING.value

    tomorrow = (__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
                  + __import__("datetime").timedelta(days=1)).date().isoformat()
    client.post("/api/tasks", json={
        "title": "Follow up",
        "due_date": tomorrow,
        "deal_id": deal["id"],
        "assigned_to_id": sales.id,
    })

    stages = client.get("/api/deals/board").json()["stages"]
    won = next(s for s in stages if s["stage_type"] == "won")
    denied = client.patch(f"/api/deals/{deal['id']}/stage", json={"stage_id": won["stage_id"]})
    assert denied.status_code == 400
    assert "approval" in str(denied.json()["detail"]).lower()


def test_admin_approves_deal_then_won_move(client, db):
    company = _company(db, "AP3")
    admin = create_active_user(db, email="admin@ap3.com", role="admin", company_id=company.id)
    sales = create_active_user(db, email="sales@ap3.com", role="sales", company_id=company.id)
    login_user(client, admin.email)
    _set_thresholds(client, deal_amount="10000")
    login_user(client, sales.email)
    deal_id = client.post("/api/deals", json={"title": "Big", "amount": "20000"}).json()["id"]

    login_user(client, admin.email)
    ok = client.post(f"/api/deals/{deal_id}/approve")
    assert ok.status_code == 200, ok.text
    assert ok.json()["approval_status"] == ApprovalStatus.APPROVED.value

    login_user(client, sales.email)
    tomorrow_task = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    tomorrow = (tomorrow_task + __import__("datetime").timedelta(days=1)).date().isoformat()
    client.post("/api/tasks", json={
        "title": "Close",
        "due_date": tomorrow,
        "deal_id": deal_id,
        "assigned_to_id": sales.id,
    })
    won = next(s for s in client.get("/api/deals/board").json()["stages"] if s["stage_type"] == "won")
    moved = client.patch(f"/api/deals/{deal_id}/stage", json={"stage_id": won["stage_id"]})
    assert moved.status_code == 200, moved.text


def test_discounted_quote_pending_blocks_accept(client, db):
    company = _company(db, "AP4")
    admin = create_active_user(db, email="admin@ap4.com", role="admin", company_id=company.id)
    sales = create_active_user(db, email="sales@ap4.com", role="sales", company_id=company.id)
    customer = create_client(db, company_id=company.id, name="Buyer", assigned_to_id=sales.id)
    login_user(client, admin.email)
    _set_thresholds(client, discount_pct=10)
    product = client.post("/api/products", json={
        "name": "Tile", "unit_price": "1000", "tax_rate": 18, "hsn": "9983",
    }).json()
    login_user(client, sales.email)
    deal = client.post("/api/deals", json={"title": "Job", "amount": "1000", "client_id": customer.id}).json()
    q = client.post("/api/quotes", json={
        "deal_id": deal["id"],
        "client_id": customer.id,
        "items": [{"product_id": product["id"], "quantity": 1, "unit_price": "800", "description": "Tile"}],
    })
    assert q.status_code == 201, q.text
    assert q.json()["approval_status"] == ApprovalStatus.PENDING.value
    denied = client.post(f"/api/quotes/{q.json()['id']}/accept")
    assert denied.status_code == 400


def test_approve_quote_then_accept(client, db):
    company = _company(db, "AP5")
    admin = create_active_user(db, email="admin@ap5.com", role="admin", company_id=company.id)
    sales = create_active_user(db, email="sales@ap5.com", role="sales", company_id=company.id)
    customer = create_client(db, company_id=company.id, name="Buyer", assigned_to_id=sales.id)
    login_user(client, admin.email)
    _set_thresholds(client, discount_pct=5)
    product = client.post("/api/products", json={
        "name": "Tile", "unit_price": "1000", "tax_rate": 18, "hsn": "9983",
    }).json()
    login_user(client, sales.email)
    deal = client.post("/api/deals", json={"title": "Job", "amount": "1000", "client_id": customer.id}).json()
    qid = client.post("/api/quotes", json={
        "deal_id": deal["id"],
        "client_id": customer.id,
        "items": [{"product_id": product["id"], "quantity": 1, "unit_price": "900", "description": "Tile"}],
    }).json()["id"]

    login_user(client, admin.email)
    assert client.post(f"/api/quotes/{qid}/approve").status_code == 200

    login_user(client, sales.email)
    accepted = client.post(f"/api/quotes/{qid}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["sales_order_id"] is not None


def test_pending_queue_lists_deals_and_quotes(client, db):
    company = _company(db, "AP6")
    admin = create_active_user(db, email="admin@ap6.com", role="admin", company_id=company.id)
    sales = create_active_user(db, email="sales@ap6.com", role="sales", company_id=company.id)
    customer = create_client(db, company_id=company.id, name="Buyer", assigned_to_id=sales.id)
    login_user(client, admin.email)
    _set_thresholds(client, deal_amount="1", discount_pct=1)
    product = client.post("/api/products", json={
        "name": "P", "unit_price": "100", "tax_rate": 18,
    }).json()
    login_user(client, sales.email)
    client.post("/api/deals", json={"title": "D", "amount": "5000"})
    deal = client.post("/api/deals", json={"title": "Q deal", "amount": "100", "client_id": customer.id}).json()
    client.post("/api/quotes", json={
        "deal_id": deal["id"],
        "client_id": customer.id,
        "items": [{"product_id": product["id"], "quantity": 1, "unit_price": "90", "description": "P"}],
    })

    login_user(client, admin.email)
    pending = client.get("/api/approvals/pending")
    assert pending.status_code == 200, pending.text
    body = pending.json()
    assert body["deals_total"] >= 1
    assert body["quotes_total"] >= 1
