import os

# Settings are built once, at import, and environment variables beat backend/.env. A developer's
# .env may turn voice calls on for manual testing; app startup would then launch the stale-call
# sweeper inside every test. Tests that need calls turn them on themselves (calls_on fixture).
os.environ["CHAT_CALLS_ENABLED"] = "false"
# A developer's .env may hold real mail credentials; a test must never send a real email.
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_USER"] = ""
os.environ["SMTP_PASSWORD"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.main import app  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from tests.chat.safety import disposable_pg_url  # noqa: E402

# In-memory SQLite by default. CRM_PG_TEST_URL runs the tests on a throwaway local PostgreSQL
# instead, because SQLite hides real problems such as column length limits and foreign keys.
PG_TEST_URL = os.environ.get("CRM_PG_TEST_URL", "")
if PG_TEST_URL:
    if not disposable_pg_url(PG_TEST_URL):
        raise RuntimeError("CRM_PG_TEST_URL must be a local PostgreSQL database whose name ends in _test")
    engine = create_engine(PG_TEST_URL.replace("postgresql://", "postgresql+psycopg://", 1))
else:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session")
def db_engine():
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _call_code_uses_test_db(monkeypatch):
    """Call code opens its own sessions (call_live.SessionFactory, SessionLocal by default); in
    tests they must hit the in-memory test database, never the developer's DATABASE_URL."""
    from app.services.chat import call_live

    monkeypatch.setattr(call_live, "SessionFactory", TestingSessionLocal)


@pytest.fixture(scope="function")
def db(db_engine):
    session = TestingSessionLocal()
    yield session
    session.rollback()
    session.close()
    # Clean up data after each test for isolation
    with db_engine.connect() as conn:
        if PG_TEST_URL:
            tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        else:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(table.delete())
        conn.commit()


@pytest.fixture(scope="function")
def client(db):
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
