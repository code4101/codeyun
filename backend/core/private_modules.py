from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


def _iter_private_module_names(base_dir: Path) -> list[str]:
    if not base_dir.exists() or not base_dir.is_dir():
        return []
    names = []
    for path in sorted(base_dir.iterdir(), key=lambda item: item.name):
        if path.name.startswith("_") or not path.is_dir():
            continue
        if (path / "__init__.py").is_file():
            names.append(path.name)
    return names


def _import_private_module(base_dir: Path, module_name: str) -> ModuleType:
    base_text = str(base_dir.resolve(strict=False))
    inserted = False
    if base_text not in sys.path:
        sys.path.insert(0, base_text)
        inserted = True
    try:
        sys.modules.pop(module_name, None)
        return importlib.import_module(module_name)
    finally:
        if inserted:
            try:
                sys.path.remove(base_text)
            except ValueError:
                pass


def register_private_modules(app: Any, *, base_dir: Path | str | None = None) -> tuple[str, ...]:
    modules_dir = Path(base_dir) if base_dir is not None else Path(__file__).resolve().parents[2] / "private_modules"
    loaded: list[str] = []
    for module_name in _iter_private_module_names(modules_dir):
        module = _import_private_module(modules_dir, module_name)
        register = getattr(module, "register", None)
        if not callable(register):
            continue
        register(app)
        loaded.append(module_name)
    return tuple(loaded)
