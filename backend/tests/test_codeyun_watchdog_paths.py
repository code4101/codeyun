from __future__ import annotations

import os
import tempfile
from pathlib import Path

from backend.core import runtime_management
from backend.core.runtime import codeyun_watchdog


def test_codeyun_watchdog_default_paths_stay_outside_repo(monkeypatch):
    monkeypatch.delenv("CODEYUN_WATCHDOG_LOG", raising=False)
    monkeypatch.delenv("CODEYUN_WATCHDOG_LOCK", raising=False)

    log_path = codeyun_watchdog.get_codeyun_watchdog_log_path()
    lock_path = codeyun_watchdog.get_codeyun_watchdog_lock_path()
    temp_root = Path(tempfile.gettempdir()).resolve(strict=False)
    repo_root = codeyun_watchdog.ROOT_DIR.resolve(strict=False)

    assert log_path.is_relative_to(temp_root)
    assert lock_path.is_relative_to(temp_root)
    assert not log_path.is_relative_to(repo_root)
    assert not lock_path.is_relative_to(repo_root)
    assert os.fspath(log_path).endswith(os.path.join("codeyun", "codeyun-watchdog", "codeyun-watchdog.log"))
    assert os.fspath(lock_path).endswith(os.path.join("codeyun", "codeyun-watchdog", "codeyun-watchdog.pid"))


def test_codeyun_watchdog_status_uses_quick_scan_by_default(monkeypatch):
    calls: list[bool] = []

    def fake_list_processes(*, full_scan: bool = True):
        calls.append(full_scan)
        return []

    monkeypatch.setattr(codeyun_watchdog, "list_codeyun_watchdog_processes", fake_list_processes)

    status = codeyun_watchdog.get_codeyun_watchdog_status()

    assert calls == [False]
    assert status["running"] is False


def test_codeyun_watchdog_status_can_skip_related_process_details(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(codeyun_watchdog, "_read_lock_pid", lambda: 123)
    monkeypatch.setattr(
        codeyun_watchdog,
        "_watchdog_process_from_pid",
        lambda pid: calls.append(f"watchdog:{pid}") or codeyun_watchdog.CodeYunWatchdogProcess(
            pid=123,
            parent_pid=99,
            name="pythonw.exe",
            cmdline="pythonw scripts/codeyun_watchdog.py --loop",
            started_at=1.0,
        ),
    )
    monkeypatch.setattr(
        codeyun_watchdog,
        "list_codeyun_watchdog_processes",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not scan related processes")),
    )
    monkeypatch.setattr(
        codeyun_watchdog,
        "_ancestor_pids",
        lambda _pid: (_ for _ in ()).throw(AssertionError("should not read ancestor pids")),
    )

    status = codeyun_watchdog.get_codeyun_watchdog_status(
        include_startup=False,
        include_process_details=False,
    )

    assert calls == ["watchdog:123"]
    assert status["running"] is True
    assert status["pids"] == [123]
    assert status["launcher_pids"] == []
    assert status["stale_pids"] == []


def test_codeyun_watchdog_lock_pid_uses_current_temp_lock_only(monkeypatch, tmp_path):
    lock_path = tmp_path / "codeyun-watchdog.pid"
    lock_path.write_text("123", encoding="utf-8")
    monkeypatch.setenv("CODEYUN_WATCHDOG_LOCK", os.fspath(lock_path))
    monkeypatch.setattr(codeyun_watchdog.psutil, "pid_exists", lambda pid: pid == 123)

    assert codeyun_watchdog._read_lock_pid() == 123


def test_local_builtin_services_keep_proxy_audit_disabled_by_default(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(runtime_management, "is_attendance_behavior_tree_service_enabled", lambda: False)
    monkeypatch.delenv("CODEYUN_WATCHDOG_AUTOSTART", raising=False)
    monkeypatch.delenv("CODEYUN_PROXY_TRAFFIC_AUDIT_AUTOSTART", raising=False)
    monkeypatch.setenv("CODEYUN_CRITICAL_COMMAND_SERVICES_AUTOSTART", "0")
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

    assert calls == ["watchdog"]
    assert result["codeyun-watchdog"]["status"] == "started"
    assert "proxy-traffic-audit" not in result


def test_local_builtin_services_can_explicitly_enable_proxy_audit(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(runtime_management, "is_attendance_behavior_tree_service_enabled", lambda: False)
    monkeypatch.setenv("CODEYUN_WATCHDOG_AUTOSTART", "0")
    monkeypatch.setenv("CODEYUN_PROXY_TRAFFIC_AUDIT_AUTOSTART", "1")
    monkeypatch.setenv("CODEYUN_CRITICAL_COMMAND_SERVICES_AUTOSTART", "0")
    monkeypatch.setattr(
        runtime_management,
        "start_proxy_traffic_audit",
        lambda: calls.append("audit") or {"status": "started"},
    )

    result = runtime_management.ensure_local_builtin_services_on_startup()

    assert calls == ["audit"]
    assert result["proxy-traffic-audit"]["status"] == "started"


def test_local_builtin_services_autostart_can_be_disabled(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(runtime_management, "is_attendance_behavior_tree_service_enabled", lambda: False)
    monkeypatch.setenv("CODEYUN_WATCHDOG_AUTOSTART", "0")
    monkeypatch.setenv("CODEYUN_PROXY_TRAFFIC_AUDIT_AUTOSTART", "false")
    monkeypatch.setenv("CODEYUN_CRITICAL_COMMAND_SERVICES_AUTOSTART", "false")
    monkeypatch.setattr(runtime_management, "start_codeyun_watchdog", lambda: calls.append("watchdog"))
    monkeypatch.setattr(runtime_management, "start_proxy_traffic_audit", lambda: calls.append("audit"))

    assert runtime_management.ensure_local_builtin_services_on_startup() == {}
    assert calls == []


def test_local_builtin_services_autostart_reports_errors(monkeypatch):
    monkeypatch.setattr(runtime_management, "is_attendance_behavior_tree_service_enabled", lambda: False)

    def fail_watchdog():
        raise runtime_management.CodeYunWatchdogError("boom")

    monkeypatch.delenv("CODEYUN_WATCHDOG_AUTOSTART", raising=False)
    monkeypatch.setenv("CODEYUN_PROXY_TRAFFIC_AUDIT_AUTOSTART", "0")
    monkeypatch.setenv("CODEYUN_CRITICAL_COMMAND_SERVICES_AUTOSTART", "0")
    monkeypatch.setattr(runtime_management, "start_codeyun_watchdog", fail_watchdog)

    result = runtime_management.ensure_local_builtin_services_on_startup()

    assert result["codeyun-watchdog"] == {"status": "error", "error": "boom"}


def test_local_builtin_services_autostart_ensures_attendance_on_mf(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(runtime_management, "is_attendance_behavior_tree_service_enabled", lambda: True)
    monkeypatch.setattr(
        runtime_management,
        "ensure_attendance_behavior_tree_service",
        lambda: calls.append("attendance") or {"status": "already_running", "pid": 123},
    )
    monkeypatch.setenv("CODEYUN_WATCHDOG_AUTOSTART", "0")
    monkeypatch.setenv("CODEYUN_PROXY_TRAFFIC_AUDIT_AUTOSTART", "0")
    monkeypatch.setenv("CODEYUN_CRITICAL_COMMAND_SERVICES_AUTOSTART", "0")

    result = runtime_management.ensure_local_builtin_services_on_startup()

    assert calls == ["attendance"]
    assert result["attendance-behavior-tree"] == {"status": "already_running", "pid": 123}
