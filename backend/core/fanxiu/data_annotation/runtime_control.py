from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pyxllib.prog import (
    append_status_log,
    filter_status_logs,
    job_payload_values,
    remove_job_by_id,
    repair_orphaned_scheduled_runs,
    scheduled_task_payload_with_meta,
    select_due_scheduled_tasks,
)

from backend.core.fanxiu.runtime.behavior_tree import (
    data_annotation_asset_tree_path,
    ensure_fanxiu_behavior_tree_service,
    ensure_fanxiu_runtime_jobs_registered,
    fanxiu_data_annotation_manual_job_state_path,
    fanxiu_data_annotation_runtime_state_path,
    fanxiu_data_annotation_scheduler_settings_path,
    fanxiu_data_annotation_scheduler_state_path,
    fanxiu_data_annotation_world_facts_path,
    fanxiu_runtime_guard_definitions,
    get_fanxiu_runtime_runner,
    fanxiu_runtime_runner_running,
    fanxiu_runtime_runner_status,
    fanxiu_runtime_runner_wake,
    fanxiu_runtime_task_label,
    read_fanxiu_behavior_tree_service_owner,
    replace_fanxiu_runtime_logs,
    set_fanxiu_runtime_guard,
    set_fanxiu_runtime_guard_group_enabled,
    start_fanxiu_manual_runtime_task,
    stop_fanxiu_behavior_tree_current_task,
)
from backend.core.fanxiu.data_annotation.jobs import (
    create_data_annotation_manual_job,
    data_annotation_manual_jobs_state,
    get_fanxiu_data_annotation_manual_job_definition,
    is_deprecated_data_annotation_job_type,
    pop_next_data_annotation_manual_job,
    read_data_annotation_manual_jobs,
    requeue_running_data_annotation_manual_jobs,
)
from backend.core.fanxiu.data_annotation.scheduler import (
    build_data_annotation_scheduler_plan,
    merge_data_annotation_scheduler_task_updates,
    preserve_data_annotation_scheduler_runtime_state,
    data_annotation_scheduler_run_now_task,
    data_annotation_scheduler_time_order_key,
    data_annotation_scheduler_task_plan_reason,
    data_annotation_world_facts_summary,
    repair_data_annotation_scheduler_tasks,
    sync_data_annotation_scheduler_tasks_from_world_facts,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import default_data_annotation_scheduler_tasks
from backend.core.fanxiu.data_annotation.runner import create_fanxiu_runtime_runner
from backend.core.fanxiu.data_annotation.state import (
    append_data_annotation_runtime_log_once,
    data_annotation_runtime_owner_message,
    data_annotation_scheduler_task_state,
    data_annotation_task_due,
    is_data_annotation_runtime_live_empty,
    normalize_data_annotation_runtime_display,
    normalize_data_annotation_runtime_logs_for_display,
    next_data_annotation_scheduler_time,
    normalize_data_annotation_runtime_guard_items,
    normalize_data_annotation_scheduler_settings,
    persist_data_annotation_runtime_status,
    read_data_annotation_json,
    read_data_annotation_runtime_status,
    read_data_annotation_world_facts,
    record_data_annotation_scheduler_task_fact,
    write_data_annotation_json,
    write_data_annotation_world_facts,
)
from backend.core.runtime.process_launcher import popen_python_script_service
from backend.core.temp_paths import codeyun_temp_root
from backend.core.fanxiu.runtime.behavior_tree_service import stop_behavior_tree_service


def _canonical_runtime_task_type(task_type: str) -> str:
    task_type = str(task_type or "").strip()
    if task_type == "mail_claim_check":
        return "mail_cleanup"
    return task_type


def read_world_facts(path: Path | None = None) -> dict[str, Any]:
    return read_data_annotation_world_facts(path or fanxiu_data_annotation_world_facts_path())


def doctor_watch_latest_path() -> Path:
    return codeyun_temp_root("fanxiu-watch") / "doctor_watch_latest.json"


def doctor_watch_heartbeat_path() -> Path:
    return codeyun_temp_root("fanxiu-watch") / "doctor_watch_heartbeat.json"


def read_doctor_watch_heartbeat(path: Path | None = None, *, stale_after_seconds: float = 180.0) -> dict[str, Any]:
    heartbeat_path = path or doctor_watch_heartbeat_path()
    payload = read_data_annotation_json(heartbeat_path, None)
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "exists": heartbeat_path.exists(),
            "path": str(heartbeat_path),
            "active": False,
            "age_seconds": None,
            "message": "巡检心跳不存在或不是 JSON object",
        }
    try:
        updated_at = float(payload.get("updated_at") or 0)
    except (TypeError, ValueError):
        updated_at = 0.0
    age_seconds = max(0.0, time.time() - updated_at) if updated_at > 0 else None
    expected_stable_latest_path = str(doctor_watch_latest_path())
    actual_stable_latest_path = str(payload.get("stable_latest_path") or "")
    runtime_consistent = bool(actual_stable_latest_path) and actual_stable_latest_path == expected_stable_latest_path
    active = isinstance(age_seconds, (int, float)) and age_seconds <= stale_after_seconds and runtime_consistent
    auto_run_due_enabled = bool(payload.get("auto_run_due_enabled"))
    message = "巡检心跳正常" if active else "巡检心跳过期或路径不一致"
    return {
        "ok": True,
        "exists": True,
        "path": str(heartbeat_path),
        "active": active,
        "age_seconds": age_seconds,
        "stale_after_seconds": stale_after_seconds,
        "runtime_consistent": runtime_consistent,
        "auto_run_due_enabled": auto_run_due_enabled,
        "expected_stable_latest_path": expected_stable_latest_path,
        "message": message,
        **payload,
    }


