from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SUBPROCESS_UTILS = BACKEND / "core" / "runtime" / "subprocess_utils.py"
ALLOWED_DIRECT_POPEN = {SUBPROCESS_UTILS}
ALLOWED_DIRECT_RUN = {SUBPROCESS_UTILS}
HIDDEN_HELPERS = {
    "hidden_subprocess_kwargs",
    "_hidden_process_kwargs",
    "build_background_popen_kwargs",
}


def _python_files() -> list[Path]:
    return [
        path
        for path in BACKEND.rglob("*.py")
        if "tests" not in path.parts
        and "__pycache__" not in path.parts
    ]


def _is_subprocess_call(node: ast.Call, names: set[str]) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "subprocess"
        and func.attr in names
    )


def _helper_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return _helper_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _has_hidden_kwargs(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if keyword.arg is None and _helper_name(keyword.value) in HIDDEN_HELPERS:
            return True
    return False


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
            if _has_hidden_kwargs(node):
                continue
            rel = path.relative_to(ROOT).as_posix()
            violations.append(f"{rel}:{node.lineno}")

    assert not violations, "Backend subprocess short commands must use hidden_subprocess_kwargs():\n" + "\n".join(violations)
