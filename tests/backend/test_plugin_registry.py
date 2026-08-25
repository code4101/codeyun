from __future__ import annotations

import sys
import types

from backend.plugins import registry


def test_load_plugin_module_reuses_an_already_imported_local_module(tmp_path) -> None:
    module_dir = tmp_path / "example"
    module_dir.mkdir()
    init_file = module_dir / "__init__.py"
    init_file.write_text("LOAD_COUNT = 1\n", encoding="utf-8")
    module_name = "backend.plugins.modules.example"
    existing = types.ModuleType(module_name)
    existing.__file__ = str(init_file)
    sys.modules[module_name] = existing

    try:
        assert registry._load_plugin_module(module_dir) is existing
    finally:
        sys.modules.pop(module_name, None)
