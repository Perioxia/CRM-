"""An `async def` endpoint runs on the event loop, so sync database work in it freezes the whole
worker (chat, calls and every other request on it). Plain `def` endpoints run in a thread and are
fine. Async endpoints that take a database session must hand that work to a thread, and are listed
here once reviewed."""
import inspect

from fastapi.routing import APIRoute, APIWebSocketRoute

from app.database import get_db
from app.main import app

REVIEWED = {
    "app.routers.billing.razorpay_webhook",
    "app.routers.chat.files.chat_download_file",
    "app.routers.chat.files.chat_upload_file",
    "app.routers.chat.rest.chat_attachment_links",
    "app.routers.chat.rest.chat_conversation",
    "app.routers.chat.rest.chat_conversations",
    "app.routers.chat.rest.chat_directory",
    "app.routers.chat.rest.chat_mark_read",
    "app.routers.chat.rest.chat_messages",
    "app.routers.chat.rest.chat_presence",
    "app.routers.chat.rest.chat_send",
    "app.routers.chat.rest.chat_start_direct",
    "app.routers.chat.rest.chat_start_team",
    "app.routers.chat.rest.chat_sync",
    "app.routers.chat.rest.chat_unread",
    "app.routers.sales.telephony.exotel_webhook",
    "app.routers.sales.whatsapp.gupshup_webhook",
}


def _async_endpoints_with_db() -> dict[str, object]:
    found = {}
    for route in app.routes:
        if not isinstance(route, (APIRoute, APIWebSocketRoute)):
            continue
        if not inspect.iscoroutinefunction(route.endpoint):
            continue
        if any(dep.call is get_db for dep in route.dependant.dependencies):
            found[f"{route.endpoint.__module__}.{route.endpoint.__name__}"] = route.endpoint
    return found


def test_async_endpoints_with_a_db_session_are_reviewed():
    unreviewed = sorted(set(_async_endpoints_with_db()) - REVIEWED)
    assert not unreviewed, (
        f"{unreviewed}: make these plain `def`, or run their database work with run_in_threadpool "
        "and add them to REVIEWED"
    )


def test_reviewed_endpoints_hand_db_work_to_a_thread():
    for name, endpoint in _async_endpoints_with_db().items():
        source = inspect.getsource(endpoint)
        assert "run_in_threadpool(" in source or "_db(" in source, f"{name} does database work on the event loop"
