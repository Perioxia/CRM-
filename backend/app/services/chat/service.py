"""Company chat: 1-to-1 conversations and one room per team.

Team rooms are visible only to current members of that team (membership rows,
or the user's primary team). Leaving the team removes access.

Authorization lives here, not in RLS: every conversation access goes through
`_my_participation`, which requires the caller to be a participant in their own
company. Anything else is a 404, including for admins, MDs and platform admins.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.core.chat import ChatAttachment, ChatCall, ChatConversation, ChatMessage, ChatParticipant
from app.models.core.company import Company
from app.models.core.team import Team
from app.models.core.team_membership import TeamMembership
from app.models.core.user import User
from app.services.chat import files
from app.services.chat.crypto import ChatDecryptError, decrypt_body, encrypt_body

MAX_BODY_CHARS = 4000
PREVIEW_CHARS = 120
PAGE_MAX = 50
CONVERSATION_PAGE_MAX = 100
DIRECTORY_MAX = 50
LINKS_MAX = 100
# Namespace for pg_advisory_xact_lock(namespace, company_id): serialises the final
# storage-quota check of concurrent uploads in one company.
_UPLOAD_QUOTA_LOCK = 41041
_TEAM_KEY_PREFIX = "team:"
_TEAM_MEMBERS_CAP = 200


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _role(user: User) -> str:
    return getattr(user.role, "value", user.role)


def require_company_member(user: User) -> None:
    if user.company_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Chat is available to company users only")


def account_is_usable(db: Session, user_id: int, token_version: int | None = None) -> User | None:
    """Same account checks as get_current_user (app/utils/dependencies.py), for WebSockets."""
    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active or getattr(user.status, "value", user.status) == "disabled":
        return None
    if token_version is not None and int(user.token_version or 0) != int(token_version):
        return None
    if user.company_id is None:
        return None
    company = db.query(Company).filter(Company.id == user.company_id).first()
    if company is None:
        return None
    company_status = str(getattr(company.status, "value", company.status)).lower()
    if company_status not in ("active", "trial"):
        return None
    if company_status == "trial" and company.trial_ends_at is not None:
        ends = company.trial_ends_at
        if ends.tzinfo is None:
            ends = ends.replace(tzinfo=timezone.utc)
        if ends < datetime.now(timezone.utc):
            return None
    return user


def _participation_row(db: Session, me: User, conversation_id: int):
    return (
        db.query(ChatConversation, ChatParticipant)
        .join(ChatParticipant, ChatParticipant.conversation_id == ChatConversation.id)
        .filter(
            ChatConversation.id == conversation_id,
            ChatConversation.company_id == me.company_id,
            ChatParticipant.user_id == me.id,
            ChatParticipant.company_id == me.company_id,
        )
        .first()
    )


def _my_participation(db: Session, me: User, conversation_id: int) -> tuple[ChatConversation, ChatParticipant]:
    row = _participation_row(db, me, conversation_id)
    if row is not None:
        conv, part = row
        if conv.kind == "team":
            tid = team_id_from_key(conv.direct_key)
            if tid is None or not _is_team_member(db, me, tid):
                db.delete(part)
                db.commit()
                raise _not_found()
        return row

    conv = (
        db.query(ChatConversation)
        .filter(
            ChatConversation.id == conversation_id,
            ChatConversation.company_id == me.company_id,
            ChatConversation.kind == "team",
        )
        .first()
    )
    tid = team_id_from_key(conv.direct_key) if conv is not None else None
    if conv is None or tid is None or not _is_team_member(db, me, tid):
        raise _not_found()
    try:
        with db.begin_nested():
            part = ChatParticipant(
                company_id=me.company_id,
                conversation_id=conv.id,
                user_id=me.id,
                last_read_seq=int(conv.last_seq or 0),
            )
            db.add(part)
            db.flush()
        db.commit()
    except IntegrityError:
        db.rollback()
        row = _participation_row(db, me, conversation_id)
        if row is None:
            raise _not_found()
        return row
    db.refresh(part)
    return conv, part


def participant_user_ids(db: Session, me: User, conversation_id: int) -> list[int]:
    _my_participation(db, me, conversation_id)
    rows = db.query(ChatParticipant.user_id).filter(
        ChatParticipant.conversation_id == conversation_id,
        ChatParticipant.company_id == me.company_id,
    )
    return [r[0] for r in rows]


def _other_participant(db: Session, conversation_id: int, me: User) -> User | None:
    return (
        db.query(User)
        .join(ChatParticipant, ChatParticipant.user_id == User.id)
        .filter(
            ChatParticipant.conversation_id == conversation_id,
            ChatParticipant.user_id != me.id,
            User.company_id == me.company_id,
        )
        .first()
    )


def _user_card(user: User | None, team_names: dict[int, str]) -> dict | None:
    if user is None:
        return None
    return {
        "id": user.id,
        "full_name": user.full_name,
        "role": _role(user),
        "team": team_names.get(user.team_id) if user.team_id else None,
        "is_active": bool(user.is_active) and getattr(user.status, "value", user.status) != "disabled",
    }


def _team_names(db: Session, company_id: int, team_ids: set[int]) -> dict[int, str]:
    if not team_ids:
        return {}
    rows = db.query(Team.id, Team.name).filter(Team.company_id == company_id, Team.id.in_(team_ids))
    return {tid: name for tid, name in rows}


def team_key(team_id: int) -> str:
    return f"{_TEAM_KEY_PREFIX}{int(team_id)}"


def team_id_from_key(key: str | None) -> int | None:
    if not key or not str(key).startswith(_TEAM_KEY_PREFIX):
        return None
    rest = str(key)[len(_TEAM_KEY_PREFIX):]
    if not rest.isdigit():
        return None
    return int(rest)


def my_team_ids(db: Session, me: User) -> list[int]:
    """Teams this user belongs to in their company, via membership or primary team_id."""
    member_of = Team.id.in_(
        select(TeamMembership.team_id).where(
            TeamMembership.company_id == me.company_id,
            TeamMembership.user_id == me.id,
        )
    )
    on_team = or_(member_of, Team.id == int(me.team_id)) if me.team_id else member_of
    rows = db.query(Team.id).filter(Team.company_id == me.company_id, on_team)
    return sorted(tid for (tid,) in rows)


def _member_user_ids(db: Session, company_id: int, team_id: int) -> set[int]:
    ids = {
        uid
        for (uid,) in db.query(TeamMembership.user_id).filter(
            TeamMembership.company_id == company_id,
            TeamMembership.team_id == team_id,
        )
    }
    ids.update(
        uid
        for (uid,) in db.query(User.id).filter(
            User.company_id == company_id,
            User.team_id == team_id,
        )
    )
    return ids


def _is_team_member(db: Session, me: User, team_id: int) -> bool:
    return team_id in my_team_ids(db, me)


def _sync_team_members(db: Session, company_id: int, conv: ChatConversation, team_id: int) -> bool:
    """Match participants to the team's current members. New members start caught up."""
    wanted = _member_user_ids(db, company_id, team_id)
    existing = db.query(ChatParticipant).filter(ChatParticipant.conversation_id == conv.id).all()
    have = {part.user_id: part for part in existing}
    changed = False
    for uid, part in have.items():
        if uid not in wanted:
            db.delete(part)
            changed = True
    caught_up = int(conv.last_seq or 0)
    for uid in wanted - set(have):
        try:
            with db.begin_nested():
                db.add(ChatParticipant(
                    company_id=company_id,
                    conversation_id=conv.id,
                    user_id=uid,
                    last_read_seq=caught_up,
                ))
                db.flush()
            changed = True
        except IntegrityError:
            continue
    return changed


