from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
if os.fspath(ROOT_DIR) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT_DIR))

from backend.core.services.launcher import python_script_service_command, run_quiet
from scripts import codeyun_popup_audit
from scripts import codeyun_watchdog


TASK_NAME = "CodeYun Stability Check"
DEFAULT_POPUP_WINDOW_HOURS = 2.0
DEFAULT_STARTUP_GRACE_SECONDS = 180.0
REPORT_DIR = Path(tempfile.gettempdir()) / "codeyun" / "stability-check"
LATEST_REPORT_PATH = REPORT_DIR / "latest.json"
HISTORY_REPORT_PATH = REPORT_DIR / "history.jsonl"
SCRIPT_PATH = ROOT_DIR / "scripts" / "codeyun_stability_check.py"


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _write_report(report: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with HISTORY_REPORT_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(report, ensure_ascii=False) + "\n")


def _health_problem(
    health: dict[str, Any],
    console_host: dict[str, Any],
    *,
    startup_grace_seconds: float,
) -> tuple[str | None, str | None]:
    if health.get("healthy"):
        return None, None

    backend_message = str((health.get("backend") or {}).get("message") or "unknown")
    frontend_message = str((health.get("frontend") or {}).get("message") or "unknown")
    message = f"health failed: backend={backend_message}; frontend={frontend_message}"

    if not console_host.get("running"):
        return message, None

    started_at = float(console_host.get("started_at") or 0.0)
    age_seconds = max(0.0, time.time() - started_at) if started_at else None
    if age_seconds is not None and age_seconds < startup_grace_seconds:
        return None, f"{message}; console host is still inside startup grace ({age_seconds:.1f}s)"
    return f"{message}; console host is alive but services are not healthy", None


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    problems: list[str] = []
    warnings: list[str] = []
    checked_at = _now_text()

    health = codeyun_watchdog.check_health(args.backend_url, args.frontend_url, args.timeout)
    console_host = codeyun_watchdog.read_console_host_status(
        max_age_seconds=max(codeyun_watchdog.DEFAULT_CONSOLE_HOST_STALE_SECONDS, float(args.watchdog_interval) * 2.5)
    )

    try:
        watchdog_processes = codeyun_watchdog.list_watchdog_processes()
    except Exception as exc:
        watchdog_processes = []
        problems.append(f"watchdog status failed: {exc}")
    if not watchdog_processes:
        problems.append("watchdog loop is not running")

    health_error, health_warning = _health_problem(
        health,
        console_host,
        startup_grace_seconds=args.startup_grace,
    )
    if health_error:
        problems.append(health_error)
    if health_warning:
        warnings.append(health_warning)

    monitor_status = None
    if args.ensure_popup_monitor:
        monitor_status = codeyun_popup_audit.ensure_monitor_running(
            min_covered_until=datetime.now() + timedelta(hours=float(args.popup_window_hours))
        )
    popup_audit = codeyun_popup_audit.audit_recent(args.popup_window_hours, monitor_status=monitor_status)
    if not popup_audit.get("coverage_valid"):
        problems.append("visible-console monitor coverage is not valid")
    if int(popup_audit.get("codeyun_events") or 0) > 0:
        problems.append(f"CodeYun service visible console events detected: {popup_audit['codeyun_events']}")
    if int(popup_audit.get("codeyun_workspace_events") or 0) > 0:
        warnings.append(
            "CodeYun workspace visible console events detected: "
            f"{popup_audit['codeyun_workspace_events']}"
        )

    status = "ok"
    if problems:
        status = "attention_required"
    elif warnings:
        status = "warning"

    report = {
        "ok": not problems,
        "status": status,
        "checked_at": checked_at,
        "root_dir": os.fspath(ROOT_DIR),
        "problems": problems,
        "warnings": warnings,
        "expectations": {
            "watchdog_running": True,
            "backend_frontend_healthy": True,
            "console_host_may_own_hot_reload": True,
            "codeyun_service_visible_console_events": 0,
            "scheduled_interval_hours": 2,
        },
        "health": health,
        "console_host": console_host,
        "watchdog": {
            "running": bool(watchdog_processes),
            "process_count": len(watchdog_processes),
            "processes": watchdog_processes,
        },
        "popup_audit": popup_audit,
        "report_path": os.fspath(LATEST_REPORT_PATH),
        "history_path": os.fspath(HISTORY_REPORT_PATH),
    }
    _write_report(report)
    return report


