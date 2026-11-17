from app.models.sales.support_case import SupportCase, WebToCaseForm
from tests.helpers.factories import create_company


def test_case_columns():
    cols = {c.name for c in SupportCase.__table__.columns}
    assert cols == {
        "id", "company_id", "client_id", "subject", "body", "status",
        "requester_name", "requester_email", "source",
        "created_at", "updated_at",
    }


def test_form_columns():
    cols = {c.name for c in WebToCaseForm.__table__.columns}
    assert cols == {"id", "company_id", "slug", "is_active", "created_at"}


def test_case_persists(db):
    company = create_company(db, name="Schema Co 1", company_code="S01")
    row = SupportCase(
        company_id=company.id, subject="Leak", body="Roof leak", status="open",
        requester_email="a@x.com", source="crm",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    assert row.id is not None
    assert row.status == "open"