def _load_team_conversation(db: Session, company_id: int, team_id: int) -> ChatConversation | None:
    return (
        db.query(ChatConversation)
        .filter(
            ChatConversation.company_id == company_id,
            ChatConversation.direct_key == team_key(team_id),
        )
        .first()
    )


def ensure_team_chat(db: Session, me: User, team_id: int) -> ChatConversation:
    """Create the team's room if needed and refresh who can see it. 404 for non-members."""
    if not _is_team_member(db, me, team_id):
        raise _not_found()
    return _ensure_team_room(db, me, team_id, _load_team_conversation(db, me.company_id, team_id))


def _ensure_team_room(db: Session, me: User, team_id: int, conv: ChatConversation | None) -> ChatConversation:
    """ensure_team_chat for a team the caller is known to be on, given its room if already loaded."""
    created = False
    if conv is None:
        try:
            conv = ChatConversation(
                company_id=me.company_id,
                kind="team",
                direct_key=team_key(team_id),
                last_seq=0,
            )
            db.add(conv)
            db.flush()
            created = True
        except IntegrityError:
            db.rollback()
            conv = _load_team_conversation(db, me.company_id, team_id)
            if conv is None:
                raise
    changed = _sync_team_members(db, me.company_id, conv, team_id)
    if created or changed:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            conv = _load_team_conversation(db, me.company_id, team_id)
            if conv is None:
                raise
            _sync_team_members(db, me.company_id, conv, team_id)
            db.commit()
        db.refresh(conv)
    return conv


