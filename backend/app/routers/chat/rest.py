import logging
import re
from typing import Any, Optional

import httpx

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.database import get_db
from app.models.core.user import User
from app.services.chat import call_live, files, filetypes, service
from app.services.chat.crypto import ChatCryptoUnavailable, chat_crypto_configured
from app.services.chat.realtime import TICKET_TTL_SECONDS, RealtimeUnavailable, hub
from app.utils.dependencies import get_current_user, oauth2_scheme
from app.utils.security import decode_access_token


logger = logging.getLogger("app.chat.rest")

TURN_URL = "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate-ice-servers"
TURN_TTL_SECONDS = 2 * 3600
# Browsers block port 53; with it in the list ICE would wait for a timeout.
_PORT_53 = re.compile(r":53(?:[/?]|$)")


def require_chat_ready() -> None:
    if not settings.CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    if not chat_crypto_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Chat is not configured")


router = APIRouter(dependencies=[Depends(require_chat_ready)])


async def chat_user(current_user: User = Depends(get_current_user)) -> User:
    service.require_company_member(current_user)
    return current_user


async def _limit(key: str, limit: int, window: int) -> None:
    if not await hub.allow(key, limit, window):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests, slow down")


async def _db(fn, *args):
    try:
        return await run_in_threadpool(fn, *args)
    except ChatCryptoUnavailable:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Chat is not configured")


class StartDirectIn(BaseModel):
    user_id: int


class StartTeamIn(BaseModel):
    team_id: int


class SendIn(BaseModel):
    # Typed as Any and validated by hand so a 422 never echoes the message text back.
    body: Any = None
    client_msg_id: Any = None


class ReadIn(BaseModel):
    seq: int


class LinksIn(BaseModel):
    ids: list[int]


@router.get("/status")
async def chat_status(me: User = Depends(chat_user)):
    live = bool(settings.CHAT_WS_PUBLIC_URL) and (hub.uses_redis or hub.memory_mode_allowed())
    files_on = files.files_ready()
    return {
        "enabled": True,
        "user_id": me.id,
        "ws_url": settings.CHAT_WS_PUBLIC_URL if live else None,
        "max_body_chars": service.MAX_BODY_CHARS,
        "files": {
            "enabled": files_on,
            "max_doc_bytes": settings.CHAT_MAX_DOC_BYTES,
            "max_media_bytes": settings.CHAT_MAX_MEDIA_BYTES,
            "max_caption_chars": files.MAX_CAPTION_CHARS,
            "accept": filetypes.accept_list(),
        } if files_on else {"enabled": False},
        "calls": {"enabled": call_live.calls_ready(), "ring_seconds": settings.CHAT_CALL_RING_SECONDS},
    }


def _stun_servers() -> list[dict]:
    urls = [u.strip() for u in (settings.CHAT_STUN_URLS or "").split(",") if u.strip()]
    return [{"urls": urls}] if urls else []


def _without_port_53(servers: list) -> list[dict]:
    out = []
    for server in servers:
        if not isinstance(server, dict):
            continue
        urls = server.get("urls")
        urls = [urls] if isinstance(urls, str) else (urls or [])
        kept = [u for u in urls if isinstance(u, str) and not _PORT_53.search(u)]
        if kept:
            out.append({**server, "urls": kept})
    return out


async def _turn_servers() -> list[dict] | None:
    """Short-lived Cloudflare TURN credentials, or None (not configured / unreachable)."""
    if not (settings.CHAT_TURN_KEY_ID and settings.CHAT_TURN_API_TOKEN):
        return None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            res = await client.post(
                TURN_URL.format(key_id=settings.CHAT_TURN_KEY_ID),
                headers={"Authorization": f"Bearer {settings.CHAT_TURN_API_TOKEN}"},
                json={"ttl": TURN_TTL_SECONDS},
            )
        res.raise_for_status()
        servers = _without_port_53(res.json().get("iceServers") or [])
        return servers or None
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        logger.warning("TURN credentials unavailable (%s); calls fall back to STUN only", type(exc).__name__)
        return None


@router.get("/calls/ice-servers")
async def chat_ice_servers(me: User = Depends(chat_user)):
    if not call_live.calls_ready():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    await _limit(f"ice:{me.id}", 30, 600)
    turn = await _turn_servers()
    return {"ice_servers": (turn or _stun_servers()), "relay": turn is not None}


@router.post("/upload-token")
async def chat_upload_token(me: User = Depends(chat_user)):
    if not files.files_ready():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    await _limit(f"upload-token:{me.id}", 30, 600)
    return {
        "token": files.issue_upload_token(me.id, int(me.token_version or 0)),
        "upload_base": f"{files.public_api_base()}/api/chat",
        "expires_in": files.UPLOAD_TOKEN_TTL,
    }


@router.post("/attachments/links")
async def chat_attachment_links(body: LinksIn, me: User = Depends(chat_user), db: Session = Depends(get_db)):
    if not files.files_ready():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    if len(body.ids) > service.LINKS_MAX:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"At most {service.LINKS_MAX} ids")
    await _limit(f"links:{me.id}", 60, 60)
    return {"links": await _db(service.attachment_links, db, me, body.ids)}


