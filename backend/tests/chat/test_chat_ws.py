import json
import threading
import time
from collections import Counter

import pytest
from starlette.websockets import WebSocketDisconnect

from app.config import settings
from app.routers.chat import ws as ws_module
from app.services.chat import service
from app.services.chat.realtime import hub
from tests.chat.conftest import ORIGIN, As, change_user

WS = "/api/chat/ws"


def _ticket(caller: As) -> str:
    res = caller.post("/api/chat/ws-ticket")
    assert res.status_code == 200, res.text
    return res.json()["ticket"]


def _connect(caller: As, ticket: str | None = None, origin: str = ORIGIN):
    headers = {"origin": origin} if origin else {}
    ws = caller.client.websocket_connect(WS, headers=headers)
    sock = ws.__enter__()
    sock.send_text(json.dumps({"type": "auth", "ticket": ticket if ticket is not None else _ticket(caller)}))
    return ws, sock


def _close_code(sock) -> int:
    with pytest.raises(WebSocketDisconnect) as exc:
        while True:
            sock.receive_text()
    return exc.value.code


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _queued(user_id: int) -> list[dict]:
    out = []
    for conn in hub._local.get(user_id, ()):
        out.extend(json.loads(p) for p in list(conn.queue._queue))
    return out


def test_disabled_chat_rejects_socket(org, monkeypatch):
    monkeypatch.setattr(settings, "CHAT_ENABLED", False)
    sales = As("sales@a.test")
    with pytest.raises(WebSocketDisconnect) as exc:
        with sales.client.websocket_connect(WS, headers={"origin": ORIGIN}):
            pass
    assert exc.value.code == ws_module.CLOSE_UNAVAILABLE


@pytest.mark.parametrize("origin", ["https://evil.example", "", "https://crm.chat-test.invalid.evil.example", "http://crm.chat-test.invalid"])
def test_wrong_or_missing_origin_rejected_before_accept(org, origin):
    sales = As("sales@a.test")
    headers = {"origin": origin} if origin else {}
    with pytest.raises(WebSocketDisconnect) as exc:
        with sales.client.websocket_connect(WS, headers=headers):
            pass
    assert exc.value.code == ws_module.CLOSE_POLICY


def test_invalid_ticket_rejected(org):
    sales = As("sales@a.test")
    ws, sock = _connect(sales, ticket="x" * 43)
    assert _close_code(sock) == ws_module.CLOSE_UNAUTHORIZED
    ws.__exit__(None, None, None)


def test_non_auth_first_frame_rejected(org):
    sales = As("sales@a.test")
    with sales.client.websocket_connect(WS, headers={"origin": ORIGIN}) as sock:
        sock.send_text(json.dumps({"type": "typing", "conversation_id": 1}))
        assert _close_code(sock) == ws_module.CLOSE_UNAUTHORIZED


def test_auth_timeout(org, monkeypatch):
    monkeypatch.setattr(ws_module, "AUTH_TIMEOUT", 0.2)
    sales = As("sales@a.test")
    with sales.client.websocket_connect(WS, headers={"origin": ORIGIN}) as sock:
        assert _close_code(sock) == ws_module.CLOSE_AUTH_TIMEOUT


def test_ticket_is_single_use(org):
    sales = As("sales@a.test")
    ticket = _ticket(sales)
    ws1, sock1 = _connect(sales, ticket=ticket)
    assert json.loads(sock1.receive_text())["type"] == "ready"
    ws2, sock2 = _connect(sales, ticket=ticket)
    assert _close_code(sock2) == ws_module.CLOSE_UNAUTHORIZED
    ws2.__exit__(None, None, None)
    ws1.__exit__(None, None, None)


def test_expired_ticket_rejected(org):
    sales = As("sales@a.test")
    ticket = _ticket(sales)
    for key, (_, data) in list(hub._mem_tickets.items()):
        hub._mem_tickets[key] = (time.monotonic() - 1, data)
    ws, sock = _connect(sales, ticket=ticket)
    assert _close_code(sock) == ws_module.CLOSE_UNAUTHORIZED
    ws.__exit__(None, None, None)


def test_logout_after_ticket_issue_rejects_socket(org):
    sales = As("sales@a.test")
    ticket = _ticket(sales)
    assert sales.post("/api/auth/logout").status_code in (200, 204)
    ws, sock = _connect(sales, ticket=ticket)
    assert _close_code(sock) == ws_module.CLOSE_FORBIDDEN
    ws.__exit__(None, None, None)