def refresh_my_team_chats(db: Session, me: User) -> None:
    """Open a room for every team the caller is on, and drop rooms they have left."""
    current = set(my_team_ids(db, me))
    mine = (
        db.query(ChatConversation, ChatParticipant)
        .join(ChatParticipant, ChatParticipant.conversation_id == ChatConversation.id)
        .filter(
            ChatConversation.company_id == me.company_id,
            ChatConversation.kind == "team",
            ChatParticipant.user_id == me.id,
            ChatParticipant.company_id == me.company_id,
        )
        .all()
    )
    dropped = False
    rooms: dict[int, ChatConversation] = {}
    for conv, part in mine:
        tid = team_id_from_key(conv.direct_key)
        if tid not in current:
            db.delete(part)
            dropped = True
        else:
            rooms[tid] = conv
    if dropped:
        db.commit()
        rooms = {}
    for tid in sorted(current):
        room = rooms[tid] if tid in rooms else _load_team_conversation(db, me.company_id, tid)
        _ensure_team_room(db, me, tid, room)


def _safe_decrypt(token: str) -> str | None:
    try:
        return decrypt_body(token)
    except ChatDecryptError:
        return None


def _attachment_out(att: ChatAttachment, viewer: User | None) -> dict:
    out = {
        "id": att.id,
        "category": att.category,
        "content_type": att.content_type,
        "size": int(att.size_bytes),
        "filename": _safe_decrypt(att.filename_enc),
    }
    if viewer is not None:
        out.update(files.file_link(viewer.id, int(viewer.token_version or 0), att.id))
    return out


def _attachments_for(db: Session, message_ids: list[int]) -> dict[int, ChatAttachment]:
    if not message_ids:
        return {}
    rows = db.query(ChatAttachment).filter(ChatAttachment.message_id.in_(message_ids))
    return {att.message_id: att for att in rows}


def _calls_for(db: Session, message_ids: list[int]) -> dict[int, ChatCall]:
    if not message_ids:
        return {}
    rows = db.query(ChatCall).filter(ChatCall.message_id.in_(message_ids))
    return {call.message_id: call for call in rows}


def _call_out(call: ChatCall) -> dict:
    return {
        "id": call.id,
        "status": call.status,
        "duration_s": call.duration_s,
        "connection": call.connection,
        "caller_id": call.caller_id,
        "callee_id": call.callee_id,
    }


