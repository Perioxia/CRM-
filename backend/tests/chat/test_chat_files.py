"""Chat file sharing: upload/download over purpose tokens, authorization, limits, crash safety."""
import asyncio
import base64
import os
import time
import uuid
from urllib.parse import urlsplit

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.core.chat import ChatAttachment, ChatMessage
from app.models.core.team import Team
from app.models.core.team_membership import TeamMembership
from app.routers.chat import files as files_router
from app.services.billing.seed import seed_plans
from app.services.chat import files, filetypes, service
from app.services.chat.realtime import hub
from app.services.chat.reconcile import reconcile
from tests.chat.conftest import ORIGIN, As

API_HOST = "https://api.chat-test.invalid"

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + bytes(range(256)) * 4
PDF = b"%PDF-1.7\n" + b"0" * 2000
MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 3000


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


@pytest.fixture
def files_on(chat_on, tmp_path, monkeypatch):
    root = tmp_path / "chat-files"
    monkeypatch.setattr(settings, "CHAT_FILES_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_FILES_DIR", str(root))
    monkeypatch.setattr(settings, "CHAT_WS_PUBLIC_URL", "wss://api.chat-test.invalid/api/chat/ws")
    monkeypatch.setattr(settings, "CHAT_FILES_MIN_FREE_BYTES", 0)
    return root


@pytest.fixture
def chat(org, files_on, db):
    seed_plans(db)
    sales, md = As("sales@a.test"), As("md@a.test")
    conv_id = sales.start(org["users"]["md"].id).json()["id"]
    return {"sales": sales, "md": md, "conv": conv_id, "root": files_on, "org": org}


def upload_token(caller: As) -> str:
    res = caller.post("/api/chat/upload-token")
    assert res.status_code == 200, res.text
    return res.json()["token"]


def upload(token, conv_id, data, filename="photo.jpg", content_type="image/jpeg", caption=None,
           client_msg_id=None, origin=ORIGIN, raise_errors=True):
    """Uploads without any session cookie or bearer token, like the browser talking to the API host."""
    headers = {
        "X-Chat-Upload-Token": token,
        "Content-Type": content_type,
        "X-File-Name": b64(filename),
        "X-Client-Msg-Id": client_msg_id or str(uuid.uuid4()),
    }
    if origin:
        headers["Origin"] = origin
    if caption is not None:
        headers["X-Caption"] = b64(caption)
    anon = TestClient(app, raise_server_exceptions=raise_errors)
    return anon.post(f"/api/chat/conversations/{conv_id}/files", content=data, headers=headers)


def download(url: str, **headers):
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}" == API_HOST
    return TestClient(app).get(f"{parts.path}?{parts.query}", headers=headers)


def stored(root) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.is_file()) if root.exists() else []


def temp_files(root) -> list[str]:
    tmp = root / "tmp"
    return sorted(p.name for p in tmp.iterdir()) if tmp.exists() else []


# ---- feature flag & status -------------------------------------------------------


def test_files_off_hides_every_file_endpoint(org, files_on, monkeypatch):
    monkeypatch.setattr(settings, "CHAT_FILES_ENABLED", False)
    sales = As("sales@a.test")
    assert sales.get("/api/chat/status").json()["files"] == {"enabled": False}
    assert sales.post("/api/chat/upload-token").status_code == 404
    assert sales.post("/api/chat/attachments/links", json={"ids": [1]}).status_code == 404
    token = files.issue_upload_token(org["users"]["sales"].id, 0)
    assert upload(token, 1, JPEG).status_code == 404
    link = files.file_link(org["users"]["sales"].id, 0, 1)
    assert download(link["url"]).status_code == 404


def test_status_advertises_limits_when_ready(chat):
    info = chat["sales"].get("/api/chat/status").json()["files"]
    assert info["enabled"] is True
    assert info["max_doc_bytes"] == settings.CHAT_MAX_DOC_BYTES
    assert info["max_media_bytes"] == settings.CHAT_MAX_MEDIA_BYTES
    assert ".jpg" in info["accept"] and ".svg" not in info["accept"] and ".html" not in info["accept"]


