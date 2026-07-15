from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


JobScheduleKind = Literal["manual", "once", "interval", "daily", "weekly", "monthly", "cron", "dynamic"]


@dataclass(frozen=True)
class JobExecutionPolicy:
    concurrency_scope: str = "group"
    concurrency_key: str = "job:default"
    overlap_policy: str = "queue"
    timeout_policy: str = "none"
    timeout_seconds: int | None = None
    schedule_kind: JobScheduleKind = "dynamic"
    queue_key: str = "job:default"
    resource_lock: str = "resource:job-default"


def job_policy_payload(*, schedule_kind: JobScheduleKind = "dynamic") -> dict:
    policy = JobExecutionPolicy(schedule_kind=schedule_kind)
    values = {
        "concurrency_scope": policy.concurrency_scope,
        "concurrency_key": policy.concurrency_key,
        "overlap_policy": policy.overlap_policy,
        "timeout_policy": policy.timeout_policy,
        "timeout_seconds": policy.timeout_seconds,
        "schedule_kind": policy.schedule_kind,
        "queue_key": policy.queue_key,
        "resource_lock": policy.resource_lock,
    }
    return {**values, "policy": dict(values)}

