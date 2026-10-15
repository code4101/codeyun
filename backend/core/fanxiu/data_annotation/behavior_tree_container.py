from __future__ import annotations

import threading
import time
from contextlib import ExitStack
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


class JobExecutionBudget:
    """Total Job budget with an evidence-renewed liveness lease.

    ``max_execution_seconds`` bounds ordinary work. A Task that positively
    observes native progress (e.g. a recognised battle frame) may renew a short
    lease (<= ``max_lease_seconds``); while unexpired, the base budget does not
    stop the Job. Lost liveness ends the lease and the budget applies again.
    ``ceiling_seconds`` stays an absolute stop even for renewed leases.
    """

    def __init__(self, max_execution_seconds: float | None, *, started_at: float,
                 max_lease_seconds: float = 600.0, ceiling_seconds: float | None = None):
        self.max_execution_seconds = max_execution_seconds
        self.started_at = started_at
        self.max_lease_seconds = float(max_lease_seconds)
        if ceiling_seconds is None and max_execution_seconds is not None:
            ceiling_seconds = min(21600.0, 4.0 * float(max_execution_seconds))
        self.ceiling_seconds = ceiling_seconds
        self.lease_until: float | None = None
        self.lease_reason = ""
        self.lease_renewals = 0

    def renew(self, now: float, seconds: float, reason: str) -> float:
        seconds = float(seconds)
        if not reason or not 0 < seconds:
            raise ValueError("活性租约需要正数时长和证据原因")
        self.lease_until = now + min(seconds, self.max_lease_seconds)
        self.lease_reason = str(reason)
        self.lease_renewals += 1
        return self.lease_until

    def requires_dispatch_hold(self) -> bool:
        """A Job that ever renewed a lease had a long native action in progress.

        If such a Job is stopped by this budget, the game may still be inside
        that action (e.g. a fight); the next Job must not navigate until a
        safe boundary is verified and engineering control is resumed.
        """
        return self.lease_renewals > 0

    def exceeded(self, now: float) -> str | None:
        """None while within budget or a live lease; otherwise the stop reason."""
        if self.max_execution_seconds is None:
            return None
        elapsed = now - self.started_at
        if self.ceiling_seconds is not None and elapsed > self.ceiling_seconds:
            return f"超过绝对上限 {self.ceiling_seconds:.0f} 秒（活性租约续期 {self.lease_renewals} 次）"
        if elapsed <= self.max_execution_seconds:
            return None
        if self.lease_until is not None and now < self.lease_until:
            return None
        lease = (f"；最后活性证据「{self.lease_reason}」已过期 {now - self.lease_until:.0f} 秒"
                 if self.lease_until is not None else "；无活性租约")
        return f"超过 {self.max_execution_seconds:.0f} 秒{lease}"


@dataclass(frozen=True)
class BehaviorTreeGroupSpec:
    group_id: str
    label: str
    priority: int
    preempt_same_group: bool = False


@dataclass(frozen=True)
class BehaviorTreeNodeSpec:
    node_id: str
    group_id: str
    label: str
    priority: int
    enabled: bool


