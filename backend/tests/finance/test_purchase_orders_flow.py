"""The order to Purchase flow: the rep hears back, an order is decided once, rejected orders are listed,
the approval page gets the real numbers, and every sale lands in the Stock Register."""
import re

import pytest

from app.models.sales.notification import Notification
from app.utils.rate_limit import auth_limiter
from tests.helpers.auth import create_active_user, login_user
from tests.helpers.factories import create_client, create_company


@pytest.fixture(autouse=True)
def _reset_auth_rate_limit():
    auth_limiter._buckets.clear()
    yield


@pytest.fixture
def shop(client, db):
    company = create_company(db, name="Shop Co", company_code="SHP")
    rep = create_active_user(db, email="rep@shp.co", role="sales", company_id=company.id, full_name="Rep One")
    buyer = create_active_user(db, email="buyer@shp.co", role="purchase", company_id=company.id, full_name="Buyer One")
    admin = create_active_user(db, email="admin@shp.co", role="admin", company_id=company.id)
    customer = create_client(db, company_id=company.id, name="Customer", assigned_to_id=rep.id)

    login_user(client, buyer.email)
    fan = client.post("/api/inventory", json={"name": "Fan", "sku": "FAN", "unit_price": 1500, "quantity": 100, "reorder_level": 5}).json()
    bulb = client.post("/api/inventory", json={"name": "Bulb", "sku": "BLB", "unit_price": 50, "quantity": 40, "reorder_level": 5}).json()
    fan_product = client.post("/api/products", json={"name": "Fan", "sku": "P-FAN", "kind": "goods", "unit_price": 2200,
                                                   "tax_rate": 18, "stock_item_id": fan["id"]}).json()
    install = client.post("/api/products", json={"name": "Install", "sku": "P-INS", "kind": "service", "unit_price": 500,
                                                "tax_rate": 18}).json()
    client.headers.pop("Authorization", None)
    return {"company": company, "rep": rep, "buyer": buyer, "admin": admin, "customer": customer,
            "fan": fan, "bulb": bulb, "fan_product": fan_product, "install": install}


def _order(client, shop, discount=None):
    login_user(client, shop["rep"].email)
    body = {"client_id": shop["customer"].id, "due_days": 30, "items": [
        {"description": "", "product_id": shop["fan_product"]["id"], "quantity": 10},
        {"description": "", "product_id": shop["install"]["id"], "quantity": 1},
        {"description": "Bulb", "stock_item_id": shop["bulb"]["id"], "quantity": 4, "unit_price": 80},
    ]}
    if discount is not None:
        body["discount"] = discount
    response = client.post("/api/invoices", json=body)
    assert response.status_code == 201, response.text
    client.headers.pop("Authorization", None)
    return response.json()


def _as(client, user):
    client.headers.pop("Authorization", None)
    login_user(client, user.email)


def _saved_notifications(db, user_id):
    db.rollback()  # anything the request left unsaved is thrown away here, as it is when a real request ends
    return [n.title for n in db.query(Notification).filter(Notification.user_id == user_id).all()]


def test_approving_tells_the_rep_and_is_saved(client, db, shop):
    order = _order(client, shop)
    _as(client, shop["buyer"])
    assert client.post(f"/api/purchase/sales/{order['id']}/approve").status_code == 200
    titles = _saved_notifications(db, shop["rep"].id)
    assert any(t.startswith("Invoice approved:") for t in titles), titles


def test_rejecting_tells_the_rep_why_and_is_saved(client, db, shop):
    order = _order(client, shop)
    _as(client, shop["buyer"])
    assert client.post(f"/api/purchase/sales/{order['id']}/reject", params={"reason": "Credit limit"}).status_code == 200
    db.rollback()
    note = db.query(Notification).filter(Notification.user_id == shop["rep"].id,
                                         Notification.title.like("Invoice rejected:%")).one()
    assert "Credit limit" in note.message


def test_an_order_is_decided_only_once(client, db, shop):
    approved = _order(client, shop)
    rejected = _order(client, shop)
    _as(client, shop["buyer"])
    assert client.post(f"/api/purchase/sales/{approved['id']}/approve").status_code == 200
    assert client.post(f"/api/purchase/sales/{approved['id']}/approve").status_code == 400
    assert client.post(f"/api/purchase/sales/{approved['id']}/reject", params={"reason": "x"}).status_code == 400
    assert client.post(f"/api/purchase/sales/{rejected['id']}/reject", params={"reason": "x"}).status_code == 200
    assert client.post(f"/api/purchase/sales/{rejected['id']}/reject", params={"reason": "x"}).status_code == 400
    assert client.post(f"/api/purchase/sales/{rejected['id']}/approve").status_code == 400
    statuses = {row["id"]: row["status"] for row in client.get("/api/purchase/invoices").json()["invoices"]}
    assert statuses[approved["id"]] == "pending"
    assert statuses[rejected["id"]] == "cancelled"


