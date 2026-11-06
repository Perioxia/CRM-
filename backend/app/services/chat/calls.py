"""1-to-1 voice calls: the call log and its chat-history entry.

Audio flows browser-to-browser (WebRTC); the server only relays call setup
messages over the chat WebSocket and keeps this log. Authorization follows the
chat rules: only the two participants of a conversation in the same company.
Nothing about the media (SDP, ICE candidates, audio) is stored here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.core.chat import ChatCall, ChatParticipant
from app.models.core.user import User
from app.services.chat import service
from app.services.chat.crypto import encrypt_body

LIVE = ("ringing", "active")
END_ACTIONS = ("cancel", "decline", "hangup", "timeout")
FINAL = ("completed", "missed", "declined", "cancelled", "busy", "unavailable", "failed", "interrupted")
CONNECTIONS = ("direct", "relay", "unknown")


def _is_usable(user: User | None) -> bool:
    return user is not None and bool(user.is_active) and getattr(user.status, "value", user.status) != "disabled"


def create_call(db: Session, caller: User, conversation_id: int) -> ChatCall:
    """Start a call log row (status ringing). 404 unless `caller` takes part in the conversation;
    409 if the other person is deactivated."""
    conv, _ = service._my_participation(db, caller, conversation_id)
    if conv.kind != "direct":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Voice calls are only available in direct chats")
    callee = service._other_participant(db, conv.id, caller)
    if not _is_usable(callee):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This user is deactivated")
    call = ChatCall(
        company_id=caller.company_id,
        conversation_id=conv.id,
        caller_id=caller.id,
        callee_id=callee.id,
        status="ringing",
        connection="unknown",
        started_at=datetime.now(timezone.utc),
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def _locked(db: Session, call_id: int, company_id: int) -> ChatCall | None:
    return (
        db.query(ChatCall)
        .filter(ChatCall.id == call_id, ChatCall.company_id == company_id)
        .with_for_update()
        .first()
    )


def mark_active(db: Session, call_id: int, company_id: int) -> str | None:
    """ringing → active. Returns the call's status afterwards: "active" if this answered it,
    otherwise the status it already had (e.g. "cancelled" or "missed" won the race), or None."""
    call = _locked(db, call_id, company_id)
    if call is None or call.status != "ringing":
        current = call.status if call is not None else None
        db.rollback()
        return current
    call.status = "active"
    call.answered_at = datetime.now(timezone.utc)
    db.commit()
    return "active"


def set_connection(db: Session, call_id: int, company_id: int, user_id: int, connection: str) -> bool:
    """Record how the browsers connected; only a participant of the call may report it."""
    if connection not in CONNECTIONS:
        return False
    call = _locked(db, call_id, company_id)
    if call is None or user_id not in (call.caller_id, call.callee_id):
        db.rollback()
        return False
    call.connection = connection
    db.commit()
    return True


def finish_call(db: Session, call_id: int, company_id: int, final_status: str) -> dict | None:
    """End a live call with an explicit outcome (busy, unavailable, failed, interrupted, …).

    Returns the result for the call that was ended now, or None if it had already ended —
    duplicate or racing endings are harmless and write exactly one history entry.
    """
    if final_status not in FINAL:
        raise ValueError(f"not a final call status: {final_status}")
    call = _locked(db, call_id, company_id)
    if call is None or call.status in FINAL:
        db.rollback()
        return None
    return _close(db, call, final_status)


def end_call(db: Session, call_id: int, company_id: int, actor_id: int | None, action: str) -> dict | None:
    """End a call because someone acted, deciding the outcome from the call's real state
    (under the row lock, so racing events can't produce contradictory results):

    - cancel / decline / hangup while ringing → "cancelled" (by the caller) or "declined" (by the callee)
    - any of them once answered → "completed"
    - timeout → "missed" only if still ringing; an answered call is left alone

    Returns the result, or None if nothing changed (already ended, answered before the timeout,
    or `actor_id` isn't a participant).
    """
    if action not in END_ACTIONS:
        raise ValueError(f"unknown call action: {action}")
    call = _locked(db, call_id, company_id)
    if call is None or call.status in FINAL:
        db.rollback()
        return None
    if action == "timeout":
        if call.status != "ringing":
            db.rollback()
            return None
        final = "missed"
    elif call.status == "active":
        final = "completed"
    elif actor_id == call.caller_id:
        final = "cancelled"
    elif actor_id == call.callee_id:
        final = "declined"
    else:
        db.rollback()
        return None
    return _close(db, call, final)


def _close(db: Session, call: ChatCall, final_status: str) -> dict:
    """Set the final status and duration and write the call's one chat-history entry (locked row)."""
    now = datetime.now(timezone.utc)
    answered = call.answered_at
    if answered is not None and answered.tzinfo is None:
        answered = answered.replace(tzinfo=timezone.utc)
    call.status = final_status
    call.ended_at = now
    call.duration_s = max(0, int((now - answered).total_seconds())) if answered is not None else None
    ciphertext, key_id = encrypt_body("")
    msg = service.append_message(
        db, call.company_id, call.conversation_id, call.caller_id, ciphertext, key_id, str(uuid.uuid4()), now
    )
    call.message_id = msg.id
    if answered is not None or final_status == "declined":
        # The callee handled this call (answered or declined it), so its entry isn't news to them.
        # Only advance their read position when nothing else is unread — never hide other messages.
        db.execute(
            update(ChatParticipant)
            .where(
                ChatParticipant.conversation_id == call.conversation_id,
                ChatParticipant.user_id == call.callee_id,
                ChatParticipant.last_read_seq == msg.seq - 1,
            )
            .values(last_read_seq=msg.seq)
        )
    db.commit()
    db.refresh(msg)
    db.refresh(call)
    return {
        "status": final_status,
        "message": service._message_out(msg, None, None, call),
        "recipients": [call.caller_id, call.callee_id],
        "conversation_id": call.conversation_id,
    }


def stale_candidates(db: Session, older_than: timedelta) -> list[tuple[int, int]]:
    """(call_id, company_id) of calls still ringing/active after `older_than` — candidates for
    cleanup if their live state is gone (e.g. the worker holding them restarted)."""
    cutoff = datetime.now(timezone.utc) - older_than
    rows = (
        db.query(ChatCall.id, ChatCall.company_id)
        .filter(ChatCall.status.in_(LIVE), ChatCall.started_at < cutoff)
        .order_by(ChatCall.id)
        .limit(500)
        .all()
    )
    db.rollback()
    return [(call_id, company_id) for call_id, company_id in rows]
