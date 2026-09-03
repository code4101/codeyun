from __future__ import annotations

import threading
from types import SimpleNamespace

from backend.core.services import monitor as service_monitor
from backend.core.services.monitor import CRITICAL_LOCAL_COMMAND_SERVICE_NAMES, ServiceMonitor


def test_only_network_command_services_are_recovered_automatically():
    assert CRITICAL_LOCAL_COMMAND_SERVICE_NAMES == {"frpc", "nginx"}


def test_network_service_root_override_rebinds_sibling_and_nested_executables(tmp_path):
    old_sync = tmp_path / "old" / "sync"
    new_sync = tmp_path / "new" / "sync"
    old_host = old_sync / "codepc_mf"
    new_host = new_sync / "codepc_mf"
    new_frpc = new_sync / "frp" / "frpc.exe"
    old_nginx_root = old_host / "nginx"
    new_nginx_root = new_host / "nginx"
    new_nginx = new_nginx_root / "nginx.exe"
    new_frpc.parent.mkdir(parents=True)
    new_nginx.parent.mkdir(parents=True)
    new_frpc.touch()
    new_nginx.touch()

    frpc = SimpleNamespace(
        name="frpc",
        command=f'"{old_sync / "frp" / "frpc.exe"}" -c frpc.toml',
        cwd=str(old_host),
    )
    nginx = SimpleNamespace(
        name="nginx",
        command=str(old_nginx_root / "nginx.exe"),
        cwd=str(old_nginx_root),
    )

    frpc_command, frpc_cwd = service_monitor._network_service_root_override(frpc, new_host)
    nginx_command, nginx_cwd = service_monitor._network_service_root_override(nginx, new_host)

    assert str(new_frpc) in frpc_command
    assert str(new_nginx) in nginx_command
    assert frpc_cwd == str(new_host)
    assert nginx_cwd == str(new_nginx_root)


def test_service_monitor_checks_services_without_job_executor():
    calls: list[str] = []
    monitor = ServiceMonitor(lambda: calls.append("checked") or {"status": "ok"}, interval_seconds=60)

    assert monitor.check_now() == {"status": "ok"}
    assert calls == ["checked"]


def test_service_monitor_start_and_shutdown_own_thread():
    called = threading.Event()
    monitor = ServiceMonitor(lambda: called.set() or {"status": "ok"}, interval_seconds=0.01)
    monitor._interval_seconds = 0.01

    monitor.start()
    assert called.wait(1)
    monitor.shutdown()

    assert monitor._thread is None


def test_local_monitor_recovers_attendance_with_existing_monitor(monkeypatch):
    calls = []
    monkeypatch.setattr(
        service_monitor,
        "ensure_local_critical_command_services",
        lambda: calls.append("critical") or {"status": "ok"},
    )
    monkeypatch.setattr(
        "backend.core.attendance.behavior_tree_service.is_attendance_behavior_tree_service_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "backend.core.attendance.behavior_tree_service.ensure_attendance_behavior_tree_service",
        lambda: calls.append("attendance") or {"status": "already_running", "pid": 123},
    )

    result = service_monitor.ensure_local_monitored_services()

    assert calls == ["critical", "attendance"]
    assert result["attendance-behavior-tree"] == {"status": "already_running", "pid": 123}