class BehaviorTreeContainer:
    """Builds the dynamic data-annotation context tree from backend-owned config."""

    group_definitions = (
        BehaviorTreeGroupSpec("guard", "守护", 10, preempt_same_group=False),
        BehaviorTreeGroupSpec("job", "作业", 100, preempt_same_group=False),
    )

    def __init__(
        self,
        owner: Any,
        *,
        execution_ctx: dict[str, Any],
        asset_tree_path: Path,
        stop_event: threading.Event,
        guard_override: bool | None = None,
    ) -> None:
        self.owner = owner
        self.execution_ctx = execution_ctx
        self.asset_tree_path = asset_tree_path
        self.stop_event = stop_event
        self.guard_override = guard_override

    def group_specs(self) -> list[BehaviorTreeGroupSpec]:
        return sorted(self.group_definitions, key=lambda item: item.priority)

    def guard_specs(self) -> list[BehaviorTreeNodeSpec]:
        return [
            BehaviorTreeNodeSpec(
                node_id=guard_id,
                group_id="guard",
                label=str(definition.get("label") or guard_id),
                priority=int(definition.get("priority") or 100),
                enabled=self._is_guard_enabled(guard_id),
            )
            for guard_id, definition in self.owner.guard_definitions.items()
        ]

    def _is_guard_enabled(self, guard_id: str) -> bool:
        if self.guard_override is False:
            return False
        if self.guard_override is True:
            item_enabled = getattr(self.owner, "_guard_item_enabled", None)
            if callable(item_enabled):
                return bool(item_enabled(guard_id))
        return bool(self.owner._is_guard_enabled(guard_id))

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
            status = self.owner._guard_service_tick(
                guard_id,
                self.execution_ctx,
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
        max_execution_seconds: float | None = None,
    ) -> Any:
        result_holder: dict[str, Any] = {}
        budget = JobExecutionBudget(max_execution_seconds, started_at=time.monotonic())
        started_at_epoch = time.time()
        runner = BehaviorTreeRunner(
            self.build_job_tree(action=action, label=label, result_holder=result_holder),
            state_path=None,
            trace=0,
        )
        # The running action renews the lease through owner.renew_job_liveness_lease.
        previous_budget = getattr(self.owner, "_active_job_budget", None)
        self.owner._active_job_budget = budget
        held = False

        def hold_dispatch(reason: str) -> str:
            # A Job that held a native long action (renewed lease) and stops
            # without a proven safe exit must not hand the game to the next Job.
            nonlocal held
            if held or not budget.requires_dispatch_hold():
                return ""
            held = True
            holder = getattr(self.owner, "hold_dispatch_after_unsafe_stop", None)
            return ("；已暂停工程派发，需核验安全边界后恢复" if callable(holder)
                    and holder(label, reason, budget.lease_reason) else "；未能暂停工程派发")

        try:
            while True:
                self.owner._raise_if_stopped(self.stop_event)
                exceeded = budget.exceeded(time.monotonic())
                if exceeded:
                    self.stop_event.set()
                    raise RuntimeError(f"行为树任务超时：{label} {exceeded}{hold_dispatch(exceeded)}")
                tick_started_at = time.monotonic()
                status = runner.run_once()
                tick_elapsed = time.monotonic() - tick_started_at
                if tick_elapsed >= 10.0:
                    log = getattr(self.owner, "_log", None)
                    if callable(log):
                        log("detail", f"{label}：行为树tick耗时 {tick_elapsed:.2f}s status={status}")
                if status == BehaviorTreeStatus.SUCCESS:
                    value = result_holder.get("value")
                    releaser = getattr(self.owner, "release_dispatch_holds_on_safe_terminal", None)
                    if callable(releaser):
                        try:
                            releaser(label, value, started_at_epoch)
                        except Exception as exc:  # noqa: BLE001 - holds stay; the Job result is unchanged
                            log = getattr(self.owner, "_log", None)
                            if callable(log):
                                log("warning", f"{label}：派发阻断解除失败，阻断保留：{type(exc).__name__}: {exc}")
                    return value
                if status == BehaviorTreeStatus.FAILURE:
                    raise RuntimeError(f"行为树节点失败：{label}")
                self.stop_event.wait(max(0.1, float(tick_seconds or 1.0)))
        except BaseException as exc:
            # Technical failures (ADB/screenshot/recognition), operator stops
            # and budget stops are all unsafe exits when a long native action
            # was in progress. The original exception propagates unchanged.
            hold_dispatch(f"{type(exc).__name__}: {exc}"[:300])
            raise
        finally:
            self.owner._active_job_budget = previous_budget
            self.close_suspended_actions(runner.root)

    @staticmethod
    def close_suspended_actions(root: Node) -> None:
        """Release this Cell's generators before another Cell can start.

        An interrupt between ticks never enters the suspended generator. Leaving
        cleanup to GC lets a retained traceback/tree cycle reset task ContextVars
        and payloads during a later Job. Close synchronously, including guards;
        ExitStack still closes the other actions if one cleanup raises.
        """
        with ExitStack() as cleanup:
            pending = [root]
            while pending:
                node = pending.pop()
                pending.extend(node.children)
                if isinstance(node, Action) and node.generator is not None:
                    cleanup.callback(node.generator.close)
                    node.generator = None
