from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from pyxllib.prog import (
    build_scheduled_task_plan,
    first_valid_schedule_time_text,
    merge_scheduled_task_updates,
    schedule_kind_rank,
    schedule_task_due_timestamp,
    schedule_task_order_key,
    scheduled_task_plan_reason,
    scheduled_task_run_copy,
    sync_scheduled_tasks_from_facts,
)

from backend.core.fanxiu.data_annotation.jobs import is_deprecated_data_annotation_job_type
from backend.core.fanxiu.data_annotation.state import (
    next_data_annotation_scheduler_time,
    normalize_data_annotation_scheduler_task,
    parse_data_annotation_daily_clock,
    parse_data_annotation_task_time,
)


TaskSupported = Callable[[dict[str, Any]], bool]
TaskDue = Callable[[dict[str, Any]], bool]

_UNSCHEDULED_MANUAL_RESULTS = {"manual_check_pending"}
_DAILY_RETRY_TO_NEXT_TRIGGER_GRACE = timedelta(minutes=60)
_XIANFU_INITIAL_CHECK_TASK_IDS = {"xianfu-visit-partner", "xianfu-learn-skill"}
_DAILY_AUDIT_COMPLETION_MIN_TOTAL = {
    "daily_dungeon": 6,
}
_STANDARD_ENABLED_TASK_IDS = {
    "daily-boss",
    "legacy-daily-assistant",
    "legacy-daily-xianyuan",
    "legacy-daily-vip",
}
_DAILY_RETRY_DEFER_TO_NEXT_TRIGGER_TASK_IDS = {
    "legacy-daily-green-bottle-baiye",
}
_OBSOLETE_ASSISTANT_COVERED_TASK_IDS = {
    "legacy-daily-lingta",
    "legacy-daily-shuangxiu",
    "legacy-daily-lingzu",
    "legacy-daily-yaowang",
    "legacy-daily-yaozu",
}
_OBSOLETE_ASSISTANT_COVERED_TASK_TYPES = {
    "daily_lingta",
    "daily_shuangxiu",
    "daily_lingzu",
    "daily_yaowang",
    "daily_yaozu",
}
_OBSOLETE_ASSISTANT_COVERED_TASK_LABELS = {
    "日常_灵塔",
    "日常_双修",
    "日常_灵祖",
    "日常_妖王来袭",
    "日常_妖族袭城",
}


def _xianfu_initial_check_time(current_time: datetime) -> str:
    return datetime(current_time.year, current_time.month, current_time.day, 6, 30, 0).strftime("%Y-%m-%d %H:%M:%S")


def data_annotation_fact_time_text(fact: dict[str, Any], *keys: str) -> str | None:
    return first_valid_schedule_time_text(fact, *keys)


def data_annotation_scheduler_group_rank(task: dict[str, Any]) -> int:
    return schedule_kind_rank(task, {"daily": 10, "weekly": 10, "dynamic": 20, "manual": 30})


def data_annotation_scheduler_due_timestamp(task: dict[str, Any]) -> float:
    return schedule_task_due_timestamp(task)


def data_annotation_scheduler_order_key(task: dict[str, Any]) -> tuple[int, float, str]:
    return schedule_task_order_key(task)


def data_annotation_scheduler_time_order_key(task: dict[str, Any]) -> tuple[int, float, int, str]:
    due_ts = data_annotation_scheduler_due_timestamp(task)
    return (
        0 if task.get("enabled") else 1,
        due_ts if due_ts > 0 else float("inf"),
        data_annotation_scheduler_group_rank(task),
        str(task.get("id") or ""),
    )


def _daily_retry_should_defer_to_next_trigger(task: dict[str, Any], current_time: datetime) -> str | None:
    if str(task.get("schedule_kind") or "") != "daily":
        return None
    retry_ts = parse_data_annotation_task_time(task.get("retry_after"))
    if retry_ts is None:
        return None
    next_time = next_data_annotation_scheduler_time(task, current_time)
    next_ts = parse_data_annotation_task_time(next_time)
    if not next_time or next_ts is None:
        return None
    if str(task.get("id") or "") in _DAILY_RETRY_DEFER_TO_NEXT_TRIGGER_TASK_IDS:
        return next_time
    if retry_ts <= next_ts and next_ts - retry_ts <= _DAILY_RETRY_TO_NEXT_TRIGGER_GRACE.total_seconds():
        return next_time
    return None


def _daily_first_schedule_time_today(task: dict[str, Any], current_time: datetime) -> str | None:
    if str(task.get("schedule_kind") or "") != "daily":
        return None
    clocks = []
    for value in task.get("schedule_times", []):
        clock = parse_data_annotation_daily_clock(value)
        if clock is not None:
            clocks.append(clock)
    if not clocks:
        return None
    first_clock = sorted(clocks)[0]
    return datetime.combine(current_time.date(), first_clock).strftime("%Y-%m-%d %H:%M:%S")


