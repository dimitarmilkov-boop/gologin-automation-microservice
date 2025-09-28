import os
import sys
import time
import types
from importlib import import_module, reload
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def proxy_modules(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/testdb")
    monkeypatch.setenv("GOLOGIN_TOKEN", "token")
    monkeypatch.setenv("AIOTT_API_URL", "https://aiott.test")
    monkeypatch.setenv("AIOTT_API_KEY", "key")
    monkeypatch.setenv("API_SECRET_KEY", "secret")

    fake_settings = MagicMock()
    fake_settings.gologin_token = "token"

    sys.modules["app.utils.retry"] = types.SimpleNamespace(
        retry_gologin_api=lambda *a, **k: (lambda f: f),
        with_timeout=lambda *a, **k: (lambda f: f)
    )

    sys.modules["proxy_manager"] = types.SimpleNamespace(RoyalProxyManager=MagicMock)

    module_proxy = import_module("app.services.gologin.proxy_updater")
    reload(module_proxy)

    monkeypatch.setattr(module_proxy, "settings", fake_settings, raising=False)
    monkeypatch.setattr(module_proxy, "logging", types.SimpleNamespace(getLogger=lambda name: MagicMock()))

    return module_proxy


@pytest.fixture
def updater(proxy_modules, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/testdb")
    monkeypatch.setenv("GOLOGIN_TOKEN", "token")
    monkeypatch.setenv("AIOTT_API_URL", "https://aiott.test")
    monkeypatch.setenv("AIOTT_API_KEY", "key")
    monkeypatch.setenv("DEBUG", "false")

    import app.config as config_module

    reload(config_module)

    return proxy_modules.GoLoginProxyUpdater("token")


@pytest.fixture
def handler(proxy_modules, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/testdb")
    monkeypatch.setenv("GOLOGIN_TOKEN", "token")
    monkeypatch.setenv("AIOTT_API_URL", "https://aiott.test")
    monkeypatch.setenv("AIOTT_API_KEY", "key")
    monkeypatch.setenv("DEBUG", "false")

    import app.config as config_module

    reload(config_module)

    proxy_manager = MagicMock()
    return proxy_modules.IntegratedCloudflareProxyHandler("token", proxy_manager), proxy_manager


@patch("app.services.gologin.proxy_updater.requests.get")
def test_get_profile_proxy_success(mock_get, updater):
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = {"proxy": {"host": "1.1.1.1", "port": 8080}}
    mock_get.return_value = mock_response

    proxy = updater.get_profile_proxy("profile")

    assert proxy["host"] == "1.1.1.1"


@patch("app.services.gologin.proxy_updater.requests.get")
def test_get_profile_proxy_failure(mock_get, updater):
    mock_response = MagicMock(status_code=500, text="error")
    mock_get.return_value = mock_response

    proxy = updater.get_profile_proxy("profile")

    assert proxy is None


@patch("app.services.gologin.proxy_updater.requests.patch")
def test_update_profile_proxy_success(mock_patch, updater):
    mock_response = MagicMock(status_code=200)
    mock_patch.return_value = mock_response

    result = updater.update_profile_proxy("profile", {"host": "1.1.1.1", "port": 8000, "username": "u", "password": "p", "country": "us", "session_id": "abc"})

    assert result is True


@patch("app.services.gologin.proxy_updater.requests.patch")
def test_update_profile_proxy_failure(mock_patch, updater):
    mock_response = MagicMock(status_code=400, text="bad")
    mock_patch.return_value = mock_response

    result = updater.update_profile_proxy("profile", {"host": "1.1.1.1", "port": 8000, "username": "u", "password": "p", "country": "us", "session_id": "abc"})

    assert result is False


def test_rotate_profile_proxy_success(updater):
    proxy_manager = MagicMock()
    proxy_manager.rotate_proxy.return_value = {
        "host": "2.2.2.2",
        "port": 9000,
        "username": "u",
        "password": "p",
        "country": "de",
        "session_id": "123"
    }
    proxy_manager.test_proxy.return_value = {"success": True, "ip": "2.2.2.2"}

    with patch.object(updater, "get_profile_proxy", return_value={"customName": "Royal-us-abc"}), \
         patch.object(updater, "update_profile_proxy", return_value=True):

        result = updater.rotate_profile_proxy("profile", proxy_manager)

    assert result["success"] is True
    assert result["new_country"] == "de"


def test_rotate_profile_proxy_failure_update(updater):
    proxy_manager = MagicMock()
    proxy_manager.rotate_proxy.return_value = {
        "host": "2.2.2.2",
        "port": 9000,
        "username": "u",
        "password": "p",
        "country": "de",
        "session_id": "123"
    }

    with patch.object(updater, "get_profile_proxy", return_value=None), \
         patch.object(updater, "update_profile_proxy", return_value=False):

        result = updater.rotate_profile_proxy("profile", proxy_manager)

    assert result["success"] is False
    assert result["error"] == "Failed to update GoLogin profile proxy"


def test_integrated_handler_termination(handler):
    handler_instance, proxy_manager = handler
    handler_instance.challenge_history["profile"] = [time.time() - 300, time.time() - 200, time.time() - 100]

    result = handler_instance.handle_persistent_challenge("profile")

    assert result["should_terminate"] is True
    assert result["success"] is False


def test_integrated_handler_rotation(handler):
    handler_instance, proxy_manager = handler
    proxy_manager.rotate_proxy.return_value = {
        "host": "3.3.3.3",
        "port": 9000,
        "username": "u",
        "password": "p",
        "country": "ca",
        "session_id": "321"
    }
    proxy_manager.test_proxy.return_value = {"success": True, "ip": "3.3.3.3"}

    with patch.object(handler_instance.gologin_updater, "get_profile_proxy", return_value=None), \
         patch.object(handler_instance.gologin_updater, "update_profile_proxy", return_value=True):

        result = handler_instance.handle_persistent_challenge("profile")

    assert result["success"] is True
    assert result["requires_restart"] is True


def test_integrated_handler_rotation_failure(handler):
    handler_instance, proxy_manager = handler
    proxy_manager.rotate_proxy.return_value = {
        "host": "3.3.3.3",
        "port": 9000,
        "username": "u",
        "password": "p",
        "country": "ca",
        "session_id": "321"
    }

    with patch.object(handler_instance.gologin_updater, "get_profile_proxy", return_value=None), \
         patch.object(handler_instance.gologin_updater, "update_profile_proxy", return_value=False):

        result = handler_instance.handle_persistent_challenge("profile")

    assert result["success"] is False
    assert result["requires_manual_intervention"] is True
