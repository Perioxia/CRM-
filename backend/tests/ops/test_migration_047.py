"""Migration 047 on a database that already holds orders, as production does.

Existing order lines keep every value and get an empty stock link; a line's link is cleared,
not left dangling, when its stock item is deleted; going back down removes only the new column.
Runs only when CHAT_PG_TEST_URL points at a local *_test database (CI does).
"""
import pytest
from sqlalchemy import create_engine, inspect, text

from tests.chat.safety import disposable_pg_url
from tests.chat.test_chat_migration_postgres import PG_URL, _alembic, _create_db, _drop_db, _url
from tests.ops.test_migration_046 import _insert, _one

BEFORE = "046_schema_drift_cleanup"
AFTER = "047_invoice_item_stock"

pytestmark = pytest.mark.skipif(
    not disposable_pg_url(PG_URL),
    reason="CHAT_PG_TEST_URL not set to a local PostgreSQL *_test database",
)


@pytest.fixture(scope="module")
def db_at_046():
    name = _create_db()
    mp = pytest.MonkeyPatch()
    try:
        _alembic(name, mp, BEFORE)
    finally:
        mp.undo()
    yield name
    _drop_db(name)


@pytest.fixture
def at_046(db_at_046):
    name = _create_db(template=db_at_046)
    engine = create_engine(_url(name))
    yield name, engine
    engine.dispose()
    _drop_db(name)


def _columns(engine) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns("invoice_items")}


def _order_line(engine):
    company = _insert(engine, "companies", name="Co")
    customer = _insert(engine, "clients", company_id=company, name="Customer")
    invoice = _insert(engine, "invoices", company_id=company, client_id=customer, invoice_number="DRAFT-1")
    line = _insert(engine, "invoice_items", company_id=company, invoice_id=invoice, description="Fan", quantity=3)
    stock = _insert(engine, "stock_items", company_id=company, name="Fan", quantity=10)
    return company, line, stock


def test_existing_lines_keep_their_values_and_get_an_empty_link(at_046, monkeypatch):
    name, engine = at_046
    _, line, _ = _order_line(engine)
    before = tuple(_one(engine, "SELECT description, quantity, invoice_id FROM invoice_items WHERE id = :i", i=line))

    _alembic(name, monkeypatch, AFTER)

    assert "stock_item_id" in _columns(engine)
    assert tuple(_one(engine, "SELECT description, quantity, invoice_id FROM invoice_items WHERE id = :i", i=line)) == before
    assert _one(engine, "SELECT stock_item_id FROM invoice_items WHERE id = :i", i=line)[0] is None


def test_deleting_a_stock_item_clears_the_link(at_046, monkeypatch):
    name, engine = at_046
    _, line, stock = _order_line(engine)
    _alembic(name, monkeypatch, AFTER)

    with engine.begin() as conn:
        conn.execute(text("UPDATE invoice_items SET stock_item_id = :s WHERE id = :i"), {"s": stock, "i": line})
        conn.execute(text("DELETE FROM stock_items WHERE id = :s"), {"s": stock})
    assert _one(engine, "SELECT stock_item_id FROM invoice_items WHERE id = :i", i=line)[0] is None
    on_delete = _one(engine, "SELECT confdeltype FROM pg_constraint WHERE conname = 'invoice_items_stock_item_id_fkey'")[0]
    assert on_delete == "n"


def test_down_removes_only_the_new_column_and_up_works_again(at_046, monkeypatch):
    name, engine = at_046
    _, line, _ = _order_line(engine)
    _alembic(name, monkeypatch, AFTER)
    _alembic(name, monkeypatch, BEFORE, downgrade=True)

    assert "stock_item_id" not in _columns(engine)
    assert _one(engine, "SELECT quantity FROM invoice_items WHERE id = :i", i=line)[0] == 3

    _alembic(name, monkeypatch, AFTER)
    assert "stock_item_id" in _columns(engine)