def _scheduled_task_command() -> str:
    command = python_script_service_command(
        SCRIPT_PATH,
        "--json",
        preferred_root=ROOT_DIR,
        executable=sys.executable,
    )
    return subprocess.list2cmdline(command)


def _run_schtasks(*args: str) -> subprocess.CompletedProcess[Any]:
    return run_quiet(
        ["schtasks", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def install_scheduled_task() -> dict[str, Any]:
    if os.name != "nt":
        return {"ok": False, "status": "unsupported", "message": "Windows scheduled tasks are only supported on Windows."}
    result = _run_schtasks(
        "/Create",
        "/TN",
        TASK_NAME,
        "/SC",
        "HOURLY",
        "/MO",
        "2",
        "/TR",
        _scheduled_task_command(),
        "/F",
    )
    ok = result.returncode == 0
    return {
        "ok": ok,
        "status": "installed" if ok else "error",
        "task_name": TASK_NAME,
        "interval_hours": 2,
        "run_command": _scheduled_task_command(),
        "stdout": str(result.stdout or "").strip(),
        "stderr": str(result.stderr or "").strip(),
    }


def uninstall_scheduled_task() -> dict[str, Any]:
    if os.name != "nt":
        return {"ok": False, "status": "unsupported", "message": "Windows scheduled tasks are only supported on Windows."}
    result = _run_schtasks("/Delete", "/TN", TASK_NAME, "/F")
    ok = result.returncode == 0
    return {
        "ok": ok,
        "status": "uninstalled" if ok else "error",
        "task_name": TASK_NAME,
        "stdout": str(result.stdout or "").strip(),
        "stderr": str(result.stderr or "").strip(),
    }


def scheduled_task_status() -> dict[str, Any]:
    if os.name != "nt":
        return {"ok": False, "supported": False, "task_name": TASK_NAME}
    result = _run_schtasks("/Query", "/TN", TASK_NAME)
    return {
        "ok": result.returncode == 0,
        "supported": True,
        "task_name": TASK_NAME,
        "configured": result.returncode == 0,
        "run_command": _scheduled_task_command(),
        "stdout": str(result.stdout or "").strip(),
        "stderr": str(result.stderr or "").strip(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a CodeYun stability and visible-console audit.")
    parser.add_argument("--backend-url", default=os.getenv("CODEYUN_WATCHDOG_BACKEND_URL", codeyun_watchdog.DEFAULT_BACKEND_URL))
    parser.add_argument("--frontend-url", default=os.getenv("CODEYUN_WATCHDOG_FRONTEND_URL", codeyun_watchdog.DEFAULT_FRONTEND_URL))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("CODEYUN_STABILITY_REQUEST_TIMEOUT", "10")))
    parser.add_argument(
        "--startup-grace",
        type=float,
        default=float(os.getenv("CODEYUN_STABILITY_STARTUP_GRACE_SECONDS", str(DEFAULT_STARTUP_GRACE_SECONDS))),
    )
    parser.add_argument(
        "--watchdog-interval",
        type=float,
        default=float(os.getenv("CODEYUN_WATCHDOG_INTERVAL_SECONDS", str(codeyun_watchdog.DEFAULT_INTERVAL_SECONDS))),
    )
    parser.add_argument(
        "--popup-window-hours",
        type=float,
        default=float(os.getenv("CODEYUN_STABILITY_POPUP_WINDOW_HOURS", str(DEFAULT_POPUP_WINDOW_HOURS))),
    )
    parser.add_argument("--no-ensure-popup-monitor", dest="ensure_popup_monitor", action="store_false")
    parser.set_defaults(ensure_popup_monitor=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-fail-exit", action="store_true")
    parser.add_argument("--install-task", action="store_true")
    parser.add_argument("--uninstall-task", action="store_true")
    parser.add_argument("--task-status", action="store_true")
    return parser


def _print_human(report: dict[str, Any]) -> None:
    print(f"CodeYun stability: {report['status']}")
    print(f"Checked at: {report['checked_at']}")
    for problem in report.get("problems") or []:
        print(f"PROBLEM: {problem}")
    for warning in report.get("warnings") or []:
        print(f"WARNING: {warning}")
    print(f"Report: {report['report_path']}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.install_task:
        result = install_scheduled_task()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.uninstall_task:
        result = uninstall_scheduled_task()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.task_status:
        result = scheduled_task_status()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    report = build_report(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    if args.no_fail_exit:
        return 0
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
