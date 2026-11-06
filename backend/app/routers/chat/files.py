"""Chat file upload/download, served from the API host directly (never through the Vercel proxy).

Authorised by short-lived purpose tokens (see services/chat/files.py), not the
session cookie. Every denial is a 404 so callers can't probe which
conversations or files exist.
"""
import logging
import os
import time
from types import SimpleNamespace
from typing import Optional

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.database import get_db
from app.routers.chat.rest import require_chat_ready
from app.routers.chat.ws import allowed_origins
from app.services.chat import files, filetypes, service
from app.services.chat.crypto import ChatCryptoUnavailable
from app.services.chat.realtime import hub
from app.tenancy import bind_tenant

logger = logging.getLogger("app.chat.files")
router = APIRouter(dependencies=[Depends(require_chat_ready)])

WRITE_CHUNK = 1024 * 1024


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _http(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


async def _db(fn, *args):
    try:
        return await run_in_threadpool(fn, *args)
    except ChatCryptoUnavailable:
        raise _http(status.HTTP_503_SERVICE_UNAVAILABLE, "Chat is not configured")


def _caller(db: Session, user_id: int, token_version: int):
    """Plain values only: the session is rolled back so later calls start a tenant-bound transaction."""
    try:
        user = service.account_is_usable(db, user_id, token_version)
        if user is None:
            return None
        return SimpleNamespace(
            id=user.id,
            company_id=user.company_id,
            token_version=int(user.token_version or 0),
            team_id=user.team_id,
        )
    finally:
        db.rollback()


async def _authorize(db: Session, user_id: int, token_version: int):
    caller = await _db(_caller, db, user_id, token_version)
    if caller is None:
        raise _not_found()
    bind_tenant(company_id=caller.company_id)
    return caller


def _open_new(path: str):
    return open(path, "xb")


async def _receive(request: Request, path: str, limit: int) -> tuple[int, bytes]:
    """Stream the body to `path` without blocking the event loop. Returns (bytes written, head)."""
    written = 0
    head = b""
    buffer = bytearray()
    handle = await anyio.to_thread.run_sync(_open_new, path)
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            written += len(chunk)
            if written > limit:
                raise _http(status.HTTP_413_CONTENT_TOO_LARGE, "File is too large")
            if len(head) < filetypes.HEAD_BYTES:
                head += chunk[: filetypes.HEAD_BYTES - len(head)]
            buffer.extend(chunk)
            if len(buffer) >= WRITE_CHUNK:
                data, buffer = bytes(buffer), bytearray()
                await anyio.to_thread.run_sync(handle.write, data)
        if buffer:
            await anyio.to_thread.run_sync(handle.write, bytes(buffer))
    finally:
        await anyio.to_thread.run_sync(handle.close)
    return written, head


@router.post("/conversations/{conversation_id}/files")
async def chat_upload_file(conversation_id: int, request: Request, response: Response, db: Session = Depends(get_db)):
    if not files.files_ready():
        raise _not_found()
    origin = (request.headers.get("origin") or "").rstrip("/")
    if origin and origin not in allowed_origins():
        raise _not_found()
    claims = files.read_upload_token(request.headers.get("x-chat-upload-token"))
    if claims is None:
        raise _not_found()
    me = await _authorize(db, *claims)
    await _db(service._my_participation, db, me, conversation_id)
    await _db(db.rollback)

    client_msg_id = service.validate_client_msg_id(request.headers.get("x-client-msg-id"))
    filename = files.sanitize_filename(
        files.decode_header_text(request.headers.get("x-file-name"), files.MAX_FILENAME_CHARS)
    )
    if filename is None:
        raise _http(status.HTTP_422_UNPROCESSABLE_CONTENT, "File name is missing or invalid")
    raw_caption = request.headers.get("x-caption")
    caption_text = files.decode_header_text(raw_caption, files.MAX_CAPTION_CHARS) if raw_caption else None
    if raw_caption and caption_text is None:
        raise _http(status.HTTP_422_UNPROCESSABLE_CONTENT, "Caption is invalid or too long")
    caption = service.validate_body(caption_text, allow_empty=True, max_chars=files.MAX_CAPTION_CHARS)

    # A retry of an upload that already went through: answer without reading the file again.
    existing = await _db(service.existing_message, db, me, conversation_id, client_msg_id)
    if existing is not None:
        return existing

    kind = filetypes.kind_for(filename, request.headers.get("content-type", ""))
    if kind is None:
        raise _http(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "This file type can't be sent")
    limit = filetypes.max_bytes(kind, settings.CHAT_MAX_DOC_BYTES, settings.CHAT_MAX_MEDIA_BYTES)
    try:
        declared = int(request.headers.get("content-length", ""))
    except ValueError:
        raise _http(status.HTTP_411_LENGTH_REQUIRED, "Content-Length is required")
    if declared <= 0:
        raise _http(status.HTTP_422_UNPROCESSABLE_CONTENT, "File is empty")
    if declared > limit:
        raise _http(status.HTTP_413_CONTENT_TOO_LARGE, f"File is larger than {limit // (1024 * 1024)} MB")

    if not await hub.allow(f"upload:{me.id}", files.UPLOADS_PER_10_MIN, 600):
        raise _http(status.HTTP_429_TOO_MANY_REQUESTS, "Too many uploads, slow down")
    if not await hub.consume(f"upload-bytes:{me.id}", declared, files.DAILY_BYTES_PER_USER, 86400):
        raise _http(status.HTTP_429_TOO_MANY_REQUESTS, "Daily upload limit reached")

    slot = files.new_stored_name()
    user_slot = f"user:{me.id}"
    if not await hub.acquire_slot(user_slot, slot, files.UPLOADS_PER_USER, files.UPLOAD_SLOT_TTL):
        raise _http(status.HTTP_429_TOO_MANY_REQUESTS, "Wait for your other uploads to finish")
    if not await hub.acquire_slot("server", slot, files.UPLOADS_PER_SERVER, files.UPLOAD_SLOT_TTL):
        await hub.release_slot(user_slot, slot)
        raise _http(status.HTTP_429_TOO_MANY_REQUESTS, "The server is busy, try again shortly")

    temp_path = None
    try:
        await anyio.to_thread.run_sync(files.ensure_dirs)
        if not await anyio.to_thread.run_sync(files.upload_headroom_ok):
            raise _http(status.HTTP_507_INSUFFICIENT_STORAGE, "Storage is full, try again later")
        from app.services.billing.limits import assert_can_upload

        await _db(assert_can_upload, db, me.company_id, declared)
        await _db(db.rollback)

        temp_path = os.path.join(files.tmp_dir(), slot)
        written, head = await _receive(request, temp_path, limit)
        if written != declared:
            raise _http(status.HTTP_400_BAD_REQUEST, "Upload was incomplete")
        if not filetypes.signature_ok(kind, head):
            raise _http(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "File content doesn't match its type")

        upload = {
            "temp_path": temp_path,
            "stored_name": slot,
            "filename": filename,
            "content_type": kind.content_type,
            "category": kind.category,
            "size": written,
        }
        message, created = await _db(service.send_message, db, me, conversation_id, caption, client_msg_id, upload)
    finally:
        files.discard(temp_path)
        await hub.release_slot(user_slot, slot)
        await hub.release_slot("server", slot)

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


@router.get("/files/{attachment_id}")
async def chat_download_file(attachment_id: int, t: Optional[str] = Query(None, max_length=2048), db: Session = Depends(get_db)):
    if not files.files_ready():
        raise _not_found()
    claims = files.read_file_token(t)
    if claims is None:
        raise _not_found()
    user_id, token_version, token_attachment_id, exp = claims
    if token_attachment_id != attachment_id:
        raise _not_found()
    me = await _authorize(db, user_id, token_version)
    att, filename = await _db(service.attachment_for_download, db, me, attachment_id)
    content_type, category, stored_name = att.content_type, att.category, att.stored_name
    await _db(db.rollback)

    path = files.final_path(stored_name)
    if not await anyio.to_thread.run_sync(os.path.isfile, path):
        logger.error("Chat attachment %s is missing its file on disk", attachment_id)
        raise _not_found()
    max_age = max(0, exp - int(time.time()))
    return FileResponse(
        path,
        media_type=content_type,
        filename=filename,
        content_disposition_type="inline" if category in filetypes.INLINE_CATEGORIES else "attachment",
        headers={
            "Cache-Control": f"private, max-age={max_age}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