def _doctor_watch_latest_candidates() -> list[Path]:
    stable_path = doctor_watch_latest_path()
    watch_dir = stable_path.parent
    candidates: list[Path] = [stable_path]
    legacy_fixed_path = watch_dir / "doctor_watch_until_20260616_0500.latest.json"
    if legacy_fixed_path != stable_path:
        candidates.append(legacy_fixed_path)
    try:
        sidecars = sorted(
            (path for path in watch_dir.glob("doctor_watch*.latest.json") if path not in candidates),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        sidecars = []
    candidates.extend(sidecars)
    return candidates


def _doctor_watch_direct_latest_candidates(heartbeat: dict[str, Any]) -> list[Path]:
    stable_path = doctor_watch_latest_path()
    candidates: list[Path] = []
    for key in ("latest_path", "stable_latest_path"):
        text = str(heartbeat.get(key) or "").strip()
        if not text:
            continue
        candidate = Path(text)
        if candidate not in candidates:
            candidates.append(candidate)
    if stable_path not in candidates:
        candidates.append(stable_path)
    return candidates


def read_doctor_watch_latest(path: Path | None = None) -> dict[str, Any]:
    heartbeat = read_doctor_watch_heartbeat()
    if path is not None:
        snapshot_path = path
    else:
        direct_candidates = _doctor_watch_direct_latest_candidates(heartbeat)
        snapshot_path = next((candidate for candidate in direct_candidates if candidate.exists()), None)
        if snapshot_path is None:
            snapshot_path = next(
                (candidate for candidate in _doctor_watch_latest_candidates() if candidate.exists()),
                doctor_watch_latest_path(),
            )
    payload = read_data_annotation_json(snapshot_path, None)
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "exists": snapshot_path.exists(),
            "path": str(snapshot_path),
            "snapshot": {},
            "heartbeat": heartbeat,
            "message": "巡检快照不存在或不是 JSON object",
        }
    return {
        "ok": True,
        "exists": True,
        "path": str(snapshot_path),
        "snapshot": payload,
        "heartbeat": heartbeat,
        "message": str(payload.get("summary") or ""),
    }


