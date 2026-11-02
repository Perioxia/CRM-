import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.core.user import User
from app.services.chat import call_live
from app.services.chat.realtime import hub
from app.utils.rate_limit import auth_limiter
from tests.helpers.auth import create_active_user
from tests.helpers.factories import create_company

ORIGIN = "https://crm.chat-test.invalid"


def _reset_hub() -> None:
    hub._local.clear()
    hub._mem_tickets.clear()
    hub._mem_presence.clear()
    hub._mem_rate.clear()
    hub._mem_slots.clear()
    hub._mem_totals.clear()
    hub._mem_kv.clear()


@pytest.fixture(autouse=True)
def _call_db_work_takes_turns(monkeypatch):
    """Call and socket code open their own sessions on the in-memory test database (tests/conftest.py).

    That database is one SQLite connection shared by every thread (StaticPool), which crashes
    when two threads use it at once — e.g. both sides hanging up at the same moment. In
    production each session has its own Postgres connection and the call row lock decides the
    winner (covered by tests/chat/test_chat_postgres.py). Here that database work takes turns,
    like a database serializing two writers on the same row.
    """
    import threading

    real_run = call_live._run
    one_at_a_time = threading.Lock()

    def serialized_run(company_id, fn, *args):
        with one_at_a_time:
            return real_run(company_id, fn, *args)

    monkeypatch.setattr(call_live, "_run", serialized_run)


@pytest.fixture
def chat_on(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "CHAT_WS_ALLOWED_ORIGINS", ORIGIN)
    auth_limiter._buckets.clear()
    _reset_hub()
    yield
    _reset_hub()


def token_for(client: TestClient, email: str, password: str = "pw") -> str:
    res = client.post(
        "/api/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def change_user(user_id: int, **fields) -> None:
    """Change a user while sockets are open, taking turns with their database work."""
    def _apply(session):
        session.query(User).filter(User.id == user_id).update(fields)
        session.commit()

    call_live._run(None, _apply)


class As:
    """A logged-in caller; each instance is its own TestClient (own cookies/headers)."""

    def __init__(self, email: str):
        self.client = TestClient(app)
        self.token = token_for(self.client, email)
        self.client.cookies.clear()
        self.client.headers["Authorization"] = f"Bearer {self.token}"

    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def post(self, url, **kw):
        return self.client.post(url, **kw)

    def send(self, conversation_id: int, body: str, client_msg_id: str | None = None):
        return self.post(
            f"/api/chat/conversations/{conversation_id}/messages",
            json={"body": body, "client_msg_id": client_msg_id or str(uuid.uuid4())},
        )

    def start(self, user_id: int):
        return self.post("/api/chat/conversations/direct", json={"user_id": user_id})


@pytest.fixture
def org(db, client, chat_on):
    """Two companies. A has one user per role plus a platform admin; B has an admin."""
    a = create_company(db, name="Chat A", company_code="CHA")
    b = create_company(db, name="Chat B", company_code="CHB")
    users = {}
    for role in ("sales", "manager", "md", "admin", "purchase"):
        users[role] = create_active_user(db, email=f"{role}@a.test", role=role, company_id=a.id)
    users["sales2"] = create_active_user(db, email="sales2@a.test", role="sales", company_id=a.id)
    users["b_admin"] = create_active_user(db, email="admin@b.test", role="admin", company_id=b.id)
    users["platform"] = create_active_user(db, email="platform@x.test", role="admin", company_id=None)
    return {"a": a, "b": b, "users": users}