def _message_out(
    msg: ChatMessage,
    attachment: ChatAttachment | None = None,
    viewer: User | None = None,
    call: ChatCall | None = None,
) -> dict:
    """`viewer` gets signed download links; without one (exports) links are omitted."""
    body = _safe_decrypt(msg.body_enc)
    return {
        "id": msg.id,
        "conversation_id": msg.conversation_id,
        "seq": msg.seq,
        "sender_id": msg.sender_id,
        "body": body,
        "unavailable": body is None,
        "client_msg_id": msg.client_msg_id,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
        "attachment": _attachment_out(attachment, viewer) if attachment is not None else None,
        "call": _call_out(call) if call is not None else None,
    }


def _messages_out(db: Session, rows: list[ChatMessage], viewer: User | None) -> list[dict]:
    ids = [m.id for m in rows]
    attachments = _attachments_for(db, ids)
    calls = _calls_for(db, ids)
    return [_message_out(m, attachments.get(m.id), viewer, calls.get(m.id)) for m in rows]


def directory(db: Session, me: User, q: str | None, limit: int) -> list[dict]:
    limit = max(1, min(int(limit), DIRECTORY_MAX))
    query = db.query(User).filter(
        User.company_id == me.company_id,
        User.id != me.id,
        User.is_active == True,  # noqa: E712
    )
    term = (q or "").strip()
    if term:
        query = query.filter(User.full_name.ilike(f"%{term[:100]}%"))
    users = query.order_by(User.full_name, User.id).limit(limit).all()
    teams = _team_names(db, me.company_id, {u.team_id for u in users if u.team_id})
    return [_user_card(u, teams) for u in users]


def get_or_create_direct(db: Session, me: User, other_user_id: int) -> dict:
    if other_user_id == me.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You can't start a chat with yourself")
    other = (
        db.query(User)
        .filter(User.id == other_user_id, User.company_id == me.company_id)
        .first()
    )
    if other is None or not other.is_active or getattr(other.status, "value", other.status) == "disabled":
        raise _not_found()

    low, high = sorted((me.id, other.id))
    key = f"{low}:{high}"

    def _existing() -> ChatConversation | None:
        return (
            db.query(ChatConversation)
            .filter(ChatConversation.company_id == me.company_id, ChatConversation.direct_key == key)
            .first()
        )

    conv = _existing()
    if conv is None:
        try:
            conv = ChatConversation(company_id=me.company_id, kind="direct", direct_key=key, last_seq=0)
            db.add(conv)
            db.flush()
            db.add_all([
                ChatParticipant(company_id=me.company_id, conversation_id=conv.id, user_id=low, last_read_seq=0),
                ChatParticipant(company_id=me.company_id, conversation_id=conv.id, user_id=high, last_read_seq=0),
            ])
            db.commit()
        except IntegrityError:
            db.rollback()
            conv = _existing()
            if conv is None:
                raise
    return conversation_summary(db, me, conv.id)


def get_or_create_team(db: Session, me: User, team_id: int) -> dict:
    conv = ensure_team_chat(db, me, team_id)
    return conversation_summary(db, me, conv.id)


