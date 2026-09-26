"""Optional local policy hooks shared by web requests and standalone workers.

Plugins may expose an ``extensions.py`` module with named callables. Each hook
returns a value; callers define its contract and combine the results. No plugin
means no values. Import and policy errors propagate rather than disabling a
safety policy silently. Plugin package initializers must be side-effect free.
"""
from __future__ import annotations

import importlib
from functools import lru_cache
from typing import Any, Iterator

from backend.plugins.discovery import iter_backend_plugin_module_dirs


@lru_cache(maxsize=1)
def _extensions() -> tuple[Any, ...]:
    return tuple(
        importlib.import_module(f"backend.plugins.modules.{path.name}.extensions")
        for path in iter_backend_plugin_module_dirs()
        if (path / "extensions.py").is_file()
    )


def plugin_values(hook: str, *args: Any, **kwargs: Any) -> Iterator[Any]:
    """Yield each installed plugin's result for the requested policy hook."""
    for module in _extensions():
        callback = getattr(module, hook, None)
        if callable(callback):
            yield callback(*args, **kwargs)
