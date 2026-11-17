from app.models.sales.marketplace import MarketplaceInstall
from tests.helpers.factories import create_company


def test_install_columns():
    cols = {c.name for c in MarketplaceInstall.__table__.columns}
    assert cols == {
        "id", "company_id", "app_slug", "status",
        "installed_by_id", "installed_at", "updated_at",
    }


def test_install_persists(db):
    company = create_company(db, name="Schema Co 1", company_code="S01")
    row = MarketplaceInstall(
        company_id=company.id, app_slug="scoring", status="installed",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    assert row.id is not None
    assert row.app_slug == "scoring"
    assert row.status == "installed"
