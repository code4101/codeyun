from __future__ import annotations

import os
import tempfile
from pathlib import Path

from backend.core import codeyun_watchdog_runtime
from backend.core import runtime_management


def test_codeyun_watchdog_default_paths_stay_outside_repo(monkeypatch):
    monkeypatch.delenv("CODEYUN_WATCHDOG_LOG", raising=False)
    monkeypatch.delenv("CODEYUN_WATCHDOG_LOCK", raising=False)

    log_path = codeyun_watchdog_runtime.get_codeyun_watchdog_log_path()
    lock_path = codeyun_watchdog_runtime.get_codeyun_watchdog_lock_path()
    temp_root = Path(tempfile.gettempdir()).resolve(strict=False)
    repo_root = codeyun_watchdog_runtime.ROOT_DIR.resolve(strict=False)

    assert log_path.is_relative_to(temp_root)
    assert lock_path.is_relative_to(temp_root)
    assert not log_path.is_relative_to(repo_root)
    assert not lock_path.is_relative_to(repo_root)
    assert os.fspath(log_path).endswith(os.path.join("codeyun", "codeyun-watchdog", "codeyun-watchdog.log"))
    assert os.fspath(lock_path).endswith(os.path.join("codeyun", "codeyun-watchdog", "codeyun-watchdog.pid"))


def test_local_builtin_services_autostart_defaults_to_enabled(monkeypatch):
    calls: list[str] = []

    monkeypatch.delenv("CODEYUN_WATCHDOG_AUTOSTART", raising=False)
    monkeypatch.delenv("CODEYUN_PROXY_TRAFFIC_AUDIT_AUTOSTART", raising=False)
    monkeypatch.setattr(
        runtime_management,
        "start_codeyun_watchdog",
        lambda: calls.append("watchdog") or {"status": "started"},
    )
    monkeypatch.setattr(
        runtime_management,
        "start_proxy_traffic_audit",
        lambda: calls.append("audit") or {"status": "started"},
    )

    result = runtime_management.ensure_local_builtin_services_on_startup()

    assert calls == ["watchdog", "audit"]
    assert result["codeyun-watchdog"]["status"] == "started"
    assert result["proxy-traffic-audit"]["status"] == "started"


def test_local_builtin_services_autostart_can_be_disabled(monkeypatch):
    calls: list[str] = []

    monkeypatch.setenv("CODEYUN_WATCHDOG_AUTOSTART", "0")
    monkeypatch.setenv("CODEYUN_PROXY_TRAFFIC_AUDIT_AUTOSTART", "false")
    monkeypatch.setattr(runtime_management, "start_codeyun_watchdog", lambda: calls.append("watchdog"))
    monkeypatch.setattr(runtime_management, "start_proxy_traffic_audit", lambda: calls.append("audit"))

    assert runtime_management.ensure_local_builtin_services_on_startup() == {}
    assert calls == []


def test_local_builtin_services_autostart_reports_errors(monkeypatch):
    def fail_watchdog():
        raise runtime_management.CodeYunWatchdogError("boom")

    monkeypatch.delenv("CODEYUN_WATCHDOG_AUTOSTART", raising=False)
    monkeypatch.setenv("CODEYUN_PROXY_TRAFFIC_AUDIT_AUTOSTART", "0")
    monkeypatch.setattr(runtime_management, "start_codeyun_watchdog", fail_watchdog)

    result = runtime_management.ensure_local_builtin_services_on_startup()

    assert result["codeyun-watchdog"] == {"status": "error", "error": "boom"}