def test_ticket_requires_memory_mode_or_redis_in_production(org, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    sales = As("sales@a.test")
    assert sales.post("/api/chat/ws-ticket").status_code == 503


def test_events_reach_participants_only_and_carry_no_text(org):
    sales, md, outsider = As("sales@a.test"), As("md@a.test"), As("manager@a.test")
    conv_id = sales.start(org["users"]["md"].id).json()["id"]
    sockets = []
    for caller in (md, outsider):
        ws, sock = _connect(caller)
        assert json.loads(sock.receive_text())["type"] == "ready"
        sockets.append((ws, sock))
    md_sock = sockets[0][1]

    assert sales.send(conv_id, "secret text").status_code == 201
    event = json.loads(md_sock.receive_text())
    assert event == {"type": "message.new", "conversation_id": conv_id, "seq": 1, "sender_id": org["users"]["sales"].id}
    assert "secret text" not in json.dumps(event)

    time.sleep(0.2)
    assert _queued(org["users"]["manager"].id) == []
    for ws, _ in sockets:
        ws.__exit__(None, None, None)


def test_typing_only_for_own_conversations(org):
    sales, md, outsider = As("sales@a.test"), As("md@a.test"), As("manager@a.test")
    conv_id = sales.start(org["users"]["md"].id).json()["id"]
    md_ws, md_sock = _connect(md)
    assert json.loads(md_sock.receive_text())["type"] == "ready"
    out_ws, out_sock = _connect(outsider)
    assert json.loads(out_sock.receive_text())["type"] == "ready"

    out_sock.send_text(json.dumps({"type": "typing", "conversation_id": conv_id}))
    time.sleep(0.3)
    assert _queued(org["users"]["md"].id) == []

    sales_ws, sales_sock = _connect(sales)
    assert json.loads(sales_sock.receive_text())["type"] == "ready"
    sales_sock.send_text(json.dumps({"type": "typing", "conversation_id": conv_id}))
    event = json.loads(md_sock.receive_text())
    assert event == {"type": "typing", "conversation_id": conv_id, "user_id": org["users"]["sales"].id}
    for ws in (md_ws, out_ws, sales_ws):
        ws.__exit__(None, None, None)


def test_oversized_frame_closes_socket(org):
    sales = As("sales@a.test")
    ws, sock = _connect(sales)
    assert json.loads(sock.receive_text())["type"] == "ready"
    sock.send_text("x" * (ws_module.MAX_FRAME_CHARS + 1))
    assert _close_code(sock) == ws_module.CLOSE_TOO_BIG
    ws.__exit__(None, None, None)


def test_deactivated_user_socket_closed_on_recheck(org, monkeypatch):
    monkeypatch.setattr(ws_module, "PING_SECONDS", 0.1)
    monkeypatch.setattr(ws_module, "RECHECK_SECONDS", 0.1)
    sales = As("sales@a.test")
    ws, sock = _connect(sales)
    assert json.loads(sock.receive_text())["type"] == "ready"
    change_user(org["users"]["sales"].id, is_active=False)
    assert _close_code(sock) == ws_module.CLOSE_FORBIDDEN
    ws.__exit__(None, None, None)


def test_socket_never_uses_one_session_from_two_threads(org, monkeypatch):
    """The receiver and the watchdog reach the database at the same moment. On PostgreSQL a shared
    session raises there, which closes the socket and ends any call it is in."""
    monkeypatch.setattr(ws_module, "PING_SECONDS", 0.01)
    monkeypatch.setattr(ws_module, "RECHECK_SECONDS", 0)
    real_check = service.account_is_usable
    guard = threading.Lock()
    in_use, overlaps, seen = set(), [], Counter()

    def watched_check(db, user_id, token_version=None):
        with guard:
            if overlaps or id(db) in in_use:
                overlaps.append(id(db))
                return None
            in_use.add(id(db))
            seen["recheck" if token_version is not None else "typing"] += 1
        time.sleep(0.01)
        try:
            return real_check(db, user_id, token_version)
        finally:
            with guard:
                in_use.discard(id(db))

    monkeypatch.setattr(service, "account_is_usable", watched_check)
    sales = As("sales@a.test")
    ws, sock = _connect(sales)
    assert json.loads(sock.receive_text())["type"] == "ready"
    for conversation_id in range(1, 31):
        sock.send_text(json.dumps({"type": "typing", "conversation_id": conversation_id}))
    finished = _wait_for(lambda: overlaps or (seen["typing"] == 30 and seen["recheck"] >= 3), timeout=10)
    assert not overlaps
    assert finished, seen
    # The server closes it, so nothing is still on the shared test database when the test ends.
    change_user(org["users"]["sales"].id, is_active=False)
    assert _close_code(sock) == ws_module.CLOSE_FORBIDDEN
    ws.__exit__(None, None, None)


def test_socket_closes_at_token_expiry(org, monkeypatch):
    monkeypatch.setattr(ws_module, "PING_SECONDS", 0.1)
    sales = As("sales@a.test")
    import anyio

    ticket = anyio.run(hub.issue_ticket, {
        "user_id": org["users"]["sales"].id,
        "company_id": org["a"].id,
        "token_version": 0,
        "exp": int(time.time()) + 2,
    })
    ws, sock = _connect(sales, ticket=ticket)
    assert json.loads(sock.receive_text())["type"] == "ready"
    assert _close_code(sock) == ws_module.CLOSE_UNAUTHORIZED
    ws.__exit__(None, None, None)


def test_read_receipt_event(org):
    sales, md = As("sales@a.test"), As("md@a.test")
    conv_id = sales.start(org["users"]["md"].id).json()["id"]
    sales.send(conv_id, "ping")
    ws, sock = _connect(sales)
    assert json.loads(sock.receive_text())["type"] == "ready"
    md.post(f"/api/chat/conversations/{conv_id}/read", json={"seq": 1})
    event = json.loads(sock.receive_text())
    assert event == {"type": "conversation.read", "conversation_id": conv_id, "user_id": org["users"]["md"].id, "last_read_seq": 1}
    ws.__exit__(None, None, None)