def test_paid_orders_cannot_be_rejected(client, db, shop):
    order = _order(client, shop)
    _as(client, shop["buyer"])
    client.post(f"/api/purchase/sales/{order['id']}/approve")
    client.post(f"/api/purchase/invoices/{order['id']}/mark-paid", params={"payment_date": "2026-11-16"})
    assert client.post(f"/api/purchase/sales/{order['id']}/reject", params={"reason": "x"}).status_code == 400


def test_sales_approvals_lists_rejected_orders(client, db, shop):
    waiting = _order(client, shop)
    rejected = _order(client, shop)
    _as(client, shop["buyer"])
    client.post(f"/api/purchase/sales/{rejected['id']}/reject", params={"reason": "x"})
    listed = {row["id"]: row["status"] for row in client.get("/api/purchase/sales").json()["sales"]}
    assert listed == {waiting["id"]: "Draft", rejected["id"]: "Cancelled"}


def test_approval_page_gets_subtotal_discount_tax_and_total(client, db, shop):
    order = _order(client, shop, discount=1000)
    _as(client, shop["buyer"])
    deal = client.get(f"/api/purchase/sales/{order['id']}").json()["deal"]
    subtotal, discount, tax, total = (float(deal[k]) for k in ("subtotal", "discount", "tax", "amount"))
    assert (subtotal, discount) == (22000 + 500 + 320, 1000)
    assert tax > 0
    assert round(subtotal - discount + tax, 2) == round(total, 2)


def _register(client, product):
    rows = client.get("/api/ledgers/stock_register").json()["rows"]
    return [r for r in rows if r["id"] > 0 and r["product"] == product]


def test_every_sale_is_in_the_stock_register_and_matches_the_stock_page(client, db, shop):
    order = _order(client, shop)
    _as(client, shop["buyer"])
    stock = {i["sku"]: i["quantity"] for i in client.get("/api/inventory").json()["items"]}
    assert stock == {"FAN": 90, "BLB": 36}
    for product, sku in (("Fan", "FAN"), ("Bulb", "BLB")):
        rows = _register(client, product)
        assert sum(int(r["qty_in"]) - int(r["qty_out"]) for r in rows) == stock[sku], rows
        sale = [r for r in rows if int(r["qty_out"]) > 0]
        assert len(sale) == 1 and sale[0]["remarks"] == f"Sold on order {order['invoice_number']}"
    assert _register(client, "Fan")[-1]["sale_rate"] == 2200
    assert _register(client, "Bulb")[-1]["sale_rate"] == 80
    assert not _register(client, "Install")


def test_a_sales_order_turned_into_an_invoice_is_in_the_stock_register(client, db, shop):
    login_user(client, shop["rep"].email)
    quote = client.post("/api/quotes", json={"client_id": shop["customer"].id, "items": [
        {"description": "", "product_id": shop["fan_product"]["id"], "quantity": 3}]})
    assert quote.status_code == 201, quote.text
    accepted = client.post(f"/api/quotes/{quote.json()['id']}/accept")
    assert accepted.status_code == 200, accepted.text
    invoiced = client.post(f"/api/sales-orders/{accepted.json()['sales_order_id']}/invoice")
    assert invoiced.status_code == 200, invoiced.text
    _as(client, shop["buyer"])
    assert {i["sku"]: i["quantity"] for i in client.get("/api/inventory").json()["items"]}["FAN"] == 97
    assert [int(r["qty_out"]) for r in _register(client, "Fan") if int(r["qty_out"]) > 0] == [3]


def test_a_public_api_sale_is_in_the_stock_register(client, db, shop):
    _as(client, shop["admin"])
    token = client.post("/api/api-keys", json={"name": "Shop", "access": "write"}).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    sold = client.post("/api/v1/invoices", json={"client_id": shop["customer"].id, "items": [
        {"description": "Bulb", "quantity": 5, "unit_price": 90, "stock_item_id": shop["bulb"]["id"]}]})
    assert sold.status_code == 201, sold.text
    _as(client, shop["buyer"])
    assert {i["sku"]: i["quantity"] for i in client.get("/api/inventory").json()["items"]}["BLB"] == 35
    sale = [r for r in _register(client, "Bulb") if int(r["qty_out"]) > 0]
    assert [(int(r["qty_out"]), r["sale_rate"]) for r in sale] == [(5, 90)]


