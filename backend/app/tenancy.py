"""Postgres row-level security: request tenant context + policy DDL.

SQLite and unit tests are no-ops. Policies are applied on Postgres by
`create_missing_tables.enable_rls`.
"""
from __future__ import annotations

import re
from contextvars import ContextVar

from sqlalchemy import event, inspect, text
from sqlalchemy.orm import Session

from app.database import Base

_company_id: ContextVar[int | None] = ContextVar("rls_company_id", default=None)
_bypass: ContextVar[bool] = ContextVar("rls_bypass", default=False)

RLS_EXCLUDE = frozenset({
    "users",
    "companies",
    "plans",
    "otp_codes",
    "refresh_tokens",
    "mfa_recovery_codes",
    "oauth_identities",
    "api_keys",
    "api_usage_daily",
    "webhook_events",
})

_PUBLIC_PREFIXES = (
    "/api/auth",
    "/api/portal",
    "/api/public",
    "/api/v1",
)

_TABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def current_company_id() -> int | None:
    return _company_id.get()


def current_bypass() -> bool:
    return _bypass.get()


def bind_tenant(*, company_id: int | None = None, bypass: bool = False, db: Session | None = None) -> None:
    _bypass.set(bool(bypass))
    _company_id.set(None if bypass else company_id)
    if db is not None:
        apply_rls_to_connection(db.connection())


def reset_tenant() -> None:
    _company_id.set(None)
    _bypass.set(False)


def path_uses_rls_bypass(path: str) -> bool:
    path = path.split("?", 1)[0]
    if path in ("/", "/health", "/docs", "/redoc", "/openapi.json"):
        return True
    if any(path == p or path.startswith(p + "/") for p in _PUBLIC_PREFIXES):
        return True
    if path == "/api/billing/webhook" or path.startswith("/api/billing/webhook/"):
        return True
    if path.startswith("/api/whatsapp/webhook"):
        return True
    if path.startswith("/api/telephony/") and path.rstrip("/").endswith("webhook"):
        return True
    if "/oauth/" in path and path.rstrip("/").endswith("callback"):
        return True
    return False


def bind_for_user(user, db: Session | None = None) -> None:
    role = getattr(getattr(user, "role", None), "value", getattr(user, "role", None))
    if role == "admin" and getattr(user, "company_id", None) is None:
        bind_tenant(bypass=True, db=db)
        return
    bind_tenant(company_id=getattr(user, "company_id", None), bypass=False, db=db)


def _ensure_models_loaded() -> None:
    import app.models.billing  # noqa: F401
    import app.models.core  # noqa: F401
    import app.models.finance  # noqa: F401
    import app.models.hr  # noqa: F401
    import app.models.ops  # noqa: F401
    import app.models.sales  # noqa: F401


def tenant_table_names() -> list[str]:
    _ensure_models_loaded()
    names = []
    for table in Base.metadata.tables.values():
        if table.name in RLS_EXCLUDE:
            continue
        if "company_id" in table.c:
            names.append(table.name)
    return sorted(names)


def policy_statements(table_name: str) -> list[str]:
    if not _TABLE_NAME_RE.match(table_name):
        raise ValueError(f"unsafe table name: {table_name}")
    return [
        f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS tenant_isolation ON {table_name}",
        (
            f"CREATE POLICY tenant_isolation ON {table_name} "
            f"USING ("
            f"current_setting('app.bypass_rls', true) = 'on' "
            f"OR company_id = NULLIF(current_setting('app.company_id', true), '')::integer"
            f") WITH CHECK ("
            f"current_setting('app.bypass_rls', true) = 'on' "
            f"OR company_id = NULLIF(current_setting('app.company_id', true), '')::integer"
            f")"
        ),
    ]


def policy_sql(table_name: str) -> str:
    return ";\n".join(policy_statements(table_name)) + ";"


def is_postgres_bind(bind) -> bool:
    dialect = getattr(bind, "dialect", None)
    name = getattr(dialect, "name", None) if dialect is not None else None
    return name == "postgresql"


def apply_rls_to_connection(connection) -> None:
    if not is_postgres_bind(connection):
        return
    if current_bypass():
        connection.execute(text("SELECT set_config('app.bypass_rls', 'on', true), set_config('app.company_id', '', true)"))
        return
    cid = current_company_id()
    connection.execute(
        text("SELECT set_config('app.bypass_rls', 'off', true), set_config('app.company_id', :cid, true)"),
        {"cid": "" if cid is None else str(int(cid))},
    )


@event.listens_for(Session, "after_begin")
def _rls_after_begin(session, transaction, connection) -> None:
    apply_rls_to_connection(connection)


def _tables_with_policy_in_place(conn, names: list[str]) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relkind = 'r' AND c.relname = ANY(:names) "
            "AND c.relrowsecurity AND c.relforcerowsecurity "
            "AND EXISTS (SELECT 1 FROM pg_policies p WHERE p.schemaname = n.nspname "
            "AND p.tablename = c.relname AND p.policyname = 'tenant_isolation')"
        ),
        {"names": names},
    )
    return {r[0] for r in rows}


def enable_rls(bind, *, exclude: frozenset[str] = frozenset(), only_missing: bool = False) -> int:
    """Create/replace tenant policies. No-op on SQLite. Returns the number of tables changed.

    only_missing=True leaves tables that already have RLS enabled, forced and a
    tenant_isolation policy untouched, so routine runs issue no DDL (and take no
    table locks). A changed policy definition therefore needs its own migration.

    When `bind` is an open connection (Alembic), run on that connection.
    Opening a second transaction deadlocks behind the caller's ALTER locks.
    """
    if not is_postgres_bind(bind):
        return 0
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())

    def apply(conn) -> int:
        names = [n for n in tenant_table_names() if n in existing and n not in exclude]
        done = _tables_with_policy_in_place(conn, names) if only_missing and names else set()
        applied = 0
        for name in names:
            if name in done:
                continue
            for stmt in policy_statements(name):
                conn.execute(text(stmt))
            applied += 1
        return applied

    # Engine has .connect(); a Connection does not.
    if hasattr(bind, "connect"):
        with bind.begin() as conn:
            return apply(conn)
    return apply(bind)
