"""Automated tests run with voice calls off and away from any real database, whatever the
developer's local backend/.env says (it may turn calls on for manual testing)."""
from fastapi.testclient import TestClient

from app.config import Settings, settings
from app.database import SessionLocal
from app.main import app
from app.services.chat import call_live
from tests.conftest import TestingSessionLocal


def test_calls_flag_is_off_for_tests_even_if_a_local_env_turns_it_on(tmp_path):
    local_env = tmp_path / ".env"
    local_env.write_text("CHAT_CALLS_ENABLED=true\n")
    assert Settings(_env_file=str(local_env)).CHAT_CALLS_ENABLED is False
    assert settings.CHAT_CALLS_ENABLED is False


def _recording_sweeper(monkeypatch) -> list:
    started = []

    async def fake_sweep_forever():
        started.append(True)

    monkeypatch.setattr(call_live, "sweep_forever", fake_sweep_forever)
    return started


def test_app_startup_does_not_start_the_call_sweeper(monkeypatch):
    started = _recording_sweeper(monkeypatch)
    with TestClient(app):
        pass
    assert started == []


def test_app_startup_starts_the_sweeper_when_a_test_turns_calls_on(monkeypatch):
    """Control for the test above: the recorder does see the sweeper when calls are ready."""
    started = _recording_sweeper(monkeypatch)
    monkeypatch.setattr(settings, "CHAT_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_CALLS_ENABLED", True)
    monkeypatch.setattr(settings, "CHAT_WS_PUBLIC_URL", "wss://api.chat-test.invalid/api/chat/ws")
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    with TestClient(app):
        pass
    assert started == [True]


def test_call_code_uses_the_test_database():
    from tests.conftest import engine as test_engine

    assert call_live.SessionFactory is TestingSessionLocal
    assert call_live.SessionFactory is not SessionLocal
    assert call_live.SessionFactory.kw["bind"] is test_engine