def test_stock_register_shows_the_final_order_number_after_approval(client, db, shop):
    order = _order(client, shop)
    other = _order(client, shop)
    _as(client, shop["buyer"])
    client.post(f"/api/purchase/sales/{order['id']}/approve")
    final = next(row["number"] for row in client.get("/api/purchase/invoices").json()["invoices"] if row["id"] == order["id"])
    assert final != order["invoice_number"] and not final.startswith("DRAFT-")
    remarks = [r["remarks"] for r in _register(client, "Fan") if int(r["qty_out"]) > 0]
    assert sorted(remarks) == sorted([f"Sold on order {final}", f"Sold on order {other['invoice_number']}"])


def _stock(client):
    return {i["sku"]: i["quantity"] for i in client.get("/api/inventory").json()["items"]}


def test_each_order_line_remembers_the_stock_it_took(client, db, shop):
    from app.models.finance.invoice import InvoiceItem

    order = _order(client, shop)
    lines = {row.description: row.stock_item_id for row in db.query(InvoiceItem).filter(InvoiceItem.invoice_id == order["id"])}
    assert lines == {"Fan": shop["fan"]["id"], "Install": None, "Bulb": shop["bulb"]["id"]}


def test_rejecting_gives_the_stock_back_once(client, db, shop):
    order = _order(client, shop)
    _as(client, shop["buyer"])
    assert _stock(client) == {"FAN": 90, "BLB": 36}
    assert client.post(f"/api/purchase/sales/{order['id']}/reject", params={"reason": "x"}).status_code == 200
    assert _stock(client) == {"FAN": 100, "BLB": 40}
    assert client.post(f"/api/purchase/sales/{order['id']}/reject", params={"reason": "x"}).status_code == 400
    assert _stock(client) == {"FAN": 100, "BLB": 40}

    for product, sku in (("Fan", "FAN"), ("Bulb", "BLB")):
        rows = _register(client, product)
        returned = [r for r in rows if r["remarks"] == f"Returned: order {order['invoice_number']} rejected"]
        assert len(returned) == 1
        assert sum(int(r["qty_in"]) - int(r["qty_out"]) for r in rows) == _stock(client)[sku]


def test_approving_keeps_the_stock_taken(client, db, shop):
    order = _order(client, shop)
    _as(client, shop["buyer"])
    client.post(f"/api/purchase/sales/{order['id']}/approve")
    assert _stock(client) == {"FAN": 90, "BLB": 36}


def test_an_order_from_before_this_change_is_rejected_without_touching_stock(client, db, shop):
    from app.models.finance.invoice import InvoiceItem

    order = _order(client, shop)
    db.query(InvoiceItem).filter(InvoiceItem.invoice_id == order["id"]).update({InvoiceItem.stock_item_id: None})
    db.commit()
    _as(client, shop["buyer"])
    assert client.post(f"/api/purchase/sales/{order['id']}/reject", params={"reason": "x"}).status_code == 200
    assert _stock(client) == {"FAN": 90, "BLB": 36}


def _order_from_quote(client, shop, quantity=3):
    login_user(client, shop["rep"].email)
    quote = client.post("/api/quotes", json={"client_id": shop["customer"].id, "items": [
        {"description": "", "product_id": shop["fan_product"]["id"], "quantity": quantity}]})
    accepted = client.post(f"/api/quotes/{quote.json()['id']}/accept")
    invoiced = client.post(f"/api/sales-orders/{accepted.json()['sales_order_id']}/invoice")
    assert invoiced.status_code == 200, invoiced.text
    client.headers.pop("Authorization", None)
    return invoiced.json()["invoice_id"]


def test_an_order_from_a_quote_waits_for_purchase(client, db, shop):
    invoice_id = _order_from_quote(client, shop)
    _as(client, shop["buyer"])
    listed = {row["id"]: row["status"] for row in client.get("/api/purchase/sales").json()["sales"]}
    assert listed[invoice_id] == "Draft"
    number = client.get(f"/api/purchase/sales/{invoice_id}").json()["number"]
    assert number.startswith("DRAFT-")


def test_approving_an_order_from_a_quote_gives_it_the_company_number(client, db, shop):
    invoice_id = _order_from_quote(client, shop)
    _as(client, shop["buyer"])
    assert client.post(f"/api/purchase/sales/{invoice_id}/approve").status_code == 200
    final = next(r["number"] for r in client.get("/api/purchase/invoices").json()["invoices"] if r["id"] == invoice_id)
    assert re.fullmatch(r"INV-\d{3}-\d{4}", final), final  # the company's numbering, not a random one
    assert [r["remarks"] for r in _register(client, "Fan") if int(r["qty_out"]) > 0] == [f"Sold on order {final}"]


def test_rejecting_an_order_from_a_quote_gives_its_stock_back(client, db, shop):
    invoice_id = _order_from_quote(client, shop, quantity=4)
    _as(client, shop["buyer"])
    assert _stock(client)["FAN"] == 96
    assert client.post(f"/api/purchase/sales/{invoice_id}/reject", params={"reason": "x"}).status_code == 200
    assert _stock(client)["FAN"] == 100
