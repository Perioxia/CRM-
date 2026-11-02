"""Voice-call signaling over the chat WebSocket: ringing, answering, routing, ending, cleanup."""
import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import anyio
import httpx
import pytest
from starlette.websockets import WebSocketDisconnect

from app.config import settings
from app.models.core.chat import ChatCall, ChatMessage
from app.routers.chat import rest as rest_module
from app.services.chat import call_live
from app.services.chat.realtime import hub
from tests.chat.conftest import ORIGIN, As, change_user

WS = "/api/chat/ws"
FAKE_SDP = "v=0 o=- 4611731400430051336 2 IN IP4 127.0.0.1 PRIVATE-SDP-MARKER"


@pytest.fixture
def calls_on(org, monkeypatch):
    monkeypatch.setattr(settings, "CHAT_CALLS_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_WS_PUBLIC_URL", "wss://api.chat-test.invalid/api/chat/ws")
    monkeypatch.setattr(settings, "CHAT_TURN_KEY_ID", "")
    monkeypatch.setattr(settings, "CHAT_TURN_API_TOKEN", "")
    return org


class Tab:
    """One browser tab: a logged-in caller plus an open chat socket."""

    def __init__(self, caller: As, ticket: str | None = None):
        self.caller = caller
        ticket = ticket or caller.post("/api/chat/ws-ticket").json()["ticket"]
        self._ctx = caller.client.websocket_connect(WS, headers={"origin": ORIGIN})
        self.sock = self._ctx.__enter__()
        self.sock.send_text(json.dumps({"type": "auth", "ticket": ticket}))
        ready = self.recv()
        assert ready["type"] == "ready"
        self.conn_id = ready["conn_id"]

    def recv(self, timeout: float = 3.0) -> dict:
        async def _receive():
            with anyio.fail_after(timeout):
                return await self.sock._send_rx.receive()

        message = self.sock.portal.call(_receive)
        if message["type"] == "websocket.close":
            raise WebSocketDisconnect(message.get("code", 1000))
        return json.loads(message["text"])

    def next(self, kind: str, timeout: float = 3.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            frame = self.recv(max(0.05, deadline - time.monotonic()))
            if frame["type"] == kind:
                return frame

    def drain(self, wait: float = 0.3) -> list[dict]:
        frames = []
        while True:
            try:
                frames.append(self.recv(wait))
            except TimeoutError:
                return frames

    def send(self, **frame) -> None:
        self.sock.send_text(json.dumps(frame))

    def close(self) -> None:
        self._ctx.__exit__(None, None, None)


def _conv(org) -> int:
    return As("sales@a.test").start(org["users"]["md"].id).json()["id"]


def _call_row(db, call_id) -> ChatCall:
    db.expire_all()
    return db.query(ChatCall).filter(ChatCall.id == call_id).one()


def _ring(caller: Tab, callee_tabs, conv_id: int) -> int:
    caller.send(type="call.invite", conversation_id=conv_id, request_id="r1")
    out = caller.next("call.outgoing")
    for tab in callee_tabs:
        incoming = tab.next("call.incoming")
        assert incoming["call_id"] == out["call_id"]
    return out["call_id"]


def _answer(caller: Tab, callee: Tab, call_id: int) -> None:
    callee.send(type="call.accept", call_id=call_id)
    assert caller.next("call.answered")["call_id"] == call_id
    assert callee.next("call.answered")["call_id"] == call_id


# ---- switched off --------------------------------------------------------------------


def test_calls_off_by_default(org):
    sales = As("sales@a.test")
    assert sales.get("/api/chat/status").json()["calls"]["enabled"] is False
    assert sales.get("/api/chat/calls/ice-servers").status_code == 404
    tab = Tab(sales)
    tab.send(type="call.invite", conversation_id=_conv(org), request_id="x")
    assert tab.next("call.error") == {"type": "call.error", "request_id": "x", "code": "unavailable"}
    tab.close()


def test_platform_admin_has_no_calls(calls_on):
    assert As("platform@x.test").get("/api/chat/calls/ice-servers").status_code == 403


# ---- ringing ----------------------------------------------------------------------------


def test_invite_rings_only_the_callee_on_every_tab(calls_on, db):
    conv_id = _conv(calls_on)
    caller, callee1, callee2, outsider = Tab(As("sales@a.test")), Tab(As("md@a.test")), Tab(As("md@a.test")), Tab(As("manager@a.test"))
    caller.send(type="call.invite", conversation_id=conv_id, request_id="req-1")
    out = caller.next("call.outgoing")
    assert out["request_id"] == "req-1" and out["conversation_id"] == conv_id
    assert out["callee"] == {"id": calls_on["users"]["md"].id, "full_name": calls_on["users"]["md"].full_name}
    for tab in (callee1, callee2):
        incoming = tab.next("call.incoming")
        assert incoming == {
            "type": "call.incoming",
            "call_id": out["call_id"],
            "conversation_id": conv_id,
            "caller": {"id": calls_on["users"]["sales"].id, "full_name": calls_on["users"]["sales"].full_name},
        }
    assert not [f for f in outsider.drain() if f["type"].startswith("call.")]
    assert _call_row(db, out["call_id"]).status == "ringing"
    for t in (caller, callee1, callee2, outsider):
        t.close()


@pytest.mark.parametrize("email,code", [("manager@a.test", "not_found"), ("admin@b.test", "not_found")])
def test_only_participants_can_ring(calls_on, db, email, code):
    conv_id = _conv(calls_on)
    md = Tab(As("md@a.test"))
    outsider = Tab(As(email))
    outsider.send(type="call.invite", conversation_id=conv_id, request_id="x")
    assert outsider.next("call.error")["code"] == code
    assert not [f for f in md.drain() if f["type"] == "call.incoming"]
    assert db.query(ChatCall).count() == 0
    md.close()
    outsider.close()


def test_cannot_ring_a_deactivated_user(calls_on, db):
    conv_id = _conv(calls_on)
    md_user = db.query(type(calls_on["users"]["md"])).filter_by(id=calls_on["users"]["md"].id).one()
    md_user.is_active = False
    db.commit()
    caller = Tab(As("sales@a.test"))
    caller.send(type="call.invite", conversation_id=conv_id, request_id="x")
    assert caller.next("call.error")["code"] == "deactivated"
    caller.close()


def test_offline_callee_is_unavailable_and_gets_a_missed_entry(calls_on, db):
    conv_id = _conv(calls_on)
    caller = Tab(As("sales@a.test"))
    caller.send(type="call.invite", conversation_id=conv_id, request_id="x")
    out = caller.next("call.outgoing")
    assert caller.next("call.ended") == {"type": "call.ended", "call_id": out["call_id"], "status": "unavailable"}
    assert _call_row(db, out["call_id"]).status == "unavailable"
    assert As("md@a.test").get("/api/chat/unread").json()["unread_total"] == 1
    caller.close()


def test_busy_callee(calls_on, db):
    conv_id = _conv(calls_on)
    sales2 = As("sales2@a.test")
    conv2 = sales2.start(calls_on["users"]["md"].id).json()["id"]
    caller, callee, second_caller = Tab(As("sales@a.test")), Tab(As("md@a.test")), Tab(sales2)
    first = _ring(caller, [callee], conv_id)
    second_caller.send(type="call.invite", conversation_id=conv2, request_id="x")
    out = second_caller.next("call.outgoing")
    assert second_caller.next("call.ended")["status"] == "busy"
    assert _call_row(db, out["call_id"]).status == "busy"
    assert _call_row(db, first).status == "ringing"
    for t in (caller, callee, second_caller):
        t.close()


def test_caller_already_in_a_call_cannot_ring_again(calls_on):
    conv_id = _conv(calls_on)
    caller, callee = Tab(As("sales@a.test")), Tab(As("md@a.test"))
    _ring(caller, [callee], conv_id)
    caller.send(type="call.invite", conversation_id=conv_id, request_id="again")
    assert caller.next("call.error") == {"type": "call.error", "request_id": "again", "code": "busy_self"}
    caller.close()
    callee.close()


def test_invites_are_rate_limited(calls_on, monkeypatch):
    from app.routers.chat import ws as ws_module

    monkeypatch.setattr(ws_module, "CALL_INVITES_PER_MINUTE", 2)
    conv_id = _conv(calls_on)
    caller, callee = Tab(As("sales@a.test")), Tab(As("md@a.test"))
    codes = []
    for i in range(3):
        caller.send(type="call.invite", conversation_id=conv_id, request_id=str(i))
        frame = caller.next("call.outgoing") if i == 0 else caller.next("call.error")
        codes.append(frame.get("code", "ok"))
    assert codes == ["ok", "busy_self", "rate_limited"]
    caller.close()
    callee.close()


# ---- answering + routing -------------------------------------------------------------------


def test_first_tab_to_answer_wins(calls_on, db):
    conv_id = _conv(calls_on)
    caller, tab1, tab2 = Tab(As("sales@a.test")), Tab(As("md@a.test")), Tab(As("md@a.test"))
    call_id = _ring(caller, [tab1, tab2], conv_id)
    _answer(caller, tab1, call_id)
    assert tab2.next("call.taken")["call_id"] == call_id
    tab2.send(type="call.accept", call_id=call_id)
    assert tab2.next("call.taken")["call_id"] == call_id
    row = _call_row(db, call_id)
    assert row.status == "active" and row.answered_at is not None
    for t in (caller, tab1, tab2):
        t.close()


def test_call_setup_reaches_only_the_two_call_sockets_and_is_never_stored(calls_on, db, caplog):
    caplog.set_level(logging.DEBUG)
    conv_id = _conv(calls_on)
    caller, tab1, tab2, outsider = Tab(As("sales@a.test")), Tab(As("md@a.test")), Tab(As("md@a.test")), Tab(As("manager@a.test"))
    call_id = _ring(caller, [tab1, tab2], conv_id)
    _answer(caller, tab1, call_id)
    tab2.drain()

    offer = {"kind": "offer", "sdp": FAKE_SDP}
    caller.send(type="call.signal", call_id=call_id, data=offer)
    assert tab1.next("call.signal") == {"type": "call.signal", "call_id": call_id, "data": offer}
    answer = {"kind": "answer", "sdp": FAKE_SDP}
    tab1.send(type="call.signal", call_id=call_id, data=answer)
    assert caller.next("call.signal")["data"] == answer
    ice = {"kind": "ice", "candidate": {"candidate": "candidate:1 1 udp 2122260223 192.168.1.5 50000 typ host"}}
    tab1.send(type="call.signal", call_id=call_id, data=ice)
    assert caller.next("call.signal")["data"] == ice

    # The other tab and the outsider saw none of it; a non-call socket can't inject signals.
    assert not [f for f in tab2.drain() if f["type"] == "call.signal"]
    assert not [f for f in outsider.drain() if f["type"].startswith("call.")]
    tab2.send(type="call.signal", call_id=call_id, data={"kind": "offer", "sdp": "x"})
    outsider.send(type="call.signal", call_id=call_id, data={"kind": "offer", "sdp": "x"})
    assert not [f for f in caller.drain() if f["type"] == "call.signal"]

    # Nothing about the media reached the database or the logs.
    caller.send(type="call.hangup", call_id=call_id)
    caller.next("call.ended")
    row = _call_row(db, call_id)
    assert FAKE_SDP not in json.dumps({c.name: str(getattr(row, c.name)) for c in ChatCall.__table__.columns})
    assert "PRIVATE-SDP-MARKER" not in caplog.text and "192.168.1.5" not in caplog.text
    for t in (caller, tab1, tab2, outsider):
        t.close()


def test_large_sdp_frame_is_accepted(calls_on):
    conv_id = _conv(calls_on)
    caller, callee = Tab(As("sales@a.test")), Tab(As("md@a.test"))
    call_id = _ring(caller, [callee], conv_id)
    _answer(caller, callee, call_id)
    big = {"kind": "offer", "sdp": "a=candidate " * 1000}
    caller.send(type="call.signal", call_id=call_id, data=big)
    assert callee.next("call.signal")["data"] == big
    caller.close()
    callee.close()


# ---- ending ---------------------------------------------------------------------------------


def _entries(db, conv_id):
    db.expire_all()
    return db.query(ChatMessage).filter(ChatMessage.conversation_id == conv_id).count()


@pytest.mark.parametrize("who,frame,expected", [
    ("callee", "call.decline", "declined"),
    ("caller", "call.cancel", "cancelled"),
    ("callee", "call.hangup", "declined"),
    ("caller", "call.hangup", "cancelled"),
])
def test_ending_while_ringing(calls_on, db, who, frame, expected):
    conv_id = _conv(calls_on)
    caller, callee = Tab(As("sales@a.test")), Tab(As("md@a.test"))
    call_id = _ring(caller, [callee], conv_id)
    (callee if who == "callee" else caller).send(type=frame, call_id=call_id)
    for t in (caller, callee):
        assert t.next("call.ended") == {"type": "call.ended", "call_id": call_id, "status": expected}
        assert t.next("message.new")["conversation_id"] == conv_id
    assert _call_row(db, call_id).status == expected
    assert _entries(db, conv_id) == 1
    caller.close()
    callee.close()


def test_hangup_after_answer_completes_with_duration_and_one_entry(calls_on, db):
    conv_id = _conv(calls_on)
    caller, callee = Tab(As("sales@a.test")), Tab(As("md@a.test"))
    call_id = _ring(caller, [callee], conv_id)
    _answer(caller, callee, call_id)
    callee.send(type="call.hangup", call_id=call_id)
    caller.send(type="call.hangup", call_id=call_id)
    assert caller.next("call.ended")["status"] == "completed"
    time.sleep(0.3)
    row = _call_row(db, call_id)
    assert row.status == "completed" and row.duration_s is not None and row.message_id is not None
    assert _entries(db, conv_id) == 1
    assert hub._mem_kv.get(f"chat:incall:{calls_on['users']['sales'].id}") is None
    assert hub._mem_kv.get(f"chat:incall:{calls_on['users']['md'].id}") is None
    caller.close()
    callee.close()


def test_connection_reports(calls_on, db):
    conv_id = _conv(calls_on)
    caller, callee = Tab(As("sales@a.test")), Tab(As("md@a.test"))
    call_id = _ring(caller, [callee], conv_id)
    _answer(caller, callee, call_id)
    caller.send(type="call.report", call_id=call_id, connection="relay")
    caller.send(type="call.report", call_id=call_id, connection="bogus")
    time.sleep(0.3)
    assert _call_row(db, call_id).connection == "relay"
    callee.send(type="call.report", call_id=call_id, connection="failed")
    assert caller.next("call.ended")["status"] == "failed"
    assert _call_row(db, call_id).status == "failed"
    caller.close()
    callee.close()


def test_unanswered_call_becomes_missed(calls_on, db, monkeypatch):
    monkeypatch.setattr(settings, "CHAT_CALL_RING_SECONDS", 1)
    conv_id = _conv(calls_on)
    caller, callee = Tab(As("sales@a.test")), Tab(As("md@a.test"))
    call_id = _ring(caller, [callee], conv_id)
    assert callee.next("call.ended", timeout=4)["status"] == "missed"
    assert _call_row(db, call_id).status == "missed"
    assert As("md@a.test").get("/api/chat/unread").json()["unread_total"] == 1
    caller.close()
    callee.close()


def test_closing_the_callers_tab_while_ringing_cancels(calls_on, db):
    conv_id = _conv(calls_on)
    caller, callee = Tab(As("sales@a.test")), Tab(As("md@a.test"))
    call_id = _ring(caller, [callee], conv_id)
    caller.close()
    assert callee.next("call.ended")["status"] == "cancelled"
    assert _call_row(db, call_id).status == "cancelled"
    callee.close()


def test_closing_a_tab_during_the_call_ends_it(calls_on, db):
    conv_id = _conv(calls_on)
    caller, callee = Tab(As("sales@a.test")), Tab(As("md@a.test"))
    call_id = _ring(caller, [callee], conv_id)
    _answer(caller, callee, call_id)
    callee.close()
    assert caller.next("call.ended")["status"] == "completed"
    assert _call_row(db, call_id).status == "completed"
    caller.close()


def _ticket_expiring_in(org, who: str, seconds: int) -> str:
    user = org["users"][who]
    return anyio.run(hub.issue_ticket, {
        "user_id": user.id,
        "company_id": user.company_id,
        "token_version": int(user.token_version or 0),
        "exp": int(time.time()) + seconds,
    })


def _frame_types_for(tab: Tab, seconds: float) -> list[str]:
    types, deadline = [], time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            types.append(tab.recv(max(0.05, deadline - time.monotonic()))["type"])
        except TimeoutError:
            pass
    return types


def _expect_close(tab: Tab, code: int, timeout: float = 3.0) -> None:
    with pytest.raises(WebSocketDisconnect) as closed:
        tab.next("never", timeout=timeout)
    assert closed.value.code == code


def test_login_expiring_mid_call_keeps_the_call_until_it_ends(calls_on, db, monkeypatch):
    from app.routers.chat import ws as ws_module

    monkeypatch.setattr(ws_module, "PING_SECONDS", 0.1)
    conv_id = _conv(calls_on)
    caller = Tab(As("sales@a.test"), ticket=_ticket_expiring_in(calls_on, "sales", 2))
    callee = Tab(As("md@a.test"))
    call_id = _ring(caller, [callee], conv_id)
    _answer(caller, callee, call_id)

    assert "call.ended" not in _frame_types_for(callee, 3)
    ice = {"kind": "ice", "candidate": {"candidate": "candidate:1 1 udp 2122260223 192.168.1.5 50000 typ host"}}
    callee.send(type="call.signal", call_id=call_id, data=ice)
    assert caller.next("call.signal")["data"] == ice

    callee.send(type="call.hangup", call_id=call_id)
    assert caller.next("call.ended")["status"] == "completed"
    _expect_close(caller, ws_module.CLOSE_UNAUTHORIZED)
    assert _call_row(db, call_id).status == "completed"
    caller.close()
    callee.close()


def test_logout_still_disconnects_a_call_past_login_expiry(calls_on, monkeypatch):
    from app.routers.chat import ws as ws_module

    monkeypatch.setattr(ws_module, "PING_SECONDS", 0.1)
    monkeypatch.setattr(ws_module, "RECHECK_SECONDS", 0.2)
    conv_id = _conv(calls_on)
    sales, md = As("sales@a.test"), As("md@a.test")
    caller = Tab(sales, ticket=_ticket_expiring_in(calls_on, "sales", 3))
    callee = Tab(md)
    call_id = _ring(caller, [callee], conv_id)
    _answer(caller, callee, call_id)
    assert "call.ended" not in _frame_types_for(callee, 4)

    change_user(calls_on["users"]["sales"].id, token_version=99)
    _expect_close(caller, ws_module.CLOSE_FORBIDDEN)
    assert callee.next("call.ended")["status"] == "completed"
    # The server closes this one too, so nothing is still on the shared test database when the test ends.
    change_user(calls_on["users"]["md"].id, token_version=99)
    _expect_close(callee, ws_module.CLOSE_FORBIDDEN)
    caller.close()
    callee.close()


def test_closing_one_of_the_callees_ringing_tabs_keeps_ringing(calls_on, db):
    conv_id = _conv(calls_on)
    caller, tab1, tab2 = Tab(As("sales@a.test")), Tab(As("md@a.test")), Tab(As("md@a.test"))
    call_id = _ring(caller, [tab1, tab2], conv_id)
    tab1.close()
    time.sleep(0.3)
    assert _call_row(db, call_id).status == "ringing"
    _answer(caller, tab2, call_id)
    caller.close()
    tab2.close()


def test_outsider_cannot_end_someone_elses_call(calls_on, db):
    conv_id = _conv(calls_on)
    caller, callee, outsider = Tab(As("sales@a.test")), Tab(As("md@a.test")), Tab(As("manager@a.test"))
    call_id = _ring(caller, [callee], conv_id)
    outsider.send(type="call.hangup", call_id=call_id)
    outsider.send(type="call.accept", call_id=call_id)
    outsider.next("call.ended")
    time.sleep(0.3)
    assert _call_row(db, call_id).status == "ringing"
    for t in (caller, callee, outsider):
        t.close()


# ---- stale state -------------------------------------------------------------------------------


def test_sweep_closes_calls_whose_live_state_is_gone(calls_on, db):
    conv_id = _conv(calls_on)
    caller, callee = Tab(As("sales@a.test")), Tab(As("md@a.test"))
    live_id = _ring(caller, [callee], conv_id)
    caller.close()
    callee.close()
    from app.services.chat import calls

    stale = calls.create_call(db, db.merge(calls_on["users"]["sales"]), conv_id)
    db.query(ChatCall).filter(ChatCall.id.in_([stale.id, live_id])).update(
        {"started_at": datetime.now(timezone.utc) - timedelta(minutes=10)}, synchronize_session=False
    )
    db.commit()
    assert asyncio.run(call_live.sweep_once()) == 1
    assert _call_row(db, stale.id).status == "interrupted"
    hub._mem_kv.pop("chat:calls:sweep", None)


def test_sweep_tells_both_people_when_it_closes_a_call(calls_on, db):
    """A call whose live state vanished (e.g. a worker restart lost its ring timer) is closed by the
    sweeper, and both participants' open tabs are told, so neither keeps showing it."""
    conv_id = _conv(calls_on)
    caller, callee = Tab(As("sales@a.test")), Tab(As("md@a.test"))
    from app.services.chat import calls

    stale = calls.create_call(db, db.merge(calls_on["users"]["sales"]), conv_id)
    db.query(ChatCall).filter(ChatCall.id == stale.id).update(
        {"started_at": datetime.now(timezone.utc) - timedelta(minutes=10)}, synchronize_session=False
    )
    db.commit()
    assert asyncio.run(call_live.sweep_once()) == 1
    entry_seq = None
    for tab in (caller, callee):
        assert tab.next("call.ended") == {"type": "call.ended", "call_id": stale.id, "status": "interrupted"}
        new = tab.next("message.new")
        assert new["conversation_id"] == conv_id and new["sender_id"] == calls_on["users"]["sales"].id
        assert entry_seq in (None, new["seq"])
        entry_seq = new["seq"]
    assert _call_row(db, stale.id).status == "interrupted"
    assert _entries(db, conv_id) == 1
    hub._mem_kv.pop("chat:calls:sweep", None)
    caller.close()
    callee.close()


def test_busy_markers_expire_on_their_own(calls_on):
    conv_id = _conv(calls_on)
    caller, callee = Tab(As("sales@a.test")), Tab(As("md@a.test"))
    _ring(caller, [callee], conv_id)
    key = f"chat:incall:{calls_on['users']['sales'].id}"
    expires_at, _ = hub._mem_kv[key]
    assert expires_at - time.monotonic() <= settings.CHAT_CALL_RING_SECONDS + call_live.RING_GRACE
    caller.close()
    callee.close()


# ---- ICE servers ----------------------------------------------------------------------------------


def test_ice_servers_stun_only_by_default(calls_on):
    res = As("sales@a.test").get("/api/chat/calls/ice-servers")
    assert res.status_code == 200
    body = res.json()
    assert body["relay"] is False
    assert body["ice_servers"] == [{"urls": ["stun:stun.cloudflare.com:3478", "stun:stun.l.google.com:19302"]}]


def _mock_cloudflare(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(rest_module.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_ice_servers_with_turn_filters_port_53(calls_on, monkeypatch):
    monkeypatch.setattr(settings, "CHAT_TURN_KEY_ID", "key123")
    monkeypatch.setattr(settings, "CHAT_TURN_API_TOKEN", "token-secret")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"iceServers": [
            {"urls": "stun:stun.cloudflare.com:3478"},
            {"urls": ["turn:turn.cloudflare.com:3478?transport=udp", "turn:turn.cloudflare.com:53?transport=udp",
                      "turns:turn.cloudflare.com:443?transport=tcp"],
             "username": "u", "credential": "c"},
        ]})

    _mock_cloudflare(monkeypatch, handler)
    body = As("sales@a.test").get("/api/chat/calls/ice-servers").json()
    assert seen == {
        "url": "https://rtc.live.cloudflare.com/v1/turn/keys/key123/credentials/generate-ice-servers",
        "auth": "Bearer token-secret",
        "body": {"ttl": rest_module.TURN_TTL_SECONDS},
    }
    assert body["relay"] is True
    turn = body["ice_servers"][1]
    assert turn["urls"] == ["turn:turn.cloudflare.com:3478?transport=udp", "turns:turn.cloudflare.com:443?transport=tcp"]
    assert turn["username"] == "u" and turn["credential"] == "c"


def test_ice_servers_fall_back_to_stun_when_turn_fails(calls_on, monkeypatch, caplog):
    monkeypatch.setattr(settings, "CHAT_TURN_KEY_ID", "key123")
    monkeypatch.setattr(settings, "CHAT_TURN_API_TOKEN", "token-secret")
    _mock_cloudflare(monkeypatch, lambda request: httpx.Response(503, json={}))
    body = As("sales@a.test").get("/api/chat/calls/ice-servers").json()
    assert body["relay"] is False and body["ice_servers"][0]["urls"][0].startswith("stun:")
    assert "token-secret" not in caplog.text


# ---- two tabs answering + races ---------------------------------------------------------------


def test_losing_tab_cannot_fail_the_winning_tabs_call(calls_on, db):
    conv_id = _conv(calls_on)
    caller, winner, loser = Tab(As("sales@a.test")), Tab(As("md@a.test")), Tab(As("md@a.test"))
    call_id = _ring(caller, [winner, loser], conv_id)
    _answer(caller, winner, call_id)
    # The losing tab pressed Accept too late; what its connect timeout would send:
    loser.send(type="call.accept", call_id=call_id)
    assert loser.next("call.taken")["call_id"] == call_id
    loser.send(type="call.report", call_id=call_id, connection="failed")
    loser.send(type="call.report", call_id=call_id, connection="relay")
    assert not [f for f in caller.drain(0.5) if f["type"] == "call.ended"]
    assert not [f for f in winner.drain(0.3) if f["type"] == "call.ended"]
    row = _call_row(db, call_id)
    assert row.status == "active" and row.connection == "unknown"
    # The call is still fully usable between the two real sockets.
    caller.send(type="call.signal", call_id=call_id, data={"kind": "ice", "candidate": {"candidate": "x"}})
    assert winner.next("call.signal")["call_id"] == call_id
    # Only a socket in the call can report how it connected or that it failed.
    winner.send(type="call.report", call_id=call_id, connection="direct")
    time.sleep(0.3)
    assert _call_row(db, call_id).connection == "direct"
    winner.send(type="call.report", call_id=call_id, connection="failed")
    assert caller.next("call.ended")["status"] == "failed"
    for t in (caller, winner, loser):
        t.close()


def test_ring_timeout_after_answer_does_nothing(calls_on, db, monkeypatch):
    conv_id = _conv(calls_on)
    caller, callee = Tab(As("sales@a.test")), Tab(As("md@a.test"))
    call_id = _ring(caller, [callee], conv_id)
    _answer(caller, callee, call_id)
    monkeypatch.setattr(settings, "CHAT_CALL_RING_SECONDS", 0)
    asyncio.run(call_live._ring_timeout(call_id))
    assert not [f for f in caller.drain(0.4) if f["type"] == "call.ended"]
    assert _call_row(db, call_id).status == "active"
    assert _entries(db, conv_id) == 0
    caller.close()
    callee.close()


def test_cancel_after_answer_completes(calls_on, db):
    conv_id = _conv(calls_on)
    caller, callee = Tab(As("sales@a.test")), Tab(As("md@a.test"))
    call_id = _ring(caller, [callee], conv_id)
    _answer(caller, callee, call_id)
    caller.send(type="call.cancel", call_id=call_id)
    assert callee.next("call.ended")["status"] == "completed"
    row = _call_row(db, call_id)
    assert row.status == "completed" and row.answered_at is not None
    assert _entries(db, conv_id) == 1
    caller.close()
    callee.close()


def test_answer_after_cancel_is_refused_cleanly(calls_on, db):
    conv_id = _conv(calls_on)
    caller, callee = Tab(As("sales@a.test")), Tab(As("md@a.test"))
    call_id = _ring(caller, [callee], conv_id)
    caller.send(type="call.cancel", call_id=call_id)
    assert caller.next("call.ended")["status"] == "cancelled"
    callee.send(type="call.accept", call_id=call_id)
    frames = [f for f in callee.drain(0.6) if f.get("call_id") == call_id]
    assert {"type": "call.ended", "call_id": call_id, "status": "cancelled"} in frames
    assert not [f for f in frames if f["type"] == "call.answered"]
    row = _call_row(db, call_id)
    assert row.status == "cancelled" and row.answered_at is None
    assert _entries(db, conv_id) == 1
    assert not [k for k in hub._mem_kv if k.startswith("chat:incall:") or k == f"chat:call:{call_id}"]
    caller.close()
    callee.close()


@pytest.mark.parametrize("competitor,expected", [("cancel", "cancelled"), ("timeout", "missed")])
def test_event_landing_in_the_middle_of_an_answer(calls_on, db, monkeypatch, competitor, expected):
    """A cancel or the ring timeout arrives after the answer wrote its live state but before the
    database recorded the answer: the call must end once, consistently, with no live state left."""
    from app.services.chat import calls

    conv_id = _conv(calls_on)
    caller, callee = Tab(As("sales@a.test")), Tab(As("md@a.test"))
    call_id = _ring(caller, [callee], conv_id)
    real_mark_active = calls.mark_active
    company_id = _call_row(db, call_id).company_id
    caller_id = calls_on["users"]["sales"].id

    def racing_mark_active(session, cid, comp):
        if competitor == "cancel":
            calls.end_call(session, cid, comp, caller_id, "cancel")
        else:
            calls.end_call(session, cid, comp, None, "timeout")
        return real_mark_active(session, cid, comp)

    monkeypatch.setattr(calls, "mark_active", racing_mark_active)
    callee.send(type="call.accept", call_id=call_id)
    frames = [f for f in callee.drain(0.8) if f.get("call_id") == call_id]
    assert {"type": "call.ended", "call_id": call_id, "status": expected} in frames
    assert not [f for f in frames if f["type"] == "call.answered"]
    assert not [f for f in caller.drain(0.3) if f["type"] == "call.answered"]
    row = _call_row(db, call_id)
    assert row.status == expected and row.answered_at is None and row.company_id == company_id
    assert _entries(db, conv_id) == 1
    assert not [k for k in hub._mem_kv if k.startswith("chat:incall:") or k.startswith(f"chat:call:{call_id}")]
    caller.close()
    callee.close()
