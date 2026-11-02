"""PostgreSQL-only chat checks (concurrency, RLS). SQLite can't exercise these.

Runs only when CHAT_PG_TEST_URL points at a migrated, local database named *_test, e.g.
  CHAT_PG_TEST_URL=postgresql://suraj@localhost:5432/crm_chat_test
"""
import asyncio
import os
import secrets
import threading
import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import sessionmaker
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.models.core.chat import ChatConversation, ChatMessage, ChatParticipant
from app.models.core.company import Company
from app.models.core.user import User
from app.services.chat import service
from app.tenancy import bind_tenant, current_company_id, reset_tenant
from tests.chat.safety import disposable_pg_url

PG_URL = os.environ.get("CHAT_PG_TEST_URL", "")

pytestmark = pytest.mark.skipif(
    not disposable_pg_url(PG_URL),
    reason="CHAT_PG_TEST_URL not set to a local PostgreSQL *_test database",
)


def _url(raw: str) -> str:
    return raw.replace("postgresql://", "postgresql+psycopg://", 1) if raw.startswith("postgresql://") else raw


@pytest.fixture
def pg(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    engine = create_engine(_url(PG_URL), pool_size=12, max_overflow=4)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    tag = secrets.token_hex(4)
    with Session() as db:
        companies = [Company(name=f"PG {tag} {n}", company_code=f"{tag[:2]}{n}".upper(), status="active") for n in "AB"]
        db.add_all(companies)
        db.flush()
        users = [
            User(email=f"{n}-{tag}@pg.test", full_name=f"{n} {tag}", hashed_password="x",
                 role="sales", company_id=companies[0 if n != "z" else 1].id, status="active", is_active=True)
            for n in ("x", "y", "z")
        ]
        db.add_all(users)
        db.commit()
        ids = {"companies": [c.id for c in companies], "users": [u.id for u in users]}
    yield engine, Session, ids
    with Session() as db:
        conv_ids = [r[0] for r in db.execute(text(
            "SELECT id FROM chat_conversations WHERE company_id = ANY(:c)"), {"c": ids["companies"]})]
        if conv_ids:
            db.execute(text("DELETE FROM chat_messages WHERE conversation_id = ANY(:c)"), {"c": conv_ids})
            db.execute(text("DELETE FROM chat_participants WHERE conversation_id = ANY(:c)"), {"c": conv_ids})
            db.execute(text("DELETE FROM chat_conversations WHERE id = ANY(:c)"), {"c": conv_ids})
        db.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": ids["users"]})
        db.execute(text("DELETE FROM companies WHERE id = ANY(:c)"), {"c": ids["companies"]})
        db.commit()
    engine.dispose()


def _user(db, uid):
    return db.query(User).filter(User.id == uid).one()


def _run_parallel(n, fn):
    barrier = threading.Barrier(n)
    errors = []
    results = [None] * n

    def worker(i):
        try:
            barrier.wait()
            results[i] = fn(i)
        except Exception as exc:  # noqa: BLE001 — collected and re-raised below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors
    return results


def test_concurrent_direct_creation_yields_one_conversation(pg):
    _, Session, ids = pg
    x, y, _ = ids["users"]

    def create(_i):
        with Session() as db:
            return service.get_or_create_direct(db, _user(db, x), y)["id"]

    conv_ids = set(_run_parallel(8, create))
    assert len(conv_ids) == 1
    with Session() as db:
        assert db.query(ChatParticipant).filter(ChatParticipant.conversation_id == conv_ids.pop()).count() == 2


def test_concurrent_sends_are_gap_free_and_catch_up_never_misses(pg):
    _, Session, ids = pg
    x, y, _ = ids["users"]
    with Session() as db:
        conv_id = service.get_or_create_direct(db, _user(db, x), y)["id"]

    threads, per_thread = 8, 25
    total = threads * per_thread
    seen: list[int] = []
    done = threading.Event()

    def reader():
        last = 0
        with Session() as db:
            me = _user(db, y)
            while True:
                finished = done.is_set()
                page = service.get_messages(db, me, conv_id, None, last, service.PAGE_MAX)
                db.rollback()
                for m in page["items"]:
                    seen.append(m["seq"])
                    last = m["seq"]
                if finished and not page["items"]:
                    return

    r = threading.Thread(target=reader)
    r.start()

    def sender(i):
        out = []
        with Session() as db:
            me = _user(db, x)
            for j in range(per_thread):
                msg, created = service.send_message(db, me, conv_id, f"t{i}-{j}", str(uuid.uuid4()))
                assert created
                out.append(msg["seq"])
        return out

    seqs = [s for chunk in _run_parallel(threads, sender) for s in chunk]
    done.set()
    r.join(60)
    assert sorted(seqs) == list(range(1, total + 1))
    assert seen == list(range(1, total + 1)), "reader skipped or reordered a committed message"
    with Session() as db:
        assert db.query(ChatConversation.last_seq).filter(ChatConversation.id == conv_id).scalar() == total
        assert db.query(ChatMessage).filter(ChatMessage.conversation_id == conv_id).count() == total


def test_concurrent_retries_with_same_client_msg_id_store_one_message(pg):
    _, Session, ids = pg
    x, y, _ = ids["users"]
    with Session() as db:
        conv_id = service.get_or_create_direct(db, _user(db, x), y)["id"]
    cid = str(uuid.uuid4())

    def send(_i):
        with Session() as db:
            msg, _ = service.send_message(db, _user(db, x), conv_id, "same", cid)
            return msg["seq"]

    assert set(_run_parallel(8, send)) == {1}
    with Session() as db:
        assert db.query(ChatMessage).filter(ChatMessage.conversation_id == conv_id).count() == 1
        assert db.query(ChatConversation.last_seq).filter(ChatConversation.id == conv_id).scalar() == 1


def test_rls_isolates_chat_rows_for_a_role_without_bypass(pg):
    engine, Session, ids = pg
    x, y, z = ids["users"]
    a_id, b_id = ids["companies"]
    with Session() as db:
        conv_id = service.get_or_create_direct(db, _user(db, x), y)["id"]
        service.send_message(db, _user(db, x), conv_id, "company A only", str(uuid.uuid4()))

    role = f"chat_rls_probe_{secrets.token_hex(3)}"
    password = secrets.token_urlsafe(16)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{password}'"))
        conn.execute(text(f"GRANT SELECT ON chat_conversations, chat_participants, chat_messages TO {role}"))
    probe_url = engine.url.set(username=role, password=password)
    probe = create_engine(probe_url)
    try:
        with probe.connect() as conn:
            def visible(company_id):
                conn.execute(text("SELECT set_config('app.bypass_rls', 'off', false)"))
                conn.execute(text("SELECT set_config('app.company_id', :c, false)"), {"c": str(company_id)})
                return conn.execute(text("SELECT count(*) FROM chat_messages")).scalar()

            assert visible(a_id) >= 1
            assert visible(b_id) == 0
            conn.execute(text("SELECT set_config('app.company_id', '', false)"))
            assert conn.execute(text("SELECT count(*) FROM chat_conversations")).scalar() == 0
    finally:
        probe.dispose()
        with engine.begin() as conn:
            conn.execute(text(f"REVOKE ALL ON chat_conversations, chat_participants, chat_messages FROM {role}"))
            conn.execute(text(f"DROP ROLE {role}"))


def test_tenant_context_propagates_into_threadpool_calls():
    async def scenario():
        bind_tenant(company_id=4242)
        try:
            return await run_in_threadpool(current_company_id)
        finally:
            reset_tenant()

    assert asyncio.run(scenario()) == 4242
    assert current_company_id() is None


def test_ws_account_check_releases_the_connection(pg, monkeypatch):
    from app.routers.chat.ws import _load_account
    from app.services.chat import call_live

    engine, Session, ids = pg
    monkeypatch.setattr(call_live, "SessionFactory", Session)
    check = call_live.in_own_session(ids["companies"][0], _load_account, ids["users"][0], 0)
    assert asyncio.run(check) is not None
    assert engine.pool.checkedout() == 0


def test_concurrent_uploads_cannot_exceed_the_storage_quota(pg, tmp_path, monkeypatch):
    """Eight uploads race for a 1 GB quota with 400 MB each: exactly two may fit."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.models.core.chat import ChatAttachment
    from app.services.billing import limits
    from app.services.chat import files

    _, Session, ids = pg
    x, y, _ = ids["users"]
    company_id = ids["companies"][0]
    monkeypatch.setattr(settings, "CHAT_FILES_DIR", str(tmp_path))
    monkeypatch.setattr(limits, "resolve_plan", lambda _db, _cid: SimpleNamespace(max_storage_gb=1))
    monkeypatch.setattr(limits, "storage_used_bytes", lambda db, cid: int(
        db.query(func.coalesce(func.sum(ChatAttachment.size_bytes), 0)).filter(ChatAttachment.company_id == cid).scalar()
    ))
    files.ensure_dirs()
    with Session() as db:
        conv_id = service.get_or_create_direct(db, _user(db, x), y)["id"]
    size = 400 * 1024 * 1024

    def send(i):
        stored_name = files.new_stored_name()
        temp = os.path.join(files.tmp_dir(), stored_name)
        with open(temp, "wb") as fh:
            fh.write(b"%PDF-1.7 test")
        upload = {"temp_path": temp, "stored_name": stored_name, "filename": f"f{i}.pdf",
                  "content_type": "application/pdf", "category": "file", "size": size}
        with Session() as db:
            try:
                service.send_message(db, _user(db, x), conv_id, "", str(uuid.uuid4()), upload)
                return "ok"
            except HTTPException as exc:
                return exc.status_code
            finally:
                files.discard(temp)

    outcomes = _run_parallel(8, send)
    assert sorted(outcomes, key=str) == [402] * 6 + ["ok"] * 2
    with Session() as db:
        stored = db.query(ChatAttachment).filter(ChatAttachment.company_id == company_id).all()
        assert len(stored) == 2
        assert sum(a.size_bytes for a in stored) <= 1024 ** 3
        db.execute(text("DELETE FROM chat_attachments WHERE company_id = :c"), {"c": company_id})
        db.commit()
    on_disk = [n for n in os.listdir(tmp_path) if len(n) == 32]
    assert len(on_disk) == 2


def test_concurrent_call_endings_write_exactly_one_entry(pg):
    """Hang-up from both sides, a timeout and a failure report racing: one final status, one chat entry."""
    from app.models.core.chat import ChatCall
    from app.services.chat import calls

    _, Session, ids = pg
    x, y, _ = ids["users"]
    with Session() as db:
        conv_id = service.get_or_create_direct(db, _user(db, x), y)["id"]
        call = calls.create_call(db, _user(db, x), conv_id)
        call_id, company_id = call.id, call.company_id
        assert calls.mark_active(db, call_id, company_id)

    statuses = ["completed", "completed", "failed", "missed"] * 2

    def end(i):
        with Session() as db:
            return calls.finish_call(db, call_id, company_id, statuses[i]) is not None

    assert sum(_run_parallel(len(statuses), end)) == 1
    with Session() as db:
        row = db.query(ChatCall).filter(ChatCall.id == call_id).one()
        assert row.status in statuses and row.message_id is not None
        assert db.query(ChatMessage).filter(ChatMessage.conversation_id == conv_id).count() == 1
        db.execute(text("DELETE FROM chat_calls WHERE id = :i"), {"i": call_id})
        db.commit()


def test_answer_cancel_and_timeout_racing_end_consistently(pg):
    """Answer, cancel and ring timeout fired at the same moment, many times: every call ends once,
    with a status that matches whether it was answered, and exactly one history entry."""
    from app.models.core.chat import ChatCall
    from app.services.chat import calls

    _, Session, ids = pg
    x, y, _ = ids["users"]
    with Session() as db:
        conv_id = service.get_or_create_direct(db, _user(db, x), y)["id"]

    outcomes = []
    for _round in range(12):
        with Session() as db:
            call = calls.create_call(db, _user(db, x), conv_id)
            call_id, company_id = call.id, call.company_id

        def act(i):
            with Session() as db:
                if i == 0:
                    return calls.mark_active(db, call_id, company_id)
                if i == 1:
                    return calls.end_call(db, call_id, company_id, x, "cancel")
                return calls.end_call(db, call_id, company_id, None, "timeout")

        _run_parallel(3, act)
        with Session() as db:
            row = db.query(ChatCall).filter(ChatCall.id == call_id).one()
            if row.status == "active":  # answered first; the cancel/timeout then saw an active call
                calls.end_call(db, call_id, company_id, x, "hangup")
                db.expire_all()
                row = db.query(ChatCall).filter(ChatCall.id == call_id).one()
            answered = row.answered_at is not None
            assert row.status in (("completed",) if answered else ("cancelled", "missed")), (row.status, answered)
            assert row.message_id is not None
            outcomes.append(row.status)
    with Session() as db:
        assert db.query(ChatMessage).filter(ChatMessage.conversation_id == conv_id).count() == len(outcomes)
        db.execute(text("DELETE FROM chat_calls WHERE conversation_id = :c"), {"c": conv_id})
        db.commit()
