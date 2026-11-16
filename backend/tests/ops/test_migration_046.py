"""Migration 046 on a database that already holds data, as production does.

Empty flags get their default, rows that point at a missing record are left alone while new
ones are refused, duplicate invoice share tokens stop the migration before anything changes,
and anything the database already has is skipped. Runs only when CHAT_PG_TEST_URL points at
a local *_test database (CI does).
"""
import datetime
import decimal
import itertools

import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.chat.safety import disposable_pg_url
from tests.chat.test_chat_migration_postgres import PG_URL, _alembic, _create_db, _drop_db, _url

BEFORE = "045_twilio_voice"
AFTER = "046_schema_drift_cleanup"

pytestmark = pytest.mark.skipif(
    not disposable_pg_url(PG_URL),
    reason="CHAT_PG_TEST_URL not set to a local PostgreSQL *_test database",
)

_seq = itertools.count(1)


@pytest.fixture(scope="module")
def db_at_045():
    name = _create_db()
    mp = pytest.MonkeyPatch()
    try:
        _alembic(name, mp, BEFORE)
    finally:
        mp.undo()
    yield name
    _drop_db(name)


@pytest.fixture
def at_045(db_at_045):
    name = _create_db(template=db_at_045)
    engine = create_engine(_url(name))
    yield name, engine
    engine.dispose()
    _drop_db(name)


def _placeholder(column, n: int):
    kind = column.type.python_type
    if kind is bool:
        return False
    if kind is int:
        return n
    if kind is str:
        return f"x{n}"[: column.type.length or None]
    if kind is datetime.datetime:
        return datetime.datetime(2026, 1, 1)
    if kind is datetime.date:
        return datetime.date(2026, 1, 1)
    if kind is decimal.Decimal:
        return decimal.Decimal(0)
    if kind in (dict, list):
        return kind()
    raise TypeError(f"no placeholder for {column.name} ({column.type})")


def _insert(engine, table: str, **values) -> int:
    """Insert one row, filling other required columns with placeholders. Foreign keys are not
    checked during this setup, so a row can point at a record that does not exist."""
    tbl = Table(table, MetaData(), autoload_with=engine)
    row = dict(values)
    for column in tbl.columns:
        if column.name not in row and not column.nullable and column.server_default is None and not column.primary_key:
            row[column.name] = _placeholder(column, next(_seq))
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        return conn.execute(tbl.insert().values(**row).returning(tbl.c.id)).scalar()


def _one(engine, sql: str, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).one()


def _nullable(engine, table: str, column: str) -> bool:
    return next(c["nullable"] for c in inspect(engine).get_columns(table) if c["name"] == column)


def test_empty_flags_get_their_default_and_become_required(at_045, monkeypatch):
    name, engine = at_045
    company = _insert(engine, "companies", require_2fa=None, is_sandbox=None)
    user = _insert(engine, "users", company_id=company, token_version=None, totp_enabled=None)
    document = _insert(engine, "documents", company_id=company, file_size=None)

    _alembic(name, monkeypatch, AFTER)

    assert tuple(_one(engine, "SELECT require_2fa, is_sandbox FROM companies WHERE id = :i", i=company)) == (False, False)
    assert tuple(_one(engine, "SELECT token_version, totp_enabled FROM users WHERE id = :i", i=user)) == (0, False)
    assert tuple(_one(engine, "SELECT file_size FROM documents WHERE id = :i", i=document)) == (0,)
    for table, column in [("companies", "require_2fa"), ("companies", "is_sandbox"), ("users", "token_version"),
                          ("users", "totp_enabled"), ("documents", "file_size")]:
        assert not _nullable(engine, table, column), f"{table}.{column}"
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("UPDATE users SET token_version = NULL WHERE id = :i"), {"i": user})


def test_rows_pointing_at_missing_records_are_kept_but_new_ones_are_refused(at_045, monkeypatch):
    name, engine = at_045
    task = _insert(engine, "tasks", deal_id=999999)

    _alembic(name, monkeypatch, AFTER)

    assert _one(engine, "SELECT deal_id FROM tasks WHERE id = :i", i=task)[0] == 999999
    validated, on_delete = _one(engine, "SELECT convalidated, confdeltype FROM pg_constraint WHERE conname = 'fk_tasks_deal_id'")
    assert (validated, on_delete) == (False, "n")
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("UPDATE tasks SET deal_id = 999998 WHERE id = :i"), {"i": task})


