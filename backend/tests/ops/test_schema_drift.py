"""Models and migrations must agree.

The database the migrations build already differs from the models in known ways, listed in
schema_drift_baseline.txt. A pull request may not change that list silently: a model changed
without a migration shows up as a new line, and a migration that changes something else shows
up as a missing one. Runs only when CHAT_PG_TEST_URL points at a local *_test database (CI does).
Regenerate the list with UPDATE_SCHEMA_DRIFT_BASELINE=1 once a change is intended.
"""
import os
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

import app.models  # noqa: F401  registers every table on Base.metadata
from app.database import Base
from tests.chat.safety import disposable_pg_url
from tests.chat.test_chat_migration_postgres import PG_URL, _alembic, _create_db, _drop_db, _url

BASELINE = Path(__file__).with_name("schema_drift_baseline.txt")
HEADER = "# Known differences between the models and the database the migrations build (see test_schema_drift.py).\n"

pytestmark = pytest.mark.skipif(
    not disposable_pg_url(PG_URL),
    reason="CHAT_PG_TEST_URL not set to a local PostgreSQL *_test database",
)


def _describe(op) -> str:
    kind = op[0]
    if kind in ("add_index", "remove_index"):
        ix = op[1]
        cols = ", ".join(c.name for c in ix.columns)
        return f"{kind} {ix.table.name}.{ix.name} ({cols}){' unique' if ix.unique else ''}"
    if kind in ("add_fk", "remove_fk"):
        fk = op[1]
        cols = ", ".join(e.parent.name for e in fk.elements)
        refs = ", ".join(e.column.name for e in fk.elements)
        return f"{kind} {fk.parent.name}({cols}) -> {fk.referred_table.name}({refs})"
    if kind in ("add_constraint", "remove_constraint"):
        c = op[1]
        return f"{kind} {c.table.name}.{c.name} ({', '.join(col.name for col in c.columns)})"
    if kind in ("add_table", "remove_table"):
        return f"{kind} {op[1].name}"
    if kind in ("add_column", "remove_column"):
        return f"{kind} {op[2]}.{op[3].name}"
    if kind.startswith("modify_"):
        return f"{kind} {op[2]}.{op[3]}: {op[5]!r} -> {op[6]!r}"
    return repr(op)


def _drift(url: str) -> list[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        engine.dispose()
    return sorted(_describe(op) for d in diffs for op in (d if isinstance(d, list) else [d]))


def test_models_and_migrations_differ_only_as_known(monkeypatch):
    name = _create_db()
    try:
        _alembic(name, monkeypatch, "head")
        drift = _drift(_url(name))
    finally:
        _drop_db(name)
    if os.environ.get("UPDATE_SCHEMA_DRIFT_BASELINE") == "1":
        BASELINE.write_text(HEADER + "".join(f"{line}\n" for line in drift))
    known = [line for line in BASELINE.read_text().splitlines() if line and not line.startswith("#")]
    new = sorted(set(drift) - set(known))
    gone = sorted(set(known) - set(drift))
    assert not new and not gone, (
        "Models and migrations no longer match the known list.\n"
        + "".join(f"  new:  {line}\n" for line in new)
        + "".join(f"  gone: {line}\n" for line in gone)
        + "A new line usually means a model changed without a migration. If the change is intended, "
        "rerun with UPDATE_SCHEMA_DRIFT_BASELINE=1 and commit tests/ops/schema_drift_baseline.txt."
    )