def test_relative_files_dir_is_not_ready(org, files_on, monkeypatch):
    monkeypatch.setattr(settings, "CHAT_FILES_DIR", "relative/dir")
    assert As("sales@a.test").get("/api/chat/status").json()["files"] == {"enabled": False}


# ---- happy path ---------------------------------------------------------------------


def test_photo_round_trip(chat, db):
    caption, name = "देखो यह फोटो", "साइट विज़िट.jpg"
    res = upload(upload_token(chat["sales"]), chat["conv"], JPEG, filename=name, caption=caption)
    assert res.status_code == 201, res.text
    msg = res.json()
    att = msg["attachment"]
    assert msg["body"] == caption
    assert att["category"] == "image" and att["content_type"] == "image/jpeg"
    assert att["size"] == len(JPEG) and att["filename"] == name
    assert att["url"].startswith(f"{API_HOST}/api/chat/files/{att['id']}?t=")

    got = download(att["url"])
    assert got.status_code == 200
    assert got.content == JPEG
    assert got.headers["content-type"] == "image/jpeg"
    assert got.headers["content-disposition"].startswith("inline")
    assert got.headers["x-content-type-options"] == "nosniff"
    cache = got.headers["cache-control"]
    assert cache.startswith("private, max-age=")
    assert 0 < int(cache.split("=")[1]) <= files.LINK_WINDOW + files.LINK_GRACE

    # The recipient sees it with their own link.
    items = chat["md"].get(f"/api/chat/conversations/{chat['conv']}/messages").json()["items"]
    theirs = items[-1]["attachment"]
    assert theirs["id"] == att["id"] and theirs["url"] != att["url"]
    assert download(theirs["url"]).content == JPEG

    # Filename and caption are encrypted at rest; the file sits under a random name.
    row = db.query(ChatAttachment).one()
    assert name not in row.filename_enc
    assert caption not in db.query(ChatMessage).filter(ChatMessage.id == msg["id"]).one().body_enc
    assert stored(chat["root"]) == [row.stored_name]
    assert temp_files(chat["root"]) == []


def test_team_room_upload_uses_the_callers_team(chat, db):
    """Upload auth copies a plain caller; team rooms still need team_id for membership."""
    org = chat["org"]
    team = Team(company_id=org["a"].id, name="Floor")
    db.add(team)
    db.flush()
    sales_user = org["users"]["sales"]
    sales_user.team_id = team.id
    db.add(TeamMembership(company_id=org["a"].id, team_id=team.id, user_id=sales_user.id))
    db.commit()

    opened = chat["sales"].post("/api/chat/conversations/team", json={"team_id": team.id})
    assert opened.status_code == 200, opened.text
    res = upload(
        upload_token(chat["sales"]),
        opened.json()["id"],
        PDF,
        filename="notes.pdf",
        content_type="application/pdf",
    )
    assert res.status_code == 201, res.text
    assert res.json()["attachment"]["filename"] == "notes.pdf"


def test_document_downloads_as_attachment_and_supports_range(chat):
    res = upload(upload_token(chat["sales"]), chat["conv"], PDF, filename="quote.pdf", content_type="application/pdf")
    assert res.status_code == 201, res.text
    url = res.json()["attachment"]["url"]
    full = download(url)
    assert full.headers["content-disposition"].startswith("attachment")
    part = download(url, Range="bytes=0-9")
    assert part.status_code == 206
    assert part.content == PDF[:10]


def test_video_and_generic_mime_accepted(chat):
    res = upload(upload_token(chat["sales"]), chat["conv"], MP4, filename="clip.mp4", content_type="application/octet-stream")
    assert res.status_code == 201, res.text
    assert res.json()["attachment"]["category"] == "video"
    assert res.json()["attachment"]["content_type"] == "video/mp4"


def test_caption_is_optional_and_preview_shows_category(chat):
    res = upload(upload_token(chat["sales"]), chat["conv"], JPEG)
    assert res.json()["body"] == ""
    convs = chat["md"].get("/api/chat/conversations").json()["items"]
    assert convs[0]["last_message"]["attachment"] == "image"
    assert convs[0]["last_message"]["preview"] == ""
    assert convs[0]["unread"] == 1


