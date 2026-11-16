from unittest.mock import patch

from tests.helpers.auth import create_active_user, login_user
from tests.helpers.factories import create_company


def _admin(client, db, code, role="admin"):
    company = create_company(db, name=f"Co {code}", company_code=code)
    user = create_active_user(
        db, email=f"{role}@{code.lower()}.com", role=role, company_id=company.id
    )
    login_user(client, user.email)
    return company, user


@patch("app.services.sales.crm_email.send_email", return_value=True)
def test_blast_leads(mock_send, client, db):
    _admin(client, db, "MA1")
    client.post("/api/leads", json={"name": "Ravi", "email": "ravi@x.com"})
    listed = client.get("/api/mass-email")
    assert listed.status_code == 200, listed.text
    assert listed.json()["remaining_today"] == 100
    sent = client.post("/api/mass-email", json={
        "subject": "Hi", "body": "Hello", "audience": "leads",
    })
    assert sent.status_code == 200, sent.text
    assert sent.json()["sent"] == 1
    assert sent.json()["remaining_today"] == 99
    history = client.get("/api/mass-email").json()
    assert history["total"] == 1
    mock_send.assert_called()


def test_sales_forbidden(client, db):
    _admin(client, db, "MA2", role="sales")
    assert client.get("/api/mass-email").status_code == 403
    assert client.post("/api/mass-email", json={
        "subject": "Hi", "body": "Hello", "audience": "leads",
    }).status_code == 403


def test_explicit_ids(client, db):
    _admin(client, db, "MA3")
    lead_id = client.post("/api/leads", json={"name": "Ravi", "email": "r@x.com"}).json()["id"]
    with patch("app.services.sales.crm_email.send_email", return_value=True):
        sent = client.post("/api/mass-email", json={
            "subject": "Hi", "body": "Hello", "lead_ids": [lead_id],
        })
    assert sent.status_code == 200, sent.text
    assert sent.json()["sent"] == 1