def ensure_doctor_watch_background(
    *,
    interval_seconds: float = 60.0,
    duration_seconds: float = 0.0,
    log_limit: int = 80,
    include_screenshot: bool = True,
    screenshot_every: int = 10,
    stale_after_seconds: float = 180.0,
    auto_run_due: bool = True,
) -> dict[str, Any]:
    heartbeat = read_doctor_watch_heartbeat(stale_after_seconds=stale_after_seconds)
    capability_consistent = (not auto_run_due) or bool(heartbeat.get("auto_run_due_enabled"))
    if bool(heartbeat.get("active")) and capability_consistent:
        return {
            "ok": True,
            "started": False,
            "reason": "heartbeat_recent",
            "heartbeat": heartbeat,
            "latest": read_doctor_watch_latest(),
        }

    watch_dir = codeyun_temp_root("fanxiu-watch")
    watch_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = watch_dir / f"doctor_watch_api_{stamp}.ndjson"
    stdout_path = watch_dir / f"doctor_watch_api_{stamp}.stdout.log"
    stderr_path = watch_dir / f"doctor_watch_api_{stamp}.stderr.log"
    repo_root = Path(__file__).resolve().parents[4]
    script_path = repo_root / "scripts" / "fanxiu_bt.py"
    command_args = [
        "watch-doctor",
        "--interval-seconds",
        str(max(1.0, float(interval_seconds or 60.0))),
        "--duration-seconds",
        str(max(0.0, float(duration_seconds or 0.0))),
        "--log-limit",
        str(max(1, int(log_limit or 80))),
        "--screenshot-every",
        str(max(1, int(screenshot_every or 1))),
        "--output",
        str(output_path),
    ]
    if include_screenshot:
        command_args.append("--screenshot")
    if auto_run_due:
        command_args.append("--auto-run-due")

    stdout_fh = stdout_path.open("ab")
    stderr_fh = stderr_path.open("ab")
    try:
        process = popen_python_script_service(
            script_path,
            *command_args,
            preferred_root=repo_root,
            executable=sys.executable,
            cwd=str(repo_root),
            stdin=subprocess.DEVNULL,
            stdout=stdout_fh,
            stderr=stderr_fh,
        )
    finally:
        stdout_fh.close()
        stderr_fh.close()
    return {
        "ok": True,
        "started": True,
        "pid": process.pid,
        "reason": "heartbeat_missing_or_stale",
        "previous_heartbeat": heartbeat,
        "output_path": str(output_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "command": [str(script_path), *command_args],
    }


def write_world_facts(facts: dict[str, Any], path: Path | None = None) -> None:
    write_data_annotation_world_facts(path or fanxiu_data_annotation_world_facts_path(), facts)


def record_scheduler_task_fact(
    task: dict[str, Any],
    result: str,
    *,
    world_facts_path: Path | None = None,
) -> None:
    record_data_annotation_scheduler_task_fact(world_facts_path or fanxiu_data_annotation_world_facts_path(), task, result)


def persist_runtime_status(
    status: dict[str, Any],
    *,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> None:
    persist_data_annotation_runtime_status(
        runtime_state_path or fanxiu_data_annotation_runtime_state_path(),
        world_facts_path or fanxiu_data_annotation_world_facts_path(),
        status,
    )


def finalize_runtime_status(
    status: dict[str, Any],
    *,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    normalize_runtime_guard_items(status)
    normalize_data_annotation_runtime_display(status)
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def read_runtime_status(path: Path | None = None) -> dict[str, Any]:
    return read_data_annotation_runtime_status(path or fanxiu_data_annotation_runtime_state_path())


def append_runtime_log_once(status: dict[str, Any], kind: str, message: str) -> None:
    append_data_annotation_runtime_log_once(status, kind, message, time_text=datetime.now().strftime("%H:%M:%S"))


def normalize_runtime_guard_items(status: dict[str, Any]) -> None:
    normalize_data_annotation_runtime_guard_items(status, fanxiu_runtime_guard_definitions())


def read_manual_jobs(path: Path | None = None) -> list[dict[str, Any]]:
    raw = read_data_annotation_json(path or fanxiu_data_annotation_manual_job_state_path(), [])
    return read_data_annotation_manual_jobs(raw)


def write_manual_jobs(jobs: list[dict[str, Any]], path: Path | None = None) -> None:
    write_data_annotation_json(path or fanxiu_data_annotation_manual_job_state_path(), data_annotation_manual_jobs_state(jobs))


def requeue_running_manual_jobs(path: Path | None = None) -> int:
    jobs = read_manual_jobs(path)
    updated, changed_count = requeue_running_data_annotation_manual_jobs(jobs)
    if changed_count:
        write_manual_jobs(updated, path)
    return changed_count


def remove_manual_job(job_id: str, path: Path | None = None) -> None:
    job_id = str(job_id or "")
    if not job_id:
        return
    write_manual_jobs(remove_job_by_id(read_manual_jobs(path), job_id), path)


def enqueue_manual_job(
    task_type: str,
    payload: dict[str, Any] | None = None,
    *,
    label: str = "",
    group: str = "manual_job",
    interruptible: bool | None = None,
    manual_job_path: Path | None = None,
    created_at: float | None = None,
) -> dict[str, Any]:
    task_type = _canonical_runtime_task_type(task_type or "detect_scene") or "detect_scene"
    ensure_fanxiu_runtime_jobs_registered()
    definition = get_fanxiu_data_annotation_manual_job_definition(task_type)
    job = create_data_annotation_manual_job(
        task_type,
        payload,
        label=label,
        group=group,
        interruptible=interruptible,
        definition=definition,
        task_label=fanxiu_runtime_task_label,
        now=created_at if created_at is not None else time.time(),
    )
    jobs = read_manual_jobs(manual_job_path)
    jobs.append(job)
    write_manual_jobs(jobs, manual_job_path)
    return job


def pending_scheduler_manual_job(task_id: str, *, manual_job_path: Path | None = None) -> dict[str, Any] | None:
    task_id = str(task_id or "").strip()
    if not task_id:
        return None
    for job in read_manual_jobs(manual_job_path):
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        if str(payload.get("__scheduler_task_id") or "").strip() == task_id:
            return job
    return None


def pop_next_manual_job(path: Path | None = None) -> dict[str, Any] | None:
    jobs = read_manual_jobs(path)
    selected, claimed_jobs = pop_next_data_annotation_manual_job(jobs)
    if selected is None:
        return None
    write_manual_jobs(claimed_jobs, path)
    return selected


def repair_orphaned_scheduler_runs(
    tasks: list[dict[str, Any]],
    *,
    manual_job_path: Path | None = None,
    now_ts: float | None = None,
    running: bool | None = None,
) -> bool:
    jobs = read_manual_jobs(manual_job_path)
    current_ts = time.time() if now_ts is None else now_ts
    runner_running = fanxiu_runtime_runner_running() if running is None else running
    if runner_running:
        return False
    persisted_status = read_runtime_status()
    if bool(persisted_status.get("running")):
        try:
            updated_at = float(persisted_status.get("updated_at") or 0)
        except (TypeError, ValueError):
            updated_at = 0.0
        if updated_at > 0 and current_ts - updated_at < 120:
            return False
    kept_jobs: list[dict[str, Any]] = []
    removed_stale_scheduler_job = False
    for job in jobs:
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "")
        if str(job.get("status") or "") != "running" or not scheduler_task_id:
            kept_jobs.append(job)
            continue
        try:
            job_updated_at = float(job.get("updated_at") or job.get("created_at") or 0)
        except (TypeError, ValueError):
            job_updated_at = 0.0
        if job_updated_at > 0 and current_ts - job_updated_at < 60:
            kept_jobs.append(job)
            continue
        removed_stale_scheduler_job = True
    if removed_stale_scheduler_job:
        write_manual_jobs(kept_jobs, manual_job_path)
        return True
    pending_scheduler_ids = job_payload_values(kept_jobs, "__scheduler_task_id")
    changed = repair_orphaned_scheduled_runs(
        tasks,
        pending_task_ids=pending_scheduler_ids,
        now=current_ts,
        min_age_seconds=60,
        recovered_at_text=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    if not changed:
        return False
    for task in tasks:
        if str(task.get("last_result") or "") != "stopped" or not bool(task.get("enabled")):
            continue
        if str(task.get("schedule_kind") or "") == "manual":
            continue
        if not (isinstance(task.get("checkpoint"), dict) and task["checkpoint"].get("recovered_from_orphaned_run_at")):
            continue
        cooldown_seconds = int(task.get("cooldown_seconds") or 600)
        task["next_time"] = None
        task["retry_after"] = datetime.fromtimestamp(current_ts + cooldown_seconds).strftime("%Y-%m-%d %H:%M:%S")
    return True


def task_supported(task: dict[str, Any]) -> bool:
    ensure_fanxiu_runtime_jobs_registered()
    definition = get_fanxiu_data_annotation_manual_job_definition(_canonical_runtime_task_type(str(task.get("task_type") or "")))
    return bool(definition and definition.scheduler_supported)


def read_scheduler_tasks(
    *,
    scheduler_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    manual_job_path: Path | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    path = scheduler_state_path or fanxiu_data_annotation_scheduler_state_path()
    raw = read_data_annotation_json(path, None)
    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        default_data_annotation_scheduler_tasks(),
        read_world_facts(world_facts_path),
        task_supported=task_supported,
        now=now or datetime.now(),
    )
    if repair_orphaned_scheduler_runs(tasks, manual_job_path=manual_job_path):
        changed = True
    if changed:
        write_scheduler_tasks(tasks, scheduler_state_path=path, preserve_runtime_state=False)
    return sorted(tasks, key=data_annotation_scheduler_time_order_key)


def write_scheduler_tasks(
    tasks: list[dict[str, Any]],
    *,
    scheduler_state_path: Path | None = None,
    preserve_runtime_state: bool = True,
) -> None:
    path = scheduler_state_path or fanxiu_data_annotation_scheduler_state_path()
    payload = [data_annotation_scheduler_task_state(task) for task in tasks]
    if preserve_runtime_state:
        existing = read_data_annotation_json(path, [])
        if isinstance(existing, list):
            payload = preserve_data_annotation_scheduler_runtime_state(payload, existing)
    write_data_annotation_json(
        path,
        payload,
    )


def reset_scheduler_task_runs(
    *,
    task_ids: list[str] | None = None,
    include_disabled: bool = False,
    include_manual: bool = False,
    clear_next_time: bool = True,
    scheduler_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    tasks = read_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
    )
    selected_ids = {str(item).strip() for item in (task_ids or []) if str(item).strip()}
    target_tasks: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task.get("id") or "")
        if selected_ids and task_id not in selected_ids:
            continue
        if not include_disabled and not bool(task.get("enabled")):
            continue
        if not include_manual and str(task.get("schedule_kind") or "") == "manual":
            continue
        target_tasks.append(task)

    reset_ids = [str(task.get("id") or "") for task in target_tasks if str(task.get("id") or "")]
    facts = read_world_facts(world_facts_path)
    discoveries = facts.get("discoveries") if isinstance(facts.get("discoveries"), dict) else {}
    task_facts = discoveries.get("task") if isinstance(discoveries.get("task"), dict) else {}
    backup_dir = codeyun_temp_root("fanxiu-scheduler-reset")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"scheduler_reset_{stamp}.json"
    write_data_annotation_json(
        backup_path,
        {
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "scheduler_state_path": str(scheduler_state_path or fanxiu_data_annotation_scheduler_state_path()),
            "world_facts_path": str(world_facts_path or fanxiu_data_annotation_world_facts_path()),
            "reset_ids": reset_ids,
            "tasks": [dict(task) for task in target_tasks],
            "task_facts": {
                task_id: dict(task_facts.get(task_id) or {})
                for task_id in reset_ids
                if isinstance(task_facts.get(task_id), dict)
            },
        },
    )

    reset_fields = (
        "last_run_at",
        "last_result",
        "last_message",
        "retry_after",
        "checkpoint",
        "queued_at",
        "started_at",
        "finished_at",
        "world_fact_synced_at",
        "world_fact_updated_at",
    )
    reset_set = set(reset_ids)
    for task in tasks:
        if str(task.get("id") or "") not in reset_set:
            continue
        for key in reset_fields:
            task[key] = None
        if clear_next_time:
            task["next_time"] = None
    write_scheduler_tasks(tasks, scheduler_state_path=scheduler_state_path, preserve_runtime_state=False)

    if reset_ids and isinstance(task_facts, dict):
        changed = False
        for task_id in reset_ids:
            if task_id in task_facts:
                task_facts.pop(task_id, None)
                changed = True
        if changed:
            write_data_annotation_world_facts(
                world_facts_path or fanxiu_data_annotation_world_facts_path(),
                facts,
                preserve_existing_task_facts=False,
            )

    return {
        "reset_count": len(reset_ids),
        "reset_ids": reset_ids,
        "backup_path": str(backup_path),
        "include_disabled": include_disabled,
        "include_manual": include_manual,
        "clear_next_time": clear_next_time,
    }


def read_scheduler_settings(*, scheduler_settings_path: Path | None = None) -> dict[str, Any]:
    path = scheduler_settings_path or fanxiu_data_annotation_scheduler_settings_path()
    return normalize_data_annotation_scheduler_settings(read_data_annotation_json(path, None))


def write_scheduler_settings(
    settings: dict[str, Any],
    *,
    scheduler_settings_path: Path | None = None,
) -> dict[str, Any]:
    path = scheduler_settings_path or fanxiu_data_annotation_scheduler_settings_path()
    normalized = normalize_data_annotation_scheduler_settings(settings)
    normalized["updated_at"] = time.time()
    write_data_annotation_json(path, normalized)
    return normalized


def set_scheduler_job_group_enabled(
    enabled: bool,
    *,
    scheduler_settings_path: Path | None = None,
) -> dict[str, Any]:
    settings = read_scheduler_settings(scheduler_settings_path=scheduler_settings_path)
    settings["job_group_enabled"] = bool(enabled)
    return write_scheduler_settings(settings, scheduler_settings_path=scheduler_settings_path)


def behavior_tree_enabled(*, scheduler_settings_path: Path | None = None) -> bool:
    return bool(read_scheduler_settings(scheduler_settings_path=scheduler_settings_path).get("behavior_tree_enabled", True))


def _behavior_tree_disabled_status(
    *,
    entry_id: str = "",
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    message: str = "行为树已关闭",
) -> dict[str, Any]:
    status = runtime_status(runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    status.update({
        "behavior_tree_enabled": False,
        "running": False,
        "entry_id": entry_id or str(status.get("entry_id") or ""),
        "task_type": "",
        "current_task": "",
        "current_task_id": "",
        "status": "idle",
        "phase": "behavior_tree_disabled",
        "message": message,
        "updated_at": time.time(),
    })
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def set_behavior_tree_enabled(
    *,
    entry: Any,
    entry_id: str,
    enabled: bool,
    asset_tree_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    settings = read_scheduler_settings(scheduler_settings_path=scheduler_settings_path)
    settings["behavior_tree_enabled"] = bool(enabled)
    write_scheduler_settings(settings, scheduler_settings_path=scheduler_settings_path)
    resolved_entry_id = str(entry_id or getattr(entry, "entry_id", None) or "")
    if not enabled:
        status = ensure_runtime_service(
            entry=entry,
            entry_id=resolved_entry_id,
            asset_tree_path=asset_tree_path,
            scheduler_settings_path=scheduler_settings_path,
            runtime_state_path=runtime_state_path,
            world_facts_path=world_facts_path,
        )
        status["behavior_tree_enabled"] = False
        status["message"] = "行为树内核服务保持运行，自动调度已关闭"
        append_runtime_log_once(status, "stop", "行为树已关闭")
        persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
        return status
    status = ensure_runtime_service(
        entry=entry,
        entry_id=resolved_entry_id,
        asset_tree_path=asset_tree_path,
        scheduler_settings_path=scheduler_settings_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )
    status["behavior_tree_enabled"] = True
    return status


def update_scheduler_tasks(
    updates: list[dict[str, Any]],
    *,
    scheduler_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    manual_job_path: Path | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    tasks = merge_data_annotation_scheduler_task_updates(
        read_scheduler_tasks(
            scheduler_state_path=scheduler_state_path,
            world_facts_path=world_facts_path,
            manual_job_path=manual_job_path,
            now=now,
        ),
        updates,
        now=now or datetime.now(),
    )
    write_scheduler_tasks(tasks, scheduler_state_path=scheduler_state_path)
    return read_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
        manual_job_path=manual_job_path,
        now=now,
    )


def runtime_status(
    *,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    owner = read_fanxiu_behavior_tree_service_owner() if runtime_state_path is None else {}
    owner_active_elsewhere = (
        bool(owner.get("active"))
        and not bool(owner.get("stale"))
        and int(owner.get("pid") or 0) != os.getpid()
    )
    persisted = read_runtime_status(runtime_state_path)
    if owner_active_elsewhere and isinstance(persisted, dict) and persisted:
        status = dict(persisted)
        owner_step = str(owner.get("step") or "")
        if bool(status.get("running")) and owner_step in {
            "idle_guard",
            "idle_guard_done",
            "manual_job_poll",
            "scheduler_poll",
            "scheduler_isolated",
            "waiting_context",
        }:
            status["running"] = False
            status["status"] = "idle"
            status["phase"] = owner_step
            status["message"] = data_annotation_runtime_owner_message(owner.get("pid"), owner_step)
            status["current_task"] = ""
            status["current_task_id"] = ""
            status["task_type"] = ""
            status["updated_at"] = time.time()
    else:
        status = fanxiu_runtime_runner_status()
    if persisted and not owner_active_elsewhere and is_data_annotation_runtime_live_empty(status):
        status.update(persisted)
        status["running"] = False
        status["guard_running"] = False
        status["service_running"] = False
        status["updated_at"] = time.time()
        if persisted.get("running"):
            status["status"] = "stopped"
            status["phase"] = "stopped"
            status["message"] = "后端已重载，运行状态已结束"
            status["finished_at"] = status.get("finished_at") or time.time()
            append_runtime_log_once(status, "stop", "后端已重载，运行状态已结束")
        elif persisted.get("guard_enabled") or persisted.get("guard_running"):
            status["status"] = "idle"
            status["message"] = "后端已重载，行为树服务待恢复"
            append_runtime_log_once(status, "stop", "后端已重载，行为树服务待恢复")
    status["behavior_tree_enabled"] = behavior_tree_enabled(scheduler_settings_path=scheduler_settings_path)
    normalize_runtime_guard_items(status)
    normalize_data_annotation_runtime_display(status)
    status.pop("priority", None)
    if owner_active_elsewhere:
        return status
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def ensure_runtime_service(
    *,
    entry: Any,
    entry_id: str,
    asset_tree_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    resolved_entry_id = str(entry_id or getattr(entry, "entry_id", None) or "")
    status = ensure_fanxiu_behavior_tree_service(
        entry,
        resolved_entry_id,
        asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(resolved_entry_id),
    )
    status["behavior_tree_enabled"] = behavior_tree_enabled(scheduler_settings_path=scheduler_settings_path)
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def restart_runtime_kernel(
    *,
    entry: Any,
    entry_id: str,
    timeout_seconds: float = 5.0,
    asset_tree_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    runner = get_fanxiu_runtime_runner()
    stop_service = getattr(runner, "stop_service", None)
    stopped_status: dict[str, Any] = {}
    if callable(stop_service):
        stopped_status = stop_service(timeout_seconds=max(0.1, float(timeout_seconds or 5.0)))
    status = ensure_runtime_service(
        entry=entry,
        entry_id=entry_id,
        asset_tree_path=asset_tree_path,
        scheduler_settings_path=scheduler_settings_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )
    status["kernel_restart"] = {
        "ran": True,
        "previous_service_running": bool(stopped_status.get("service_running")),
        "service_running": bool(status.get("service_running")),
    }
    normalize_data_annotation_runtime_display(status)
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def stop_current_task(
    entry_id: str,
    *,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    status = stop_fanxiu_behavior_tree_current_task(entry_id)
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def set_runtime_guard(
    *,
    entry: Any,
    entry_id: str,
    guard_id: str,
    enabled: bool,
    interval_seconds: float,
    asset_tree_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    resolved_entry_id = str(entry_id or getattr(entry, "entry_id", None) or "")
    status = set_fanxiu_runtime_guard(
        entry=entry,
        entry_id=resolved_entry_id,
        guard_id=guard_id,
        enabled=enabled,
        interval_seconds=interval_seconds,
        asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(resolved_entry_id),
    )
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def set_runtime_guard_group_enabled(
    *,
    entry: Any,
    entry_id: str,
    enabled: bool,
    asset_tree_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    resolved_entry_id = str(entry_id or getattr(entry, "entry_id", None) or "")
    status = set_fanxiu_runtime_guard_group_enabled(
        entry=entry,
        entry_id=resolved_entry_id,
        enabled=enabled,
        asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(resolved_entry_id),
    )
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def submit_tick_task(
    *,
    entry: Any,
    entry_id: str,
    task_type: str | None = None,
    payload: dict[str, Any] | None = None,
    asset_tree_path: Path | None = None,
    manual_job_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    resolved_task_type = str(task_type or "detect_scene").strip() or "detect_scene"
    if resolved_task_type == "manual_tick":
        resolved_task_type = "detect_scene"
    return submit_runtime_task_cell(
        entry=entry,
        entry_id=entry_id,
        task_type=resolved_task_type,
        payload=payload,
        label="单步识别" if resolved_task_type == "detect_scene" else "",
        asset_tree_path=asset_tree_path,
        manual_job_path=manual_job_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )


def execute_runtime_tick(
    *,
    entry: Any,
    entry_id: str,
    guard: bool = True,
    manual_job: bool = True,
    scheduled_job: bool = True,
    run_mode: str = "tick_once",
    max_ticks: int = 10,
    timeout_seconds: float = 30.0,
    asset_tree_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    ensure_status = ensure_runtime_service(
        entry=entry,
        entry_id=entry_id,
        asset_tree_path=asset_tree_path,
        scheduler_settings_path=scheduler_settings_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )
    if not bool(ensure_status.get("behavior_tree_enabled", True)):
        ensure_status["cell_tick"] = {
            "ran": False,
            "reason": "kernel_disabled",
            "guard": bool(guard),
            "manual_job": bool(manual_job),
            "scheduled_job": bool(scheduled_job),
            "run_mode": str(run_mode or "tick_once"),
            "ticks": 0,
        }
        return finalize_runtime_status(
            ensure_status,
            runtime_state_path=runtime_state_path,
            world_facts_path=world_facts_path,
        )
    status = get_fanxiu_runtime_runner().run_service_ticks(
        guard=guard,
        manual_job=manual_job,
        scheduled_job=scheduled_job,
        run_mode=run_mode,
        max_ticks=max_ticks,
        timeout_seconds=timeout_seconds,
    )
    status["entry_id"] = str(entry_id or getattr(entry, "entry_id", None) or "")
    return finalize_runtime_status(
        status,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )


def runtime_logs(
    *,
    limit: int = 500,
    scope: str = "",
    item_id: str = "",
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> list[dict[str, Any]]:
    status = runtime_status(runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return normalize_data_annotation_runtime_logs_for_display(filter_status_logs(status, limit=limit, scope=scope, item_id=item_id))


def clear_runtime_logs(
    *,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> list[dict[str, Any]]:
    status = fanxiu_runtime_runner_status()
    status["logs"] = []
    replace_fanxiu_runtime_logs([])
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return []


def queue_runtime_task_cell_status(
    *,
    entry: Any,
    entry_id: str,
    task_type: str,
    payload: dict[str, Any] | None = None,
    label: str = "",
    group: str = "manual_job",
    interruptible: bool | None = None,
    created_at: float | None = None,
    asset_tree_path: Path | None = None,
    manual_job_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    ensure_fanxiu_behavior_tree_service(entry, entry_id, asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(entry_id))
    job = enqueue_manual_job(
        task_type,
        payload,
        label=label,
        group=group,
        interruptible=interruptible,
        manual_job_path=manual_job_path,
        created_at=created_at,
    )
    fanxiu_runtime_runner_wake()
    status = fanxiu_runtime_runner_status()
    status.update({
        "entry_id": entry_id,
        "phase": "manual_job_queued",
        "message": f"task cell 已排队：{job.get('label') or job.get('task_type')}",
        "queued_job": {
            "id": job.get("id"),
            "task_type": job.get("task_type"),
            "label": job.get("label"),
            "group": job.get("group"),
            "status": job.get("status"),
            "created_at": job.get("created_at"),
        },
        "updated_at": time.time(),
    })
    append_status_log(
        status,
        "info",
        f"[{job.get('id')}] {status['message']}",
        scope="manual_job",
        item_id="manual_job",
        time_text=datetime.now().strftime("%H:%M:%S"),
        update_timestamp=False,
    )
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def submit_runtime_task_cell(
    *,
    entry: Any,
    entry_id: str,
    task_type: str,
    payload: dict[str, Any] | None = None,
    label: str = "",
    group: str = "manual_job",
    interruptible: bool | None = None,
    created_at: float | None = None,
    asset_tree_path: Path | None = None,
    manual_job_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    task_type = _canonical_runtime_task_type(task_type or "detect_scene") or "detect_scene"
    if is_deprecated_data_annotation_job_type(task_type):
        raise ValueError(f"作业已删除，不再支持：{task_type}")
    definition = get_fanxiu_data_annotation_manual_job_definition(task_type)
    if definition is None:
        raise ValueError(f"暂不支持的任务类型：{task_type}")
    return queue_runtime_task_cell_status(
        entry=entry,
        entry_id=entry_id,
        task_type=task_type,
        payload=payload,
        label=label,
        group=group,
        interruptible=interruptible if interruptible is not None else definition.interruptible,
        created_at=created_at,
        asset_tree_path=asset_tree_path,
        manual_job_path=manual_job_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )

def start_next_manual_job_if_idle(
    entry: Any,
    entry_id: str,
    *,
    manual_job_path: Path | None = None,
    asset_tree_path: Path | None = None,
) -> dict[str, Any] | None:
    if fanxiu_runtime_runner_running():
        return None
    task = pop_next_manual_job(manual_job_path)
    if task is None:
        return None
    return start_fanxiu_manual_runtime_task(
        entry=entry,
        entry_id=entry_id,
        task=task,
        asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(entry_id),
    )


def sync_scheduler_tasks_from_world_facts(
    tasks: list[dict[str, Any]],
    *,
    world_facts_path: Path | None = None,
    now: datetime | None = None,
) -> bool:
    return sync_data_annotation_scheduler_tasks_from_world_facts(
        tasks,
        read_world_facts(world_facts_path),
        now=now or datetime.now(),
    )


def scheduler_task_view(task: dict[str, Any]) -> dict[str, Any]:
    return {
        **task,
        "supported": task_supported(task),
        "template_id": str(task.get("template_id") or task.get("task_type") or ""),
        "template_label": str(task.get("template_label") or task.get("label") or task.get("task_type") or ""),
        "template_source": str(task.get("template_source") or "preset"),
        "trigger_kind": str(task.get("trigger_kind") or task.get("schedule_kind") or "manual"),
    }


def scheduler_task_plan_reason(task: dict[str, Any], due: bool) -> str:
    return data_annotation_scheduler_task_plan_reason(
        task,
        due,
        task_supported=task_supported,
        now_ts=time.time(),
    )


def world_facts_summary(facts: dict[str, Any]) -> dict[str, Any]:
    return data_annotation_world_facts_summary(facts)


def build_scheduler_plan(
    *,
    entry: Any | None = None,
    entry_id: str | None = None,
    asset_tree_path: Path | None = None,
    scheduler_state_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    world_facts_path: Path | None = None,
    manual_job_path: Path | None = None,
) -> dict[str, Any]:
    settings = read_scheduler_settings(scheduler_settings_path=scheduler_settings_path)
    plan = build_data_annotation_scheduler_plan(
        read_scheduler_tasks(
            scheduler_state_path=scheduler_state_path,
            world_facts_path=world_facts_path,
            manual_job_path=manual_job_path,
        ),
        fanxiu_runtime_runner_status(),
        read_world_facts(world_facts_path),
        scheduler_state_path or fanxiu_data_annotation_scheduler_state_path(),
        task_supported=task_supported,
        task_due=data_annotation_task_due,
        now_ts=time.time(),
    )
    plan["job_group_enabled"] = bool(settings.get("job_group_enabled", True))
    if not plan["job_group_enabled"]:
        plan["next_action"] = "job_group_disabled"
        plan["message"] = "工程作业组已关闭，工程自主入口不提交到期作业；等待 AI 保底接管"
    if plan.get("next_action") == "run_due" and bool(plan.get("job_group_enabled", True)):
        blockers = scheduler_blocking_overlays(entry=entry, entry_id=entry_id, asset_tree_path=asset_tree_path)
        if blockers:
            plan["blocking_overlays"] = blockers
            if any(bool(item.get("blocking")) for item in blockers):
                plan["message"] = str(blockers[0].get("message") or plan.get("message") or "")
                plan["next_action"] = "blocked"
    return plan


def scheduler_blocking_overlays(
    *,
    entry: Any | None = None,
    entry_id: str | None = None,
    asset_tree_path: Path | None = None,
) -> list[dict[str, Any]]:
    if asset_tree_path is None:
        if not entry_id:
            return []
        asset_tree_path = data_annotation_asset_tree_path(str(entry_id))
    if entry is None:
        entry = type("LocalFanxiuEntry", (), {"entry_id": str(entry_id or ""), "name": "codepc_mf"})()
    try:
        runner = create_fanxiu_runtime_runner()
        tree = runner._load_asset_tree(asset_tree_path)
        ctx = {
            "entry": entry,
            "asset_tree": tree,
            "asset_tree_path": asset_tree_path,
            "images": runner._index_images(tree),
        }
        blocking_overlay = runner._known_blocking_overlay_info(ctx)
        if not blocking_overlay:
            return []
        return [blocking_overlay]
    except Exception as exc:
        return [{
            "blocking": False,
            "message": f"阻断态巡检失败：{exc}",
            "error": str(exc),
        }]


def mark_due_scheduler_tasks_blocked(
    tasks: list[dict[str, Any]],
    due_tasks: list[dict[str, Any]],
    message: str,
    *,
    scheduler_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> None:
    due_ids = {str(task.get("id") or "") for task in due_tasks if str(task.get("id") or "")}
    if not due_ids:
        return
    now_ts = time.time()
    changed = False
    for task in tasks:
        task_id = str(task.get("id") or "")
        if task_id not in due_ids:
            continue
        task["last_result"] = "blocked"
        checkpoint = task.get("checkpoint") if isinstance(task.get("checkpoint"), dict) else {}
        checkpoint = dict(checkpoint)
        manual_note = checkpoint.pop("manual_inspection_note", None)
        if manual_note:
            checkpoint["previous_manual_inspection_note"] = manual_note
        task["checkpoint"] = {**checkpoint, "blocked_message": message, "blocked_at": now_ts}
        task["retry_after"] = None
        changed = True
        record_scheduler_task_fact(task, "blocked", world_facts_path=world_facts_path)
    if changed:
        write_scheduler_tasks(tasks, scheduler_state_path=scheduler_state_path)


def task_payload_with_meta(task: dict[str, Any]) -> dict[str, Any]:
    return scheduled_task_payload_with_meta(task)


def next_scheduler_time(task: dict[str, Any], now: datetime | None = None) -> str | None:
    return next_data_annotation_scheduler_time(task, now if now is not None else datetime.now())


def scheduler_task_queue_timestamp(task: dict[str, Any]) -> float | None:
    value = data_annotation_scheduler_time_order_key(task)[1]
    return value if value != float("inf") else None


def prepare_runtime_for_scheduler_task(
    task: dict[str, Any],
    tasks: list[dict[str, Any]],
    *,
    entry_id: str | None = None,
    interrupt_same_group: bool = False,
    wait_timeout_seconds: float = 8.0,
    scheduler_state_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any] | None:
    status = fanxiu_runtime_runner_status()
    if not status.get("running"):
        return None
    if interrupt_same_group and _is_scheduler_runtime_status(status):
        if not bool(status.get("interruptible", True)):
            message = f"当前作业不可中断，{task.get('id') or task.get('label') or task.get('task_type')} 暂不触发"
            status.update({"message": message, "updated_at": time.time()})
            persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
            return status
        stop_fanxiu_behavior_tree_current_task(str(entry_id or status.get("entry_id") or ""))
        if _wait_runtime_idle(wait_timeout_seconds):
            return None
        status = fanxiu_runtime_runner_status()
        message = f"已请求中断当前作业，{task.get('id') or task.get('label') or task.get('task_type')} 等待运行时空闲"
        status.update({"message": message, "updated_at": time.time()})
        persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
        return status
    task_id = str(task.get("id") or "")
    message = f"当前有任务运行，{task_id or task.get('label') or task.get('task_type')} 暂不触发"
    status.update({"message": message, "updated_at": time.time()})
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def _is_scheduler_runtime_status(status: dict[str, Any]) -> bool:
    task_type = str(status.get("task_type") or "")
    phase = str(status.get("phase") or "")
    current_task_id = str(status.get("current_task_id") or "")
    if task_type in {"scheduler_run_due", "scheduler_run_now"}:
        return True
    if phase == "scheduler_task":
        return True
    return current_task_id in {"scheduler_run_due", "scheduler_run_now"}


def _wait_runtime_idle(timeout_seconds: float) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds or 0.0))
    while time.monotonic() <= deadline:
        status = fanxiu_runtime_runner_status()
        if not status.get("running") and str(status.get("status") or "") != "stopping":
            return True
        time.sleep(0.1)
    status = fanxiu_runtime_runner_status()
    return not status.get("running") and str(status.get("status") or "") != "stopping"


def run_now_scheduler_task(
    *,
    entry: Any,
    entry_id: str,
    task_id: str,
    payload_override: dict[str, Any] | None = None,
    interrupt_same_group: bool = True,
    scheduler_state_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    manual_job_path: Path | None = None,
    asset_tree_path: Path | None = None,
) -> dict[str, Any]:
    tasks = read_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
        manual_job_path=manual_job_path,
    )
    state_task = next((item for item in tasks if item.get("id") == task_id), None)
    run_task = data_annotation_scheduler_run_now_task(tasks, task_id, payload_override)
    if state_task is None or run_task is None:
        raise LookupError("任务不存在")
    if not task_supported(run_task):
        raise ValueError("任务尚未纳入当前框架验收")
    blocked_status = prepare_runtime_for_scheduler_task(
        state_task,
        tasks,
        entry_id=entry_id,
        interrupt_same_group=interrupt_same_group,
        scheduler_state_path=scheduler_state_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )
    if blocked_status is not None:
        return blocked_status
    state_task["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state_task["last_result"] = "queued"
    write_scheduler_tasks(tasks, scheduler_state_path=scheduler_state_path)
    return submit_runtime_task_cell(
        entry=entry,
        entry_id=entry_id,
        task_type=str(run_task.get("task_type") or ""),
        payload=task_payload_with_meta(run_task),
        label=f"作业实例：{run_task.get('label') or run_task.get('id') or run_task.get('task_type')}",
        interruptible=bool(run_task.get("interruptible", True)),
        asset_tree_path=asset_tree_path,
        manual_job_path=manual_job_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )


def run_due_scheduler_tasks(
    *,
    entry: Any,
    entry_id: str,
    ignore_job_group_disabled: bool = False,
    scheduler_state_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    manual_job_path: Path | None = None,
    asset_tree_path: Path | None = None,
) -> dict[str, Any]:
    settings = read_scheduler_settings(scheduler_settings_path=scheduler_settings_path)
    ensure_fanxiu_behavior_tree_service(entry, entry_id, asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(entry_id))
    behavior_enabled = bool(settings.get("behavior_tree_enabled", True))
    job_group_enabled = bool(settings.get("job_group_enabled", True))
    if not behavior_enabled or (not job_group_enabled and not bool(ignore_job_group_disabled)):
        status = runtime_status(
            scheduler_settings_path=scheduler_settings_path,
            runtime_state_path=runtime_state_path,
            world_facts_path=world_facts_path,
        )
        disabled_reason = (
            "自动调度已关闭，到期作业暂不自动执行"
            if not behavior_enabled
            else "作业组已关闭，工程自主入口不提交到期作业；等待 AI 保底接管"
        )
        status.update({
            "message": disabled_reason,
            "phase": "scheduler_job_group_disabled",
            "updated_at": time.time(),
        })
        persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
        return status
    tasks = read_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
        manual_job_path=manual_job_path,
    )
    due_tasks = select_due_scheduled_tasks(
        tasks,
        task_due=data_annotation_task_due,
        task_supported=task_supported,
    )
    if due_tasks:
        due_tasks = [
            task
            for task in due_tasks
            if not is_deprecated_data_annotation_job_type(str(task.get("task_type") or ""))
        ]
    if not due_tasks:
        status = runtime_status(runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
        status.update({"message": "没有可执行的到期任务", "updated_at": time.time()})
        persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
        return status
    blockers = scheduler_blocking_overlays(entry=entry, entry_id=entry_id, asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(entry_id))
    blocking_item = next((item for item in blockers if bool(item.get("blocking"))), None)
    if blocking_item is not None:
        message = str(blocking_item.get("message") or "检测到阻断浮层，到期作业暂不自动执行")
        mark_due_scheduler_tasks_blocked(
            tasks,
            due_tasks,
            message,
            scheduler_state_path=scheduler_state_path,
            world_facts_path=world_facts_path,
        )
        status = runtime_status(runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
        status.update({
            "entry_id": entry_id,
            "phase": "scheduler_blocked",
            "message": message,
            "blocking_overlays": blockers,
            "updated_at": time.time(),
        })
        append_status_log(
            status,
            "warning",
            message,
            scope="job",
            item_id="scheduler",
            time_text=datetime.now().strftime("%H:%M:%S"),
            update_timestamp=False,
        )
        persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
        return status
    if not job_group_enabled and bool(ignore_job_group_disabled):
        due_task = due_tasks[0]
        queued_job = pending_scheduler_manual_job(str(due_task.get("id") or ""), manual_job_path=manual_job_path)
        if queued_job is None:
            due_task["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            due_task["last_result"] = "queued"
            write_scheduler_tasks(tasks, scheduler_state_path=scheduler_state_path)
            status = submit_runtime_task_cell(
                entry=entry,
                entry_id=entry_id,
                task_type=str(due_task.get("task_type") or ""),
                payload=task_payload_with_meta(due_task),
                label=f"AI保底接管到期任务：{due_task.get('label') or due_task.get('id') or due_task.get('task_type')}",
                group="job",
                interruptible=bool(due_task.get("interruptible", True)),
                created_at=scheduler_task_queue_timestamp(due_task),
                asset_tree_path=asset_tree_path,
                manual_job_path=manual_job_path,
                runtime_state_path=runtime_state_path,
                world_facts_path=world_facts_path,
            )
        else:
            status = fanxiu_runtime_runner_status()
            status.update({
                "entry_id": entry_id,
                "phase": "scheduler_due_queued",
                "message": f"到期任务已在 task cell 队列等待：{due_task.get('label') or due_task.get('id')}",
                "queued_job": {
                    "id": queued_job.get("id"),
                    "task_type": queued_job.get("task_type"),
                    "label": queued_job.get("label"),
                    "group": queued_job.get("group"),
                    "status": queued_job.get("status"),
                    "created_at": queued_job.get("created_at"),
                },
                "updated_at": time.time(),
            })
            persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
            if str(due_task.get("last_result") or "") != "queued":
                due_task["last_result"] = "queued"
                write_scheduler_tasks(tasks, scheduler_state_path=scheduler_state_path)
        if bool(status.get("running")):
            status.update({
                "message": f"到期任务已排队，等待当前作业完成：{due_task.get('label') or due_task.get('id')}",
                "updated_at": time.time(),
            })
            persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
        status.update({
            "phase": "scheduler_due_queued",
            "message": status.get("message")
            if bool(status.get("running"))
            else f"AI保底已接管作业组关闭下的到期任务：{due_task.get('label') or due_task.get('id')}",
            "job_group_enabled": False,
            "job_group_override": True,
            "updated_at": time.time(),
        })
        persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
        return status
    blocked_status = prepare_runtime_for_scheduler_task(
        due_tasks[0],
        tasks,
        scheduler_state_path=scheduler_state_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )
    if blocked_status is not None:
        return blocked_status
    fanxiu_runtime_runner_wake()
    status = fanxiu_runtime_runner_status()
    status.update({
        "entry_id": entry_id,
        "phase": "scheduler_due_queued",
        "message": f"已唤醒常驻行为树执行到期任务：{due_tasks[0].get('label') or due_tasks[0].get('id')}",
        "updated_at": time.time(),
    })
    persist_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status

