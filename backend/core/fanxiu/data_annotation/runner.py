from __future__ import annotations

from typing import Any


_BEHAVIOR_TREE_EXECUTOR_CLASS: type[Any] | None = None


def register_behavior_tree_executor_class(runner_cls: type[Any]) -> type[Any]:
    global _BEHAVIOR_TREE_EXECUTOR_CLASS
    _BEHAVIOR_TREE_EXECUTOR_CLASS = runner_cls
    return runner_cls


def _default_behavior_tree_executor_class() -> type[Any]:
    from backend.core.fanxiu.data_annotation.behavior_tree_executor import BehaviorTreeExecutor

    return BehaviorTreeExecutor


def get_behavior_tree_executor_class() -> type[Any]:
    global _BEHAVIOR_TREE_EXECUTOR_CLASS
    if _BEHAVIOR_TREE_EXECUTOR_CLASS is None:
        _BEHAVIOR_TREE_EXECUTOR_CLASS = _default_behavior_tree_executor_class()
    return _BEHAVIOR_TREE_EXECUTOR_CLASS


def create_behavior_tree_executor() -> Any:
    return get_behavior_tree_executor_class()()

