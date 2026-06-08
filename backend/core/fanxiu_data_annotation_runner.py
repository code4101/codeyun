from __future__ import annotations

from typing import Any


_RUNTIME_RUNNER_CLASS: type[Any] | None = None


def register_fanxiu_runtime_runner_class(runner_cls: type[Any]) -> type[Any]:
    global _RUNTIME_RUNNER_CLASS
    _RUNTIME_RUNNER_CLASS = runner_cls
    return runner_cls


def _default_fanxiu_runtime_runner_class() -> type[Any]:
    from backend.core.fanxiu_data_annotation_runtime_runner import DataAnnotationRuntimeRunner

    return DataAnnotationRuntimeRunner


def get_fanxiu_runtime_runner_class() -> type[Any]:
    global _RUNTIME_RUNNER_CLASS
    if _RUNTIME_RUNNER_CLASS is None:
        _RUNTIME_RUNNER_CLASS = _default_fanxiu_runtime_runner_class()
    return _RUNTIME_RUNNER_CLASS


def create_fanxiu_runtime_runner() -> Any:
    return get_fanxiu_runtime_runner_class()()