def test_text_messages_have_no_attachment(chat):
    assert chat["sales"].send(chat["conv"], "hello").json()["attachment"] is None


def test_retry_with_same_client_msg_id_is_idempotent(chat, db):
    token = upload_token(chat["sales"])
    cid = str(uuid.uuid4())
    first = upload(token, chat["conv"], JPEG, client_msg_id=cid)
    second = upload(token, chat["conv"], JPEG, client_msg_id=cid)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert db.query(ChatAttachment).count() == 1
    assert len(stored(chat["root"])) == 1 and temp_files(chat["root"]) == []


def test_export_includes_attachment_metadata_without_links(chat, db):
    upload(upload_token(chat["sales"]), chat["conv"], PDF, filename="quote.pdf", content_type="application/pdf")
    exported = service.export_sent_messages(db, chat["org"]["users"]["sales"])
    att = exported[-1]["attachment"]
    assert att["filename"] == "quote.pdf" and att["size"] == len(PDF)
    assert "url" not in att


# ---- authorization ----------------------------------------------------------------------


def test_platform_admin_is_forbidden_on_cookie_file_endpoints(chat):
    platform = As("platform@x.test")
    assert platform.post("/api/chat/upload-token").status_code == 403
    assert platform.post("/api/chat/attachments/links", json={"ids": [1]}).status_code == 403


def test_non_participants_get_404_everywhere(chat):
    att = upload(upload_token(chat["sales"]), chat["conv"], JPEG).json()["attachment"]
    users = chat["org"]["users"]
    for email, user in (("manager@a.test", users["manager"]), ("admin@a.test", users["admin"]), ("admin@b.test", users["b_admin"])):
        outsider = As(email)
        # upload into someone else's conversation
        assert upload(upload_token(outsider), chat["conv"], JPEG).status_code == 404, email
        # links for someone else's file are silently left out
        assert outsider.post("/api/chat/attachments/links", json={"ids": [att["id"]]}).json()["links"] == {}, email
        # even a correctly signed link for the outsider can't open it
        own_link = files.file_link(user.id, int(user.token_version or 0), att["id"])
        assert download(own_link["url"]).status_code == 404, email


def test_bad_tokens_are_404(chat, org):
    att = upload(upload_token(chat["sales"]), chat["conv"], JPEG).json()["attachment"]
    sales = org["users"]["sales"]
    good_token = urlsplit(att["url"]).query.split("t=", 1)[1]

    # token for attachment A used on attachment B
    assert TestClient(app).get(f"/api/chat/files/{att['id'] + 1}?t={good_token}").status_code == 404
    # unknown attachment id with a matching token
    assert download(files.file_link(sales.id, 0, 999999)["url"]).status_code == 404
    # missing / garbage token
    assert TestClient(app).get(f"/api/chat/files/{att['id']}").status_code == 404
    assert TestClient(app).get(f"/api/chat/files/{att['id']}?t=garbage").status_code == 404
    # expired token (issued an hour ago)
    expired, _ = files.file_token(sales.id, 0, att["id"], now=time.time() - 3600)
    assert TestClient(app).get(f"/api/chat/files/{att['id']}?t={expired}").status_code == 404
    # an upload token is not a download token, and vice versa
    upload_tok = upload_token(chat["sales"])
    assert TestClient(app).get(f"/api/chat/files/{att['id']}?t={upload_tok}").status_code == 404
    assert upload(good_token, chat["conv"], JPEG).status_code == 404
    # a normal login token is not an upload token
    assert upload(chat["sales"].token, chat["conv"], JPEG).status_code == 404


