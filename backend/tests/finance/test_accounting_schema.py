from app.models.finance.accounting import AccountingConnection, AccountingSyncItem
from tests.helpers.factories import create_company


def test_connection_columns():
    cols = {c.name for c in AccountingConnection.__table__.columns}
    assert cols == {
        "id", "company_id", "provider", "status",
        "connected_at", "last_sync_at", "last_error",
        "tally_url", "tally_company_name",
    }


def test_sync_item_columns():
    cols = {c.name for c in AccountingSyncItem.__table__.columns}
    assert cols == {
        "id", "company_id", "entity_type", "entity_id", "provider",
        "external_id", "status", "payload_hash", "last_synced_at",
    }


def test_connection_persists(db):
    company = create_company(db, name="Schema Co 1", company_code="S01")
    row = AccountingConnection(company_id=company.id, provider="tally", status="connected")
    db.add(row)
    db.commit()
    db.refresh(row)
    assert row.id is not None
    assert row.status == "connected"


def test_sync_item_persists(db):
    company = create_company(db, name="Schema Co 1", company_code="S01")
    row = AccountingSyncItem(
        company_id=company.id, entity_type="invoice", entity_id=9,
        provider="tally", external_id="abc", status="synced", payload_hash="h",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    assert row.id is not None
    assert row.entity_id == 9
