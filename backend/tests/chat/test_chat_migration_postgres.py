"""Migration ownership on real PostgreSQL, using throwaway databases on a LOCAL server.

Runs only when CHAT_PG_TEST_URL points at a local *_test PostgreSQL database; the
tests create and drop their own chat_mig_* databases on that server and never
touch the database named in the URL.
"""
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.database import Base
from app.models.core.chat import CHAT_TABLES, CHAT_TABLES_040, CHAT_TABLES_041, CHAT_TABLES_042
from app.schema_sync import apply_schema
from tests.chat.safety import MIGRATION_DB_PREFIX, disposable_pg_url

PG_URL = os.environ.get("CHAT_PG_TEST_URL", "")
BACKEND = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not disposable_pg_url(PG_URL),
    reason="CHAT_PG_TEST_URL not set to a local PostgreSQL *_test database",
)

DDL = re.compile(r"(?i)^\s*(CREATE|ALTER|DROP|COMMENT)\b")


def _url(database: str) -> str:
    url = make_url(PG_URL).set(drivername="postgresql+psycopg", database=database)
    return url.render_as_string(hide_password=False)


def _admin():
    return create_engine(_url("postgres"), isolation_level="AUTOCOMMIT")


def _create_db(template: str | None = None) -> str:
    name = f"{MIGRATION_DB_PREFIX}{secrets.token_hex(4)}"
    admin = _admin()
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"' + (f' TEMPLATE "{template}"' if template else "")))
    admin.dispose()
    return name


def _drop_db(name: str) -> None:
    admin = _admin()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


# Runs in the child's environment: proves the URL alembic will use is the throwaway database.
_GUARD = (
    "import sys; from app.config import settings; from tests.chat.safety import migration_target_ok; "
    "sys.exit(0 if migration_target_ok(settings.get_database_url(), sys.argv[1]) else 3)"
)


def _alembic(database: str, monkeypatch, target: str, downgrade: bool = False) -> None:
    """Run alembic in a child process pinned to the throwaway database.

    In-process alembic reads app.config.settings, which another test may have
    reloaded from the environment / .env — that must never redirect a migration
    (and especially a downgrade) to a real database.
    """
    env = {**os.environ, "DATABASE_URL": _url(database)}
    guard = subprocess.run([sys.executable, "-c", _GUARD, database], cwd=BACKEND, env=env, timeout=60)
    assert guard.returncode == 0, f"refusing to migrate: resolved URL is not the throwaway database {database}"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade" if downgrade else "upgrade", target],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-3000:])


def _capture_ddl(engine) -> list[str]:
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, params, context, executemany):
        if DDL.match(statement):
            statements.append(" ".join(statement.split()))

    return statements


def _rls_state(engine, tables) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
            "EXISTS (SELECT 1 FROM pg_policies p WHERE p.tablename = c.relname AND p.policyname = 'tenant_isolation') "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname = ANY(:t)"
        ), {"t": list(tables)})
        return {r[0]: (r[1], r[2], r[3]) for r in rows}


@pytest.fixture(scope="module")
def db_at_039():
    """A database migrated from scratch to 039, used as a template for each test."""
    name = _create_db()
    mp = pytest.MonkeyPatch()
    try:
        _alembic(name, mp, "039_lead_sheet")
    finally:
        mp.undo()
    yield name
    _drop_db(name)


@pytest.fixture
def fresh(db_at_039):
    name = _create_db(template=db_at_039)
    engine = create_engine(_url(name))
    yield name, engine
    engine.dispose()
    _drop_db(name)


def test_migrations_016_to_039_do_not_create_chat_tables(fresh):
    _, engine = fresh
    tables = set(inspect(engine).get_table_names())
    assert not tables & set(CHAT_TABLES)
    assert {"users", "leads", "company_settings"} <= tables