def test_file_tokens_cannot_log_in(chat):
    att = upload(upload_token(chat["sales"]), chat["conv"], JPEG).json()["attachment"]
    file_tok = urlsplit(att["url"]).query.split("t=", 1)[1]
    for tok in (file_tok, upload_token(chat["sales"])):
        anon = TestClient(app)
        assert anon.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
        assert anon.get("/api/chat/conversations", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_logout_revokes_file_links(chat, db, org):
    att = upload(upload_token(chat["sales"]), chat["conv"], JPEG).json()["attachment"]
    md_url = chat["md"].get(f"/api/chat/conversations/{chat['conv']}/messages").json()["items"][-1]["attachment"]["url"]
    md = org["users"]["md"]
    md.token_version = int(md.token_version or 0) + 1
    db.commit()
    assert download(md_url).status_code == 404
    assert download(att["url"]).status_code == 200


def test_bad_origin_is_404(chat):
    assert upload(upload_token(chat["sales"]), chat["conv"], JPEG, origin="https://evil.example").status_code == 404


# ---- type, size and abuse limits ------------------------------------------------------


@pytest.mark.parametrize("filename,content_type,data,expected", [
    ("drawing.svg", "image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'/>", 415),
    ("page.html", "text/html", b"<html></html>", 415),
    ("tool.exe", "application/octet-stream", b"MZ" + b"\x00" * 100, 415),
    ("fake.pdf", "application/pdf", b"<html><script>alert(1)</script></html>", 415),
    ("fake.jpg", "image/jpeg", PDF, 415),
    ("photo.jpg", "image/jpeg", b"", 422),
])
def test_rejected_files_leave_nothing_behind(chat, filename, content_type, data, expected):
    res = upload(upload_token(chat["sales"]), chat["conv"], data, filename=filename, content_type=content_type)
    assert res.status_code == expected, res.text
    assert stored(chat["root"]) == [] and temp_files(chat["root"]) == []
    assert hub._mem_slots.get("server", {}) == {}


def test_size_limit_per_category(chat, monkeypatch):
    monkeypatch.setattr(settings, "CHAT_MAX_DOC_BYTES", 1000)
    token = upload_token(chat["sales"])
    assert upload(token, chat["conv"], JPEG, filename="big.jpg").status_code == 413
    assert upload(token, chat["conv"], MP4, filename="clip.mp4", content_type="video/mp4").status_code == 201


def test_body_longer_than_limit_is_cut_off_while_streaming(tmp_path):
    class FakeRequest:
        async def stream(self):
            for _ in range(5):
                yield b"x" * 400

    path = str(tmp_path / "upload")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(files_router._receive(FakeRequest(), path, limit=1000))
    assert exc.value.status_code == 413


def test_invalid_filename_and_caption(chat):
    token = upload_token(chat["sales"])
    assert upload(token, chat["conv"], JPEG, filename="...").status_code == 422
    assert upload(token, chat["conv"], JPEG, filename="../").status_code == 415
    assert upload(token, chat["conv"], JPEG, caption="x" * (files.MAX_CAPTION_CHARS + 1)).status_code == 422
    traversal = upload(token, chat["conv"], JPEG, filename="../../etc/passwd.jpg")
    assert traversal.status_code == 201
    assert "/" not in traversal.json()["attachment"]["filename"]


def test_daily_byte_cap(chat, monkeypatch):
    monkeypatch.setattr(files, "DAILY_BYTES_PER_USER", len(JPEG) + 10)
    token = upload_token(chat["sales"])
    assert upload(token, chat["conv"], JPEG).status_code == 201
    assert upload(token, chat["conv"], JPEG).status_code == 429


def test_concurrent_upload_slots(chat, org):
    sales_id = org["users"]["sales"].id
    asyncio.run(hub.acquire_slot(f"user:{sales_id}", "a", files.UPLOADS_PER_USER, 60))
    asyncio.run(hub.acquire_slot(f"user:{sales_id}", "b", files.UPLOADS_PER_USER, 60))
    token = upload_token(chat["sales"])
    assert upload(token, chat["conv"], JPEG).status_code == 429
    asyncio.run(hub.release_slot(f"user:{sales_id}", "a"))
    assert upload(token, chat["conv"], JPEG).status_code == 201
    assert set(hub._mem_slots[f"user:{sales_id}"]) == {"b"}


def test_slots_expire_on_their_own():
    async def scenario():
        assert await hub.acquire_slot("t", "a", 1, 60)
        assert not await hub.acquire_slot("t", "b", 1, 60)
        hub._mem_slots["t"]["a"] = time.monotonic() - 1  # crashed upload, slot timed out
        assert await hub.acquire_slot("t", "b", 1, 60)

    asyncio.run(scenario())
    hub._mem_slots.clear()


def test_low_disk_is_507(chat, monkeypatch):
    monkeypatch.setattr(files, "disk_free_bytes", lambda: 10)
    monkeypatch.setattr(settings, "CHAT_FILES_MIN_FREE_BYTES", 1)
    res = upload(upload_token(chat["sales"]), chat["conv"], JPEG)
    assert res.status_code == 507
    assert stored(chat["root"]) == [] and temp_files(chat["root"]) == []


def test_storage_quota_is_402_and_counts_chat_files(chat, db, monkeypatch):
    from app.services.billing import limits

    upload(upload_token(chat["sales"]), chat["conv"], JPEG)
    company_id = chat["org"]["a"].id
    assert limits.storage_used_bytes(db, company_id) == len(JPEG)
    monkeypatch.setattr(limits, "storage_used_bytes", lambda _db, _cid: 10**15)
    res = upload(upload_token(chat["sales"]), chat["conv"], JPEG)
    assert res.status_code == 402
    assert len(stored(chat["root"])) == 1 and temp_files(chat["root"]) == []


def test_failed_commit_after_move_leaves_no_file(chat, db, monkeypatch):
    real_commit = db.commit

    def failing_commit():
        if any(isinstance(obj, ChatAttachment) for obj in db.identity_map.values()):
            raise RuntimeError("simulated crash at commit")
        real_commit()

    monkeypatch.setattr(db, "commit", failing_commit)
    res = upload(upload_token(chat["sales"]), chat["conv"], JPEG, raise_errors=False)
    assert res.status_code == 500
    assert stored(chat["root"]) == [] and temp_files(chat["root"]) == []


def test_missing_file_on_disk_is_404(chat):
    att = upload(upload_token(chat["sales"]), chat["conv"], JPEG).json()["attachment"]
    for name in stored(chat["root"]):
        os.unlink(chat["root"] / name)
    assert download(att["url"]).status_code == 404


# ---- links ----------------------------------------------------------------------------------


def test_links_refresh_and_limits(chat):
    att = upload(upload_token(chat["sales"]), chat["conv"], JPEG).json()["attachment"]
    links = chat["md"].post("/api/chat/attachments/links", json={"ids": [att["id"], 999999]}).json()["links"]
    assert set(links) == {str(att["id"])}
    assert download(links[str(att["id"])]["url"]).status_code == 200
    too_many = chat["md"].post("/api/chat/attachments/links", json={"ids": list(range(service.LINKS_MAX + 1))})
    assert too_many.status_code == 422


def test_links_are_stable_within_a_window_and_expire_after_grace():
    window_start = 1_800_000_000 - (1_800_000_000 % files.LINK_WINDOW)
    a, exp_a = files.file_token(1, 0, 7, now=window_start + 1)
    b, exp_b = files.file_token(1, 0, 7, now=window_start + files.LINK_WINDOW - 1)
    c, _ = files.file_token(1, 0, 7, now=window_start + files.LINK_WINDOW + 1)
    assert a == b and exp_a == exp_b
    assert c != a
    assert exp_a == window_start + files.LINK_WINDOW + files.LINK_GRACE
    assert exp_a - (window_start + 1) <= files.LINK_WINDOW + files.LINK_GRACE


# ---- reconcile -------------------------------------------------------------------------------


def test_reconcile_reports_and_repairs(chat, db):
    upload(upload_token(chat["sales"]), chat["conv"], JPEG)
    kept = upload(upload_token(chat["sales"]), chat["conv"], PDF, filename="a.pdf", content_type="application/pdf")
    root = chat["root"]
    old = time.time() - 2 * 3600

    orphan = root / ("ab" * 16)
    orphan.write_bytes(b"orphan")
    os.utime(orphan, (old, old))
    fresh_orphan = root / ("cd" * 16)
    fresh_orphan.write_bytes(b"in flight")
    stale_tmp = root / "tmp" / "leftover"
    stale_tmp.write_bytes(b"x")
    os.utime(stale_tmp, (old, old))
    missing_row = db.query(ChatAttachment).filter(ChatAttachment.id == kept.json()["attachment"]["id"]).one()
    os.unlink(root / missing_row.stored_name)

    dry = reconcile(db, apply=False)
    assert dry["orphans_found"] == 1
    assert dry["missing_files"] == [{"attachment_id": missing_row.id, "conversation_id": chat["conv"]}]
    assert orphan.exists() and stale_tmp.exists()

    done = reconcile(db, apply=True)
    assert done["orphans_quarantined"] == 1 and done["temp_files_removed"] == 1
    assert not orphan.exists() and (root / "orphans" / orphan.name).exists()
    assert fresh_orphan.exists() and not stale_tmp.exists()

    week_old = time.time() - 8 * 86400
    os.utime(root / "orphans" / orphan.name, (week_old, week_old))
    assert reconcile(db, apply=True)["expired_orphans_deleted"] == 1
    assert not (root / "orphans" / orphan.name).exists()


# ---- pure helpers ------------------------------------------------------------------------------


@pytest.mark.parametrize("filename,mime,head,category", [
    ("a.jpg", "image/jpeg", JPEG, "image"),
    ("a.png", "image/png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "image"),
    ("a.gif", "image/gif", b"GIF89a" + b"\x00" * 8, "image"),
    ("a.webp", "image/webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image"),
    ("a.heic", "image/heic", b"\x00\x00\x00\x18ftypheic", "file"),
    ("a.mov", "video/quicktime", b"\x00\x00\x00\x14ftypqt  ", "video"),
    ("a.webm", "video/webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 8, "video"),
    ("a.mp3", "audio/mpeg", b"ID3\x04" + b"\x00" * 8, "audio"),
    ("a.m4a", "audio/mp4", b"\x00\x00\x00\x20ftypM4A ", "audio"),
    ("a.ogg", "audio/ogg", b"OggS" + b"\x00" * 8, "audio"),
    ("a.wav", "audio/wav", b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio"),
    ("a.docx", "", b"PK\x03\x04" + b"\x00" * 8, "file"),
    ("a.xls", "application/vnd.ms-excel", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "file"),
    ("a.csv", "text/csv", "name,नाम\n".encode(), "file"),
])
def test_allowed_kinds(filename, mime, head, category):
    kind = filetypes.kind_for(filename, mime)
    assert kind is not None and kind.category == category
    assert filetypes.signature_ok(kind, head)


@pytest.mark.parametrize("filename,mime,head", [
    ("a.mp4", "video/mp4", b"\x00\x00\x00\x18ftypheic"),  # HEIC photo renamed to .mp4
    ("a.m4a", "audio/mp4", b"\x00\x00\x00\x14ftypqt  "),  # QuickTime video renamed to .m4a
    ("a.txt", "text/plain", b"abc\x00def"),
    ("a.png", "image/png", b"GIF89a"),
])
def test_signature_mismatch(filename, mime, head):
    assert not filetypes.signature_ok(filetypes.kind_for(filename, mime), head)


def test_kind_rejects_mismatched_declared_type():
    assert filetypes.kind_for("a.jpg", "text/html") is None
    assert filetypes.kind_for("a.svg", "image/svg+xml") is None
    assert filetypes.kind_for("noext", "image/jpeg") is None


def test_header_text_and_filename_helpers():
    assert files.decode_header_text(b64("नमस्ते"), 10) == "नमस्ते"
    assert files.decode_header_text("!!!", 10) is None
    assert files.decode_header_text(b64("x" * 11), 10) is None
    assert files.sanitize_filename("a/b\\c\x00.jpg") == "a_b_c_.jpg"
    assert files.sanitize_filename("...") is None
    assert len(files.sanitize_filename("x" * 300 + ".jpg")) == files.MAX_FILENAME_CHARS
