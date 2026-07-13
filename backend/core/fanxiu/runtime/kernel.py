from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from backend.core.fanxiu.runtime import behavior_tree


CellKind = Literal["task", "code"]


@dataclass(frozen=True)
class FanxiuCell:
    """A user-facing Runtime cell.

    The cell API is the public mental model. Queue files, resident owner leases
    and service wakeups are implementation details hidden behind submit/run.
    """

    kernel: "FanxiuKernel"
    kind: CellKind
    task_type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    code: str = ""
    mode: str = "readonly"
    timeout_seconds: float = 120.0
    max_output_chars: int = 4000

    def submit(self) -> dict[str, Any]:
        if self.kind == "code":
            return self.kernel.submit_code(
                self.code,
                mode=self.mode,
                timeout_seconds=self.timeout_seconds,
                max_output_chars=self.max_output_chars,
                wait=False,
            )
        return self.kernel.submit_task(self.task_type, self.payload, wait=False)

    def run(self, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        wait_timeout = timeout_seconds
        if wait_timeout is None and self.kind == "code":
            wait_timeout = self.timeout_seconds + 30.0
        if self.kind == "code":
            return self.kernel.submit_code(
                self.code,
                mode=self.mode,
                timeout_seconds=self.timeout_seconds,
                max_output_chars=self.max_output_chars,
                wait=True,
                wait_timeout_seconds=wait_timeout,
            )
        return self.kernel.submit_task(
            self.task_type,
            self.payload,
            wait=True,
            wait_timeout_seconds=wait_timeout,
        )


@dataclass(frozen=True)
class FanxiuKernel:
    """Thin facade for the single Fanxiu resident Runtime kernel."""

    entry_id: str = behavior_tree.DEFAULT_FANXIU_ENTRY_ID
    isolate_jobs: bool = True

    def task(self, task_type: str, payload: dict[str, Any] | None = None, **payload_fields: Any) -> FanxiuCell:
        data = dict(payload or {})
        data.update(payload_fields)
        return FanxiuCell(
            kernel=self,
            kind="task",
            task_type=str(task_type or ""),
            payload=data,
        )

    def cell(
        self,
        code: str,
        *,
        timeout_seconds: float = 120.0,
        max_output_chars: int = 4000,
    ) -> FanxiuCell:
        return FanxiuCell(
            kernel=self,
            kind="code",
            code=str(code or ""),
            mode="jupyter",
            timeout_seconds=float(timeout_seconds or 120.0),
            max_output_chars=int(max_output_chars or 4000),
        )

    def code(
        self,
        code: str,
        *,
        mode: str = "readonly",
        timeout_seconds: float = 120.0,
        max_output_chars: int = 4000,
    ) -> FanxiuCell:
        """Compatibility alias for :meth:`cell`."""
        return self.cell(
            code,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )

    def submit_task(
        self,
        task_type: str,
        payload: dict[str, Any] | None = None,
        *,
        wait: bool = False,
        wait_timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return behavior_tree.submit_fanxiu_task_cell(
            str(task_type or ""),
            dict(payload or {}),
            entry_id=self.entry_id,
            isolate_jobs=self.isolate_jobs,
            wait=wait,
            wait_timeout_seconds=float(wait_timeout_seconds or 300.0),
        )

    def submit_code(
        self,
        code: str,
        *,
        mode: str = "readonly",
        timeout_seconds: float = 120.0,
        max_output_chars: int = 4000,
        wait: bool = False,
        wait_timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        from backend.core.fanxiu.runtime.jupyter_kernel import execute_fanxiu_jupyter_cell

        entry = behavior_tree.resolve_fanxiu_entry(self.entry_id)
        behavior_tree.ensure_fanxiu_behavior_tree_service(entry, self.entry_id)
        return execute_fanxiu_jupyter_cell(
            str(code or ""),
            timeout_seconds=float(wait_timeout_seconds or timeout_seconds or 120.0),
            max_output_chars=max_output_chars,
        )

    def status(self) -> dict[str, Any]:
        return behavior_tree.fanxiu_data_annotation_runtime_status()

    def logs(self, *, limit: int = 200, scope: str = "", item_id: str = "") -> list[dict[str, Any]]:
        return behavior_tree.fanxiu_data_annotation_runtime_logs(
            limit=limit,
            scope=scope,
            item_id=item_id,
        )


def kernel(
    entry_id: str = behavior_tree.DEFAULT_FANXIU_ENTRY_ID,
    *,
    isolate_jobs: bool = True,
) -> FanxiuKernel:
    return FanxiuKernel(entry_id=str(entry_id or behavior_tree.DEFAULT_FANXIU_ENTRY_ID), isolate_jobs=bool(isolate_jobs))
