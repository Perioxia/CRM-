"""Live 1-to-1 voice calls: ringing, answering, hanging up, and relaying call setup between two tabs.

State of a live call sits in Redis (shared by all workers) with short TTLs; the
database keeps the log (services/chat/calls.py). Call setup data (SDP offers and
ICE candidates) is passed through to the other participant's socket and never
stored or logged.

Ending rules (kept simple on purpose — no resumption):
- caller cancels / callee declines while ringing; nobody answers in time → missed
- either side hangs up, or the caller's/callee's socket closes → the call ends
- one of the two sockets in the call reports the connection failed → failed

The outcome is decided in the database under the call's row lock (calls.end_call), and
Redis is cleaned up afterwards, so racing events (answer vs. cancel vs. timeout, duplicate
hang-ups) always end in one consistent status and one chat entry.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.database import SessionLocal
from app.models.core.user import User
from app.services.chat import calls, service
from app.services.chat.realtime import Connection, RealtimeUnavailable, hub
from app.tenancy import bind_tenant, reset_tenant

logger = logging.getLogger("app.chat.calls")

# Swapped out by tests; every database call here opens and closes its own session.
SessionFactory = SessionLocal

ACTIVE_TTL = 90  # refreshed every ping (25 s) by both sockets of an active call
RING_GRACE = 15
STALE_AFTER = timedelta(minutes=2)
SWEEP_SECONDS = 60

# Strong references so ring timers aren't garbage-collected before they fire.
_timers: set[asyncio.Task] = set()


def calls_ready() -> bool:
    return bool(
        settings.CHAT_ENABLED
        and settings.CHAT_CALLS_ENABLED
        and settings.CHAT_WS_PUBLIC_URL
        and (hub.uses_redis or hub.memory_mode_allowed())
    )


def _state_key(call_id: int) -> str:
    return f"chat:call:{int(call_id)}"


def _busy_key(user_id: int) -> str:
    return f"chat:incall:{int(user_id)}"


def _answer_key(call_id: int) -> str:
    return f"chat:call:{int(call_id)}:answered"


def _run(company_id: int | None, fn, *args):
    db = SessionFactory()
    try:
        if company_id is None:
            bind_tenant(bypass=True, db=db)
        else:
            bind_tenant(company_id=company_id, db=db)
        return fn(db, *args)
    finally:
        db.close()
        reset_tenant()


async def in_own_session(company_id: int | None, fn, *args):
    return await run_in_threadpool(_run, company_id, fn, *args)


def _create(db, caller_id: int, conversation_id: int):
    caller = service.account_is_usable(db, caller_id)
    if caller is None:
        raise HTTPException(status_code=404, detail="Not found")
    call = calls.create_call(db, caller, conversation_id)
    callee = db.query(User).filter(User.id == call.callee_id).one()
    return {
        "id": call.id,
        "company_id": call.company_id,
        "conversation_id": call.conversation_id,
        "caller_id": call.caller_id,
        "callee_id": call.callee_id,
        "caller_name": caller.full_name,
        "callee_name": callee.full_name,
    }


async def _load(call_id: int) -> dict | None:
    raw = await hub.kv_get(_state_key(call_id))
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


async def _save(state: dict, ttl: int) -> None:
    await hub.kv_set(_state_key(state["id"]), json.dumps(state), ttl)


async def _send(conn: Connection, event: dict) -> None:
    conn.offer(json.dumps(event, separators=(",", ":")))


async def _clear_busy(state: dict) -> None:
    for uid in (state["caller_id"], state["callee_id"]):
        if await hub.kv_get(_busy_key(uid)) == str(state["id"]):
            await hub.kv_delete(_busy_key(uid))


async def _drop_live_state(call: dict) -> None:
    try:
        await hub.kv_delete(_state_key(call["id"]), _answer_key(call["id"]))
        await _clear_busy(call)
    except RealtimeUnavailable:
        logger.warning("Call %s ended while Redis was unavailable; its keys expire on their own", call["id"])


async def _announce(call: dict, done: dict) -> None:
    users = [call["caller_id"], call["callee_id"]]
    await hub.publish(users, {"type": "call.ended", "call_id": call["id"], "status": done["status"]})
    await hub.publish(users, {
        "type": "message.new",
        "conversation_id": call["conversation_id"],
        "seq": done["message"]["seq"],
        "sender_id": call["caller_id"],
    })


async def finish(call: dict, final_status: str) -> None:
    """End a call with an explicit outcome (busy, unavailable, failed, …): log + chat entry first,
    then Redis state and busy markers, then tell both sides. Safe to repeat."""
    done = await in_own_session(call["company_id"], calls.finish_call, call["id"], call["company_id"], final_status)
    await _drop_live_state(call)
    if done is not None:
        await _announce(call, done)


async def _end(call: dict, actor_id: int | None, action: str) -> None:
    """End a call because of cancel / decline / hangup / timeout; the database decides the outcome."""
    done = await in_own_session(call["company_id"], calls.end_call, call["id"], call["company_id"], actor_id, action)
    if done is None and action == "timeout":
        return  # answered (or ended) before the timeout: leave it alone
    await _drop_live_state(call)
    if done is not None:
        await _announce(call, done)


async def invite(conn: Connection, conversation_id: int) -> dict:
    """Ring the other participant. Returns the call; if it can't ring (callee busy or not online)
    `call["end_now"]` holds that status and the caller should `finish` it after replying.
    Raises HTTPException (404/409) or RealtimeUnavailable."""
    if await hub.kv_get(_busy_key(conn.user_id)) is not None:
        raise HTTPException(status_code=409, detail="You are already in a call")
    call = await in_own_session(conn.company_id, _create, conn.user_id, conversation_id)
    call.update({"caller_conn": conn.conn_id, "callee_conn": None, "state": "ringing"})
    ring_ttl = settings.CHAT_CALL_RING_SECONDS + RING_GRACE

    if not await hub.kv_set(_busy_key(conn.user_id), str(call["id"]), ring_ttl, only_if_absent=True):
        await finish(call, "cancelled")
        raise HTTPException(status_code=409, detail="You are already in a call")
    call["end_now"] = None
    if not await hub.online_user_ids([call["callee_id"]]):
        call["end_now"] = "unavailable"
        return call
    if not await hub.kv_set(_busy_key(call["callee_id"]), str(call["id"]), ring_ttl, only_if_absent=True):
        call["end_now"] = "busy"
        return call

    await _save(call, ring_ttl)
    await hub.publish([call["callee_id"]], {
        "type": "call.incoming",
        "call_id": call["id"],
        "conversation_id": call["conversation_id"],
        "caller": {"id": call["caller_id"], "full_name": call["caller_name"]},
    })
    timer = asyncio.create_task(_ring_timeout(call["id"]))
    _timers.add(timer)
    timer.add_done_callback(_timers.discard)
    return call


async def _ring_timeout(call_id: int) -> None:
    await asyncio.sleep(settings.CHAT_CALL_RING_SECONDS)
    try:
        call = await _load(call_id)
        if call is not None and call["state"] == "ringing":
            await _end(call, None, "timeout")
    except RealtimeUnavailable:
        pass
    except Exception:
        logger.exception("Ring timeout failed for call %s", call_id)


async def accept(conn: Connection, call_id: int) -> bool:
    call = await _load(call_id)
    if call is None or call["callee_id"] != conn.user_id or call["company_id"] != conn.company_id:
        await _send(conn, {"type": "call.ended", "call_id": call_id, "status": "unavailable"})
        return False
    if call["state"] != "ringing" or not await hub.kv_set(_answer_key(call_id), conn.conn_id, ACTIVE_TTL, only_if_absent=True):
        await _send(conn, {"type": "call.taken", "call_id": call_id})
        return False
    # Live state first, then the database: if a cancel or the ring timeout ends the call in
    # between, the database refuses the answer below and the live state is removed again.
    call.update({"state": "active", "callee_conn": conn.conn_id})
    await _save(call, ACTIVE_TTL)
    for uid in (call["caller_id"], call["callee_id"]):
        await hub.kv_set(_busy_key(uid), str(call_id), ACTIVE_TTL)
    outcome = await in_own_session(call["company_id"], calls.mark_active, call_id, call["company_id"])
    if outcome != "active":
        await _drop_live_state(call)
        await _send(conn, {"type": "call.ended", "call_id": call_id, "status": outcome or "cancelled"})
        return False
    await hub.publish([call["caller_id"]], {"type": "call.answered", "call_id": call_id}, call["caller_conn"])
    await hub.publish([call["callee_id"]], {"type": "call.answered", "call_id": call_id}, conn.conn_id)
    await hub.publish([call["callee_id"]], {"type": "call.taken", "call_id": call_id})
    return True


def _in_call(conn: Connection, call: dict) -> bool:
    """True for the two sockets actually in the call (the caller's, and the callee tab that answered)."""
    return conn.conn_id in (call["caller_conn"], call["callee_conn"])


async def end(conn: Connection, call_id: int, action: str) -> None:
    """decline | cancel | hangup from either participant (any of their tabs); failed only from one of
    the two sockets in the call, so another tab of the same person can't end a call it isn't part of."""
    call = await _load(call_id)
    if call is None or conn.user_id not in (call["caller_id"], call["callee_id"]) or call["company_id"] != conn.company_id:
        return
    if action == "failed":
        if _in_call(conn, call):
            await finish(call, "failed")
        return
    await _end(call, conn.user_id, action)


async def signal(conn: Connection, call_id: int, data: dict) -> None:
    """Pass an SDP offer/answer or ICE candidate to the other side's socket. Not stored, not logged."""
    call = await _load(call_id)
    if call is None or call["state"] != "active":
        return
    if conn.conn_id == call["caller_conn"]:
        target_user, target_conn = call["callee_id"], call["callee_conn"]
    elif conn.conn_id == call["callee_conn"]:
        target_user, target_conn = call["caller_id"], call["caller_conn"]
    else:
        return
    await hub.publish([target_user], {"type": "call.signal", "call_id": call_id, "data": data}, target_conn)


async def report(conn: Connection, call_id: int, connection: str) -> None:
    if connection == "failed":
        await end(conn, call_id, "failed")
        return
    call = await _load(call_id)
    if call is None or not _in_call(conn, call):
        return
    await in_own_session(conn.company_id, calls.set_connection, call_id, conn.company_id, conn.user_id, connection)


async def keep_alive(conn: Connection, call_ids: set[int]) -> None:
    """Refresh the TTLs of this socket's active calls (called on every ping)."""
    for call_id in list(call_ids):
        call = await _load(call_id)
        if call is None:
            call_ids.discard(call_id)
            continue
        if call["state"] == "active" and conn.conn_id in (call["caller_conn"], call["callee_conn"]):
            await hub.kv_expire(_state_key(call_id), ACTIVE_TTL)
            await hub.kv_expire(_answer_key(call_id), ACTIVE_TTL)
            await hub.kv_expire(_busy_key(conn.user_id), ACTIVE_TTL)


async def holds_call(conn: Connection, call_ids: set[int]) -> bool:
    """True while closing this socket would end one of its calls (see socket_closed)."""
    for call_id in list(call_ids):
        call = await _load(call_id)
        if call is None:
            continue
        if call["state"] == "ringing" and conn.conn_id == call["caller_conn"]:
            return True
        if call["state"] == "active" and _in_call(conn, call):
            return True
    return False


async def socket_closed(conn: Connection, call_ids: set[int]) -> None:
    """A tab went away: end the calls it was part of (ringing as caller, or active on either side)."""
    for call_id in list(call_ids):
        try:
            call = await _load(call_id)
            if call is None:
                continue
            if call["state"] == "ringing" and conn.conn_id == call["caller_conn"]:
                await _end(call, conn.user_id, "cancel")
            elif call["state"] == "active" and _in_call(conn, call):
                await _end(call, conn.user_id, "hangup")
        except RealtimeUnavailable:
            pass
        except Exception:
            logger.exception("Ending call %s after socket close failed", call_id)


async def sweep_once() -> int:
    """Close calls whose live state disappeared (worker restart). Runs on one worker at a time."""
    if not await hub.kv_set("chat:calls:sweep", "1", SWEEP_SECONDS - 5, only_if_absent=True):
        return 0
    closed = 0
    for call_id, company_id in await in_own_session(None, calls.stale_candidates, STALE_AFTER):
        if await hub.kv_get(_state_key(call_id)) is not None:
            continue
        done = await in_own_session(company_id, calls.finish_call, call_id, company_id, "interrupted")
        if done is not None:
            closed += 1
            # Tell both people, so no tab keeps showing the call and the chat shows its entry.
            caller_id, callee_id = done["recipients"]
            await _announce(
                {"id": call_id, "caller_id": caller_id, "callee_id": callee_id, "conversation_id": done["conversation_id"]},
                done,
            )
    return closed


async def sweep_forever() -> None:
    while True:
        try:
            await sweep_once()
        except asyncio.CancelledError:
            raise
        except RealtimeUnavailable:
            pass
        except Exception:
            logger.exception("Stale call sweep failed")
        await asyncio.sleep(SWEEP_SECONDS)
