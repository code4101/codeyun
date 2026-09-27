"""资源聚合作业的执行适配；业务顺序由 resource_daily 定义。

这里负责组件完成凭证、旧 Task 参数隔离和结果归一化。组件继续拥有
自己的完成判据；本适配不实现游戏动作，不为失败组件创建重试或新作业。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from inspect import isgenerator
from types import ModuleType
import time
from typing import Any, Callable

from .aggregate_progress import AggregateJobProgress
from .resource_daily_contract import resource_daily_cycle_key


def internal_component_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy child settings without granting ownership of the parent's schedule."""
    return {**{key: value for key, value in payload.items()
               if not key.startswith("__scheduler_")}, "schedule": False}


@dataclass
class ResourceDailyExecution:
    """One frozen occurrence and one receipt owner for the complete daily run.

    The business composition uses ``component`` for context-based operations,
    ``internalized`` for retired Task handlers, and ``aggregate`` for components
    which already expose their own stage boundaries. Context creation stays lazy: completed receipts perform
    no game work. A failure propagates before any subsequent component starts.
    """

    runner: Any
    ctx: Any
    payload: dict[str, Any]
    stop_event: Any
    progress: AggregateJobProgress
    moment: datetime
    domains: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    @property
    def daily_cycle(self) -> str:
        return self.moment.date().isoformat()

    def component(self, module: ModuleType, operation: Callable, label: str,
                  *, cycle: str | None = None, with_moment: bool = False,
                  synchronous: bool = False, with_deadline: bool = False):
        """Run a component under its existing ID/version and append its result.

        Eligibility belongs to the business composition: it must omit an
        ineligible component. ``cycle=None`` means the frozen daily occurrence.
        Synchronous APIs receive the existing behavior-tree driver, and an
        optional deadline comes from the parent Job's configured time budget.
        """
        def execute():
            context = self.runner._behavior_tree_context(self.ctx, stop_event=self.stop_event)
            kwargs = {"moment": self.moment} if with_moment else {}
            if with_deadline:
                timeout = self.runner._task_timeout_seconds(self.payload)
                kwargs['stop_at'] = self.started_at + timeout if timeout is not None else float('inf')
            if synchronous:
                from ..debug_eval import BehaviorTreeDebugContext
                driver = BehaviorTreeDebugContext(
                    self.runner, self.ctx, self.stop_event, readonly=False)
                return operation(context, driver.run, **kwargs)
            return (yield from operation(context, **kwargs))

        result = yield from self.progress.run(
            module.STAGE_ID, self.daily_cycle if cycle is None else cycle, execute,
            version=getattr(module, "STAGE_VERSION", "1"),
        )
        self.domains.append({"domain": label, "result": result})
        return result

    def internalized(self, stages):
        """Preserve legacy component settings while retaining one Scheduler owner."""
        from backend.core.fanxiu.data_annotation.jobs import (
            get_fanxiu_data_annotation_task_cell_definition,
        )

        internalized = self.payload.get("internalized_jobs") or {}
        for stage in stages:
            if stage.monday_only and self.moment.weekday() != 0:
                continue
            definition = get_fanxiu_data_annotation_task_cell_definition(stage.task_type)
            if definition is None:
                raise RuntimeError(f"资源_每日处理：缺少内部任务 {stage.task_type}")
            child_payload = internal_component_payload(
                (internalized.get(stage.task_id) or {}).get("payload") or {},
            )

            def execute_child():
                result = definition.handler(self.runner, self.ctx, child_payload, self.stop_event)
                if isgenerator(result):
                    result = yield from result
                if result == "success":
                    return {"result": "success", "component": stage.label}
                if not isinstance(result, dict) or result.get("result") != "success":
                    raise RuntimeError(f"{stage.label} 未取得业务完成终态：{result!r}")
                return {key: value for key, value in result.items()
                        if key not in {"backpack_debug", "backpack_debug_after"}}

            result = yield from self.progress.run(
                stage.task_id, resource_daily_cycle_key(stage, self.moment), execute_child,
            )
            self.domains.append({"domain": stage.label, "result": result})

    def aggregate(self, operation):
        """Adapt a Task aggregate while retaining the parent's stage receipts.

        The operation owns stage order and returns a ``domains`` result. It
        receives a stage_executor callback, so already completed stages skip
        lazily and failures stop the aggregate before the next stage begins.
        """
        def run_stage(stage_id, execute):
            return (yield from self.progress.run(stage_id, self.daily_cycle, execute))

        result = yield from operation(
            self.runner, self.ctx, {**self.payload, "schedule": False}, self.stop_event,
            stage_executor=run_stage,
        )
        self.domains.extend(result["domains"])
        return result
