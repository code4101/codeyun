"""Persistent business completion receipts, independent of a Cell's execution steps."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
    read_scheduler_job_progress,
    record_scheduler_job_stage,
)
from backend.core.fanxiu.data_annotation.subtask_execution import observe_subtask, subtask_node_id, subtask_log_context


@dataclass
class AggregateJobProgress:
    """Re-observe an unfinished component; reuse only its verified terminal result.

    ``operation`` is a fresh generator factory, never a persisted generator or
    GUI cursor. The caller chooses a business cycle and changes ``version`` when
    a component's completion contract changes. Side effects before a failed
    receipt write are recovered by the component's own idempotent observation.
    """

    task_id: str
    attempt_id: str
    scheduler_state_path: Path | None = None
    log: Callable[[str], Any] | None = None
    business_time: str = ""

    def run(self, stage_id: str, cycle_key: str, operation: Callable,
            *, version: str = "1", label: str = ""):
        if not self.attempt_id:
            raise ValueError("聚合作业必须携带当前 Scheduler attempt_id")
        previous = read_scheduler_job_progress(
            self.task_id, cycle_key, scheduler_state_path=self.scheduler_state_path,
        ).get(stage_id, {})
        if previous.get("status") == "complete" and str(previous.get("stage_version")) == version:
            if self.log:
                with subtask_log_context(self.task_id, self.attempt_id, subtask_node_id(self.task_id, cycle_key, stage_id)):
                    self.log(f"{label or stage_id}：本周期已完成，跳过（{cycle_key}）")
            return previous["result"]
        try:
            with observe_subtask(self.task_id, self.attempt_id,
                                 subtask_node_id(self.task_id, cycle_key, stage_id), label or stage_id,
                                 log=self.log, scheduler_state_path=self.scheduler_state_path,
                                 business_time=self.business_time):
                result = yield from operation()
                if result is None:
                    raise RuntimeError(f"{stage_id} 未返回业务完成凭证")
                if isinstance(result, dict) and result.get("outcome") == "partial_safe":
                    raise RuntimeError(f"{stage_id} 仍有未处理项，不能标记完成：{result}")
        except Exception as exc:
            record_scheduler_job_stage(
                self.task_id, cycle_key, stage_id, stage_version=version,
                status="failed", result={"error": str(exc)[:2000]},
                expected_attempt_id=self.attempt_id,
                scheduler_state_path=self.scheduler_state_path,
            )
            raise
        record_scheduler_job_stage(
            self.task_id, cycle_key, stage_id, stage_version=version,
            status="complete", result=result, expected_attempt_id=self.attempt_id,
            scheduler_state_path=self.scheduler_state_path,
        )
        return result
