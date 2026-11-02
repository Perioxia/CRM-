"""Team chat WebSocket: wss://api.perioxia.com/api/chat/ws

Handshake: exact Origin allowlist → accept → first frame must be
{"type": "auth", "ticket": "..."} within AUTH_TIMEOUT. Tickets are single-use
(see realtime.Hub.consume_ticket). The socket closes at the JWT's expiry (or
when a call it is in ends, if later) and re-checks the account every RECHECK_SECONDS.
"""
import asyncio
import json
import logging
import time
from collections import deque

import anyio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import parse_origins, settings
from app.services.chat import call_live, service
from app.services.chat.crypto import chat_crypto_configured
from app.services.chat.realtime import Connection, RealtimeUnavailable, hub

logger = logging.getLogger("app.chat.ws")
router = APIRouter()

AUTH_TIMEOUT = 10.0
# Large enough for a WebRTC SDP offer/answer (call setup); everything else is far smaller.
MAX_FRAME_CHARS = 16384
PING_SECONDS = 25.0
RECHECK_SECONDS = 60.0
TYPING_MIN_INTERVAL = 2.0
PARTICIPANT_CACHE_SECONDS = 60.0
CALL_FRAMES_PER_10S = 120
CALL_INVITES_PER_MINUTE = 10
CONNECTION_REPORTS = ("direct", "relay", "failed")

CLOSE_POLICY = 1008
CLOSE_TOO_BIG = 1009
CLOSE_UNAVAILABLE = 1013
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_AUTH_TIMEOUT = 4408
CLOSE_SLOW_CONSUMER = 4429


def allowed_origins() -> set[str]:
    production = (settings.ENVIRONMENT or "").lower() == "production"
    return set(parse_origins(settings.CHAT_WS_ALLOWED_ORIGINS or settings.CORS_ORIGINS, production))


# A socket's tasks reach the database at the same moment and a Session can't be shared between
# threads, so these run through call_live.in_own_session: one short session per step.
def _load_account(db: Session, user_id: int, token_version: int):
    user = service.account_is_usable(db, user_id, token_version)
    return (user.id, user.company_id) if user is not None else None


def _participants(db: Session, user_id: int, conversation_id: int) -> list[int]:
    try:
        me = service.account_is_usable(db, user_id)
        if me is None:
            return []
        return service.participant_user_ids(db, me, conversation_id)
    except HTTPException:
        return []


