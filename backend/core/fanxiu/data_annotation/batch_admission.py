"""Read-only admission at a verified, interruptible business batch boundary."""
from datetime import datetime, timedelta
from math import isfinite
from typing import Any, Iterable


def plan_scheduler_batch_admission(
    tasks: Iterable[dict[str, Any]], *, task_id: str, now: datetime,
    batch_seconds: float, release_seconds: float = 60.0,
) -> dict[str, Any]:
    """Reserve time for higher-level jobs before beginning another atomic batch.

    Consume the Scheduler's effective-time projection. This policy also applies
    during AI ownership: disabling automatic dispatch cannot erase due jobs.
    It never interrupts a submitted batch, changes triggers or marks work done.
    After yielding, a new attempt reconstructs progress from verified receipts.
    """
    if not task_id or not isfinite(batch_seconds) or batch_seconds <= 0:
        raise ValueError("批次需要明确作业身份和有限正耗时预算")
    if not isfinite(release_seconds) or release_seconds < 0:
        raise ValueError("交权预算必须为有限非负数")
    rows = list(tasks)
    current = next((row for row in rows if row.get('id') == task_id), None)
    if current is None:
        raise ValueError("批次对应的正式作业不存在")
    level = int(current.get('dispatch_level') or 0)
    finish = now + timedelta(seconds=batch_seconds + release_seconds)
    candidates = []
    for row in rows:
        if row.get('id') == task_id or int(row.get('dispatch_level') or 0) <= level:
            continue
        raw = row.get('next_time')
        if not raw:
            continue
        trigger = datetime.fromisoformat(str(raw))
        if trigger.tzinfo is None and now.tzinfo is not None:
            trigger = trigger.replace(tzinfo=now.tzinfo)
        elif trigger.tzinfo is not None and now.tzinfo is None:
            trigger = trigger.astimezone().replace(tzinfo=None)
        if trigger <= finish:
            candidates.append((trigger, -int(row.get('dispatch_level') or 0), str(row['id'])))
    if not candidates:
        return {'admitted': True}
    trigger, _, protected_id = min(candidates)
    return {'admitted': False, 'protected_task_id': protected_id,
            'protected_next_time': trigger.isoformat(),
            'retry_at': (max(now, trigger) + timedelta(seconds=60)).isoformat(),
            'reason': '下一原子批次及安全离场将覆盖更高级作业触发时间'}
