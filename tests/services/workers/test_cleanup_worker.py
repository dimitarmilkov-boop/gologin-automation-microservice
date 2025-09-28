import os
import sys
import types
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GOLOGIN_TOKEN", "token")
os.environ.setdefault("AIOTT_API_URL", "https://aiott.test")
os.environ.setdefault("AIOTT_API_KEY", "key")
os.environ.setdefault("API_SECRET_KEY", "secret")
os.environ.setdefault("DEBUG", "false")

settings_stub = types.SimpleNamespace(
    database_url="sqlite:///:memory:",
    database_pool_size=5,
    database_max_overflow=5,
    debug=False
)
config_stub = types.ModuleType("app.config")
config_stub.settings = settings_stub
sys.modules["app.config"] = config_stub

if "app.services.gologin_service" not in sys.modules:
    stub = types.ModuleType("app.services.gologin_service")
    stub.GoLoginService = object  # placeholder
    sys.modules["app.services.gologin_service"] = stub

# Stub app.database before importing cleanup worker
if "app.database" not in sys.modules:
    database_stub = types.ModuleType("app.database")
    database_stub.SessionLocal = MagicMock()
    database_stub.Base = MagicMock()
    sys.modules["app.database"] = database_stub

models_stub = types.ModuleType("app.models")
class AuthorizationSessionStub:
    status = MagicMock()
    started_at = MagicMock()

AuthorizationSessionStub.status.__eq__.return_value = MagicMock()
AuthorizationSessionStub.started_at.__lt__.return_value = MagicMock()
AuthorizationSessionStub.started_at.__gt__.return_value = MagicMock()

models_stub.AuthorizationSession = AuthorizationSessionStub
sys.modules["app.models"] = models_stub

from app.services.workers.cleanup_worker import CleanupWorker
from app.utils.exceptions import DatabaseConnectionException


def _make_session(stale_sessions):
    session = MagicMock()
    query = MagicMock()
    query.filter.return_value.all.return_value = stale_sessions
    session.query.return_value = query
    return session, query


@pytest.fixture
def stale_session():
    sess = MagicMock()
    sess.id = 1
    sess.account_id = "acct"
    sess.api_app = "AIOTT1"
    sess.started_at = datetime.utcnow() - timedelta(hours=3)
    return sess


@pytest.mark.asyncio
async def test_cleanup_iteration_marks_timeout(monkeypatch, stale_session):
    session, query = _make_session([stale_session])
    monkeypatch.setattr("app.services.workers.cleanup_worker.SessionLocal", lambda: session)

    worker = CleanupWorker()
    await worker._cleanup_iteration()

    assert stale_session.status == "timeout"
    assert session.commit.called
    assert session.close.called


@pytest.mark.asyncio
async def test_cleanup_iteration_no_stale(monkeypatch):
    session, query = _make_session([])
    monkeypatch.setattr("app.services.workers.cleanup_worker.SessionLocal", lambda: session)

    worker = CleanupWorker()
    await worker._cleanup_iteration()

    session.commit.assert_not_called()
    assert session.close.called


@pytest.mark.asyncio
async def test_cleanup_iteration_database_error(monkeypatch):
    session = MagicMock()
    session.query.side_effect = Exception("db down")
    monkeypatch.setattr("app.services.workers.cleanup_worker.SessionLocal", lambda: session)

    worker = CleanupWorker()

    with pytest.raises(DatabaseConnectionException):
        await worker._cleanup_iteration()

    session.rollback.assert_called_once()
    session.close.assert_called_once()


@pytest.mark.asyncio
async def test_force_cleanup(monkeypatch, stale_session):
    session, query = _make_session([stale_session])
    monkeypatch.setattr("app.services.workers.cleanup_worker.SessionLocal", lambda: session)

    worker = CleanupWorker()
    result = await worker.force_cleanup()

    assert result["status"] == "success"
    assert session.commit.called


@pytest.mark.asyncio
async def test_get_cleanup_stats(monkeypatch):
    pending_query = MagicMock()
    pending_query.filter.return_value.count.return_value = 3

    timeout_query = MagicMock()
    timeout_query.filter.return_value.count.return_value = 2

    completed_query = MagicMock()
    completed_query.filter.return_value.count.return_value = 5

    recent_query = MagicMock()
    recent_query.filter.return_value.count.return_value = 4

    session = MagicMock()
    session.query.side_effect = [pending_query, timeout_query, completed_query, recent_query]
    monkeypatch.setattr("app.services.workers.cleanup_worker.SessionLocal", lambda: session)

    worker = CleanupWorker()
    stats = await worker.get_cleanup_stats()

    assert stats["pending_sessions"] == 3
    assert stats["timeout_sessions"] == 2
    assert stats["completed_sessions"] == 5
    assert stats["recent_sessions_24h"] == 4
    session.close.assert_called_once()
