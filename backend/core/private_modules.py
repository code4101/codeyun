from __future__ import annotations

import importlib.util
import logging
import sys
import types
from pathlib import Path
from types import ModuleType

from fastapi import FastAPI

from backend.core.settings import ROOT_DIR

logger = logging.getLogger(__name__)

PRIVATE_MODULES_DIR = ROOT_DIR / "backend" / "private_modules"


def _iter_private_module_dirs(base_dir: Path) -> list[Path]:
    if not base_dir.exists():
        return []
    return sorted(
        child
        for child in base_dir.iterdir()
        if child.is_dir() and (child / "__init__.py").is_file()
    )


def _ensure_private_namespace(base_dir: Path) -> None:
    package_name = "backend.private_modules"
    package = sys.modules.get(package_name)

    if package is None:
        package = types.ModuleType(package_name)
        package.__path__ = [str(base_dir)]
        sys.modules[package_name] = package
        return

    existing_paths = list(getattr(package, "__path__", []))
    base_dir_str = str(base_dir)
    if base_dir_str not in existing_paths:
        existing_paths.append(base_dir_str)
        package.__path__ = existing_paths


def _load_private_module(module_dir: Path) -> ModuleType:
    module_name = f"backend.private_modules.{module_dir.name}"
    init_file = module_dir / "__init__.py"

    _ensure_private_namespace(module_dir.parent)

    spec = importlib.util.spec_from_file_location(
        module_name,
        init_file,
        submodule_search_locations=[str(module_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to create import spec for {init_file}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def register_private_modules(app: FastAPI, base_dir: Path | None = None) -> tuple[str, ...]:
    modules_dir = base_dir or PRIVATE_MODULES_DIR
    loaded_names: list[str] = []

    for module_dir in _iter_private_module_dirs(modules_dir):
        try:
            module = _load_private_module(module_dir)
        except Exception:
            logger.exception("Failed to import private module '%s'", module_dir.name)
            continue

        register = getattr(module, "register", None)
        if not callable(register):
            logger.warning(
                "Skipping private module '%s' because register(app) is missing",
                module_dir.name,
            )
            continue

        try:
            register(app)
        except Exception:
            logger.exception("Failed to register private module '%s'", module_dir.name)
            continue

        loaded_names.append(module_dir.name)

    if loaded_names:
        logger.info("Loaded private modules: %s", ", ".join(loaded_names))

    return tuple(loaded_names)
