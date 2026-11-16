from unittest.mock import patch

from tests.helpers.auth import create_active_user, login_user
from tests.helpers.factories import create_company


def _admin(client, db, code):
    company = create_company(db, name=f"Co {code}", company_code=code)
    user = create_active_user(
        db, email=f"admin@{code.lower()}.com", role="admin", company_id=company.id
    )
    login_user(client, user.email)
    return company, user


def test_cannot_blast_foreign_leads_or_see_history(client, db):
    _, admin_a = _admin(client, db, "MXA")
    login_user(client, admin_a.email)
    lead_id = client.post("/api/leads", json={"name": "A", "email": "a@x.com"}).json()["id"]
    with patch("app.services.sales.crm_email.send_email", return_value=True):
        assert client.post("/api/mass-email", json={
            "subject": "Hi", "body": "Hello", "lead_ids": [lead_id],
        }).status_code == 200

    _, admin_b = _admin(client, db, "MXB")
    login_user(client, admin_b.email)
    assert client.get("/api/mass-email").json()["total"] == 0
    bad = client.post("/api/mass-email", json={
        "subject": "Hi", "body": "Hello", "lead_ids": [lead_id],
    })
    assert bad.status_code == 400
