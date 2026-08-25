from __future__ import annotations

from typing import Any


_BEHAVIOR_TREE_RUNTIME_RUNNER_CLASS: type[Any] | None = None


def register_behavior_tree_runtime_runner_class(runner_cls: type[Any]) -> type[Any]:
    global _BEHAVIOR_TREE_RUNTIME_RUNNER_CLASS
    _BEHAVIOR_TREE_RUNTIME_RUNNER_CLASS = runner_cls
    return runner_cls


def _default_behavior_tree_runtime_runner_class() -> type[Any]:
    from backend.core.fanxiu.data_annotation.behavior_tree_runtime import BehaviorTreeRuntimeRunner

    return BehaviorTreeRuntimeRunner


def get_behavior_tree_runtime_runner_class() -> type[Any]:
    global _BEHAVIOR_TREE_RUNTIME_RUNNER_CLASS
    if _BEHAVIOR_TREE_RUNTIME_RUNNER_CLASS is None:
        _BEHAVIOR_TREE_RUNTIME_RUNNER_CLASS = _default_behavior_tree_runtime_runner_class()
    return _BEHAVIOR_TREE_RUNTIME_RUNNER_CLASS


def create_behavior_tree_runtime_runner() -> Any:
    return get_behavior_tree_runtime_runner_class()()