def test_deleting_a_record_unlinks_rows_instead_of_failing(at_045, monkeypatch):
    name, engine = at_045
    _alembic(name, monkeypatch, AFTER)
    deal = _insert(engine, "deals")
    task = _insert(engine, "tasks", deal_id=deal)

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM deals WHERE id = :i"), {"i": deal})

    assert _one(engine, "SELECT deal_id FROM tasks WHERE id = :i", i=task)[0] is None


def test_duplicate_invoice_share_tokens_stop_the_migration_before_any_change(at_045, monkeypatch):
    name, engine = at_045
    _insert(engine, "invoices", share_token_hash="same")
    _insert(engine, "invoices", share_token_hash="same")
    company = _insert(engine, "companies", require_2fa=None)

    with pytest.raises(RuntimeError, match=r"invoices\(share_token_hash\) values are duplicated"):
        _alembic(name, monkeypatch, AFTER)

    assert _one(engine, "SELECT version_num FROM alembic_version")[0] == BEFORE
    assert _one(engine, "SELECT require_2fa FROM companies WHERE id = :i", i=company)[0] is None
    assert "ix_invoices_share_token_hash" not in {ix["name"] for ix in inspect(engine).get_indexes("invoices")}


def test_steps_the_database_already_has_are_skipped(at_045, monkeypatch):
    name, engine = at_045
    with engine.begin() as conn:
        conn.execute(text("CREATE INDEX ix_leads_deleted_at ON leads (deleted_at)"))
        conn.execute(text("ALTER TABLE tasks ADD CONSTRAINT tasks_deal_link FOREIGN KEY (deal_id) REFERENCES deals (id)"))
        conn.execute(text("ALTER TABLE users ALTER COLUMN token_version SET NOT NULL"))

    _alembic(name, monkeypatch, AFTER)

    task_fks = [fk["name"] for fk in inspect(engine).get_foreign_keys("tasks") if fk["constrained_columns"] == ["deal_id"]]
    assert task_fks == ["tasks_deal_link"]
    assert [ix["name"] for ix in inspect(engine).get_indexes("leads")].count("ix_leads_deleted_at") == 1


def test_downgrade_removes_the_new_rules(at_045, monkeypatch):
    name, engine = at_045
    _alembic(name, monkeypatch, AFTER)
    _alembic(name, monkeypatch, BEFORE, downgrade=True)

    assert _nullable(engine, "users", "token_version")
    assert not [fk for fk in inspect(engine).get_foreign_keys("tasks") if fk["name"] == "fk_tasks_deal_id"]


def test_a_database_shaped_like_production_ends_matching_the_models(at_045, monkeypatch):
    """Production (read-only check, 2026-11-11) differs from a migration-built database: it has
    some indexes and a ledger foreign key the migrations never made, keeps two uniqueness rules
    as constraints, stores ledger_entries.created_at with a time zone, and lacks a few things
    the old create-what-is-missing helper never added there."""
    from tests.ops.test_schema_drift import _drift

    name, engine = at_045
    with engine.begin() as conn:
        for sql in [
            "ALTER TABLE ledger_entries ADD CONSTRAINT ledger_entries_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES users (id)",
            "CREATE INDEX ix_teams_name ON teams (name)",
            "CREATE INDEX ix_team_memberships_company_id ON team_memberships (company_id)",
            "CREATE INDEX ix_ai_conversations_id ON ai_conversations (id)",
            "CREATE INDEX ix_notifications_id ON notifications (id)",
            "CREATE INDEX ix_otp_codes_id ON otp_codes (id)",
            "CREATE INDEX ix_team_memberships_id ON team_memberships (id)",
            "ALTER TABLE invites ADD CONSTRAINT invites_token_key UNIQUE USING INDEX ix_invites_token",
            "ALTER TABLE teams ADD CONSTRAINT ix_teams_company_name UNIQUE USING INDEX ix_teams_company_name",
            "ALTER TABLE ledger_entries ALTER COLUMN created_at TYPE TIMESTAMP WITH TIME ZONE",
            "DROP INDEX ix_companies_status",
            "DROP INDEX ix_users_company_id",
            "DROP INDEX ix_quotes_plan_id",
            "DROP INDEX ix_products_kind",
            "ALTER TABLE products ALTER COLUMN kind DROP NOT NULL",
        ]:
            conn.execute(text(sql))
        plan_fk = conn.execute(text(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'quotes'::regclass AND contype = 'f' AND conkey = "
            "ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = 'quotes'::regclass AND attname = 'plan_id')]"
        )).scalar()
        conn.execute(text(f"ALTER TABLE quotes DROP CONSTRAINT {plan_fk}"))

    _alembic(name, monkeypatch, "head")  # the models describe the newest migration, not 046

    assert _drift(_url(name)) == []