def _daily_task_success_today(task: dict[str, Any], current_time: datetime) -> bool:
    if str(task.get("last_result") or "") != "success":
        return False
    last_run_ts = parse_data_annotation_task_time(task.get("last_run_at"))
    if last_run_ts is None:
        return False
    return datetime.fromtimestamp(last_run_ts).date() == current_time.date()


def _daily_audit_row_is_valid_completed(row: dict[str, Any]) -> bool:
    task_type = str(row.get("task_type") or "")
    progress = row.get("progress") if isinstance(row.get("progress"), dict) else {}
    try:
        current = int(progress.get("current"))
        total = int(progress.get("total"))
    except (TypeError, ValueError):
        return bool(row.get("done"))
    min_total = _DAILY_AUDIT_COMPLETION_MIN_TOTAL.get(task_type)
    if min_total is not None:
        return total >= min_total and current >= total
    return bool(row.get("done")) or current >= total


def sync_data_annotation_scheduler_tasks_from_world_facts(
    tasks: list[dict[str, Any]],
    facts: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    discoveries = facts.get("discoveries") if isinstance(facts.get("discoveries"), dict) else {}
    task_facts = discoveries.get("task") if isinstance(discoveries.get("task"), dict) else {}
    filtered_task_facts = dict(task_facts) if isinstance(task_facts, dict) else {}
    current_time = now or datetime.now()
    daily_audit = discoveries.get("daily_audit") if isinstance(discoveries.get("daily_audit"), dict) else {}
    audit_updated_at = float(daily_audit.get("updated_at") or 0) if isinstance(daily_audit, dict) else 0.0
    audit_date = datetime.fromtimestamp(audit_updated_at).date() if audit_updated_at > 0 else None
    audit_completed_ids = {
        str(task_id)
        for task_id in (daily_audit.get("completed_task_ids") or [])
        if str(task_id)
    } if isinstance(daily_audit, dict) and audit_date == current_time.date() else set()
    if isinstance(daily_audit, dict) and audit_date == current_time.date():
        for row in daily_audit.get("mapped_completed") or []:
            if isinstance(row, dict) and str(row.get("task_id") or "") and _daily_audit_row_is_valid_completed(row):
                audit_completed_ids.add(str(row.get("task_id") or ""))
            elif isinstance(row, dict) and str(row.get("task_id") or ""):
                audit_completed_ids.discard(str(row.get("task_id") or ""))
        for row in daily_audit.get("rows") or []:
            if isinstance(row, dict) and str(row.get("task_id") or "") and _daily_audit_row_is_valid_completed(row):
                audit_completed_ids.add(str(row.get("task_id") or ""))
            elif isinstance(row, dict) and str(row.get("task_id") or ""):
                audit_completed_ids.discard(str(row.get("task_id") or ""))
    audit_completed_changed = False
    for task in tasks:
        task_id = str(task.get("id") or "")
        if task_id in audit_completed_ids:
            last_run_at = parse_data_annotation_task_time(task.get("last_run_at"))
            if last_run_at is None or audit_updated_at > last_run_at + 1.0:
                audit_time = datetime.fromtimestamp(audit_updated_at)
                next_time = next_data_annotation_scheduler_time(task, audit_time)
                task["last_result"] = "success"
                task["last_run_at"] = daily_audit.get("updated_at_text") or audit_time.strftime("%Y-%m-%d %H:%M:%S")
                task["retry_after"] = None
                if next_time:
                    task["next_time"] = next_time
                audit_completed_changed = True
                fact = dict(filtered_task_facts.get(task_id) or {})
                fact.update({
                    "id": task_id,
                    "task_type": str(task.get("task_type") or ""),
                    "label": str(task.get("label") or task_id),
                    "source": str(task.get("source") or ""),
                    "schedule_kind": str(task.get("schedule_kind") or ""),
                    "last_result": "success",
                    "last_run_at": daily_audit.get("updated_at_text") or audit_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "next_time": next_time,
                    "retry_after": None,
                    "updated_at": audit_updated_at,
                    "visual_audit_result": "completed",
                })
                fact.pop("discovered_retry_after", None)
                if next_time:
                    fact["discovered_next_time"] = next_time
                filtered_task_facts[task_id] = fact
                continue
        fact = filtered_task_facts.get(task_id)
        if not isinstance(fact, dict):
            continue
        fact_result = str(fact.get("last_result") or "")
        if fact_result in {"error", "stopped", "skipped", "unsupported"} and (fact.get("discovered_retry_after") or fact.get("retry_after")):
            fact = dict(fact)
            fact.pop("discovered_next_time", None)
            fact.pop("next_time", None)
        if fact_result in _UNSCHEDULED_MANUAL_RESULTS:
            fact = dict(fact)
            fact.pop("discovered_next_time", None)
            fact.pop("next_time", None)
            fact.pop("discovered_retry_after", None)
            fact.pop("retry_after", None)
            filtered_task_facts[task_id] = fact
        fact_updated_at = float(fact.get("updated_at") or 0)
        last_run_at = parse_data_annotation_task_time(task.get("last_run_at"))
        fact_last_run_at = parse_data_annotation_task_time(fact.get("last_run_at"))
        if (
            fact_result == "success"
            and str(task.get("last_result") or "") in {"error", "stopped", "skipped", "unsupported"}
            and fact_last_run_at is not None
            and (last_run_at is None or fact_last_run_at >= last_run_at)
        ):
            fact_time = datetime.fromtimestamp(fact_last_run_at)
            next_time = str(fact.get("discovered_next_time") or fact.get("next_time") or "").strip()
            if not next_time:
                next_time = next_data_annotation_scheduler_time(task, fact_time) or ""
            task["last_result"] = "success"
            task["last_run_at"] = str(fact.get("last_run_at") or fact_time.strftime("%Y-%m-%d %H:%M:%S"))
            task["retry_after"] = None
            task["next_time"] = next_time or None
            checkpoint = task.get("checkpoint") if isinstance(task.get("checkpoint"), dict) else {}
            checkpoint["world_fact_synced_at"] = current_time.strftime("%Y-%m-%d %H:%M:%S")
            checkpoint["world_fact_updated_at"] = fact.get("updated_at")
            task["checkpoint"] = checkpoint
            audit_completed_changed = True
            filtered_task_facts.pop(task_id, None)
            continue
        if (
            fact_result == "success"
            and str(task.get("last_result") or "") in {"error", "stopped", "skipped", "unsupported"}
            and fact_last_run_at is not None
            and last_run_at is not None
            and fact_last_run_at < last_run_at
        ):
            filtered_task_facts.pop(task_id, None)
            continue
        if (
            fact_result in {"queued", "running"}
            and str(task.get("last_result") or "") in {"error", "stopped", "skipped", "unsupported"}
            and fact_last_run_at is not None
            and last_run_at is not None
            and fact_last_run_at <= last_run_at
        ):
            filtered_task_facts.pop(task_id, None)
            continue
        fact_has_success_next_time = (
            fact_result == "success"
            and bool(fact.get("discovered_next_time") or fact.get("next_time"))
            and (not task.get("next_time") or bool(task.get("retry_after")))
            and (not fact_updated_at or not last_run_at or fact_updated_at >= last_run_at)
        )
        if fact_updated_at and last_run_at and fact_updated_at <= last_run_at + 1.0 and not fact_has_success_next_time:
            filtered_task_facts.pop(task_id, None)
    if not filtered_task_facts:
        return audit_completed_changed
    sync_time = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    synced = sync_scheduled_tasks_from_facts(
        tasks,
        filtered_task_facts,
        time_field_sources={
            "next_time": ("discovered_next_time", "next_time"),
            "retry_after": ("discovered_retry_after", "retry_after"),
            "last_run_at": ("last_run_at",),
        },
        text_field_sources={"last_result": ("last_result",)},
        synced_at_key="world_fact_synced_at",
        fact_updated_at_key="world_fact_updated_at",
        synced_at_text=sync_time,
    )
    return bool(synced or audit_completed_changed)


def merge_data_annotation_scheduler_task_updates(
    current_tasks: list[dict[str, Any]],
    incoming_tasks: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    incoming_by_id = {
        str(task.get("id") or ""): task
        for task in incoming_tasks
        if isinstance(task, dict) and str(task.get("id") or "")
    }
    if incoming_by_id and len(incoming_by_id) < len([task for task in current_tasks if str(task.get("id") or "")]):
        merged_input: list[dict[str, Any]] = []
        seen: set[str] = set()
        for current in current_tasks:
            task_id = str(current.get("id") or "")
            if not task_id:
                continue
            seen.add(task_id)
            merged_input.append(incoming_by_id.get(task_id, current))
        for task_id, incoming in incoming_by_id.items():
            if task_id not in seen:
                merged_input.append(incoming)
        incoming_tasks = merged_input
    return merge_scheduled_task_updates(
        current_tasks,
        incoming_tasks,
        normalizer=normalize_data_annotation_scheduler_task,
        next_time_resolver=lambda task, base_time: next_data_annotation_scheduler_time(task, base_time),
        base_time=now or datetime.now(),
    )


def repair_data_annotation_scheduler_tasks(
    raw: Any,
    default_tasks: list[dict[str, Any]],
    facts: dict[str, Any],
    *,
    task_supported: TaskSupported,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    source = raw if isinstance(raw, list) else default_tasks
    tasks = [task for item in source if (task := normalize_data_annotation_scheduler_task(item))]
    if not tasks:
        tasks = default_tasks
    legacy_mail_cleanup_task: dict[str, Any] | None = None
    legacy_daily_boss_task: dict[str, Any] | None = None
    legacy_xianfu_visit_task: dict[str, Any] | None = None
    legacy_xianfu_skill_task: dict[str, Any] | None = None
    legacy_daily_lingzu_task: dict[str, Any] | None = None
    legacy_daily_jianling_task: dict[str, Any] | None = None
    legacy_daily_lingta_task: dict[str, Any] | None = None
    legacy_daily_xianyuan_task: dict[str, Any] | None = None
    legacy_daily_assistant_task: dict[str, Any] | None = None
    legacy_daily_shuangxiu_task: dict[str, Any] | None = None
    legacy_daily_dungeon_task: dict[str, Any] | None = None
    legacy_daily_yaowang_task: dict[str, Any] | None = None
    legacy_daily_yaozu_task: dict[str, Any] | None = None
    legacy_daily_vip_task: dict[str, Any] | None = None
    legacy_daily_xianmeng_task: dict[str, Any] | None = None
    for task in tasks:
        if str(task.get("id") or "") == "mail-claim-check" or str(task.get("task_type") or "") == "mail_claim_check":
            legacy_mail_cleanup_task = task
            task["id"] = "mail-cleanup"
            task["task_type"] = "mail_cleanup"
            task["label"] = "邮件_清理"
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            payload["__scheduler_definition_task_type"] = "mail_cleanup"
            payload.setdefault("max_runtime_seconds", 3600)
            task["payload"] = payload
        elif str(task.get("id") or "") == "mail-cleanup" and str(task.get("task_type") or "") == "mail_cleanup":
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            if payload.get("max_runtime_seconds") != 3600:
                payload["max_runtime_seconds"] = 3600
                task["payload"] = payload
                legacy_mail_cleanup_task = task
            if str(task.get("label") or "") != "邮件_清理":
                task["label"] = "邮件_清理"
                legacy_mail_cleanup_task = task
        elif str(task.get("id") or "") == "legacy-dynamic-daily-boss":
            legacy_daily_boss_task = task
            task["id"] = "daily-boss"
            task["task_type"] = "daily_boss"
            task["label"] = "日常_首领"
            task["source"] = "data_annotation_runtime"
            task["schedule_kind"] = "daily"
            task["schedule_times"] = ["05:00"]
            task["legacy_name"] = "日常_首领"
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            task["payload"] = {key: value for key, value in payload.items() if key != "legacy_name"}
            task["payload"].setdefault("max_runtime_seconds", 1800)
        elif str(task.get("id") or "") == "daily-boss" and str(task.get("task_type") or "") == "daily_boss":
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            if int(payload.get("max_runtime_seconds") or 0) < 1800:
                payload["max_runtime_seconds"] = 1800
                task["payload"] = payload
                legacy_daily_boss_task = task
        elif str(task.get("id") or "") == "legacy-daily-xianmeng" and str(task.get("task_type") or "") == "daily_xianmeng":
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            if int(payload.get("max_runtime_seconds") or 0) < 7200:
                payload["max_runtime_seconds"] = 7200
                task["payload"] = payload
                legacy_daily_xianmeng_task = task
        elif str(task.get("id") or "") == "legacy-dynamic-xianfu-visit":
            legacy_xianfu_visit_task = task
            task["id"] = "xianfu-visit-partner"
            task["task_type"] = "xianfu_visit_partner"
            task["label"] = "仙府_寻访仙侣"
            task["source"] = "data_annotation_runtime"
            task["schedule_kind"] = "dynamic"
            task["legacy_name"] = "仙府_寻访仙侣"
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            task["payload"] = {key: value for key, value in payload.items() if key != "legacy_name"}
        elif str(task.get("id") or "") == "legacy-dynamic-xianfu-skill":
            legacy_xianfu_skill_task = task
            task["id"] = "xianfu-learn-skill"
            task["task_type"] = "xianfu_learn_skill"
            task["label"] = "仙府_领悟绝技"
            task["source"] = "data_annotation_runtime"
            task["schedule_kind"] = "dynamic"
            task["legacy_name"] = "仙府_领悟绝技"
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            task["payload"] = {key: value for key, value in payload.items() if key != "legacy_name"}
        elif str(task.get("id") or "") == "legacy-daily-lingzu" and str(task.get("task_type") or "") in {"legacy_daily_task", "legacy_dynamic_task"}:
            legacy_daily_lingzu_task = task
            task["task_type"] = "daily_lingzu"
            task["label"] = "日常_灵祖"
            task["source"] = "data_annotation_runtime"
            task["schedule_kind"] = "daily"
            task["schedule_times"] = ["05:00"]
            task["legacy_name"] = "日常_灵祖"
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            task["payload"] = {key: value for key, value in payload.items() if key != "legacy_name"}
        elif str(task.get("id") or "") == "legacy-daily-jianling" and str(task.get("task_type") or "") in {"legacy_daily_task", "legacy_dynamic_task"}:
            legacy_daily_jianling_task = task
            task["task_type"] = "daily_jianling"
            task["label"] = "日常_剑灵"
            task["source"] = "data_annotation_runtime"
            task["schedule_kind"] = "daily"
            task["schedule_times"] = ["05:00"]
            task["legacy_name"] = "日常_剑灵"
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            task["payload"] = {key: value for key, value in payload.items() if key != "legacy_name"}
        elif str(task.get("id") or "") == "legacy-daily-lingta" and str(task.get("task_type") or "") in {"legacy_daily_task", "legacy_dynamic_task"}:
            legacy_daily_lingta_task = task
            task["task_type"] = "daily_lingta"
            task["label"] = "日常_灵塔"
            task["source"] = "data_annotation_runtime"
            task["schedule_kind"] = "daily"
            task["schedule_times"] = ["05:00"]
            task["legacy_name"] = "日常_灵塔"
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            task["payload"] = {key: value for key, value in payload.items() if key != "legacy_name"}
        elif str(task.get("id") or "") == "legacy-daily-xianyuan" and str(task.get("task_type") or "") in {"legacy_daily_task", "legacy_dynamic_task"}:
            legacy_daily_xianyuan_task = task
            task["task_type"] = "daily_xianyuan"
            task["label"] = "日常_挑战仙缘"
            task["source"] = "data_annotation_runtime"
            task["schedule_kind"] = "daily"
            task["schedule_times"] = ["05:00"]
            task["legacy_name"] = "日常_挑战仙缘"
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            task["payload"] = {key: value for key, value in payload.items() if key != "legacy_name"}
        elif str(task.get("id") or "") == "legacy-daily-assistant" and str(task.get("task_type") or "") in {"legacy_daily_task", "legacy_dynamic_task"}:
            legacy_daily_assistant_task = task
            task["task_type"] = "daily_assistant"
            task["label"] = "日常_助手"
            task["source"] = "data_annotation_runtime"
            task["schedule_kind"] = "daily"
            task["schedule_times"] = ["00:00", "06:00", "12:00", "18:00"]
            task["legacy_name"] = "日常_助手"
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            task["payload"] = {key: value for key, value in payload.items() if key != "legacy_name"}
        elif str(task.get("id") or "") == "legacy-daily-shuangxiu" and str(task.get("task_type") or "") in {"legacy_daily_task", "legacy_dynamic_task"}:
            legacy_daily_shuangxiu_task = task
            task["task_type"] = "daily_shuangxiu"
            task["label"] = "日常_双修"
            task["source"] = "data_annotation_runtime"
            task["schedule_kind"] = "daily"
            task["schedule_times"] = ["05:00"]
            task["legacy_name"] = "日常_双修"
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            task["payload"] = {key: value for key, value in payload.items() if key not in {"legacy_name", "args"}}
        elif str(task.get("id") or "") == "legacy-daily-dungeon" and str(task.get("task_type") or "") in {"legacy_daily_task", "legacy_dynamic_task"}:
            legacy_daily_dungeon_task = task
            task["task_type"] = "daily_dungeon"
            task["label"] = "日常_每日副本"
            task["source"] = "data_annotation_runtime"
            task["schedule_kind"] = "daily"
            task["schedule_times"] = ["05:00"]
            task["legacy_name"] = "日常_每日副本"
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            task["payload"] = {key: value for key, value in payload.items() if key not in {"legacy_name", "args"}}
        elif str(task.get("id") or "") == "legacy-daily-yaowang" and str(task.get("task_type") or "") in {"legacy_daily_task", "legacy_dynamic_task"}:
            legacy_daily_yaowang_task = task
            task["task_type"] = "daily_yaowang"
            task["label"] = "日常_妖王来袭"
            task["source"] = "data_annotation_runtime"
            task["schedule_kind"] = "daily"
            task["schedule_times"] = ["05:00"]
            task["legacy_name"] = "日常_妖王来袭"
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            task["payload"] = {key: value for key, value in payload.items() if key not in {"legacy_name", "args"}}
        elif str(task.get("id") or "") == "legacy-daily-yaozu" and str(task.get("task_type") or "") in {"legacy_daily_task", "legacy_dynamic_task"}:
            legacy_daily_yaozu_task = task
            task["task_type"] = "daily_yaozu"
            task["label"] = "日常_妖族袭城"
            task["source"] = "data_annotation_runtime"
            task["schedule_kind"] = "daily"
            task["schedule_times"] = ["05:00"]
            task["legacy_name"] = "日常_妖族袭城"
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            task["payload"] = {key: value for key, value in payload.items() if key not in {"legacy_name", "args"}}
        elif str(task.get("id") or "") == "legacy-daily-vip" and str(task.get("task_type") or "") in {"legacy_daily_task", "legacy_dynamic_task"}:
            legacy_daily_vip_task = task
            task["task_type"] = "daily_vip"
            task["label"] = "日常_vip"
            task["source"] = "data_annotation_runtime"
            task["schedule_kind"] = "daily"
            task["schedule_times"] = ["00:00"]
            task["legacy_name"] = "日常_vip"
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            task["payload"] = {key: value for key, value in payload.items() if key not in {"legacy_name", "args"}}
    obsolete_task_ids = {
        "gift-code-real-test",
        "gift-code-test-real",
        "real-test-gift-code",
        "mail-full-scan",
        "legacy-daily-jianling",
        "legacy-daily-youli",
        "legacy-daily-yihuo",
        *_OBSOLETE_ASSISTANT_COVERED_TASK_IDS,
    }
    obsolete_task_types = set(_OBSOLETE_ASSISTANT_COVERED_TASK_TYPES)
    obsolete_task_labels = {"真实测试礼包码", "邮件_全量遍历", "日常_剑灵", "日常_游历", "日常_异火", *_OBSOLETE_ASSISTANT_COVERED_TASK_LABELS}
    before_cleanup_count = len(tasks)
    tasks = [
        task
        for task in tasks
        if str(task.get("id") or "") not in obsolete_task_ids
        and str(task.get("task_type") or "") not in obsolete_task_types
        and not is_deprecated_data_annotation_job_type(str(task.get("task_type") or ""))
        and str(task.get("label") or "").strip() not in obsolete_task_labels
    ]
    changed = len(tasks) != before_cleanup_count
    if legacy_mail_cleanup_task is not None:
        changed = True
    if legacy_daily_boss_task is not None:
        changed = True
    if legacy_xianfu_visit_task is not None:
        changed = True
    if legacy_xianfu_skill_task is not None:
        changed = True
    if legacy_daily_lingzu_task is not None:
        changed = True
    if legacy_daily_jianling_task is not None:
        changed = True
    if legacy_daily_lingta_task is not None:
        changed = True
    if legacy_daily_xianyuan_task is not None:
        changed = True
    if legacy_daily_assistant_task is not None:
        changed = True
    if legacy_daily_shuangxiu_task is not None:
        changed = True
    if legacy_daily_dungeon_task is not None:
        changed = True
    if legacy_daily_yaowang_task is not None:
        changed = True
    if legacy_daily_yaozu_task is not None:
        changed = True
    if legacy_daily_vip_task is not None:
        changed = True
    if legacy_daily_xianmeng_task is not None:
        changed = True
    defaults_by_id = {
        str(task.get("id") or ""): task
        for task in default_tasks
        if str(task.get("id") or "")
    }
    for task in tasks:
        default_task = defaults_by_id.get(str(task.get("id") or ""))
        if not default_task:
            continue
        previous_task_type = str(task.get("task_type") or "")
        default_task_type = str(default_task.get("task_type") or "")
        for key in ("task_type", "source", "schedule_kind", "legacy_name", "schedule_times", "window"):
            task[key] = default_task.get(key)
        default_payload = default_task.get("payload") if isinstance(default_task.get("payload"), dict) else {}
        task_payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
        definition_marker = "__scheduler_definition_task_type"
        marker_matches = str(task_payload.get(definition_marker) or "") == default_task_type
        is_migrated_legacy_task = (
            previous_task_type in {"legacy_daily_task", "legacy_dynamic_task"}
            and default_task_type not in {"legacy_daily_task", "legacy_dynamic_task"}
        )
        if is_migrated_legacy_task and (
            previous_task_type != default_task_type or not marker_matches
        ):
            for key in ("label", "enabled", "interruptible", "cooldown_seconds"):
                task[key] = default_task.get(key)
            task_payload = {}
        task["payload"] = {**default_payload, **task_payload}
        task["payload"][definition_marker] = default_task_type
        if (
            str(task.get("id") or "") in {
                "daily-boss",
                "xianfu-visit-partner",
                "xianfu-learn-skill",
                "legacy-daily-jianling",
                "legacy-daily-xianyuan",
                "legacy-daily-assistant",
                "legacy-daily-dungeon",
            }
            and task.get("cooldown_seconds") != default_task.get("cooldown_seconds")
        ):
            task["cooldown_seconds"] = default_task.get("cooldown_seconds")
            changed = True
        if str(task.get("id") or "") == "legacy-daily-assistant":
            for key in ("schedule_times", "cooldown_seconds"):
                if task.get(key) != default_task.get(key):
                    task[key] = default_task.get(key)
                    changed = True
        if (
            str(task.get("id") or "") in _STANDARD_ENABLED_TASK_IDS
            and default_task.get("enabled") is True
            and task.get("enabled") is not True
        ):
            task["enabled"] = True
            changed = True
    by_id = {str(task.get("id") or ""): task for task in tasks}
    if len(by_id) != len(tasks):
        deduped: dict[str, dict[str, Any]] = {}
        for task in tasks:
            task_id = str(task.get("id") or "")
            if not task_id:
                continue
            existing = deduped.get(task_id)
            if existing is None:
                deduped[task_id] = task
                continue
            if existing.get("enabled") is False and task.get("enabled"):
                existing["enabled"] = True
            for key in ("last_run_at", "last_result", "retry_after", "checkpoint"):
                if not existing.get(key) and task.get(key):
                    existing[key] = task.get(key)
        tasks = list(deduped.values())
        by_id = {str(task.get("id") or ""): task for task in tasks}
        changed = True
    for default_task in defaults_by_id.values():
        task_id = str(default_task.get("id") or "")
        if task_id and task_id not in by_id:
            task = normalize_data_annotation_scheduler_task(default_task) or default_task
            tasks.append(task)
            changed = True
    if sync_data_annotation_scheduler_tasks_from_world_facts(tasks, facts, now=now):
        changed = True
    current_time = now or datetime.now()
    current_ts = current_time.timestamp()
    for task in tasks:
        daily_retry_deferred = False
        if str(task.get("schedule_kind") or "") == "manual" and task.get("enabled"):
            task["enabled"] = False
            changed = True
        if (
            task.get("enabled")
            and str(task.get("last_result") or "") == "success"
            and task.get("retry_after")
        ):
            next_ts = parse_data_annotation_task_time(task.get("next_time"))
            if next_ts is not None and next_ts > current_ts:
                task["retry_after"] = None
                changed = True
        if task.get("enabled") and task.get("retry_after"):
            deferred_next_time = _daily_retry_should_defer_to_next_trigger(task, current_time)
            if deferred_next_time:
                task["next_time"] = deferred_next_time
                task["retry_after"] = None
                checkpoint = task.get("checkpoint") if isinstance(task.get("checkpoint"), dict) else {}
                checkpoint.pop("manual_inspection_note", None)
                if checkpoint:
                    task["checkpoint"] = checkpoint
                else:
                    task["checkpoint"] = None
                daily_retry_deferred = True
                changed = True
        if (
            task.get("enabled")
            and str(task.get("last_result") or "") in _UNSCHEDULED_MANUAL_RESULTS
        ):
            if task.get("next_time") or task.get("retry_after"):
                task["next_time"] = None
                task["retry_after"] = None
                changed = True
        if (
            task.get("enabled")
            and str(task.get("last_result") or "") in {"error", "stopped", "skipped", "unsupported"}
            and task.get("next_time")
            and str(task.get("id") or "") not in _DAILY_RETRY_DEFER_TO_NEXT_TRIGGER_TASK_IDS
        ):
            task["next_time"] = None
            changed = True
        if (
            task.get("enabled")
            and str(task.get("last_result") or "") in {"error", "stopped", "skipped", "unsupported"}
            and not task.get("retry_after")
            and not (
                str(task.get("id") or "") in _DAILY_RETRY_DEFER_TO_NEXT_TRIGGER_TASK_IDS
                and task.get("next_time")
            )
        ):
            cooldown_seconds = int(task.get("cooldown_seconds") or 600)
            task["next_time"] = None
            task["retry_after"] = datetime.fromtimestamp(current_ts + cooldown_seconds).strftime("%Y-%m-%d %H:%M:%S")
            changed = True
        if (
            task.get("enabled")
            and str(task.get("schedule_kind") or "") == "daily"
            and str(task.get("last_result") or "") not in _UNSCHEDULED_MANUAL_RESULTS
            and not task.get("next_time")
            and not task.get("retry_after")
        ):
            next_time = next_data_annotation_scheduler_time(task, current_time)
            if next_time:
                task["next_time"] = next_time
                changed = True
        if (
            task.get("enabled")
            and str(task.get("id") or "") in _XIANFU_INITIAL_CHECK_TASK_IDS
            and str(task.get("schedule_kind") or "") == "dynamic"
            and not task.get("next_time")
            and not task.get("retry_after")
            and not str(task.get("last_result") or "")
        ):
            task["next_time"] = _xianfu_initial_check_time(current_time)
            changed = True
        if (
            task.get("enabled")
            and str(task.get("schedule_kind") or "") == "daily"
            and str(task.get("last_result") or "") not in {"error", "stopped", "skipped", "unsupported", *_UNSCHEDULED_MANUAL_RESULTS}
            and not task.get("retry_after")
            and not daily_retry_deferred
            and not _daily_task_success_today(task, current_time)
        ):
            today_time = _daily_first_schedule_time_today(task, current_time)
            today_ts = parse_data_annotation_task_time(today_time)
            actual_ts = parse_data_annotation_task_time(task.get("next_time"))
            if today_time and today_ts is not None and (
                actual_ts is None
                or actual_ts > current_ts
                or datetime.fromtimestamp(actual_ts).date() > current_time.date()
            ):
                task["next_time"] = today_time
                changed = True
        if (
            task.get("enabled")
            and str(task.get("schedule_kind") or "") == "daily"
            and str(task.get("last_result") or "") == "success"
            and isinstance(task.get("schedule_times"), list)
            and len([value for value in task.get("schedule_times", []) if str(value or "").strip()]) > 1
            and not task.get("retry_after")
        ):
            expected_next_time = next_data_annotation_scheduler_time(task, current_time)
            expected_ts = parse_data_annotation_task_time(expected_next_time)
            actual_ts = parse_data_annotation_task_time(task.get("next_time"))
            if (
                expected_next_time
                and expected_ts is not None
                and expected_ts > current_ts
                and actual_ts is not None
                and actual_ts > current_ts
                and task.get("next_time") != expected_next_time
            ):
                task["next_time"] = expected_next_time
                changed = True
    for task in tasks:
        if not task_supported(task) and task.get("enabled"):
            task["enabled"] = False
            task["last_result"] = "unsupported"
            changed = True
    if raw != tasks:
        changed = True
    return tasks, changed


def data_annotation_scheduler_task_plan_reason(
    task: dict[str, Any],
    due: bool,
    *,
    task_supported: TaskSupported,
    now_ts: float | None = None,
) -> str:
    return scheduled_task_plan_reason(
        task,
        due,
        task_supported=task_supported,
        now=now_ts,
    )


def data_annotation_world_facts_summary(facts: dict[str, Any]) -> dict[str, Any]:
    discoveries = facts.get("discoveries") if isinstance(facts.get("discoveries"), dict) else {}
    runtime = facts.get("runtime") if isinstance(facts.get("runtime"), dict) else {}
    guard = facts.get("guard") if isinstance(facts.get("guard"), dict) else {}
    events = facts.get("events") if isinstance(facts.get("events"), list) else []
    return {
        "updated_at": facts.get("updated_at"),
        "current_scene": runtime.get("current_scene"),
        "runtime_status": runtime.get("status") or "",
        "runtime_task": runtime.get("current_task") or "",
        "guard_enabled": bool(guard.get("enabled")),
        "guard_running": bool(guard.get("running")),
        "scene_count": len(discoveries.get("scene") or {}) if isinstance(discoveries.get("scene"), dict) else 0,
        "popup_count": len(discoveries.get("popup") or {}) if isinstance(discoveries.get("popup"), dict) else 0,
        "occlusion_count": len(discoveries.get("occlusion") or {}) if isinstance(discoveries.get("occlusion"), dict) else 0,
        "task_fact_count": len(discoveries.get("task") or {}) if isinstance(discoveries.get("task"), dict) else 0,
        "last_events": [item for item in events[-5:] if isinstance(item, dict)],
    }


def build_data_annotation_scheduler_plan(
    tasks: list[dict[str, Any]],
    runtime: dict[str, Any],
    facts: dict[str, Any],
    scheduler_state_path: Path,
    *,
    task_supported: TaskSupported,
    task_due: TaskDue,
    now_ts: float | None = None,
) -> dict[str, Any]:
    discoveries = facts.get("discoveries") if isinstance(facts.get("discoveries"), dict) else {}
    task_facts = discoveries.get("task") if isinstance(discoveries.get("task"), dict) else {}
    daily_audit = discoveries.get("daily_audit") if isinstance(discoveries.get("daily_audit"), dict) else {}
    runtime_running = bool(runtime.get("running"))
    current_ts = time.time() if now_ts is None else now_ts
    audit_updated_at = float(daily_audit.get("updated_at") or 0) if isinstance(daily_audit, dict) else 0.0
    audit_date = datetime.fromtimestamp(audit_updated_at).date() if audit_updated_at > 0 else None
    current_date = datetime.fromtimestamp(current_ts).date()
    audit_incomplete_ids = {
        str(task_id)
        for task_id in (daily_audit.get("incomplete_task_ids") or [])
        if str(task_id)
    } if isinstance(daily_audit, dict) and audit_date == current_date else set()

    def visual_audit_task_due(task: dict[str, Any]) -> bool:
        task_id = str(task.get("id") or "")
        if task_id in audit_incomplete_ids:
            last_run_ts = parse_data_annotation_task_time(task.get("last_run_at"))
            if last_run_ts is None or audit_updated_at > last_run_ts + 1.0:
                return True
        return task_due(task)

    plan = build_scheduled_task_plan(
        tasks,
        runtime_running=runtime_running,
        runtime_task=str(runtime.get("current_task") or runtime.get("task_type") or ""),
        task_supported=task_supported,
        task_due=visual_audit_task_due,
        task_facts=task_facts,
        now=current_ts,
    )
    return {
        "next_action": plan["next_action"],
        "message": plan["message"],
        "runtime": {
            "running": runtime_running,
            "status": runtime.get("status") or "",
            "current_task": runtime.get("current_task") or "",
            "current_task_id": runtime.get("current_task_id") or "",
            "task_type": runtime.get("task_type") or "",
            "phase": runtime.get("phase") or "",
            "current_scene": runtime.get("current_scene"),
            "interruptible": bool(runtime.get("interruptible", True)),
        },
        "facts_summary": data_annotation_world_facts_summary(facts),
        "daily_audit": daily_audit,
        "due_tasks": plan["due_tasks"],
        "tasks": plan["tasks"],
        "path": str(scheduler_state_path),
    }


def data_annotation_scheduler_run_now_task(
    tasks: list[dict[str, Any]],
    task_id: str,
    payload_override: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    return scheduled_task_run_copy(tasks, task_id, payload_override)