@router.get("/directory")
async def chat_directory(
    q: Optional[str] = Query(None, max_length=100),
    limit: int = Query(20, ge=1, le=service.DIRECTORY_MAX),
    me: User = Depends(chat_user),
    db: Session = Depends(get_db),
):
    await _limit(f"dir:{me.id}", 60, 60)
    return {"items": await _db(service.directory, db, me, q, limit)}


@router.get("/presence")
async def chat_presence(
    user_ids: str = Query("", max_length=2000),
    me: User = Depends(chat_user),
    db: Session = Depends(get_db),
):
    try:
        requested = [int(x) for x in user_ids.split(",") if x.strip()][:200]
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="user_ids must be integers")
    allowed = await _db(service.same_company_user_ids, db, me, requested)
    online = await hub.online_user_ids(allowed)
    return {"online": sorted(online)}


@router.get("/conversations")
async def chat_conversations(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=service.CONVERSATION_PAGE_MAX),
    me: User = Depends(chat_user),
    db: Session = Depends(get_db),
):
    return await _db(service.list_conversations, db, me, skip, limit)


@router.post("/conversations/direct")
async def chat_start_direct(body: StartDirectIn, me: User = Depends(chat_user), db: Session = Depends(get_db)):
    await _limit(f"start:{me.id}", 30, 60)
    return await _db(service.get_or_create_direct, db, me, body.user_id)


@router.post("/conversations/team")
async def chat_start_team(body: StartTeamIn, me: User = Depends(chat_user), db: Session = Depends(get_db)):
    await _limit(f"start:{me.id}", 30, 60)
    return await _db(service.get_or_create_team, db, me, body.team_id)


@router.get("/conversations/{conversation_id}")
async def chat_conversation(conversation_id: int, me: User = Depends(chat_user), db: Session = Depends(get_db)):
    return await _db(service.conversation_summary, db, me, conversation_id)


@router.get("/conversations/{conversation_id}/messages")
async def chat_messages(
    conversation_id: int,
    before_seq: Optional[int] = Query(None, ge=1),
    after_seq: Optional[int] = Query(None, ge=0),
    limit: int = Query(service.PAGE_MAX, ge=1, le=service.PAGE_MAX),
    me: User = Depends(chat_user),
    db: Session = Depends(get_db),
):
    if before_seq is not None and after_seq is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Use before_seq or after_seq, not both")
    return await _db(service.get_messages, db, me, conversation_id, before_seq, after_seq, limit)


@router.post("/conversations/{conversation_id}/messages")
async def chat_send(
    conversation_id: int,
    payload: SendIn,
    response: Response,
    me: User = Depends(chat_user),
    db: Session = Depends(get_db),
):
    body = service.validate_body(payload.body)
    client_msg_id = service.validate_client_msg_id(payload.client_msg_id)
    await _limit(f"send:{me.id}", settings.CHAT_SEND_LIMIT_PER_10S, 10)
    message, created = await _db(service.send_message, db, me, conversation_id, body, client_msg_id)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    if created:
        recipients = await _db(service.participant_user_ids, db, me, conversation_id)
        await hub.publish(recipients, {
            "type": "message.new",
            "conversation_id": conversation_id,
            "seq": message["seq"],
            "sender_id": me.id,
        })
    return message


@router.post("/conversations/{conversation_id}/read")
async def chat_mark_read(conversation_id: int, body: ReadIn, me: User = Depends(chat_user), db: Session = Depends(get_db)):
    result = await _db(service.mark_read, db, me, conversation_id, body.seq)
    if result["advanced"]:
        recipients = await _db(service.participant_user_ids, db, me, conversation_id)
        await hub.publish(recipients, {
            "type": "conversation.read",
            "conversation_id": conversation_id,
            "user_id": me.id,
            "last_read_seq": result["last_read_seq"],
        })
    return result


@router.get("/unread")
async def chat_unread(me: User = Depends(chat_user), db: Session = Depends(get_db)):
    return {"unread_total": await _db(service.unread_total, db, me)}


@router.get("/sync")
async def chat_sync(me: User = Depends(chat_user), db: Session = Depends(get_db)):
    return await _db(service.sync_state, db, me)


@router.post("/ws-ticket")
async def chat_ws_ticket(token: Optional[str] = Depends(oauth2_scheme), me: User = Depends(chat_user)):
    await _limit(f"ticket:{me.id}", 10, 60)
    payload = decode_access_token(token, audience="crm") if token else None
    if not payload or "exp" not in payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")
    try:
        ticket = await hub.issue_ticket({
            "user_id": me.id,
            "company_id": me.company_id,
            "token_version": int(me.token_version or 0),
            "exp": int(payload["exp"]),
        })
    except RealtimeUnavailable:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Live updates are unavailable")
    return {"ticket": ticket, "expires_in": TICKET_TTL_SECONDS}
