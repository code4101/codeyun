from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.core.fanxiu.behavior_tree import runtime as behavior_tree


@dataclass(frozen=True)
class FanxiuCell:
    """One ordinary Python cell submitted to the current Fanxiu kernel."""

    kernel: "FanxiuKernel"
    code: str
    timeout_seconds: float | None = 120.0
    max_output_chars: int = 4000

    def run(self, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        return self.kernel.execute(
            self.code,
            timeout_seconds=self.timeout_seconds if timeout_seconds is None else timeout_seconds,
            max_output_chars=self.max_output_chars,
        )


@dataclass(frozen=True)
class FanxiuKernel:
    """Small facade over one long-lived, native Jupyter kernel."""

    entry_id: str = behavior_tree.DEFAULT_FANXIU_ENTRY_ID

    def cell(
        self,
        code: str,
        *,
        timeout_seconds: float | None = 120.0,
        max_output_chars: int = 4000,
    ) -> FanxiuCell:
        return FanxiuCell(
            kernel=self,
            code=str(code or ""),
            timeout_seconds=None if timeout_seconds is None else float(timeout_seconds),
            max_output_chars=int(max_output_chars or 4000),
        )

    def task(
        self,
        task_type: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = 21630.0,
        effective_now: str | None = None,
        **payload_fields: Any,
    ) -> FanxiuCell:
        """Compile a registered task call into an ordinary Python cell."""
        data = dict(payload or {})
        data.update(payload_fields)
        if effective_now is not None:
            data["effective_now"] = str(effective_now)
        source = (
            "# fanxiu:managed-task-cell\n"
            f"run_task_cell({str(task_type or '')!r}, {data!r})"
        )
        return self.cell(source, timeout_seconds=timeout_seconds, max_output_chars=20000)

    def execute(
        self,
        code: str,
        *,
        timeout_seconds: float | None = 120.0,
        max_output_chars: int = 4000,
    ) -> dict[str, Any]:
        from backend.core.fanxiu.behavior_tree.jupyter_kernel import execute_fanxiu_jupyter_cell

        entry = behavior_tree.resolve_fanxiu_entry(self.entry_id)
        behavior_tree.ensure_fanxiu_behavior_tree_service(entry, self.entry_id)
        return execute_fanxiu_jupyter_cell(
            str(code or ""),
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )

    def status(self) -> dict[str, Any]:
        from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_manager_status

        return {
            "kernel": fanxiu_kernel_manager_status(),
            "runtime": behavior_tree.fanxiu_behavior_tree_runtime_status(),
        }

    def interrupt(self, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        from backend.core.fanxiu.behavior_tree.jupyter_kernel import send_fanxiu_kernel_manager_command

        return send_fanxiu_kernel_manager_command("interrupt", timeout_seconds=timeout_seconds)

    def restart(self, *, timeout_seconds: float = 20.0) -> dict[str, Any]:
        from backend.core.fanxiu.behavior_tree.jupyter_kernel import send_fanxiu_kernel_manager_command

        return send_fanxiu_kernel_manager_command("restart", timeout_seconds=timeout_seconds)

    def shutdown(self, *, timeout_seconds: float = 15.0) -> dict[str, Any]:
        from backend.core.fanxiu.behavior_tree.jupyter_kernel import send_fanxiu_kernel_manager_command

        return send_fanxiu_kernel_manager_command("shutdown", timeout_seconds=timeout_seconds)

    def logs(self, *, limit: int = 200, scope: str = "", item_id: str = "") -> list[dict[str, Any]]:
        return behavior_tree.fanxiu_behavior_tree_runtime_logs(limit=limit, scope=scope, item_id=item_id)


def kernel(entry_id: str = behavior_tree.DEFAULT_FANXIU_ENTRY_ID) -> FanxiuKernel:
    return FanxiuKernel(entry_id=str(entry_id or behavior_tree.DEFAULT_FANXIU_ENTRY_ID))
