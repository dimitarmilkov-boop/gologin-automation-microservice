import os
import sys
import types
import asyncio
from importlib import import_module, reload
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GOLOGIN_TOKEN", "token")
os.environ.setdefault("AIOTT_API_URL", "https://aiott.test")
os.environ.setdefault("AIOTT_API_KEY", "key")
os.environ.setdefault("API_SECRET_KEY", "secret")
os.environ.setdefault("PROFILE_SYNC_INTERVAL", "900")
os.environ.setdefault("DEBUG", "false")

settings_stub = types.SimpleNamespace(profile_sync_interval=15)
config_stub = types.ModuleType("app.config")
config_stub.settings = settings_stub
sys.modules["app.config"] = config_stub

if "app.services.gologin_service" not in sys.modules:
    stub_service_module = types.ModuleType("app.services.gologin_service")
    class _StubGoLoginService:  # pragma: no cover - placeholder for type checking
        pass
    stub_service_module.GoLoginService = _StubGoLoginService
    sys.modules["app.services.gologin_service"] = stub_service_module

if "app.utils.retry" not in sys.modules:
    retry_module = types.ModuleType("app.utils.retry")
    def _identity(*args, **kwargs):
        def decorator(func):
            return func
        if args and callable(args[0]) and len(args) == 1 and not kwargs:
            return args[0]
        return decorator
    retry_module.retry_gologin_api = _identity
    retry_module.with_timeout = _identity
    sys.modules["app.utils.retry"] = retry_module


def load_sync_worker():
    sys.modules.pop("app.services.workers.sync_worker", None)
    module = import_module("app.services.workers.sync_worker")
    return reload(module)


@pytest.fixture
def module():
    return load_sync_worker()


@pytest.fixture
def mock_service():
    service = AsyncMock()
    service.sync_profiles.return_value = {"total": 2, "new": 1, "updated": 1}
    service.cleanup_stale_profiles.return_value = None
    return service


@pytest.fixture
def worker(module, mock_service):
    worker_cls = module.ProfileSyncWorker
    return worker_cls(mock_service)


@pytest.mark.asyncio
async def test_sync_iteration_success(worker, mock_service):
    await worker._sync_iteration()

    mock_service.sync_profiles.assert_awaited_once_with(force=False)
    mock_service.cleanup_stale_profiles.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_iteration_surface_exception(worker, mock_service):
    mock_service.sync_profiles.side_effect = Exception("boom")

    with pytest.raises(Exception):
        await worker._sync_iteration()


@pytest.mark.asyncio
async def test_run_loop_stops_on_flag(worker, mock_service):
    worker.sync_interval = 0.01

    async def stop_after_delay():
        await asyncio.sleep(0.03)
        worker.stop()

    task = asyncio.create_task(worker.run())
    await stop_after_delay()
    await asyncio.wait_for(task, timeout=1)

    assert mock_service.sync_profiles.await_count >= 1


@pytest.mark.asyncio
async def test_force_sync(worker, mock_service):
    result = await worker.force_sync()

    assert result["status"] == "success"
    assert mock_service.sync_profiles.await_count >= 1
    assert mock_service.cleanup_stale_profiles.await_count >= 1