def _summaries(db: Session, me: User, conversation_ids: list[int]) -> list[dict]:
    """Batch-load summaries (fixed number of queries) for conversations `me` participates in."""
    if not conversation_ids:
        return []
    mine = {
        conv.id: (conv, part)
        for conv, part in db.query(ChatConversation, ChatParticipant)
        .join(ChatParticipant, ChatParticipant.conversation_id == ChatConversation.id)
        .filter(
            ChatConversation.id.in_(conversation_ids),
            ChatConversation.company_id == me.company_id,
            ChatParticipant.user_id == me.id,
            ChatParticipant.company_id == me.company_id,
        )
    }
    direct_ids = [cid for cid, (conv, _) in mine.items() if conv.kind != "team"]
    team_ids_present = [cid for cid, (conv, _) in mine.items() if conv.kind == "team"]
    others = {
        part.conversation_id: (part, user)
        for part, user in db.query(ChatParticipant, User)
        .join(User, User.id == ChatParticipant.user_id)
        .filter(
            ChatParticipant.conversation_id.in_(direct_ids or [-1]),
            ChatParticipant.user_id != me.id,
            User.company_id == me.company_id,
        )
    }
    teams = _team_names(
        db,
        me.company_id,
        {tid for cid in team_ids_present if (tid := team_id_from_key(mine[cid][0].direct_key))}
        | {u.team_id for _, u in others.values() if u.team_id},
    )
    team_name_by_conv: dict[int, tuple] = {}
    members_by_conv: dict[int, list[dict]] = {}
    if team_ids_present:
        grouped: dict[int, list[User]] = {cid: [] for cid in team_ids_present}
        member_rows = (
            db.query(ChatParticipant.conversation_id, User)
            .join(User, User.id == ChatParticipant.user_id)
            .filter(
                ChatParticipant.conversation_id.in_(team_ids_present),
                User.company_id == me.company_id,
            )
            .order_by(User.full_name, User.id)
        )
        for cid, user in member_rows:
            grouped.setdefault(cid, []).append(user)
        for cid in team_ids_present:
            tid = team_id_from_key(mine[cid][0].direct_key)
            people = grouped.get(cid, [])
            team_name_by_conv[cid] = (tid, teams.get(tid) if tid is not None else None, len(people))
            members_by_conv[cid] = [
                {
                    "id": user.id,
                    "full_name": user.full_name,
                    "is_active": bool(user.is_active) and getattr(user.status, "value", user.status) != "disabled",
                }
                for user in people[:_TEAM_MEMBERS_CAP]
            ]
    last_messages = {
        msg.conversation_id: msg
        for msg in db.query(ChatMessage)
        .join(
            ChatConversation,
            (ChatMessage.conversation_id == ChatConversation.id) & (ChatMessage.seq == ChatConversation.last_seq),
        )
        .filter(ChatConversation.id.in_(list(mine)))
    }
    last_ids = [m.id for m in last_messages.values()]
    last_attachments = _attachments_for(db, last_ids)
    last_calls = _calls_for(db, last_ids)

    out = []
    for cid in conversation_ids:
        if cid not in mine:
            continue
        conv, part = mine[cid]
        is_team = conv.kind == "team"
        other_part, other_user = (None, None) if is_team else others.get(cid, (None, None))
        team_out = None
        members = []
        if is_team:
            tid, name, count = team_name_by_conv.get(cid, (None, None, 0))
            members = members_by_conv.get(cid, [])
            team_out = {"id": tid, "name": name or "Team", "member_count": count}
        last = None
        msg = last_messages.get(cid)
        if msg is not None:
            body = _safe_decrypt(msg.body_enc)
            att = last_attachments.get(msg.id)
            call = last_calls.get(msg.id)
            last = {
                "seq": msg.seq,
                "sender_id": msg.sender_id,
                "preview": body[:PREVIEW_CHARS] if body is not None else None,
                "attachment": att.category if att is not None else None,
                "call": _call_out(call) if call is not None else None,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            }
        out.append({
            "id": conv.id,
            "kind": conv.kind,
            "team": team_out,
            "members": members if is_team else [],
            "other_user": None if is_team else _user_card(other_user, teams),
            "last_seq": conv.last_seq,
            "last_read_seq": part.last_read_seq,
            "other_last_read_seq": other_part.last_read_seq if other_part else 0,
            "unread": max(0, conv.last_seq - part.last_read_seq),
            "last_message": last,
            "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
        })
    return out


def conversation_summary(db: Session, me: User, conversation_id: int) -> dict:
    _my_participation(db, me, conversation_id)
    return _summaries(db, me, [conversation_id])[0]