def test_chat_migrations_create_schema_matching_the_models_with_policies(fresh, monkeypatch):
    name, engine = fresh
    _alembic(name, monkeypatch, "head")
    insp = inspect(engine)
    for table in CHAT_TABLES:
        model = Base.metadata.tables[table]
        assert {c["name"] for c in insp.get_columns(table)} == {c.name for c in model.columns}
        assert {u["name"] for u in insp.get_unique_constraints(table)} == {
            c.name for c in model.constraints if c.__class__.__name__ == "UniqueConstraint"
        }
        model_indexes = {i.name for i in model.indexes}
        assert model_indexes <= {i["name"] for i in insp.get_indexes(table)}
    assert _rls_state(engine, CHAT_TABLES) == {t: (True, True, True) for t in CHAT_TABLES}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "045_twilio_voice"


def test_apply_schema_on_up_to_date_database_issues_no_ddl(fresh, monkeypatch):
    name, engine = fresh
    _alembic(name, monkeypatch, "head")
    statements = _capture_ddl(engine)
    assert apply_schema(engine) == 0
    assert statements == []
    # Positive control: the old full re-apply is visible to the same capture.
    from app.tenancy import enable_rls

    assert enable_rls(engine) > 0
    assert any(s.startswith("DROP POLICY") for s in statements)


def test_apply_schema_repairs_only_a_missing_policy(fresh, monkeypatch):
    name, engine = fresh
    _alembic(name, monkeypatch, "head")
    with engine.begin() as conn:
        conn.execute(text("DROP POLICY tenant_isolation ON leads"))
    statements = _capture_ddl(engine)
    assert apply_schema(engine) == 1
    touched = {m for s in statements for m in re.findall(r"(?i)\b(?:ON|TABLE)\s+(\w+)", s)}
    assert touched == {"leads"}, statements
    assert _rls_state(engine, ["leads"]) == {"leads": (True, True, True)}


