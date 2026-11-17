from app.models.sales.campaign import EmailCampaign, EmailCampaignRecipient
from tests.helpers.factories import create_company


def test_campaign_columns():
    cols = {c.name for c in EmailCampaign.__table__.columns}
    assert cols == {
        "id", "company_id", "name", "subject", "body", "audience",
        "status", "created_by_id", "created_at", "sent_at",
    }


def test_recipient_columns():
    cols = {c.name for c in EmailCampaignRecipient.__table__.columns}
    assert cols == {
        "id", "company_id", "campaign_id", "to_email",
        "lead_id", "client_id", "email_log_id", "status",
    }


def test_campaign_persists(db):
    company = create_company(db, name="Schema Co 1", company_code="S01")
    row = EmailCampaign(
        company_id=company.id, name="Spring", subject="Hi", body="Hello",
        audience="leads", status="draft",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    assert row.id is not None
    rec = EmailCampaignRecipient(
        company_id=company.id, campaign_id=row.id, to_email="a@x.com", status="sent",
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    assert rec.campaign_id == row.id