def list_conversations(db: Session, me: User, skip: int, limit: int) -> dict:
    limit = max(1, min(int(limit), CONVERSATION_PAGE_MAX))
    skip = max(0, int(skip))
    refresh_my_team_chats(db, me)
    base = (
        db.query(ChatConversation.id)
        .join(ChatParticipant, ChatParticipant.conversation_id == ChatConversation.id)
        .filter(
            ChatConversation.company_id == me.company_id,
            ChatParticipant.user_id == me.id,
            ChatParticipant.company_id == me.company_id,
        )
    )
    order = (
        ChatConversation.last_message_at.is_(None),
        ChatConversation.last_message_at.desc(),
        ChatConversation.id.desc(),
    )
    direct = base.filter(ChatConversation.kind != "team")
    teams = base.filter(ChatConversation.kind == "team")
    total = base.count()
    direct_ids = [r[0] for r in direct.order_by(*order).offset(skip).limit(limit)]
    # Team rooms stay on the first page so a busy direct list cannot hide them.
    team_ids = [r[0] for r in teams.order_by(*order).limit(100)] if skip == 0 else []
    ids = team_ids + direct_ids
    return {"items": _summaries(db, me, ids), "total": total, "skip": skip, "limit": limit}


def get_messages(
    db: Session,
    me: User,
    conversation_id: int,
    before_seq: int | None,
    after_seq: int | None,
    limit: int,
) -> dict:
    conv, _ = _my_participation(db, me, conversation_id)
    limit = max(1, min(int(limit), PAGE_MAX))
    query = db.query(ChatMessage).filter(ChatMessage.conversation_id == conv.id)
    if after_seq is not None:
        rows = query.filter(ChatMessage.seq > after_seq).order_by(ChatMessage.seq.asc()).limit(limit).all()
    else:
        if before_seq is not None:
            query = query.filter(ChatMessage.seq < before_seq)
        rows = list(reversed(query.order_by(ChatMessage.seq.desc()).limit(limit).all()))
    return {
        "conversation_id": conv.id,
        "last_seq": conv.last_seq,
        "items": _messages_out(db, rows, me),
        "has_more_before": bool(rows) and rows[0].seq > 1,
    }


def validate_body(raw, allow_empty: bool = False, max_chars: int = MAX_BODY_CHARS) -> str:
    """A caption on a file message may be empty; a text message may not."""
    if raw is None and allow_empty:
        return ""
    if not isinstance(raw, str):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Message must be text")
    body = raw.strip()
    if not body and not allow_empty:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Message is empty")
    if len(body) > max_chars:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Message is longer than {max_chars} characters",
        )
    return body


def validate_client_msg_id(raw) -> str:
    try:
        return str(uuid.UUID(str(raw)))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="client_msg_id must be a UUID")


def _existing_by_client_id(db: Session, me: User, client_msg_id: str) -> ChatMessage | None:
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.sender_id == me.id, ChatMessage.client_msg_id == client_msg_id)
        .first()
    )


def existing_message(db: Session, me: User, conversation_id: int, client_msg_id: str) -> dict | None:
    """The already-saved message for this client_msg_id (lets a retried upload skip re-sending the file)."""
    conv, _ = _my_participation(db, me, conversation_id)
    existing = _existing_by_client_id(db, me, client_msg_id)
    if existing is None:
        return None
    if existing.conversation_id != conv.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="client_msg_id already used")
    return _messages_out(db, [existing], me)[0]


def _lock_company_uploads(db: Session, company_id: int) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:ns, :cid)"), {"ns": _UPLOAD_QUOTA_LOCK, "cid": int(company_id)})


