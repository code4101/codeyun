"""旧 Scheduler 实例归并为当前业务所有者的纯数据迁移。

保留历史完成凭据、运行中 attempt 和下一执行时间；输入快照不变，
重复迁移不重置当前进度。本模块不读写状态文件、不访问游戏，
持久化和锁由调用方 maintain_scheduler_tasks 管理。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from .job_times import next_business_time

from backend.core.fanxiu.activity.ranking_lifecycle import (
    RANKING_LIFECYCLE_TASK_ID,
    RANKING_LIFECYCLE_TASK_TYPE,
    RESOURCE_RANKING_TASK_ID,
    RESOURCE_RANKING_TASK_TYPE,
    RETIRED_GAMEPLAY_RANKING_TASK_IDS,
    RETIRED_GAMEPLAY_RANKING_TASK_TYPES,
    RETIRED_RESOURCE_RANKING_TASK_IDS,
    RETIRED_RESOURCE_RANKING_TASK_TYPES,
)

from backend.core.fanxiu.activity.theme_collection import (
    THEME_COLLECTION_INTERNALIZED_TASK_IDS,
    THEME_COLLECTION_INTERNALIZED_TASK_TYPES,
    THEME_COLLECTION_LABEL,
    THEME_COLLECTION_TASK_ID,
    THEME_COLLECTION_TASK_TYPE,
)

from backend.core.fanxiu.data_annotation.tasks.resource_daily_contract import (
    RESOURCE_DAILY_INTERNALIZED_JOBS_KEY,
    RESOURCE_DAILY_LABEL,
    RESOURCE_DAILY_PAYLOAD_SCHEMA_KEY,
    RESOURCE_DAILY_PROGRESS_KEY,
    RESOURCE_DAILY_RETIRED_TASK_IDS,
    RESOURCE_DAILY_RETIRED_TASK_TYPES,
    RESOURCE_DAILY_SCHEMA_VERSION,
    RESOURCE_DAILY_STAGES,
    RESOURCE_DAILY_TASK_ID,
    RESOURCE_DAILY_TASK_TYPE,
    RESOURCE_DAILY_TRIGGER_DESCRIPTION,
    match_resource_daily_stage,
    next_resource_daily_time,
    resource_daily_completion,
)

_CONSOLIDATED_ARENA_SCHEDULER_IDS = {
    "sunday-daofa": "daily-daofa",
    "sunday-xianyuan-duel": "daily-xianyuan-duel",
}

RETIRED_SCHEDULER_TASK_IDS = {
    # 日常_助手的一键执行已经包含供奉，不再保留独立调度实例。
    "legacy-daily-gongfeng",
    # 三段式气泡链路已合并为一个闭环 Job。
    "bubble-weekly-restart",
    "bubble-claim-pills",
    "bubble-hide",
    # 玩法榜专用子任务只允许通过普通行为树 Cell 调试；Scheduler
    # 持久实例统一归属于 ranking-lifecycle。
    *RETIRED_GAMEPLAY_RANKING_TASK_IDS,
    *RETIRED_RESOURCE_RANKING_TASK_IDS,
    # 主题集旧一级任务统一归属唯一的 theme-collection。
    *THEME_COLLECTION_INTERNALIZED_TASK_IDS,
    # 资源日常及周一兽魂更新归属唯一的 resource-auto-use。
    *RESOURCE_DAILY_RETIRED_TASK_IDS,
}

RETIRED_SCHEDULER_TASK_TYPES = {
    "bubble_weekly_restart",
    "bubble_claim_pills",
    "bubble_hide",
    *RETIRED_GAMEPLAY_RANKING_TASK_TYPES,
    *RETIRED_RESOURCE_RANKING_TASK_TYPES,
    *THEME_COLLECTION_INTERNALIZED_TASK_TYPES,
    *RESOURCE_DAILY_RETIRED_TASK_TYPES,
}

def migrate_scheduler_instances(
    raw: Any,
    *,
    now: datetime | None = None,
) -> tuple[Any, bool]:
    """Fold obsolete instances and remove explicitly retired Scheduler Jobs."""

    if not isinstance(raw, list):
        return raw, False
    items = deepcopy(raw)
    migration_changed = False
    bootstrap_time = next_business_time(("00:10",), now=now or datetime.now())

    def migrate_family(
        *,
        canonical_id: str,
        canonical_type: str,
        label: str,
        retired_ids: frozenset[str],
        retired_types: frozenset[str],
        clean_payload_keys: frozenset[str] = frozenset(),
    ) -> bool:
        canonical = next(
            (
                item for item in items
                if isinstance(item, dict) and str(item.get("id") or "") == canonical_id
            ),
            None,
        )
        retired = [
            item for item in items
            if isinstance(item, dict)
            and (
                str(item.get("id") or "") in retired_ids
                or str(item.get("task_type") or "") in retired_types
            )
        ]
        changed = False
        if retired:
            candidates = [item.get("next_time") for item in retired]
            candidates.append(canonical.get("next_time") if canonical is not None else bootstrap_time)
            valid: list[str] = []
            for value in candidates:
                text = str(value or "").strip()
                try:
                    datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                valid.append(text)
            earliest = min(valid) if valid else None
            if canonical is None:
                canonical = {
                    "id": canonical_id,
                    "task_type": canonical_type,
                    "label": label,
                    "template_id": canonical_type,
                    "template_label": label,
                    "trigger_description": "动态",
                    "next_time": earliest,
                    "payload": {"max_execution_seconds": 10800},
                }
                items.append(canonical)
            elif earliest is not None and canonical.get("next_time") != earliest:
                canonical["next_time"] = earliest
            changed = True
        if canonical is not None and clean_payload_keys:
            payload = canonical.get("payload")
            if isinstance(payload, dict):
                cleaned = {key: value for key, value in payload.items() if key not in clean_payload_keys}
                if cleaned != payload:
                    canonical["payload"] = cleaned
                    changed = True
        if canonical is not None:
            canonical_shape = {
                "task_type": canonical_type,
                "label": label,
                "template_id": canonical_type,
                "template_label": label,
            }
            for key, value in canonical_shape.items():
                if canonical.get(key) != value:
                    canonical[key] = value
                    changed = True
        return changed

    def migrate_resource_daily_family() -> bool:
        """Absorb retired first-level Jobs into one canonical daily Job.

        This is deliberately *not* ``migrate_family``: the canonical trigger is
        a fixed next 00:00, never the earliest retired ``next_time``, and the
        retired runs are converted into durable ``aggregate_progress`` evidence
        instead of being added as new triggers.  The one-time
        ``resource_daily_schema_version`` marker makes the work idempotent and
        keeps later maintenance from overwriting a running ``next_time``.
        """

        canonical = next(
            (
                item for item in items
                if isinstance(item, dict) and str(item.get("id") or "") == RESOURCE_DAILY_TASK_ID
            ),
            None,
        )
        retired = [
            item for item in items
            if isinstance(item, dict) and match_resource_daily_stage(item) is not None
        ]
        if canonical is None and not retired:
            # No canonical and no retired instances: the default catalogue owns
            # the sole canonical Job, so an empty legacy raw needs no creation.
            return False

        changed = False
        if canonical is None:
            canonical = {
                "id": RESOURCE_DAILY_TASK_ID,
                "task_type": RESOURCE_DAILY_TASK_TYPE,
                "label": RESOURCE_DAILY_LABEL,
                "template_id": RESOURCE_DAILY_TASK_TYPE,
                "template_label": RESOURCE_DAILY_LABEL,
                "legacy_name": RESOURCE_DAILY_LABEL,
                "trigger_description": RESOURCE_DAILY_TRIGGER_DESCRIPTION,
                # Initial value only; the conversion below rewrites it.
                "next_time": next_resource_daily_time(now or datetime.now()),
                "last_run_at": None,
                "last_result": "",
                "last_message": "",
                "error_retry_delay_seconds": 0,
                "payload": {"max_rounds": 3, "max_execution_seconds": 10800},
                "scheduler_meta": None,
            }
            items.append(canonical)
            changed = True

        canonical_shape = {
            "task_type": RESOURCE_DAILY_TASK_TYPE,
            "label": RESOURCE_DAILY_LABEL,
            "template_id": RESOURCE_DAILY_TASK_TYPE,
            "template_label": RESOURCE_DAILY_LABEL,
            "legacy_name": RESOURCE_DAILY_LABEL,
            "trigger_description": RESOURCE_DAILY_TRIGGER_DESCRIPTION,
        }
        for key, value in canonical_shape.items():
            if canonical.get(key) != value:
                canonical[key] = value
                changed = True

        payload = canonical.get("payload")
        if not isinstance(payload, dict):
            payload = {}
            canonical["payload"] = payload
            changed = True
        if payload.get(RESOURCE_DAILY_PAYLOAD_SCHEMA_KEY) != RESOURCE_DAILY_SCHEMA_VERSION:
            # Copy the payload before normalizing its nested migration records.
            payload = deepcopy(payload)
            canonical["payload"] = payload
            payload.setdefault("max_execution_seconds", 10800)
            internalized = payload.get(RESOURCE_DAILY_INTERNALIZED_JOBS_KEY)
            internalized = dict(internalized) if isinstance(internalized, dict) else {}
            progress = payload.get(RESOURCE_DAILY_PROGRESS_KEY)
            progress = dict(progress) if isinstance(progress, dict) else {}
            for stage in RESOURCE_DAILY_STAGES:
                matches = [
                    item for item in retired
                    if match_resource_daily_stage(item) == stage
                ]
                source = next(
                    (item for item in matches if str(item.get("id") or "") == stage.task_id),
                    matches[0] if matches else None,
                )
                if source is None:
                    continue
                # Full copy preserves every historical and configuration field.
                internalized[stage.task_id] = deepcopy(source)
                completion = resource_daily_completion(stage, source)
                if completion is not None:
                    cycle_key, record = completion
                    cycle_records = progress.get(cycle_key)
                    cycle_records = dict(cycle_records) if isinstance(cycle_records, dict) else {}
                    cycle_records[stage.task_id] = record
                    progress[cycle_key] = cycle_records
            payload[RESOURCE_DAILY_INTERNALIZED_JOBS_KEY] = internalized
            payload[RESOURCE_DAILY_PROGRESS_KEY] = progress
            payload[RESOURCE_DAILY_PAYLOAD_SCHEMA_KEY] = RESOURCE_DAILY_SCHEMA_VERSION
            canonical["error_retry_delay_seconds"] = 600
            canonical["next_time"] = next_resource_daily_time(now or datetime.now())
            changed = True
        return changed

    migration_changed |= migrate_family(
        canonical_id=RANKING_LIFECYCLE_TASK_ID,
        canonical_type=RANKING_LIFECYCLE_TASK_TYPE,
        label="玩法榜",
        retired_ids=RETIRED_GAMEPLAY_RANKING_TASK_IDS,
        retired_types=RETIRED_GAMEPLAY_RANKING_TASK_TYPES,
        clean_payload_keys=frozenset({"magic_invasion_progress"}),
    )
    migration_changed |= migrate_family(
        canonical_id=RESOURCE_RANKING_TASK_ID,
        canonical_type=RESOURCE_RANKING_TASK_TYPE,
        label="资源榜",
        retired_ids=RETIRED_RESOURCE_RANKING_TASK_IDS,
        retired_types=RETIRED_RESOURCE_RANKING_TASK_TYPES,
    )

    # The single canonical theme-collection Job subsumes every legacy
    # first-level theme Task.  migrate_family folds their earliest next_time
    # into the canonical record, and the retired-id/type filter below then
    # removes the legacy instances so a persisted catalogue cannot readmit them.
    migration_changed |= migrate_family(
        canonical_id=THEME_COLLECTION_TASK_ID,
        canonical_type=THEME_COLLECTION_TASK_TYPE,
        label=THEME_COLLECTION_LABEL,
        retired_ids=frozenset(THEME_COLLECTION_INTERNALIZED_TASK_IDS),
        retired_types=frozenset(THEME_COLLECTION_INTERNALIZED_TASK_TYPES),
    )
    migration_changed |= migrate_resource_daily_family()
    before_retirement = len(items)
    items = [
        item
        for item in items
        if not (
            isinstance(item, dict)
            and (
                str(item.get("id") or "") in RETIRED_SCHEDULER_TASK_IDS
                or str(item.get("task_type") or "") in RETIRED_SCHEDULER_TASK_TYPES
            )
        )
    ]
    changed = migration_changed or len(items) != before_retirement
    for obsolete_id, canonical_id in _CONSOLIDATED_ARENA_SCHEDULER_IDS.items():
        obsolete_index = next(
            (
                index
                for index, item in enumerate(items)
                if isinstance(item, dict) and str(item.get("id") or "") == obsolete_id
            ),
            None,
        )
        if obsolete_index is None:
            continue
        canonical_index = next(
            (
                index
                for index, item in enumerate(items)
                if isinstance(item, dict) and str(item.get("id") or "") == canonical_id
            ),
            None,
        )
        obsolete = items[obsolete_index]
        if canonical_index is None:
            obsolete["id"] = canonical_id
        else:
            canonical = items[canonical_index]
            trigger_candidates = [
                str(value)
                for value in (canonical.get("next_time"), obsolete.get("next_time"))
                if str(value or "").strip()
            ]
            canonical["next_time"] = min(trigger_candidates) if trigger_candidates else None
            if str(obsolete.get("last_run_at") or "") > str(canonical.get("last_run_at") or ""):
                for key in ("last_run_at", "last_result", "last_message", "finished_at"):
                    canonical[key] = obsolete.get(key)
            if str(obsolete.get("last_result") or "") == "running":
                for key in (
                    "last_result",
                    "last_message",
                    "attempt_id",
                    "attempt_original_trigger",
                    "attempt_kernel_generation",
                    "attempt_kernel_idle_since",
                    "queued_at",
                    "started_at",
                ):
                    canonical[key] = obsolete.get(key)
            items.pop(obsolete_index)
        changed = True
    return items, changed
