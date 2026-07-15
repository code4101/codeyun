from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ServiceScheduleKind = Literal["manual", "once", "interval", "daily", "weekly", "monthly", "cron"]


def _task_attr(task: Any, name: str, default: Any = None) -> Any:
    return task.get(name, default) if isinstance(task, dict) else getattr(task, name, default)


def _task_text(task: Any, name: str) -> str:
    return str(_task_attr(task, name, "") or "").strip()


def is_legacy_codeyun_service(task: Any) -> bool:
    name = _task_text(task, "name").lower()
    command = _task_text(task, "command").lower().replace("\\", "/")
    if name != "codeyun":
        return False
    return command.endswith(" dev.py") or command.endswith("/dev.py") or command == "uv run dev.py"


def command_service_group(task: Any) -> tuple[str, str]:
    name = _task_text(task, "name").lower()
    if name in {"frpc", "nginx"}:
        return "service:network", "网络服务"
    if name in {"sync", "syncthing"}:
        return "service:sync", "同步服务"
    if name in {"server", "xlserver", "xlproject-jupyter", "capture"}:
        return "service:base", "基础服务"
    return "service:default", "默认服务组"


@dataclass(frozen=True)
class ServiceExecutionPolicy:
    concurrency_scope: str
    concurrency_key: str
    overlap_policy: str
    timeout_policy: str
    timeout_seconds: int | None
    schedule_kind: ServiceScheduleKind
    queue_key: None = None
    resource_lock: None = None


def resolve_service_policy(task: Any) -> ServiceExecutionPolicy:
    schedule_policy = _task_attr(task, "schedule_policy", None) or {}
    trigger = schedule_policy.get("trigger") if isinstance(schedule_policy, dict) else None
    trigger_kind = str((trigger or {}).get("type") or "").strip().lower() if isinstance(trigger, dict) else ""
    schedule_kind: ServiceScheduleKind = (
        trigger_kind if trigger_kind in {"once", "interval", "daily", "weekly", "monthly", "cron"}
        else ("cron" if _task_attr(task, "schedule", None) else "manual")
    )
    timeout = _task_attr(task, "timeout", None)
    timeout_seconds = int(timeout) if isinstance(timeout, int) and timeout > 0 else None
    task_id = str(_task_attr(task, "id", "") or "")
    return ServiceExecutionPolicy(
        concurrency_scope="unit",
        concurrency_key=f"service:{task_id}",
        overlap_policy="replace",
        timeout_policy="terminate" if timeout_seconds else "none",
        timeout_seconds=timeout_seconds,
        schedule_kind=schedule_kind,
    )


def service_policy_payload(policy: ServiceExecutionPolicy) -> dict:
    values = {
        "concurrency_scope": policy.concurrency_scope,
        "concurrency_key": policy.concurrency_key,
        "overlap_policy": policy.overlap_policy,
        "timeout_policy": policy.timeout_policy,
        "timeout_seconds": policy.timeout_seconds,
        "schedule_kind": policy.schedule_kind,
        "queue_key": None,
        "resource_lock": None,
    }
    return {**values, "policy": dict(values)}