def append_message(
    db: Session,
    company_id: int,
    conversation_id: int,
    sender_id: int,
    ciphertext: str,
    key_id: str,
    client_msg_id: str,
    now: datetime,
) -> ChatMessage:
    """Insert the next message of a conversation (caller commits).

    The row lock taken by the UPDATE on the conversation orders concurrent sends,
    so seq is gap-free per conversation. The sender's own message counts as read.
    """
    seq = db.execute(
        update(ChatConversation)
        .where(ChatConversation.id == conversation_id)
        .values(last_seq=ChatConversation.last_seq + 1, last_message_at=now)
        .returning(ChatConversation.last_seq)
    ).scalar_one()
    msg = ChatMessage(
        company_id=company_id,
        conversation_id=conversation_id,
        seq=seq,
        sender_id=sender_id,
        body_enc=ciphertext,
        key_id=key_id,
        client_msg_id=client_msg_id,
        created_at=now,
    )
    db.add(msg)
    db.execute(
        update(ChatParticipant)
        .where(
            ChatParticipant.conversation_id == conversation_id,
            ChatParticipant.user_id == sender_id,
            ChatParticipant.last_read_seq < seq,
        )
        .values(last_read_seq=seq)
    )
    db.flush()
    return msg


def send_message(
    db: Session,
    me: User,
    conversation_id: int,
    body: str,
    client_msg_id: str,
    upload: dict | None = None,
) -> tuple[dict, bool]:
    """Returns (message, created). A retry with the same client_msg_id returns the original.

    `upload` (file messages) holds the verified temp file: temp_path, stored_name,
    filename, content_type, category, size. The file is moved into place just
    before commit and removed again if the commit doesn't happen; the caller
    deletes the temp file whenever this returns created=False or raises.
    """
    conv, _ = _my_participation(db, me, conversation_id)

    existing = _existing_by_client_id(db, me, client_msg_id)
    if existing is not None:
        if existing.conversation_id != conv.id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="client_msg_id already used")
        return _messages_out(db, [existing], me)[0], False

    if conv.kind == "team":
        tid = team_id_from_key(conv.direct_key)
        if tid is None:
            raise _not_found()
        _sync_team_members(db, me.company_id, conv, tid)
    else:
        other = _other_participant(db, conv.id, me)
        if other is None or not other.is_active or getattr(other.status, "value", other.status) == "disabled":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This user is deactivated")

    ciphertext, key_id = encrypt_body(body)
    attachment = None
    if upload is not None:
        filename_enc, filename_key_id = encrypt_body(upload["filename"])
    now = datetime.now(timezone.utc)
    moved_to = None
    try:
        if upload is not None:
            # Concurrent uploads in one company take turns here, so together they can't exceed the quota.
            _lock_company_uploads(db, me.company_id)
            from app.services.billing.limits import assert_can_upload

            assert_can_upload(db, me.company_id, int(upload["size"]))
        msg = append_message(db, me.company_id, conv.id, me.id, ciphertext, key_id, client_msg_id, now)
        if upload is not None:
            attachment = ChatAttachment(
                company_id=me.company_id,
                conversation_id=conv.id,
                message_id=msg.id,
                uploader_id=me.id,
                stored_name=upload["stored_name"],
                filename_enc=filename_enc,
                key_id=filename_key_id,
                content_type=upload["content_type"],
                category=upload["category"],
                size_bytes=int(upload["size"]),
                created_at=now,
            )
            db.add(attachment)
            db.flush()
            moved_to = files.final_path(upload["stored_name"])
            os.replace(upload["temp_path"], moved_to)
        db.commit()
    except IntegrityError:
        db.rollback()
        files.discard(moved_to)
        existing = _existing_by_client_id(db, me, client_msg_id)
        if existing is not None and existing.conversation_id == conv.id:
            return _messages_out(db, [existing], me)[0], False
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Message could not be saved, please retry")
    except BaseException:
        db.rollback()
        files.discard(moved_to)
        raise
    db.refresh(msg)
    if attachment is not None:
        db.refresh(attachment)
    return _message_out(msg, attachment, me), True