def test_create_missing_tables_script_skips_chat_tables(fresh):
    name, engine = fresh
    env = {**os.environ, "DATABASE_URL": _url(name)}
    result = subprocess.run(
        [sys.executable, "create_missing_tables.py"], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert not set(inspect(engine).get_table_names()) & set(CHAT_TABLES)
    assert "chat_" not in result.stdout


def test_040_refuses_a_mismatched_existing_chat_table(fresh, monkeypatch):
    name, engine = fresh
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE chat_messages (id integer primary key, body text)"))
    with pytest.raises(RuntimeError, match="chat_messages already exists"):
        _alembic(name, monkeypatch, "head")
    insp = inspect(engine)
    assert "chat_conversations" not in insp.get_table_names()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "039_lead_sheet"


def test_040_downgrade_then_upgrade_round_trip(fresh, monkeypatch):
    name, engine = fresh
    _alembic(name, monkeypatch, "head")
    _alembic(name, monkeypatch, "039_lead_sheet", downgrade=True)
    assert not set(inspect(engine).get_table_names()) & set(CHAT_TABLES)
    _alembic(name, monkeypatch, "head")
    assert set(CHAT_TABLES) <= set(inspect(engine).get_table_names())


def test_041_refuses_a_mismatched_existing_attachments_table(fresh, monkeypatch):
    name, engine = fresh
    _alembic(name, monkeypatch, "040_team_chat")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE chat_attachments (id integer primary key, path text)"))
    with pytest.raises(RuntimeError, match="chat_attachments already exists"):
        _alembic(name, monkeypatch, "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "040_team_chat"


def test_041_downgrade_drops_only_attachments(fresh, monkeypatch):
    name, engine = fresh
    _alembic(name, monkeypatch, "041_chat_attachments")
    _alembic(name, monkeypatch, "040_team_chat", downgrade=True)
    tables = set(inspect(engine).get_table_names())
    assert set(CHAT_TABLES_040) <= tables
    assert not set(CHAT_TABLES_041) & tables
    _alembic(name, monkeypatch, "head")
    assert _rls_state(engine, CHAT_TABLES_041) == {t: (True, True, True) for t in CHAT_TABLES_041}


def test_042_refuses_a_mismatched_existing_calls_table(fresh, monkeypatch):
    name, engine = fresh
    _alembic(name, monkeypatch, "041_chat_attachments")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE chat_calls (id integer primary key, audio bytea)"))
    with pytest.raises(RuntimeError, match="chat_calls already exists"):
        _alembic(name, monkeypatch, "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "041_chat_attachments"


def test_042_downgrade_drops_only_calls(fresh, monkeypatch):
    name, engine = fresh
    _alembic(name, monkeypatch, "head")
    _alembic(name, monkeypatch, "041_chat_attachments", downgrade=True)
    tables = set(inspect(engine).get_table_names())
    assert set(CHAT_TABLES_040 + CHAT_TABLES_041) <= tables
    assert not set(CHAT_TABLES_042) & tables
    _alembic(name, monkeypatch, "head")
    assert _rls_state(engine, CHAT_TABLES_042) == {t: (True, True, True) for t in CHAT_TABLES_042}


# ---- 043: the companies id counter on a database built from migrations ------------------------


@pytest.fixture(scope="module")
def db_at_042():
    """A database migrated from scratch to 042, before the counter fix, used as a template."""
    name = _create_db()
    mp = pytest.MonkeyPatch()
    try:
        _alembic(name, mp, "042_chat_calls")
    finally:
        mp.undo()
    yield name
    _drop_db(name)


@pytest.fixture
def at_042(db_at_042):
    name = _create_db(template=db_at_042)
    engine = create_engine(_url(name))
    yield name, engine
    engine.dispose()
    _drop_db(name)


def _add_company(engine, code: str) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text("INSERT INTO companies (name, company_code, status) VALUES (:name, :code, 'active') RETURNING id"),
            {"name": f"Company {code}", "code": code},
        ).scalar()


def _companies(engine) -> list:
    with engine.connect() as conn:
        return conn.execute(text("SELECT id, name, status, plan FROM companies ORDER BY id")).all()


def test_before_043_a_fresh_database_cannot_add_its_first_company(at_042):
    _, engine = at_042
    with pytest.raises(IntegrityError, match="companies_pkey"):
        _add_company(engine, "AAA")


def test_043_lets_a_fresh_database_add_companies(at_042, monkeypatch):
    name, engine = at_042
    before = _companies(engine)
    assert [row.id for row in before] == [1]
    _alembic(name, monkeypatch, "043_company_id_sequence")
    assert [_add_company(engine, "AAA"), _add_company(engine, "BBB")] == [2, 3]
    assert _companies(engine)[:1] == before


def test_043_never_moves_a_used_counter_backwards(at_042, monkeypatch):
    name, engine = at_042
    with engine.begin() as conn:
        conn.execute(text("SELECT setval(pg_get_serial_sequence('companies', 'id'), 500, true)"))
    _alembic(name, monkeypatch, "043_company_id_sequence")
    assert _add_company(engine, "AAA") == 501


def test_043_keeps_an_unused_counter_that_is_already_ahead(at_042, monkeypatch):
    name, engine = at_042
    with engine.begin() as conn:
        conn.execute(text("SELECT setval(pg_get_serial_sequence('companies', 'id'), 50, false)"))
    _alembic(name, monkeypatch, "043_company_id_sequence")
    assert _add_company(engine, "AAA") == 50


def test_043_moves_past_rows_added_with_explicit_ids(at_042, monkeypatch):
    name, engine = at_042
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO companies (id, name, company_code, status) VALUES (10, 'Ten', 'TEN', 'active')"))
    _alembic(name, monkeypatch, "043_company_id_sequence")
    assert _add_company(engine, "AAA") == 11


def test_043_on_an_empty_companies_table_starts_at_1(at_042, monkeypatch):
    name, engine = at_042
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM companies"))
    _alembic(name, monkeypatch, "043_company_id_sequence")
    assert _add_company(engine, "AAA") == 1
