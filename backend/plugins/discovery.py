from __future__ import annotations

from pathlib import Path

from backend.core.settings import ROOT_DIR


BACKEND_PLUGIN_MODULES_DIR = ROOT_DIR / "backend" / "plugins" / "modules"
FRONTEND_PLUGIN_MODULES_DIR = ROOT_DIR / "frontend" / "src" / "plugins" / "modules"
PLUGIN_PERMISSION_REGISTRY_FILENAME = "permissionRegistry.json"


def iter_backend_plugin_module_dirs(base_dir: Path | None = None) -> tuple[Path, ...]:
    modules_dir = base_dir or BACKEND_PLUGIN_MODULES_DIR
    if not modules_dir.exists():
        return ()
    return tuple(
        sorted(
            child
            for child in modules_dir.iterdir()
            if child.is_dir() and (child / "__init__.py").is_file()
        )
    )


def iter_frontend_plugin_module_dirs(base_dir: Path | None = None) -> tuple[Path, ...]:
    modules_dir = base_dir or FRONTEND_PLUGIN_MODULES_DIR
    if not modules_dir.exists():
        return ()
    return tuple(sorted(child for child in modules_dir.iterdir() if child.is_dir()))


def iter_plugin_permission_registry_files(base_dir: Path | None = None) -> tuple[Path, ...]:
    registry_files: list[Path] = []
    for module_dir in iter_frontend_plugin_module_dirs(base_dir):
        registry_file = module_dir / PLUGIN_PERMISSION_REGISTRY_FILENAME
        if registry_file.is_file():
            registry_files.append(registry_file)
    return tuple(sorted(registry_files))