@router.websocket("/ws")
async def chat_ws(websocket: WebSocket):
    if not settings.CHAT_ENABLED or not chat_crypto_configured():
        await websocket.close(code=CLOSE_UNAVAILABLE)
        return
    origin = (websocket.headers.get("origin") or "").rstrip("/")
    if not origin or origin not in allowed_origins():
        await websocket.close(code=CLOSE_POLICY)
        return

    await websocket.accept()
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), AUTH_TIMEOUT)
    except asyncio.TimeoutError:
        await websocket.close(code=CLOSE_AUTH_TIMEOUT)
        return
    except (WebSocketDisconnect, RuntimeError, KeyError):
        return
    if len(raw) > MAX_FRAME_CHARS:
        await websocket.close(code=CLOSE_TOO_BIG)
        return
    try:
        frame = json.loads(raw)
    except ValueError:
        frame = None
    if not isinstance(frame, dict) or frame.get("type") != "auth":
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return

    ticket = await hub.consume_ticket(frame.get("ticket"))
    if not ticket or int(ticket.get("exp", 0)) <= time.time():
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return
    account = await call_live.in_own_session(
        ticket.get("company_id"), _load_account, int(ticket["user_id"]), int(ticket["token_version"])
    )
    if account is None or account[1] != ticket.get("company_id"):
        await websocket.close(code=CLOSE_FORBIDDEN)
        return

    user_id, company_id = account
    token_version = int(ticket["token_version"])
    expires_at = int(ticket["exp"])
    conn = Connection(user_id, company_id)
    hub.register(conn)
    await hub.touch_presence(user_id)
    conn.offer(json.dumps({"type": "ready", "user_id": user_id, "conn_id": conn.conn_id}))

    allowed_conversations: dict[int, tuple[float, list[int]]] = {}
    last_typing: dict[int, float] = {}
    close_code = {"value": 1000}
    my_calls: set[int] = set()
    call_frames: deque = deque()

    def _call_frame_allowed() -> bool:
        now = time.monotonic()
        while call_frames and call_frames[0] <= now - 10:
            call_frames.popleft()
        if len(call_frames) >= CALL_FRAMES_PER_10S:
            return False
        call_frames.append(now)
        return True

    def _reply(event: dict) -> None:
        conn.offer(json.dumps(event, separators=(",", ":")))

    async def handle_call(msg: dict) -> None:
        kind = msg["type"]
        request_id = msg.get("request_id") if isinstance(msg.get("request_id"), str) else None
        if request_id is not None:
            request_id = request_id[:64]
        if not call_live.calls_ready() or not _call_frame_allowed():
            if kind == "call.invite":
                _reply({"type": "call.error", "request_id": request_id, "code": "unavailable"})
            return
        try:
            if kind == "call.invite":
                conv_id = msg.get("conversation_id")
                if not isinstance(conv_id, int) or conv_id <= 0:
                    return
                if not await hub.allow(f"call-invite:{user_id}", CALL_INVITES_PER_MINUTE, 60):
                    _reply({"type": "call.error", "request_id": request_id, "code": "rate_limited"})
                    return
                try:
                    call = await call_live.invite(conn, conv_id)
                except HTTPException as exc:
                    code = "busy_self" if "already in a call" in str(exc.detail) else (
                        "deactivated" if exc.status_code == 409 else "not_found"
                    )
                    _reply({"type": "call.error", "request_id": request_id, "code": code})
                    return
                my_calls.add(call["id"])
                _reply({
                    "type": "call.outgoing",
                    "request_id": request_id,
                    "call_id": call["id"],
                    "conversation_id": call["conversation_id"],
                    "callee": {"id": call["callee_id"], "full_name": call["callee_name"]},
                })
                if call["end_now"]:
                    await call_live.finish(call, call["end_now"])
                return
            call_id = msg.get("call_id")
            if not isinstance(call_id, int) or call_id <= 0:
                return
            if kind == "call.accept":
                if await call_live.accept(conn, call_id):
                    my_calls.add(call_id)
            elif kind in ("call.decline", "call.cancel", "call.hangup"):
                await call_live.end(conn, call_id, kind.split(".", 1)[1])
            elif kind == "call.signal":
                data = msg.get("data")
                if isinstance(data, dict):
                    await call_live.signal(conn, call_id, data)
            elif kind == "call.report":
                connection = msg.get("connection")
                if connection in CONNECTION_REPORTS:
                    await call_live.report(conn, call_id, connection)
        except RealtimeUnavailable:
            if kind == "call.invite":
                _reply({"type": "call.error", "request_id": request_id, "code": "unavailable"})
        except Exception as exc:  # noqa: BLE001 — a failed call frame must not drop the chat socket
            logger.warning("Call frame %s failed: %s", kind[:32], type(exc).__name__)
            if kind == "call.invite":
                _reply({"type": "call.error", "request_id": request_id, "code": "unavailable"})

    async def sender() -> None:
        while True:
            payload = await conn.queue.get()
            if conn.overflowed:
                close_code["value"] = CLOSE_SLOW_CONSUMER
                return
            await websocket.send_text(payload)

    async def receiver() -> None:
        while True:
            text = await websocket.receive_text()
            if len(text) > MAX_FRAME_CHARS:
                close_code["value"] = CLOSE_TOO_BIG
                return
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if isinstance(msg.get("type"), str) and msg["type"].startswith("call."):
                await handle_call(msg)
                continue
            if msg.get("type") != "typing":
                continue
            conv_id = msg.get("conversation_id")
            if not isinstance(conv_id, int) or conv_id <= 0:
                continue
            now = time.monotonic()
            if now - last_typing.get(conv_id, 0.0) < TYPING_MIN_INTERVAL:
                continue
            last_typing[conv_id] = now
            cached = allowed_conversations.get(conv_id)
            if cached is None or cached[0] < now:
                ids = await call_live.in_own_session(company_id, _participants, user_id, conv_id)
                cached = (now + PARTICIPANT_CACHE_SECONDS, ids)
                allowed_conversations[conv_id] = cached
            recipients = [uid for uid in cached[1] if uid != user_id]
            if user_id in cached[1] and recipients:
                await hub.publish(recipients, {"type": "typing", "conversation_id": conv_id, "user_id": user_id})

    async def login_over() -> bool:
        # Closing at the login's expiry makes the tab reconnect with its renewed login, but it would
        # also end a call this socket is in, so that waits for the call to end. The account re-check
        # below still closes the socket on logout or deactivation.
        if expires_at - time.time() > 0:
            return False
        try:
            return not await call_live.holds_call(conn, my_calls)
        except RealtimeUnavailable:
            return True

    async def watchdog() -> None:
        next_recheck = time.monotonic() + RECHECK_SECONDS
        while True:
            if await login_over():
                close_code["value"] = CLOSE_UNAUTHORIZED
                return
            remaining = expires_at - time.time()
            await asyncio.sleep(min(PING_SECONDS, remaining) if remaining > 0 else PING_SECONDS)
            if await login_over():
                close_code["value"] = CLOSE_UNAUTHORIZED
                return
            conn.offer(json.dumps({"type": "ping"}))
            await hub.touch_presence(user_id)
            if my_calls:
                try:
                    await call_live.keep_alive(conn, my_calls)
                except RealtimeUnavailable:
                    pass
            if time.monotonic() >= next_recheck:
                next_recheck = time.monotonic() + RECHECK_SECONDS
                if await call_live.in_own_session(company_id, _load_account, user_id, token_version) is None:
                    close_code["value"] = CLOSE_FORBIDDEN
                    return
                allowed_conversations.clear()

    tasks = [asyncio.create_task(t()) for t in (sender, receiver, watchdog)]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, (WebSocketDisconnect, RuntimeError, KeyError)):
                logger.warning("Chat socket task failed: %s", type(exc).__name__)
    finally:
        hub.unregister(conn)
        if my_calls:
            # Shielded: ending the calls (log + chat entry + telling the other side) must finish even
            # when this task is being cancelled; bounded so shutdown can't hang. The sweep is the backstop.
            with anyio.CancelScope(shield=True, deadline=anyio.current_time() + 10):
                await call_live.socket_closed(conn, my_calls)
        try:
            await websocket.close(code=close_code["value"])
        except Exception:
            pass
