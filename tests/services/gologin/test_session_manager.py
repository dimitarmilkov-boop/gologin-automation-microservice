import time
import sys
import types
from importlib import reload
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def manager(monkeypatch):
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

    dummy_gologin = types.ModuleType("gologin")
    dummy_gologin.GoLogin = MagicMock()
    sys.modules["gologin"] = dummy_gologin

    dummy_webdriver_manager = types.ModuleType("webdriver_manager.chrome")
    dummy_webdriver_manager.ChromeDriverManager = MagicMock(return_value=MagicMock(install=lambda: "chromedriver"))
    sys.modules["webdriver_manager"] = types.ModuleType("webdriver_manager")
    sys.modules["webdriver_manager.chrome"] = dummy_webdriver_manager

    import app.config as config_module

    reload(config_module)

    import app.services.gologin.session_manager as manager_module

    reload(manager_module)

    mgr = manager_module.GlobalGoLoginSessionManager.get_instance()
    mgr.local_sessions.clear()
    return mgr, manager_module.GlobalGoLoginSessionManager


def test_singleton_instance(manager):
    mgr, cls = manager
    other = cls.get_instance()
    assert mgr is other


def test_has_active_session(manager):
    mgr, _ = manager
    profile_id = "profile-1"
    mgr.local_sessions[profile_id] = {"status": "active"}

    assert mgr.has_active_session(profile_id) is True
    assert mgr.has_active_session("other") is False


def test_get_driver(manager):
    mgr, _ = manager
    profile_id = "profile-driver"
    driver = MagicMock()
    mgr.local_sessions[profile_id] = {"status": "active", "driver": driver}

    assert mgr.get_driver(profile_id) is driver
    assert mgr.get_driver("missing") is None


def test_is_session_ready_true(manager):
    mgr, _ = manager
    profile_id = "ready"
    driver = MagicMock()
    driver.current_url = "https://example.com"
    mgr.local_sessions[profile_id] = {"status": "active", "driver": driver}

    assert mgr.is_session_ready(profile_id) is True


def test_is_session_ready_false(manager):
    mgr, _ = manager
    profile_id = "not-ready"
    mgr.local_sessions[profile_id] = {"status": "inactive"}

    assert mgr.is_session_ready(profile_id) is False


def test_list_active_sessions(manager):
    mgr, _ = manager
    now = time.time()
    mgr.local_sessions["p1"] = {
        "status": "active",
        "start_time": now - 10,
        "execution_mode": "headless",
        "debugger_address": "ws://example",
        "chromium_version": "120"
    }
    mgr.local_sessions["p2"] = {"status": "inactive"}

    sessions = mgr.list_active_sessions()

    assert "p1" in sessions
    assert sessions["p1"]["execution_mode"] == "headless"
    assert "p2" not in sessions


def test_cleanup_stale_sessions(manager):
    mgr, _ = manager
    past = time.time() - 10_000
    mgr.local_sessions["stale"] = {"status": "active", "start_time": past}

    with patch.object(mgr, "stop_local_session") as stop_mock:
        stop_mock.return_value = {"status": "success"}
        mgr.cleanup_stale_sessions(max_age_hours=1)

    stop_mock.assert_called_once_with("stale", cleanup=False)
