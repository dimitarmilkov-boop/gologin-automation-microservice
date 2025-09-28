import asyncio
import sys
import types
from importlib import reload
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.exceptions import ConcurrentProfileLimitException, DatabaseConnectionException


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/testdb")
    monkeypatch.setenv("GOLOGIN_TOKEN", "token")
    monkeypatch.setenv("AIOTT_API_URL", "https://aiott.test")
    monkeypatch.setenv("AIOTT_API_KEY", "key")
    monkeypatch.setenv("DEBUG", "false")

    dummy_retry = types.ModuleType("app.utils.retry")

    def _identity_decorator(*args, **kwargs):
        def wrapper(func):
            return func
        if callable(args[0]) and not kwargs and len(args) == 1:
            return args[0]
        return wrapper

    dummy_retry.retry_gologin_api = _identity_decorator
    dummy_retry.with_timeout = _identity_decorator
    sys.modules["app.utils.retry"] = dummy_retry

    import app.config as config_module

    reload(config_module)

    import app.services.gologin.service as service_module

    reload(service_module)

    svc = service_module.GoLoginService()
    svc.client = AsyncMock()
    return svc


@pytest.fixture
def mock_settings(monkeypatch):
    from app.config import Settings

    MonkeySettings = Settings

    def _settings_factory():
        return MonkeySettings(
            host="0.0.0.0",
            port=8000,
            environment="test",
            debug=False,
            database_url="sqlite://",
            gologin_token="token",
            aiott_api_url="https://aiott.test",
            aiott_api_key="key"
        )

    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("GOLOGIN_TOKEN", "token")
    monkeypatch.setenv("AIOTT_API_URL", "https://aiott.test")
    monkeypatch.setenv("AIOTT_API_KEY", "key")

    monkeypatch.setattr("app.config.settings", _settings_factory())
    return _settings_factory()


@pytest.mark.asyncio
async def test_start_profile_reuses_active(service):
    service.active_profiles = {
        "profile-1": {"profile_id": "profile-1", "port": 4000}
    }

    result = await service.start_profile("profile-1")

    assert result["port"] == 4000
    service.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_start_profile_enforces_limit(service):
    service.max_concurrent = 1
    service.profile_semaphore = asyncio.Semaphore(service.max_concurrent)
    service.active_profiles = {"p1": {}}

    with pytest.raises(ConcurrentProfileLimitException):
        await service.start_profile("p2")


@pytest.mark.asyncio
async def test_start_profile_success(service):
    service.client.post.return_value = AsyncMock(status_code=200, json=MagicMock(return_value={"port": 3500}))

    result = await service.start_profile("profile-new")

    assert result["port"] == 3500
    assert "profile-new" in service.active_profiles
    service.client.post.assert_called_once()


@pytest.mark.asyncio
async def test_stop_profile_handles_missing(service):
    service.active_profiles = {}

    assert await service.stop_profile("not-started") is True
    service.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_get_profile_by_name_success(monkeypatch, service):
    mock_db = MagicMock()
    profile_obj = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = profile_obj

    result = await service.get_profile_by_name("1234", mock_db)

    assert result is profile_obj


@pytest.mark.asyncio
async def test_get_profile_by_name_error(monkeypatch, service):
    mock_db = MagicMock()
    mock_db.query.side_effect = RuntimeError("db down")

    with pytest.raises(DatabaseConnectionException):
        await service.get_profile_by_name("1234", mock_db)


@pytest.mark.asyncio
async def test_sync_profiles_creates_and_updates(monkeypatch, service):
    service.get_profiles = AsyncMock(return_value=[
        {"id": "1", "name": "1111", "proxy": {}, "browserType": "chrome"},
        {"id": "2", "name": "2222", "proxy": {}, "browserType": "firefox"}
    ])

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.side_effect = [None, MagicMock()]

    with patch("app.services.gologin.service.SessionLocal", return_value=mock_db):
        result = await service.sync_profiles()

    assert result["total"] == 2
    assert result["new"] == 1
    assert result["updated"] == 1
    assert mock_db.add.called
    assert mock_db.commit.called
