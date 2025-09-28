import os
import sys
import types
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import psutil

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GOLOGIN_TOKEN", "token")
os.environ.setdefault("AIOTT_API_URL", "https://aiott.test")
os.environ.setdefault("AIOTT_API_KEY", "key")
os.environ.setdefault("API_SECRET_KEY", "secret")
os.environ.setdefault("MAX_CONCURRENT_PROFILES", "10")

config_stub = types.ModuleType("app.config")
config_stub.settings = MagicMock(max_concurrent_profiles=10)
sys.modules.setdefault("app.config", config_stub)

from app.services.workers.monitor_worker import MonitorWorker, settings as monitor_settings

monitor_settings.max_concurrent_profiles = 10


class ColumnStub:
    def __eq__(self, other):
        return MagicMock()

    def __gt__(self, other):
        return MagicMock()

    def __lt__(self, other):
        return MagicMock()


class AuthorizationSessionStub:
    status = ColumnStub()
    started_at = ColumnStub()


class ProfileStub:
    status = ColumnStub()


def _ensure_stubs():
    monitor_module.AuthorizationSession = AuthorizationSessionStub
    monitor_module.Profile = ProfileStub
    monitor_module.settings = MagicMock(max_concurrent_profiles=10)
    monitor_module.app = MagicMock()


_ensure_stubs()


def refresh_monitor_stubs():
    monitor_module.AuthorizationSession = AuthorizationSessionStub
    monitor_module.Profile = ProfileStub
    monitor_module.settings = MagicMock(max_concurrent_profiles=10)
    monitor_module.app = MagicMock()


@pytest.fixture(autouse=True)
def _refresh_stubs():
    refresh_monitor_stubs()
    yield
    refresh_monitor_stubs()


@pytest.fixture
def worker():
    return MonitorWorker()


@pytest.fixture
def metrics(worker):
    return {
        "total_profiles": 10,
        "pending_sessions": 5,
        "active_profiles": 3,
        "auth_success_rate_1h": 0.7,
        "auth_failure_rate_1h": 0.3,
        "total_sessions_1h": 20,
        "successful_sessions_1h": 14,
        "failed_sessions_1h": 6,
        "memory_usage_percent": 40.0,
        "memory_available_gb": 8.0,
        "cpu_usage_percent": 20.0,
        "max_concurrent_profiles": 10,
        "profile_utilization_percent": 30.0,
        "collected_at": datetime.utcnow()
    }


@pytest.mark.asyncio
async def test_collect_metrics(monkeypatch, worker):
    with patch.object(monitor_module, "logger"):
        session = MagicMock()

        profile_query = MagicMock()
        profile_query.filter.return_value.count.return_value = 10

        pending_query = MagicMock()
        pending_query.filter.return_value.count.return_value = 4

        recent_query = MagicMock()
        recent_query.filter.return_value.all.return_value = []

        session.query.side_effect = [profile_query, pending_query, recent_query]
        with patch.object(monitor_module, "SessionLocal", return_value=session), \
             patch("psutil.virtual_memory", lambda: MagicMock(percent=50, available=4 * (1024**3))), \
             patch("psutil.cpu_percent", lambda interval=1: 30.0), \
             patch.object(monitor_module, "app", MagicMock(state=MagicMock(profile_manager=MagicMock(get_active_profiles_count=MagicMock(return_value=2))))):

            metrics = await worker._collect_metrics()

    assert metrics["total_profiles"] == 10
    assert metrics["pending_sessions"] == 4


@pytest.mark.asyncio
async def test_collect_metrics_db_failure(monkeypatch, worker):
    session = MagicMock()
    session.query.side_effect = Exception("db error")
    monkeypatch.setattr("app.services.workers.monitor_worker.SessionLocal", lambda: session)

    with pytest.raises(Exception):
        await worker._collect_metrics()

    session.close.assert_called_once()


@pytest.mark.asyncio
async def test_check_thresholds_triggers_alerts(worker, metrics):
    metrics["auth_failure_rate_1h"] = 0.6
    metrics["memory_usage_percent"] = 90
    metrics["pending_sessions"] = 200
    metrics["profile_utilization_percent"] = 95

    with patch.object(monitor_module, "logger") as mock_logger:
        mock_logger.warning = MagicMock()
        await worker._check_thresholds(metrics)

        assert mock_logger.warning.call_count == 4
        alert_types = {call.kwargs["alert_type"] for call in mock_logger.warning.call_args_list}
        assert {"high_failure_rate", "high_memory_usage", "high_pending_sessions", "high_profile_utilization"} == alert_types


@pytest.mark.asyncio
async def test_check_thresholds_ok(worker, metrics):
    metrics["auth_failure_rate_1h"] = 0.1
    metrics["memory_usage_percent"] = 50
    metrics["pending_sessions"] = 10
    metrics["profile_utilization_percent"] = 20

    with patch.object(monitor_module, "logger") as mock_logger:
        mock_logger.warning = MagicMock()
        mock_logger.debug = MagicMock()
        await worker._check_thresholds(metrics)

        mock_logger.warning.assert_not_called()
        mock_logger.debug.assert_called_once()
        assert mock_logger.debug.call_args[0][0] == "monitor_worker.all_thresholds_ok"


@pytest.mark.asyncio
async def test_current_metrics(monkeypatch, worker, metrics):
    async def fake_collect():
        return metrics

    monkeypatch.setattr(worker, "_collect_metrics", fake_collect)

    result = await worker.get_current_metrics()
    assert result["status"] == "success"
    assert result["metrics"] == metrics


def test_update_thresholds(worker):
    result = worker.update_thresholds({"failed_auth_rate": 0.3})

    assert result["status"] == "success"
    assert worker.alert_thresholds["failed_auth_rate"] == 0.3

    error = worker.update_thresholds({"failed_auth_rate": -1})
    assert error["status"] == "error"
