from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import GeneratorType
from typing import Any, Callable

from pyxllib.prog import (
    Action,
    BehaviorTreeRunner,
    BehaviorTreeStatus,
    Every,
    Node,
    Root,
    WithServices,
)


@dataclass(frozen=True)
class DataAnnotationRuntimeGroupSpec:
    group_id: str
    label: str
    priority: int
    preempt_same_group: bool = False


@dataclass(frozen=True)
class DataAnnotationRuntimeNodeSpec:
    node_id: str
    group_id: str
    label: str
    priority: int
    enabled: bool


class DataAnnotationRuntimeContainer:
    """Builds the dynamic data-annotation runtime tree from backend-owned config."""

    group_definitions = (
        DataAnnotationRuntimeGroupSpec("guard", "守护", 10, preempt_same_group=False),
        DataAnnotationRuntimeGroupSpec("job", "作业", 100, preempt_same_group=False),
    )

    def __init__(
        self,
        owner: Any,
        *,
        runtime_ctx: dict[str, Any],
        asset_tree_path: Path,
        stop_event: threading.Event,
        guard_override: bool | None = None,
    ) -> None:
        self.owner = owner
        self.runtime_ctx = runtime_ctx
        self.asset_tree_path = asset_tree_path
        self.stop_event = stop_event
        self.guard_override = guard_override

    def group_specs(self) -> list[DataAnnotationRuntimeGroupSpec]:
        return sorted(self.group_definitions, key=lambda item: item.priority)

    def guard_specs(self) -> list[DataAnnotationRuntimeNodeSpec]:
        return [
            DataAnnotationRuntimeNodeSpec(
                node_id=guard_id,
                group_id="guard",
                label=str(definition.get("label") or guard_id),
                priority=int(definition.get("priority") or 100),
                enabled=self._guard_enabled(guard_id),
            )
            for guard_id, definition in self.owner.guard_definitions.items()
        ]

    def _guard_enabled(self, guard_id: str) -> bool:
        if self.guard_override is False:
            return False
        if self.guard_override is True:
            item_enabled = getattr(self.owner, "_runtime_guard_item_enabled", None)
            if callable(item_enabled):
                return bool(item_enabled(guard_id))
        return bool(self.owner._runtime_guard_enabled(guard_id))

    def guard_nodes(self) -> list[Node]:
        return [
            Action(
                lambda guard_id=spec.node_id: self._run_guard_service(guard_id),
                label=spec.label,
            )
            for spec in self.guard_specs()
            if spec.enabled
        ]

    def _run_guard_service(self, guard_id: str):
        while True:
            status = self.owner._runtime_guard_service_tick(
                guard_id,
                self.runtime_ctx,
                self.asset_tree_path,
                self.stop_event,
                allow_during_task=True,
                guard_override=self.guard_override,
            )
            if status == BehaviorTreeStatus.RUNNING:
                yield 1
                continue
            return status

    def build_job_tree(self, *, action: Callable[[], Any], label: str, result_holder: dict[str, Any]) -> Root:
        def guarded_action() -> Any:
            result = action()
            if isinstance(result, GeneratorType):
                result = yield from result
            result_holder["value"] = result
            return BehaviorTreeStatus.SUCCESS

        # Same-group preemption is intentionally disabled. One job action keeps
        # its generator memory until it completes; higher-priority groups only
        # pause it for the current tick through WithServices.
        job_node = Every(
            24 * 60 * 60,
            child=Action(guarded_action, label=label),
            label=label,
        )
        return Root(WithServices(job_node, *self.guard_nodes()))

    def run_job_until_complete(
        self,
        *,
        action: Callable[[], Any],
        label: str,
        tick_seconds: float = 1.0,
        max_runtime_seconds: float | None = None,
    ) -> Any:
        result_holder: dict[str, Any] = {}
        started_at = time.monotonic()
        runner = BehaviorTreeRunner(
            self.build_job_tree(action=action, label=label, result_holder=result_holder),
            state_path=None,
            trace=0,
        )
        while True:
            self.owner._raise_if_stopped(self.stop_event)
            if max_runtime_seconds is not None and time.monotonic() - started_at > max_runtime_seconds:
                self.stop_event.set()
                raise RuntimeError(f"行为树任务超时：{label} 超过 {max_runtime_seconds:.0f} 秒")
            tick_started_at = time.monotonic()
            status = runner.run_once()
            tick_elapsed = time.monotonic() - tick_started_at
            if tick_elapsed >= 10.0:
                log = getattr(self.owner, "_log", None)
                if callable(log):
                    log("detail", f"{label}：行为树tick耗时 {tick_elapsed:.2f}s status={status}")
            if status == BehaviorTreeStatus.SUCCESS:
                return result_holder.get("value")
            if status == BehaviorTreeStatus.FAILURE:
                raise RuntimeError(f"行为树节点失败：{label}")
            self.stop_event.wait(max(0.1, float(tick_seconds or 1.0)))
