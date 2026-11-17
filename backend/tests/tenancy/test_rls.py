import pytest
from app.tenancy import (
    RLS_EXCLUDE,
    bind_tenant,
    current_bypass,
    current_company_id,
    path_uses_rls_bypass,
    policy_sql,
    reset_tenant,
    tenant_table_names,
)


def test_login_tables_are_excluded():
    for name in ("users", "companies", "otp_codes", "api_keys"):
        assert name in RLS_EXCLUDE
    names = tenant_table_names()
    assert "leads" in names
    assert "invoices" in names
    assert "accounts" in names
    assert "saved_filters" in names
    assert "webhook_endpoints" in names
    assert "webhook_deliveries" in names
    assert "saml_configs" in names
    assert "privacy_requests" in names
    assert "users" not in names
    assert "companies" not in names


def test_policy_sql_uses_setting_and_check():
    sql = policy_sql("leads")
    assert "leads" in sql
    assert "app.company_id" in sql
    assert "app.bypass_rls" in sql
    assert "WITH CHECK" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql


def test_public_paths_bypass():
    assert path_uses_rls_bypass("/api/auth/login") is True
    assert path_uses_rls_bypass("/api/portal/invoices/abc") is True
    assert path_uses_rls_bypass("/api/whatsapp/webhook") is True
    assert path_uses_rls_bypass("/api/telephony/exotel/webhook") is True
    assert path_uses_rls_bypass("/api/billing/webhook") is True
    assert path_uses_rls_bypass("/api/mailbox/oauth/google/callback") is True
    assert path_uses_rls_bypass("/api/leads") is False
    assert path_uses_rls_bypass("/api/whatsapp/send") is False


def test_enable_rls_noop_on_sqlite():
    from app.tenancy import enable_rls, is_postgres_bind
    from tests.conftest import engine as test_engine

    if test_engine.dialect.name != "sqlite":
        pytest.skip("checks the SQLite case; this run uses Postgres")
    assert is_postgres_bind(test_engine) is False
    assert enable_rls(test_engine) == 0
    reset_tenant()
    assert current_company_id() is None
    assert current_bypass() is False
    bind_tenant(company_id=42)
    assert current_company_id() == 42
    assert current_bypass() is False
    bind_tenant(bypass=True)
    assert current_bypass() is True
    reset_tenant()
    assert current_company_id() is None
    assert current_bypass() is False
