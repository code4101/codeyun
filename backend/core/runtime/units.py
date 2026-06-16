from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


RuntimeKind = Literal["service", "job"]
RuntimeSource = Literal["command", "builtin"]
RuntimeConcurrencyScope = Literal["unit", "group"]
RuntimeOverlapPolicy = Literal["replace", "queue", "skip"]
RuntimeTimeoutPolicy = Literal["none", "terminate"]
RuntimeScheduleKind = Literal["manual", "once", "interval", "daily", "weekly", "monthly", "cron", "dynamic"]

DEFAULT_COMMAND_JOB_TIMEOUT_SECONDS = 12 * 60 * 60
DEFAULT_JOB_CONCURRENCY_KEY = "job:default"
DEFAULT_JOB_RESOURCE_LOCK = "resource:job-default"

COMMAND_JOB_NAME_HINTS = (
    "rime_",
    "清理",
    "主机名",
    "py环境",
    "日志",
    "weekly",
    "关闭凡修",
    "桌面布局",
)

COMMAND_SERVICE_NAME_HINTS = {
    "capture",
    "codeyun",
    "frpc",
    "nginx",
    "server",
    "sync",
    "syncthing",
    "xlproject-jupyter",
    "xlserver",
}


def is_legacy_codeyun_command_task(task: Any) -> bool:
    """Return True for the old local CodeYun service row superseded by the watchdog."""

    name = _normalized_task_text(task, "name").lower()
    command = _normalized_task_text(task, "command").lower().replace("\\", "/")
    if name != "codeyun":
        return False
    return (
        command.endswith(" dev.py")
        or command.endswith("/dev.py")
        or " uv run dev.py" in f" {command}"
        or command == "uv run dev.py"
    )


@dataclass(frozen=True)
class RuntimeExecutionPolicy:
    kind: RuntimeKind
    concurrency_scope: RuntimeConcurrencyScope
    concurrency_key: str
    overlap_policy: RuntimeOverlapPolicy
    timeout_policy: RuntimeTimeoutPolicy
    timeout_seconds: int | None
    schedule_kind: RuntimeScheduleKind
    queue_key: str | None = None
    resource_lock: str | None = None


def command_runtime_queue_name(task_id: str) -> str:
    return f"command:{task_id}"


def _task_attr(task: Any, name: str, default: Any = None) -> Any:
    if isinstance(task, dict):
        return task.get(name, default)
    return getattr(task, name, default)


def _normalized_task_text(task: Any, name: str) -> str:
    return str(_task_attr(task, name, "") or "").strip()


def infer_command_runtime_kind(task: Any) -> RuntimeKind:
    explicit_kind = str(_task_attr(task, "runtime_kind", "") or "").strip().lower()
    if explicit_kind in {"service", "job"}:
        return explicit_kind  # type: ignore[return-value]

    name = _normalized_task_text(task, "name").lower()
    command = _normalized_task_text(task, "command").lower()
    description = _normalized_task_text(task, "description").lower()

    if name in COMMAND_SERVICE_NAME_HINTS:
        return "service"
    if any(hint.lower() in name for hint in COMMAND_JOB_NAME_HINTS):
        return "job"
    if "小狼毫" in description or ("同步" in description and "临时" in description):
        return "job"
    if command.startswith("python -c") or command.startswith("python.exe -c"):
        return "job"
    if command.startswith("taskkill "):
        return "job"
    if _task_attr(task, "schedule", None):
        return "job"
    return "service"


def command_runtime_group(task: Any, kind: RuntimeKind) -> tuple[str, str]:
    name = _normalized_task_text(task, "name").lower()
    description = _normalized_task_text(task, "description")
    if kind == "job":
        if name.startswith("rime_") or "小狼毫" in description:
            return "job:rime", "输入法"
        if _task_attr(task, "schedule", None):
            return "job:scheduled-command", "命令调度"
        return "job:command", "命令作业"

    if name in {"frpc", "nginx"}:
        return "service:network", "网络服务"
    if name in {"sync", "syncthing"}:
        return "service:sync", "同步服务"
    if name in {"server", "codeyun", "xlserver", "xlproject-jupyter", "capture"}:
        return "service:base", "基础服务"
    return "service:default", "默认服务组"


