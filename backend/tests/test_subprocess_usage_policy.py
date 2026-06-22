from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SUBPROCESS_UTILS = BACKEND / "core" / "runtime" / "subprocess_utils.py"
PROCESS_LAUNCHER = BACKEND / "core" / "runtime" / "process_launcher.py"
ALLOWED_DIRECT_POPEN = {SUBPROCESS_UTILS}
ALLOWED_DIRECT_RUN = {SUBPROCESS_UTILS}
ALLOWED_SUBPROCESS_UTILS_IMPORTS = {PROCESS_LAUNCHER}
LOCAL_RUNTIME_SCRIPTS = [
    ROOT / "dev.py",
    ROOT / "scripts" / "codeyun_watchdog.py",
    ROOT / "scripts" / "codeyun_popup_audit.py",
    ROOT / "scripts" / "codeyun_visible_console_monitor.py",
]
SERVICE_ENTRYPOINTS_REQUIRING_NO_WINDOW_DEFAULT = {
    BACKEND / "app.py",
    BACKEND / "services" / "ocr_daemon.py",
    BACKEND / "services" / "game_window_daemon.py",
    BACKEND / "services" / "proxy_traffic_audit_daemon.py",
    BACKEND / "core" / "runtime" / "uvicorn_hidden.py",
}


def _python_files() -> list[Path]:
    files = [
        path
        for path in BACKEND.rglob("*.py")
        if "tests" not in path.parts
        and "__pycache__" not in path.parts
    ]
    files.extend(LOCAL_RUNTIME_SCRIPTS)
    return files


def _is_subprocess_call(node: ast.Call, names: set[str]) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "subprocess"
        and func.attr in names
    )


def test_backend_background_processes_use_unified_popen_wrapper():
    violations: list[str] = []
    for path in _python_files():
        if path in ALLOWED_DIRECT_POPEN:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_subprocess_call(node, {"Popen"}):
                rel = path.relative_to(ROOT).as_posix()
                violations.append(f"{rel}:{node.lineno}")

    assert not violations, "Use subprocess_utils.popen_background/python helpers instead:\n" + "\n".join(violations)


def test_backend_short_commands_hide_windows_consoles():
    violations: list[str] = []
    for path in _python_files():
        if path in ALLOWED_DIRECT_RUN:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_subprocess_call(node, {"run", "check_call", "check_output"}):
                continue
            rel = path.relative_to(ROOT).as_posix()
            violations.append(f"{rel}:{node.lineno}")

    assert not violations, "Use process_launcher.run_quiet/check_call_quiet/check_output_quiet instead:\n" + "\n".join(violations)


def test_runtime_callers_do_not_import_low_level_subprocess_utils():
    violations: list[str] = []
    for path in _python_files():
        if path in ALLOWED_SUBPROCESS_UTILS_IMPORTS or path == SUBPROCESS_UTILS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, ast.ImportFrom) and node.module == "backend.core.runtime.subprocess_utils":
                violations.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "backend.core.runtime.subprocess_utils":
                        violations.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")

    assert not violations, "Import backend.core.runtime.process_launcher instead of subprocess_utils:\n" + "\n".join(violations)


def _calls_install_child_process_no_window_default(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "install_child_process_no_window_default":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "install_child_process_no_window_default":
            return True
    return False


def test_codeyun_service_entrypoints_install_no_window_popen_default():
    violations: list[str] = []
    for path in SERVICE_ENTRYPOINTS_REQUIRING_NO_WINDOW_DEFAULT:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if not _calls_install_child_process_no_window_default(tree):
            violations.append(path.relative_to(ROOT).as_posix())

    assert not violations, (
        "Long-running CodeYun Python service entrypoints must install the process-wide "
        "no-window subprocess default:\n" + "\n".join(violations)
    )
