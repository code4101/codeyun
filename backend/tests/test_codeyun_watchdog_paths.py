from __future__ import annotations

import os
import tempfile
from pathlib import Path
from subprocess import CompletedProcess

import dev
from backend.core.runtime import codeyun_watchdog as codeyun_watchdog_runtime
from backend.core.runtime import management as runtime_management


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


def test_dev_setup_env_does_not_disable_watchdog_autostart(monkeypatch):
    monkeypatch.delenv("CODEYUN_WATCHDOG_AUTOSTART", raising=False)

    env, _python_executable, _npm_exec = dev.setup_env(os.fspath(codeyun_watchdog_runtime.ROOT_DIR))

    assert "CODEYUN_WATCHDOG_AUTOSTART" not in env


def test_watchdog_launcher_prefers_repo_pythonw_on_windows(monkeypatch, tmp_path):
    scripts_dir = tmp_path / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    pythonw = scripts_dir / "pythonw.exe"
    pythonw.write_text("", encoding="utf-8")

    monkeypatch.setattr(codeyun_watchdog_runtime, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(codeyun_watchdog_runtime.os, "name", "nt")
    monkeypatch.setattr(codeyun_watchdog_runtime.sys, "executable", os.fspath(scripts_dir / "python.exe"))

    assert codeyun_watchdog_runtime._resolve_watchdog_python_executable() == os.fspath(pythonw)


def test_watchdog_startup_status_reads_windows_task_xml(monkeypatch):
    xml = """<?xml version="1.0" encoding="UTF-16"?>
<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Settings><Enabled>true</Enabled></Settings>
</Task>"""

    monkeypatch.setattr(codeyun_watchdog_runtime.os, "name", "nt")
    monkeypatch.setattr(
        codeyun_watchdog_runtime,
        "_run_schtasks",
        lambda *args: CompletedProcess(["schtasks", *args], 0, stdout=xml, stderr=""),
    )

    status = codeyun_watchdog_runtime.get_codeyun_watchdog_startup_status()

    assert status["supported"] is True
    assert status["configured"] is True
    assert status["enabled"] is True
    assert status["task_name"] == "CodeYun Watchdog"


def test_enable_watchdog_startup_creates_onlogon_task(monkeypatch, tmp_path):
    calls: list[tuple[str, ...]] = []
    script = tmp_path / "scripts" / "codeyun_watchdog.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('watchdog')", encoding="utf-8")

    def fake_run(*args: str):
        calls.append(args)
        if args[:2] == ("/Query", "/TN"):
            return CompletedProcess(["schtasks", *args], 0, stdout="<Task><Settings><Enabled>true</Enabled></Settings></Task>", stderr="")
        return CompletedProcess(["schtasks", *args], 0, stdout="", stderr="")

    monkeypatch.setattr(codeyun_watchdog_runtime.os, "name", "nt")
    monkeypatch.setattr(codeyun_watchdog_runtime, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(codeyun_watchdog_runtime, "WATCHDOG_SCRIPT", script)
    monkeypatch.setattr(codeyun_watchdog_runtime, "_run_schtasks", fake_run)

    result = codeyun_watchdog_runtime.enable_codeyun_watchdog_startup()

    create_call = calls[0]
    assert create_call[:2] == ("/Create", "/TN")
    assert "CodeYun Watchdog" in create_call
    assert "/SC" in create_call
    assert "ONLOGON" in create_call
    assert "/F" in create_call
    assert result["startup"]["enabled"] is True


def test_runtime_autostart_configuration_only_supports_watchdog(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        runtime_management,
        "enable_codeyun_watchdog_startup",
        lambda: calls.append("enable") or {"status": "enabled"},
    )

    assert runtime_management.configure_builtin_runtime_item_autostart("codeyun-watchdog", True) == {"status": "enabled"}
    assert calls == ["enable"]

    try:
        runtime_management.configure_builtin_runtime_item_autostart("ocr", True)
    except runtime_management.HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("expected HTTPException")


def test_local_builtin_services_autostart_defaults_to_enabled(monkeypatch):
    calls: list[str] = []

    monkeypatch.delenv("CODEYUN_WATCHDOG_AUTOSTART", raising=False)
    monkeypatch.delenv("CODEYUN_PROXY_TRAFFIC_AUDIT_AUTOSTART", raising=False)
    monkeypatch.delenv("CODEYUN_CRITICAL_COMMAND_SERVICES_AUTOSTART", raising=False)
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
    monkeypatch.setattr(
        runtime_management,
        "ensure_local_critical_command_services",
        lambda: calls.append("critical") or {"status": "ok"},
    )

    result = runtime_management.ensure_local_builtin_services_on_startup()

    assert calls == ["watchdog", "audit", "critical"]
    assert result["codeyun-watchdog"]["status"] == "started"
    assert result["proxy-traffic-audit"]["status"] == "started"
    assert result["critical-command-services"]["status"] == "ok"


def test_local_builtin_services_autostart_can_be_disabled(monkeypatch):
    calls: list[str] = []

    monkeypatch.setenv("CODEYUN_WATCHDOG_AUTOSTART", "0")
    monkeypatch.setenv("CODEYUN_PROXY_TRAFFIC_AUDIT_AUTOSTART", "false")
    monkeypatch.setenv("CODEYUN_CRITICAL_COMMAND_SERVICES_AUTOSTART", "no")
    monkeypatch.setattr(runtime_management, "start_codeyun_watchdog", lambda: calls.append("watchdog"))
    monkeypatch.setattr(runtime_management, "start_proxy_traffic_audit", lambda: calls.append("audit"))
    monkeypatch.setattr(runtime_management, "ensure_local_critical_command_services", lambda: calls.append("critical"))

    assert runtime_management.ensure_local_builtin_services_on_startup() == {}
    assert calls == []


def test_local_builtin_services_autostart_reports_errors(monkeypatch):
    def fail_watchdog():
        raise runtime_management.CodeYunWatchdogError("boom")

    monkeypatch.delenv("CODEYUN_WATCHDOG_AUTOSTART", raising=False)
    monkeypatch.setenv("CODEYUN_PROXY_TRAFFIC_AUDIT_AUTOSTART", "0")
    monkeypatch.setenv("CODEYUN_CRITICAL_COMMAND_SERVICES_AUTOSTART", "0")
    monkeypatch.setattr(runtime_management, "start_codeyun_watchdog", fail_watchdog)

    result = runtime_management.ensure_local_builtin_services_on_startup()

    assert result["codeyun-watchdog"] == {"status": "error", "error": "boom"}


def test_fanxiu_behavior_tree_start_also_ensures_doctor_watch(monkeypatch):
    calls: list[str] = []

    class Entry:
        entry_id = "entry"

    monkeypatch.delenv("FANXIU_DOCTOR_WATCH_AUTOSTART", raising=False)
    monkeypatch.setattr(runtime_management, "_resolve_data_annotation_runtime_entry", lambda session: Entry())
    monkeypatch.setattr(
        runtime_management,
        "ensure_fanxiu_behavior_tree_service",
        lambda **kwargs: calls.append(f"bt:{kwargs['entry_id']}") or {"status": "started"},
    )
    monkeypatch.setattr(
        runtime_management,
        "_get_data_annotation_behavior_tree_status",
        lambda: {"running": True},
    )
    monkeypatch.setattr(
        runtime_management,
        "ensure_doctor_watch_background",
        lambda: calls.append("doctor") or {"ok": True, "started": True},
    )

    result = runtime_management.ensure_data_annotation_behavior_tree_service(object())

    assert calls == ["bt:entry", "doctor"]
    assert result["service"] == {"running": True}
    assert result["doctor_watch"] == {"ok": True, "started": True}


def test_fanxiu_behavior_tree_start_can_skip_doctor_watch(monkeypatch):
    calls: list[str] = []

    class Entry:
        entry_id = "entry"

    monkeypatch.setenv("FANXIU_DOCTOR_WATCH_AUTOSTART", "0")
    monkeypatch.setattr(runtime_management, "_resolve_data_annotation_runtime_entry", lambda session: Entry())
    monkeypatch.setattr(
        runtime_management,
        "ensure_fanxiu_behavior_tree_service",
        lambda **kwargs: calls.append(f"bt:{kwargs['entry_id']}") or {"status": "started"},
    )
    monkeypatch.setattr(runtime_management, "_get_data_annotation_behavior_tree_status", lambda: {"running": True})
    monkeypatch.setattr(
        runtime_management,
        "ensure_doctor_watch_background",
        lambda: (_ for _ in ()).throw(AssertionError("doctor watch should not start")),
    )

    result = runtime_management.ensure_data_annotation_behavior_tree_service(object())

    assert calls == ["bt:entry"]
    assert "doctor_watch" not in result


def test_fanxiu_behavior_tree_start_reports_doctor_watch_error(monkeypatch):
    class Entry:
        entry_id = "entry"

    monkeypatch.delenv("FANXIU_DOCTOR_WATCH_AUTOSTART", raising=False)
    monkeypatch.setattr(runtime_management, "_resolve_data_annotation_runtime_entry", lambda session: Entry())
    monkeypatch.setattr(runtime_management, "ensure_fanxiu_behavior_tree_service", lambda **kwargs: {"status": "started"})
    monkeypatch.setattr(runtime_management, "_get_data_annotation_behavior_tree_status", lambda: {"running": True})
    monkeypatch.setattr(
        runtime_management,
        "ensure_doctor_watch_background",
        lambda: (_ for _ in ()).throw(RuntimeError("watch boom")),
    )

    result = runtime_management.ensure_data_annotation_behavior_tree_service(object())

    assert result["status"] == "started"
    assert result["doctor_watch"] == {"ok": False, "started": False, "error": "watch boom"}
