from sqlalchemy import inspect

from app.models.billing import Plan
from app.models.core.api_key import ApiKey, ApiUsageDaily
from app.models.core.enums import ApiKeyAccess
from app.services.billing.seed import seed_plans, backfill_api_quotas
from tests.helpers.factories import create_company


def test_api_key_tables_exist_with_company_id(db_engine):
    tables = set(inspect(db_engine).get_table_names())
    assert {"api_keys", "api_usage_daily"} <= tables
    key_cols = {c["name"] for c in inspect(db_engine).get_columns("api_keys")}
    usage_cols = {c["name"] for c in inspect(db_engine).get_columns("api_usage_daily")}
    assert {"company_id", "prefix", "token_hash", "access", "revoked_at"} <= key_cols
    assert {"company_id", "usage_date", "request_count"} <= usage_cols
    plan_cols = {c["name"] for c in inspect(db_engine).get_columns("plans")}
    assert "max_api_requests_per_day" in plan_cols


def test_api_key_access_values():
    assert ApiKeyAccess.READ.value == "read"
    assert ApiKeyAccess.WRITE.value == "write"


def test_can_persist_api_key_and_usage_row(db):
    company = create_company(db, name="Schema Co 1", company_code="S01")
    key = ApiKey(
        company_id=company.id,
        name="Zapier",
        prefix="crm_live_ab12cd34",
        token_hash="a" * 64,
        access=ApiKeyAccess.READ,
    )
    usage = ApiUsageDaily(company_id=company.id, usage_date=__import__("datetime").date(2026, 8, 25), request_count=3)
    db.add_all([key, usage])
    db.commit()
    db.refresh(key)
    db.refresh(usage)
    assert key.id is not None
    assert usage.request_count == 3


def test_seed_and_backfill_set_plan_api_quotas(db):
    seed_plans(db)
    backfill_api_quotas(db)
    starter = db.query(Plan).filter(Plan.name == "Starter").one()
    growth = db.query(Plan).filter(Plan.name == "Growth").one()
    enterprise = db.query(Plan).filter(Plan.name == "Enterprise").one()
    assert starter.max_api_requests_per_day == 1000
    assert growth.max_api_requests_per_day == 10000
    assert enterprise.max_api_requests_per_day is None
    growth.max_api_requests_per_day = 50
    db.commit()
    backfill_api_quotas(db)
    db.refresh(growth)
    assert growth.max_api_requests_per_day == 50