def mark_read(db: Session, me: User, conversation_id: int, seq: int) -> dict:
    conv, mine = _my_participation(db, me, conversation_id)
    target = max(0, min(int(seq), conv.last_seq))
    db.execute(
        update(ChatParticipant)
        .where(ChatParticipant.id == mine.id, ChatParticipant.last_read_seq < target)
        .values(last_read_seq=target)
    )
    db.commit()
    current = db.query(ChatParticipant.last_read_seq).filter(ChatParticipant.id == mine.id).scalar()
    return {"conversation_id": conv.id, "last_read_seq": current, "advanced": current == target and target > 0}


def unread_total(db: Session, me: User) -> int:
    total = (
        db.query(func.coalesce(func.sum(ChatConversation.last_seq - ChatParticipant.last_read_seq), 0))
        .join(ChatParticipant, ChatParticipant.conversation_id == ChatConversation.id)
        .filter(
            ChatConversation.company_id == me.company_id,
            ChatParticipant.user_id == me.id,
            ChatParticipant.company_id == me.company_id,
            ChatConversation.last_seq > ChatParticipant.last_read_seq,
        )
        .scalar()
    )
    return int(total or 0)


def sync_state(db: Session, me: User) -> dict:
    """Small reconcile payload: every conversation's last_seq and my read position."""
    rows = (
        db.query(ChatConversation.id, ChatConversation.last_seq, ChatParticipant.last_read_seq)
        .join(ChatParticipant, ChatParticipant.conversation_id == ChatConversation.id)
        .filter(
            ChatConversation.company_id == me.company_id,
            ChatParticipant.user_id == me.id,
            ChatParticipant.company_id == me.company_id,
        )
        .order_by(ChatConversation.id)
        .limit(1000)
        .all()
    )
    conversations = [{"id": cid, "last_seq": last, "last_read_seq": read} for cid, last, read in rows]
    return {
        "conversations": conversations,
        "unread_total": sum(max(0, c["last_seq"] - c["last_read_seq"]) for c in conversations),
    }


def same_company_user_ids(db: Session, me: User, user_ids: list[int]) -> list[int]:
    if not user_ids:
        return []
    rows = db.query(User.id).filter(User.company_id == me.company_id, User.id.in_(user_ids[:200]))
    return [r[0] for r in rows]


def export_sent_messages(db: Session, user: User) -> list[dict]:
    if user.company_id is None:
        return []
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.sender_id == user.id, ChatMessage.company_id == user.company_id)
        .order_by(ChatMessage.conversation_id, ChatMessage.seq)
        .all()
    )
    return _messages_out(db, rows, None)


def attachment_for_download(db: Session, viewer: User, attachment_id: int) -> tuple[ChatAttachment, str]:
    """(attachment, decrypted filename) if `viewer` takes part in its conversation; 404 otherwise."""
    att = (
        db.query(ChatAttachment)
        .filter(ChatAttachment.id == attachment_id, ChatAttachment.company_id == viewer.company_id)
        .first()
    )
    if att is None:
        raise _not_found()
    _my_participation(db, viewer, att.conversation_id)
    return att, _safe_decrypt(att.filename_enc) or "file"


def attachment_links(db: Session, me: User, attachment_ids: list[int]) -> dict[str, dict]:
    """Fresh download links for the ids `me` may open; others are silently left out."""
    ids = [int(i) for i in attachment_ids][:LINKS_MAX]
    if not ids:
        return {}
    rows = (
        db.query(ChatAttachment.id)
        .join(
            ChatParticipant,
            (ChatParticipant.conversation_id == ChatAttachment.conversation_id) & (ChatParticipant.user_id == me.id),
        )
        .filter(
            ChatAttachment.id.in_(ids),
            ChatAttachment.company_id == me.company_id,
            ChatParticipant.company_id == me.company_id,
        )
    )
    tv = int(me.token_version or 0)
    return {str(att_id): files.file_link(me.id, tv, att_id) for (att_id,) in rows}