def command_runtime_resource_lock(task: Any, kind: RuntimeKind) -> str | None:
    if kind != "job":
        return None

    text = " ".join(
        [
            _normalized_task_text(task, "id"),
            _normalized_task_text(task, "name"),
            _normalized_task_text(task, "command"),
            _normalized_task_text(task, "description"),
        ]
    ).lower()
    if any(hint in text for hint in ("fanxiu", "凡修", "mumu", "adb", "screenshot", "doctor_watch")):
        return "resource:gui-automation"
    if any(hint in text for hint in ("wechat", "微信")):
        return "resource:wechat"
    if any(hint in text for hint in ("git", "deploy", "发布", "提交")):
        return "resource:repo"
    if "rime" in text or "小狼毫" in text:
        return "resource:rime"
    return DEFAULT_JOB_RESOURCE_LOCK


def resolve_command_runtime_policy(task: Any) -> RuntimeExecutionPolicy:
    kind = infer_command_runtime_kind(task)
    schedule_policy = _task_attr(task, "schedule_policy", None) or {}
    trigger = schedule_policy.get("trigger") if isinstance(schedule_policy, dict) else None
    trigger_kind = str((trigger or {}).get("type") or "").strip().lower() if isinstance(trigger, dict) else ""
    schedule_kind: RuntimeScheduleKind = (
        trigger_kind if trigger_kind in {"once", "interval", "daily", "weekly", "monthly", "cron"} else
        ("cron" if _task_attr(task, "schedule", None) else "manual")
    )
    task_id = str(_task_attr(task, "id", "") or "")
    timeout = _task_attr(task, "timeout", None)
    timeout_seconds = int(timeout) if isinstance(timeout, int) and timeout > 0 else None

    if kind == "service":
        return RuntimeExecutionPolicy(
            kind="service",
            concurrency_scope="unit",
            concurrency_key=f"service:{task_id}",
            overlap_policy="replace",
            timeout_policy="terminate" if timeout_seconds else "none",
            timeout_seconds=timeout_seconds,
            schedule_kind=schedule_kind,
            queue_key=None,
            resource_lock=None,
        )

    resource_lock = command_runtime_resource_lock(task, "job")
    return RuntimeExecutionPolicy(
        kind="job",
        concurrency_scope="group",
        concurrency_key=DEFAULT_JOB_CONCURRENCY_KEY,
        overlap_policy="queue",
        timeout_policy="terminate",
        timeout_seconds=timeout_seconds or DEFAULT_COMMAND_JOB_TIMEOUT_SECONDS,
        schedule_kind=schedule_kind,
        queue_key=DEFAULT_JOB_CONCURRENCY_KEY,
        resource_lock=resource_lock,
    )


def resolve_builtin_job_runtime_policy(*, schedule_kind: RuntimeScheduleKind = "dynamic") -> RuntimeExecutionPolicy:
    return RuntimeExecutionPolicy(
        kind="job",
        concurrency_scope="group",
        concurrency_key=DEFAULT_JOB_CONCURRENCY_KEY,
        overlap_policy="queue",
        timeout_policy="none",
        timeout_seconds=None,
        schedule_kind=schedule_kind,
        queue_key=DEFAULT_JOB_CONCURRENCY_KEY,
        resource_lock=DEFAULT_JOB_RESOURCE_LOCK,
    )


def runtime_policy_payload(policy: RuntimeExecutionPolicy) -> dict[str, Any]:
    return {
        "concurrency_scope": policy.concurrency_scope,
        "concurrency_key": policy.concurrency_key,
        "overlap_policy": policy.overlap_policy,
        "timeout_policy": policy.timeout_policy,
        "timeout_seconds": policy.timeout_seconds,
        "schedule_kind": policy.schedule_kind,
        "queue_key": policy.queue_key,
        "resource_lock": policy.resource_lock,
        "policy": {
            "concurrency_scope": policy.concurrency_scope,
            "concurrency_key": policy.concurrency_key,
            "overlap_policy": policy.overlap_policy,
            "timeout_policy": policy.timeout_policy,
            "timeout_seconds": policy.timeout_seconds,
            "schedule_kind": policy.schedule_kind,
            "queue_key": policy.queue_key,
            "resource_lock": policy.resource_lock,
        },
    }
