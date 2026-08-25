from __future__ import annotations

import ast
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import psutil
from filelock import FileLock, Timeout as FileLockTimeout

from pyxllib.prog import (
    append_status_log,
    filter_status_logs,
    scheduled_task_payload_with_meta,
)

from backend.core.fanxiu.behavior_tree.runtime import (
    data_annotation_asset_tree_path,
    ensure_fanxiu_behavior_tree_service,
    ensure_behavior_tree_runtime_jobs_registered,
    fanxiu_behavior_tree_runtime_state_path,
    fanxiu_data_annotation_scheduler_settings_path,
    fanxiu_data_annotation_scheduler_state_path,
    fanxiu_data_annotation_world_facts_path,
    behavior_tree_runtime_guard_definitions,
    behavior_tree_runtime_runner_status,
    replace_behavior_tree_runtime_logs,
    set_behavior_tree_runtime_guard,
    set_behavior_tree_runtime_guard_group_enabled,
    stop_fanxiu_behavior_tree_current_task,
)
from backend.core.fanxiu.runtime.code_signature import (
    fanxiu_behavior_tree_code_signature,
    fanxiu_scheduler_code_signature,
)
from backend.core.fanxiu.data_annotation.jobs import (
    canonical_fanxiu_data_annotation_task_type,
    get_fanxiu_data_annotation_task_cell_definition,
    is_deprecated_data_annotation_job_type,
)
from backend.core.fanxiu.data_annotation.job_times import clip_daily_retry_to_window
from backend.core.fanxiu.data_annotation.maintenance import (
    normalize_maintenance_gate,
)
from backend.core.fanxiu.data_annotation.scheduler import (
    SCHEDULER_RUNTIME_STATE_FIELDS,
    build_data_annotation_scheduler_plan,
    data_annotation_scheduler_dispatch_sort_key,
    merge_data_annotation_scheduler_task_updates,
    preserve_data_annotation_scheduler_runtime_state,
    data_annotation_scheduler_run_now_task,
    data_annotation_scheduler_time_order_key,
    data_annotation_scheduler_task_plan_reason,
    data_annotation_world_facts_summary,
    repair_data_annotation_scheduler_tasks,
    set_scheduler_task_trigger_time,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    LOGIN_GAME_SCHEDULER_TASK_ID,
    consolidate_arena_scheduler_instances,
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.scheduler_incidents import (
    detect_scheduler_environment_circuit,
    list_scheduler_incidents,
    record_scheduler_incident,
)
from backend.core.fanxiu.data_annotation.runner import create_behavior_tree_runtime_runner
from backend.core.fanxiu.data_annotation.state import (
    append_behavior_tree_runtime_log_once,
    data_annotation_scheduler_task_state,
    data_annotation_task_due,
    normalize_behavior_tree_runtime_display,
    normalize_behavior_tree_runtime_logs_for_display,
    normalize_behavior_tree_runtime_guard_items,
    select_behavior_tree_runtime_status,
    normalize_data_annotation_scheduler_settings,
    parse_data_annotation_task_time,
    persist_behavior_tree_runtime_status as _persist_behavior_tree_runtime_status,
    read_data_annotation_json,
    read_behavior_tree_runtime_status as _read_behavior_tree_runtime_status,
    read_data_annotation_world_facts,
    record_data_annotation_scheduler_task_fact,
    write_data_annotation_json,
    write_data_annotation_world_facts,
)
from backend.core.services.launcher import popen_python_script_service
from backend.core.temp_paths import codeyun_temp_root


def _canonical_runtime_task_type(task_type: str) -> str:
    return canonical_fanxiu_data_annotation_task_type(task_type)


def read_world_facts(path: Path | None = None) -> dict[str, Any]:
    return read_data_annotation_world_facts(path or fanxiu_data_annotation_world_facts_path())


def doctor_watch_latest_path() -> Path:
    return codeyun_temp_root("fanxiu-watch") / "doctor_watch_latest.json"


def doctor_watch_heartbeat_path() -> Path:
    return codeyun_temp_root("fanxiu-watch") / "doctor_watch_heartbeat.json"


def doctor_watch_code_signature() -> str:
    return fanxiu_scheduler_code_signature()


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
    current_code_signature = doctor_watch_code_signature()
    watch_code_signature = str(payload.get("code_signature") or "")
    code_consistent = bool(watch_code_signature) and watch_code_signature == current_code_signature
    if active and code_consistent:
        message = "巡检心跳正常"
    elif active:
        message = "巡检进程代码版本已过期"
    else:
        message = "巡检心跳过期或路径不一致"
    return {
        "ok": True,
        "exists": True,
        "path": str(heartbeat_path),
        "active": active,
        "age_seconds": age_seconds,
        "stale_after_seconds": stale_after_seconds,
        "runtime_consistent": runtime_consistent,
        "code_consistent": code_consistent,
        "current_code_signature": current_code_signature,
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
    include_screenshot: bool = False,
    screenshot_every: int = 10,
    stale_after_seconds: float = 180.0,
) -> dict[str, Any]:
    heartbeat = read_doctor_watch_heartbeat(stale_after_seconds=stale_after_seconds)
    candidate_pid = int(heartbeat.get("pid") or 0)
    process_active = False
    if candidate_pid > 0:
        try:
            process = psutil.Process(candidate_pid)
            command_line = " ".join(process.cmdline()).lower()
            process_active = process.is_running() and "fanxiu_bt.py" in command_line and "watch-doctor" in command_line
        except (psutil.Error, OSError, ValueError):
            process_active = False
    heartbeat_active = bool(heartbeat.get("active"))
    code_consistent = bool(heartbeat.get("code_consistent"))
    process_consistent = process_active if candidate_pid > 0 else heartbeat_active
    if heartbeat_active and process_consistent and code_consistent:
        return {
            "ok": True,
            "started": False,
            "reason": "heartbeat_recent",
            "heartbeat": heartbeat,
            "latest": read_doctor_watch_latest(),
        }

    replacement_reasons: list[str] = []
    if not heartbeat_active:
        replacement_reasons.append("heartbeat_missing_or_stale")
    if not code_consistent:
        replacement_reasons.append("code_signature_mismatch")
    replaced_pid: int | None = None
    if process_active and replacement_reasons:
        try:
            process = psutil.Process(candidate_pid)
            command_line = " ".join(process.cmdline()).lower()
            if "fanxiu_bt.py" in command_line and "watch-doctor" in command_line:
                process.terminate()
                process.wait(timeout=5.0)
                replaced_pid = candidate_pid
        except (psutil.Error, OSError, ValueError):
            pass

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
        "replaced_pid": replaced_pid,
        "pid": process.pid,
        "reason": replacement_reasons[0] if replacement_reasons else "watch_process_missing",
        "replacement_reasons": replacement_reasons,
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


def persist_behavior_tree_runtime_status(
    status: dict[str, Any],
    *,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> None:
    _persist_behavior_tree_runtime_status(
        runtime_state_path or fanxiu_behavior_tree_runtime_state_path(),
        world_facts_path or fanxiu_data_annotation_world_facts_path(),
        status,
    )


def finalize_behavior_tree_runtime_status(
    status: dict[str, Any],
    *,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    normalize_runtime_guard_items(status)
    normalize_behavior_tree_runtime_display(status)
    persist_behavior_tree_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def read_behavior_tree_runtime_status(path: Path | None = None) -> dict[str, Any]:
    return _read_behavior_tree_runtime_status(path or fanxiu_behavior_tree_runtime_state_path())


def append_runtime_log_once(status: dict[str, Any], kind: str, message: str) -> None:
    append_behavior_tree_runtime_log_once(status, kind, message, time_text=datetime.now().strftime("%H:%M:%S"))


def normalize_runtime_guard_items(status: dict[str, Any]) -> None:
    normalize_behavior_tree_runtime_guard_items(status, behavior_tree_runtime_guard_definitions())


def task_supported(task: dict[str, Any]) -> bool:
    ensure_behavior_tree_runtime_jobs_registered()
    definition = get_fanxiu_data_annotation_task_cell_definition(_canonical_runtime_task_type(str(task.get("task_type") or "")))
    return bool(definition and definition.scheduler_supported)


def read_scheduler_tasks(
    *,
    scheduler_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return the current Scheduler projection without changing persisted state.

    Catalogue repair and legacy consolidation remain useful to readers as an
    in-memory projection, but a read must never turn into a migration. Writers
    that own Scheduler maintenance call :func:`maintain_scheduler_tasks`
    explicitly before changing or dispatching jobs.
    """

    path = scheduler_state_path or fanxiu_data_annotation_scheduler_state_path()
    raw = read_data_annotation_json(path, None)
    raw, _consolidated = consolidate_arena_scheduler_instances(raw)
    # Repair may enrich facts while deriving a projection. Give it a private
    # copy so a read cannot mutate an object returned by a cache/test double.
    facts = deepcopy(read_world_facts(world_facts_path))
    tasks, _changed = repair_data_annotation_scheduler_tasks(
        raw,
        default_data_annotation_scheduler_tasks(),
        facts,
        task_supported=task_supported,
        now=now or datetime.now(),
    )
    return sorted(tasks, key=data_annotation_scheduler_time_order_key)


def maintain_scheduler_tasks(
    *,
    scheduler_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Repair and persist Scheduler catalogue state from an authorized writer."""

    path = scheduler_state_path or fanxiu_data_annotation_scheduler_state_path()
    raw = read_data_annotation_json(path, None)
    raw_ids_before_consolidation = {
        str(item.get("id") or "")
        for item in (raw if isinstance(raw, list) else [])
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    raw, consolidated = consolidate_arena_scheduler_instances(raw)
    raw_ids_after_consolidation = {
        str(item.get("id") or "")
        for item in (raw if isinstance(raw, list) else [])
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    facts = read_world_facts(world_facts_path)
    original_facts = deepcopy(facts)
    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        default_data_annotation_scheduler_tasks(),
        facts,
        task_supported=task_supported,
        now=now or datetime.now(),
    )
    if facts != original_facts:
        write_world_facts(facts, world_facts_path)
    if changed or consolidated:
        write_scheduler_tasks(
            tasks,
            scheduler_state_path=path,
            runtime_update_ids=set(),
            removed_task_ids=(
                raw_ids_before_consolidation - raw_ids_after_consolidation
            ),
        )
    return sorted(tasks, key=data_annotation_scheduler_time_order_key)


_SCHEDULER_CONFIGURATION_FIELDS = (
    "dispatch_level",
    "dispatch_order",
    "trigger_description",
    "error_retry_delay_seconds",
)
from backend.core.fanxiu.data_annotation.scheduler_time import (
    normalize_time_sequence,
    scheduler_task_time_view,
    scheduler_time_sequence_groups as build_scheduler_time_sequence_groups,
)


def scheduler_time_sequence_groups(
    tasks: list[dict[str, Any]],
    *,
    scheduler_settings_path: Path | None = None,
) -> list[dict[str, Any]]:
    settings = read_scheduler_settings(
        scheduler_settings_path=scheduler_settings_path
    )
    return build_scheduler_time_sequence_groups(
        tasks,
        settings["time_sequence"],
    )


def update_scheduler_time_sequence(
    group_updates: list[dict[str, Any]],
    *,
    scheduler_settings_path: Path | None = None,
) -> dict[str, Any]:
    settings = read_scheduler_settings(
        scheduler_settings_path=scheduler_settings_path
    )
    sequence = dict(settings["time_sequence"])
    for update in group_updates:
        clock = str(update.get("key") or "").strip()
        task_ids = [
            str(value or "").strip()
            for value in update.get("task_ids") or []
            if str(value or "").strip()
        ]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError(f"{clock} 的作业顺序存在重复")
        sequence[clock] = task_ids
    settings["time_sequence"] = normalize_time_sequence(sequence)
    return write_scheduler_settings(
        settings,
        scheduler_settings_path=scheduler_settings_path,
    )


def _scheduler_configuration(tasks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(task.get("id") or ""): {
            key: deepcopy(task.get(key))
            for key in _SCHEDULER_CONFIGURATION_FIELDS
        }
        for task in tasks
        if str(task.get("id") or "")
    }


def write_scheduler_tasks(
    tasks: list[dict[str, Any]],
    *,
    scheduler_state_path: Path | None = None,
    preserve_runtime_state: bool = True,
    runtime_update_ids: set[str] | None = None,
    expected_runtime_attempt_ids: dict[str, str | None] | None = None,
    removed_task_ids: set[str] | None = None,
) -> bool:
    """Atomically merge and persist Scheduler state across processes.

    ``runtime_update_ids`` identifies the only jobs whose runtime fields this
    caller owns. Runtime fields for every other job are copied from the latest
    on-disk snapshot while holding a cross-process lock.

    ``expected_runtime_attempt_ids`` is a compare-and-swap guard for attempt
    ownership. A dispatcher may claim an idle task only while its observed
    attempt id is still current, and a terminal writer may clear only the
    attempt it owns. This prevents API run-now and watch-doctor from both
    submitting a Cell after observing the same idle boundary.

    Omission is never deletion: jobs present in the latest snapshot but absent
    from a stale caller snapshot are retained. A framework migration must name
    every intentional removal through ``removed_task_ids``. This makes standard
    checklist membership monotonic across API, Scheduler and Kernel writers.
    """
    path = scheduler_state_path or fanxiu_data_annotation_scheduler_state_path()
    payload: list[dict[str, Any]] = []
    for task in tasks:
        state = data_annotation_scheduler_task_state(task)
        # Configuration tests and maintenance callers may provide a sparse
        # id-addressed record.  Never let normalization erase its identity.
        if not str(state.get("id") or "") and str(task.get("id") or ""):
            state = deepcopy(task)
        payload.append(state)
    explicit_removals = {
        str(value or "").strip()
        for value in (removed_task_ids or set())
        if str(value or "").strip()
    }
    lock_path = path.with_name(f"{path.name}.lock")
    with FileLock(str(lock_path), timeout=30):
        existing = read_data_annotation_json(path, [])
        if isinstance(existing, list) and expected_runtime_attempt_ids:
            existing_by_id = {
                str(item.get("id") or ""): item
                for item in existing
                if isinstance(item, dict) and str(item.get("id") or "")
            }
            for task_id, expected_attempt_id in expected_runtime_attempt_ids.items():
                current = existing_by_id.get(str(task_id or ""))
                current_attempt_id = (
                    str(current.get("attempt_id") or "") or None
                    if isinstance(current, dict)
                    else None
                )
                normalized_expected = str(expected_attempt_id or "") or None
                if current_attempt_id != normalized_expected:
                    return False
        if isinstance(existing, list):
            incoming_ids = {
                str(item.get("id") or "")
                for item in payload
                if isinstance(item, dict) and str(item.get("id") or "")
            }
            payload.extend(
                deepcopy(item)
                for item in existing
                if isinstance(item, dict)
                and str(item.get("id") or "")
                and str(item.get("id") or "") not in incoming_ids
                and str(item.get("id") or "") not in explicit_removals
            )
        if isinstance(existing, list) and _scheduler_configuration(existing) != _scheduler_configuration(payload):
            backup_path = path.with_name(f"{path.stem}.previous-config{path.suffix}")
            write_data_annotation_json(backup_path, existing)
        if isinstance(existing, list) and runtime_update_ids is not None:
            owned_ids = {str(value or "").strip() for value in runtime_update_ids if str(value or "").strip()}
            existing_by_id = {
                str(item.get("id") or ""): item
                for item in existing
                if isinstance(item, dict) and str(item.get("id") or "")
            }
            merged_payload: list[dict[str, Any]] = []
            for item in payload:
                task_id = str(item.get("id") or "")
                current = existing_by_id.get(task_id)
                if task_id not in owned_ids and isinstance(current, dict):
                    item = dict(item)
                    for key in SCHEDULER_RUNTIME_STATE_FIELDS:
                        if key in current:
                            item[key] = deepcopy(current.get(key))
                merged_payload.append(item)
            payload = merged_payload
        elif preserve_runtime_state and isinstance(existing, list):
            payload = preserve_data_annotation_scheduler_runtime_state(payload, existing)
        write_data_annotation_json(path, payload)
    return True


def set_scheduler_task_next_time(
    task_name: str,
    next_time: datetime | str | None,
    *,
    scheduler_state_path: Path | None = None,
    now: datetime | None = None,
) -> str | None:
    """Atomically set one Job's sole trigger timestamp.

    This is the shared command used by Jobs, manual scheduling and
    Job-owned fact patrols.  It updates only ``next_time`` on the latest disk
    record; it does not carry business context or interpret why the Job should
    run.
    """

    path = scheduler_state_path or fanxiu_data_annotation_scheduler_state_path()
    lock_path = path.with_name(f"{path.name}.lock")
    with FileLock(str(lock_path), timeout=30):
        tasks = read_data_annotation_json(path, [])
        if not isinstance(tasks, list):
            tasks = []
        current_time = now or datetime.now()
        try:
            task = set_scheduler_task_trigger_time(
                tasks,
                task_name,
                next_time,
                now=current_time,
            )
        except LookupError:
            # A fresh or partially damaged file may not yet contain a standard
            # Job. Repair membership from the one canonical catalogue while
            # still holding the same lock, then retry the field update. This
            # is not a second add-job path: non-standard selectors still fail.
            tasks, _changed = repair_data_annotation_scheduler_tasks(
                tasks,
                default_data_annotation_scheduler_tasks(),
                {},
                task_supported=task_supported,
                now=current_time,
            )
            task = set_scheduler_task_trigger_time(
                tasks,
                task_name,
                next_time,
                now=current_time,
            )
        write_data_annotation_json(path, tasks)
    return str(task["next_time"]) if task.get("next_time") else None


def trigger_scheduler_task_once(
    task_id: str,
    *,
    scheduler_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    now: datetime | None = None,
) -> str:
    """Make one Job due without bypassing Scheduler dispatch semantics."""

    current_time = now or datetime.now()
    tasks = maintain_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
        now=current_time,
    )
    task = next((item for item in tasks if str(item.get("id") or "") == task_id), None)
    if task is None:
        raise LookupError("任务不存在")
    if not task_supported(task):
        raise ValueError("任务尚未纳入当前框架验收")
    triggered_at = set_scheduler_task_next_time(
        task_id,
        current_time,
        scheduler_state_path=scheduler_state_path,
        now=current_time,
    )
    if not triggered_at:
        raise RuntimeError("触发作业失败：next_time 未写入")
    return triggered_at


def schedule_login_job_first(
    *,
    scheduler_state_path: Path | None = None,
    now: datetime | None = None,
) -> str | None:
    """Put login at the head of the timestamp queue without submitting a Cell.

    The timestamp is one minute earlier than both ``now`` and every other
    materialized Job timestamp.  Repeated detections are idempotent: an already
    earlier login timestamp is preserved instead of drifting backwards forever.
    """

    current_time = now or datetime.now()
    path = scheduler_state_path or fanxiu_data_annotation_scheduler_state_path()
    lock_path = path.with_name(f"{path.name}.lock")
    with FileLock(str(lock_path), timeout=30):
        tasks = read_data_annotation_json(path, [])
        if not isinstance(tasks, list):
            tasks = []
        if not any(str(item.get("id") or "") == LOGIN_GAME_SCHEDULER_TASK_ID for item in tasks):
            tasks, _changed = repair_data_annotation_scheduler_tasks(
                tasks,
                default_data_annotation_scheduler_tasks(),
                {},
                task_supported=task_supported,
                now=current_time,
            )

        other_timestamps = [
            parsed
            for item in tasks
            if str(item.get("id") or "") != LOGIN_GAME_SCHEDULER_TASK_ID
            if (parsed := parse_data_annotation_task_time(item.get("next_time"))) is not None
        ]
        queue_head_timestamp = min([current_time.timestamp(), *other_timestamps]) - 60
        login_task = next(
            item for item in tasks if str(item.get("id") or "") == LOGIN_GAME_SCHEDULER_TASK_ID
        )
        existing_login_timestamp = parse_data_annotation_task_time(login_task.get("next_time"))
        if existing_login_timestamp is not None:
            queue_head_timestamp = min(queue_head_timestamp, existing_login_timestamp)
        scheduled_at = datetime.fromtimestamp(queue_head_timestamp)
        task = set_scheduler_task_trigger_time(
            tasks,
            LOGIN_GAME_SCHEDULER_TASK_ID,
            scheduled_at,
            now=current_time,
        )
        write_data_annotation_json(path, tasks)
    return str(task["next_time"]) if task.get("next_time") else None


def reset_scheduler_task_runs(
    *,
    task_ids: list[str] | None = None,
    clear_next_time: bool = False,
    scheduler_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    tasks = maintain_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
    )
    selected_ids = {str(item).strip() for item in (task_ids or []) if str(item).strip()}
    target_tasks: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task.get("id") or "")
        if selected_ids and task_id not in selected_ids:
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
        "scheduler_meta",
        "attempt_id",
        "attempt_original_trigger",
        "attempt_kernel_generation",
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
    write_scheduler_tasks(
        tasks,
        scheduler_state_path=scheduler_state_path,
        runtime_update_ids=set(reset_ids),
    )

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
    status = behavior_tree_runtime_status(runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
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
    persist_behavior_tree_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
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
        status = ensure_behavior_tree_runtime(
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
        persist_behavior_tree_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
        return status
    status = ensure_behavior_tree_runtime(
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
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    tasks = merge_data_annotation_scheduler_task_updates(
        maintain_scheduler_tasks(
            scheduler_state_path=scheduler_state_path,
            world_facts_path=world_facts_path,
            now=now,
        ),
        [deepcopy(update) for update in updates],
        now=now or datetime.now(),
    )
    write_scheduler_tasks(
        tasks,
        scheduler_state_path=scheduler_state_path,
        runtime_update_ids=set(),
    )
    return read_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
        now=now,
    )


def behavior_tree_runtime_status(
    *,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    persisted = read_behavior_tree_runtime_status(runtime_state_path)
    status = behavior_tree_runtime_runner_status()
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_manager_status

    kernel_state = fanxiu_kernel_manager_status()
    status = select_behavior_tree_runtime_status(status, persisted)
    if status.get("running"):
        try:
            status_age_seconds = max(0.0, time.time() - float(status.get("updated_at") or 0.0))
        except (TypeError, ValueError):
            status_age_seconds = float("inf")
        kernel_still_running_cell = (
            bool(kernel_state.get("alive"))
            and str(kernel_state.get("execution_state") or "") == "busy"
        )
        terminal_writeback_pending = bool(kernel_state.get("alive")) and status_age_seconds < 30.0
        if not kernel_still_running_cell and not terminal_writeback_pending:
            status["running"] = False
            status["guard_running"] = False
            status["status"] = "stopped"
            status["phase"] = "stopped"
            status["message"] = "执行进程已重载，先前业务任务已结束"
            status["finished_at"] = status.get("finished_at") or time.time()
            append_runtime_log_once(status, "stop", "执行进程已重载，先前业务任务已结束")
    elif (
        str(status.get("status") or "") == "running"
        and str(kernel_state.get("execution_state") or "") != "busy"
    ):
        # `running=false` is already a terminal execution fact.  A stale text
        # projection must not keep the UI looking active after an interrupt or
        # a completed Cell.
        status["guard_running"] = False
        status["status"] = "stopped"
        status["phase"] = "stopped"
        status["task_type"] = ""
        status["current_task"] = ""
        status["current_task_id"] = ""
        status["message"] = "当前 Cell 已结束，Kernel 保持空闲"
        status["finished_at"] = status.get("finished_at") or time.time()
        append_runtime_log_once(status, "stop", "当前 Cell 已结束，Kernel 保持空闲")
    elif persisted and (persisted.get("guard_enabled") or persisted.get("guard_running")):
        status["guard_running"] = False
        terminal = (
            str(status.get("phase") or "") in {"done", "error", "stopped", "interrupted"}
            or str(status.get("status") or "") in {"success", "error", "stopped", "interrupted"}
        )
        if not terminal:
            status["status"] = "idle"
            if bool(kernel_state.get("alive")):
                # The resident Jupyter Kernel is the behavior-tree service.
                # A backend reload only clears this process' in-memory facade;
                # it does not leave the Kernel waiting for recovery.
                status["phase"] = "idle"
                status["current_scene"] = 0
                status["message"] = "Kernel 已就绪，等待作业触发"
                append_runtime_log_once(status, "info", "Kernel 已就绪，等待作业触发")
            else:
                status["message"] = "后端已重载，行为树服务待恢复"
                append_runtime_log_once(status, "stop", "后端已重载，行为树服务待恢复")
    status["behavior_tree_enabled"] = behavior_tree_enabled(scheduler_settings_path=scheduler_settings_path)
    status["kernel"] = kernel_state
    normalize_runtime_guard_items(status)
    normalize_behavior_tree_runtime_display(status)
    status.pop("priority", None)
    return status


def ensure_behavior_tree_runtime(
    *,
    entry: Any,
    entry_id: str,
    asset_tree_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    resolved_entry_id = str(entry_id or getattr(entry, "entry_id", None) or "")
    kernel_state = ensure_fanxiu_behavior_tree_service(
        entry,
        resolved_entry_id,
        asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(resolved_entry_id),
    )
    status = behavior_tree_runtime_status(
        scheduler_settings_path=scheduler_settings_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )
    status["kernel"] = kernel_state
    return status


def restart_behavior_tree_kernel(
    *,
    entry: Any,
    entry_id: str,
    timeout_seconds: float = 5.0,
    asset_tree_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    del entry, asset_tree_path
    from backend.core.fanxiu.behavior_tree.kernel import FanxiuKernel

    result = FanxiuKernel(entry_id=str(entry_id)).restart(timeout_seconds=max(1.0, float(timeout_seconds or 5.0)))
    status = behavior_tree_runtime_status(
        scheduler_settings_path=scheduler_settings_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )
    status["kernel_restart"] = result
    normalize_behavior_tree_runtime_display(status)
    persist_behavior_tree_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def stop_current_task(
    entry_id: str,
    *,
    scheduler_state_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    before = behavior_tree_runtime_status(
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )
    task_id = str(before.get("current_task_id") or "")
    task_label = str(before.get("current_task") or before.get("task_type") or "当前 Cell")
    kernel = stop_fanxiu_behavior_tree_current_task(entry_id)
    if str(kernel.get("execution_state") or "") != "idle":
        return {**before, "kernel": kernel}

    now = datetime.now()
    message = f"{task_label}：已由用户显式中断，Kernel 保持存活"
    terminal = {
        **before,
        "running": False,
        "guard_running": False,
        "status": "interrupted",
        "phase": "interrupted",
        "task_type": "",
        "current_task": "",
        "current_task_id": "",
        "message": message,
        "error": "",
        "finished_at": time.time(),
        "updated_at": time.time(),
        "kernel": kernel,
    }
    persist_behavior_tree_runtime_status(
        terminal,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )

    if task_id:
        tasks = read_scheduler_tasks(scheduler_state_path=scheduler_state_path)
        for task in tasks:
            if str(task.get("id") or "") != task_id:
                continue
            if str(task.get("last_result") or "") == "running":
                interrupted_task = deepcopy(task)
                interrupted_attempt_id = str(task.get("attempt_id") or "")
                original_next_time = (
                    task.get("attempt_original_trigger")
                    if "attempt_original_trigger" in task
                    else task.get("next_time")
                )
                task["last_result"] = "interrupted"
                task["last_message"] = message
                task["finished_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
                task["next_time"] = original_next_time
                task["attempt_id"] = None
                task["attempt_original_trigger"] = None
                task["attempt_kernel_generation"] = None
                task["attempt_kernel_idle_since"] = None
                write_scheduler_tasks(
                    tasks,
                    scheduler_state_path=scheduler_state_path,
                    runtime_update_ids={task_id},
                )
                record_scheduler_task_fact(
                    task,
                    "interrupted",
                    world_facts_path=world_facts_path,
                )
                record_scheduler_incident(
                    task=interrupted_task,
                    original_next_time=original_next_time,
                    next_time=task.get("next_time"),
                    incident={
                        "kind": "attempt_interrupted",
                        "cycle_kind": "scheduler",
                        "reason": message,
                    },
                    attempt_id=interrupted_attempt_id,
                    entry_id=entry_id,
                    occurred_at=now,
                    runtime_status=before,
                    scheduler_state_path=scheduler_state_path,
                )
            break
    return terminal


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
    status = set_behavior_tree_runtime_guard(
        entry=entry,
        entry_id=resolved_entry_id,
        guard_id=guard_id,
        enabled=enabled,
        interval_seconds=interval_seconds,
        asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(resolved_entry_id),
    )
    persist_behavior_tree_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
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
    status = set_behavior_tree_runtime_guard_group_enabled(
        entry=entry,
        entry_id=resolved_entry_id,
        enabled=enabled,
        asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(resolved_entry_id),
    )
    persist_behavior_tree_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def behavior_tree_logs(
    *,
    limit: int = 500,
    scope: str = "",
    item_id: str = "",
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> list[dict[str, Any]]:
    status = behavior_tree_runtime_status(runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return normalize_behavior_tree_runtime_logs_for_display(filter_status_logs(status, limit=limit, scope=scope, item_id=item_id))


def clear_behavior_tree_logs(
    *,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> list[dict[str, Any]]:
    status = behavior_tree_runtime_runner_status()
    status["logs"] = []
    replace_behavior_tree_runtime_logs([])
    persist_behavior_tree_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return []


def submit_runtime_task_cell(
    *,
    entry: Any,
    entry_id: str,
    task_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_type = _canonical_runtime_task_type(task_type or "detect_scene") or "detect_scene"
    if is_deprecated_data_annotation_job_type(task_type):
        raise ValueError(f"作业已删除，不再支持：{task_type}")
    definition = get_fanxiu_data_annotation_task_cell_definition(task_type)
    if definition is None:
        raise ValueError(f"暂不支持的任务类型：{task_type}")
    from backend.core.fanxiu.data_annotation.behavior_tree_framework import submit_task_cell

    return submit_task_cell(
        entry=entry,
        entry_id=entry_id,
        task_type=task_type,
        payload=payload,
    )


def scheduler_task_view(
    task: dict[str, Any],
    *,
    tasks: list[dict[str, Any]],
    scheduler_settings_path: Path | None = None,
) -> dict[str, Any]:
    settings = read_scheduler_settings(
        scheduler_settings_path=scheduler_settings_path
    )
    return {
        **scheduler_task_time_view(task, tasks, settings["time_sequence"]),
        "supported": task_supported(task),
        "template_id": str(task.get("template_id") or task.get("task_type") or ""),
        "template_label": str(task.get("template_label") or task.get("label") or task.get("task_type") or ""),
        "template_source": str(task.get("template_source") or "preset"),
        "trigger_description": str(task.get("trigger_description") or ""),
    }


def scheduler_task_views(
    tasks: list[dict[str, Any]],
    *,
    scheduler_settings_path: Path | None = None,
) -> list[dict[str, Any]]:
    settings = read_scheduler_settings(
        scheduler_settings_path=scheduler_settings_path
    )
    return [
        {
            **scheduler_task_time_view(task, tasks, settings["time_sequence"]),
            "supported": task_supported(task),
            "template_id": str(task.get("template_id") or task.get("task_type") or ""),
            "template_label": str(task.get("template_label") or task.get("label") or task.get("task_type") or ""),
            "template_source": str(task.get("template_source") or "preset"),
            "trigger_description": str(task.get("trigger_description") or ""),
        }
        for task in tasks
    ]


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
    include_blocking_overlays: bool = True,
) -> dict[str, Any]:
    settings = read_scheduler_settings(scheduler_settings_path=scheduler_settings_path)
    tasks = read_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
    )
    facts = read_world_facts(world_facts_path)
    effective_tasks = scheduler_tasks_for_dispatch(
        tasks,
        scheduler_settings_path=scheduler_settings_path,
    )
    plan = build_data_annotation_scheduler_plan(
        effective_tasks,
        behavior_tree_runtime_runner_status(),
        facts,
        scheduler_state_path or fanxiu_data_annotation_scheduler_state_path(),
        task_supported=task_supported,
        task_due=data_annotation_task_due,
        now_ts=time.time(),
    )
    plan["job_group_enabled"] = bool(settings.get("job_group_enabled", True))
    availability = facts.get("availability") if isinstance(facts.get("availability"), dict) else {}
    maintenance_gate = normalize_maintenance_gate(availability.get("game"))
    plan["maintenance_gate"] = maintenance_gate
    if not plan["job_group_enabled"]:
        plan["next_action"] = "job_group_disabled"
        plan["message"] = "AI 调度器占用运行权，工程不自动提交到期作业"
    return plan


def scheduler_blocking_overlays(
    *,
    entry: Any | None = None,
    entry_id: str | None = None,
    asset_tree_path: Path | None = None,
    environment_circuit: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if asset_tree_path is None:
        if not entry_id:
            return []
        asset_tree_path = data_annotation_asset_tree_path(str(entry_id))
    if entry is None:
        entry = type("LocalFanxiuEntry", (), {"entry_id": str(entry_id or ""), "name": "codepc_mf"})()
    try:
        runner = create_behavior_tree_runtime_runner()
        tree = runner._load_asset_tree(asset_tree_path)
        ctx = {
            "entry": entry,
            "asset_tree": tree,
            "asset_tree_path": asset_tree_path,
            "images": runner._index_images(tree),
        }
        if isinstance(environment_circuit, dict):
            try:
                scene_id = int(environment_circuit.get("scene_id"))
            except (TypeError, ValueError):
                scene_id = 0
            image = ctx["images"].get(scene_id) if scene_id > 0 else None
            task_ids = [
                str(task_id)
                for task_id in (environment_circuit.get("task_ids") or [])
                if str(task_id)
            ]
            if not isinstance(image, dict):
                return [{
                    "kind": "repeated_environment_failure",
                    "scene_id": scene_id or None,
                    "blocking": True,
                    "task_ids": task_ids,
                    "incident_ids": list(environment_circuit.get("incident_ids") or []),
                    "message": (
                        f"同一稳定环境已阻断 {len(task_ids)} 个不同作业，但事故候选 #{scene_id or '?'} "
                        "不存在于当前资产树；无法证明环境已经恢复，保持派发阻断"
                    ),
                }]
            from backend.core.fanxiu.data_annotation.unknown_recovery import (
                reference_frame_similarity,
            )

            frame = runner._screencap(ctx)
            similarity = reference_frame_similarity(runner, image, frame)
            if not isinstance(similarity, (int, float)):
                return [{
                    "kind": "repeated_environment_failure",
                    "scene_id": scene_id,
                    "title": str(image.get("title") or image.get("filename") or f"#{scene_id}"),
                    "blocking": True,
                    "task_ids": task_ids,
                    "incident_ids": list(environment_circuit.get("incident_ids") or []),
                    "message": (
                        f"同一稳定环境已阻断 {len(task_ids)} 个不同作业，但当前帧复核失败；"
                        "无法证明环境已经恢复，保持派发阻断"
                    ),
                }]
            if float(similarity) >= 90.0:
                return [{
                    "kind": "repeated_environment_failure",
                    "scene_id": scene_id,
                    "title": str(image.get("title") or image.get("filename") or f"#{scene_id}"),
                    "blocking": True,
                    "frame_similarity": round(float(similarity), 1),
                    "task_ids": task_ids,
                    "incident_ids": list(environment_circuit.get("incident_ids") or []),
                    "message": (
                        f"同一稳定环境已阻断 {len(task_ids)} 个不同作业，"
                        f"当前帧与事故候选 #{scene_id} 仍有 {float(similarity):.0f}% 全图相似；"
                        "暂停提交新的到期 Cell，所有作业 next_time 保持不变"
                    ),
                }]
        blocking_overlay = runner._known_blocking_overlay_info(ctx)
        if not blocking_overlay:
            return []
        return [blocking_overlay]
    except Exception as exc:
        return [{
            "kind": "repeated_environment_failure" if isinstance(environment_circuit, dict) else "overlay_probe_failed",
            "blocking": isinstance(environment_circuit, dict),
            "scene_id": environment_circuit.get("scene_id") if isinstance(environment_circuit, dict) else None,
            "task_ids": list(environment_circuit.get("task_ids") or []) if isinstance(environment_circuit, dict) else [],
            "incident_ids": list(environment_circuit.get("incident_ids") or []) if isinstance(environment_circuit, dict) else [],
            "message": (
                f"稳定环境事故复核失败，无法证明已经恢复，保持派发阻断：{exc}"
                if isinstance(environment_circuit, dict)
                else f"阻断态巡检失败：{exc}"
            ),
            "error": str(exc),
        }]


def scheduler_environment_circuit(
    *,
    tasks: list[dict[str, Any]] | None = None,
    scheduler_state_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Derive a cross-task environment circuit from immutable incidents."""

    incidents = list_scheduler_incidents(
        scheduler_state_path=scheduler_state_path,
        limit=500,
    )
    if tasks is not None:
        failed_task_ids = {
            str(task.get("id") or "")
            for task in tasks
            if isinstance(task, dict) and str(task.get("last_result") or "") == "error"
        }
        if len(failed_task_ids) < 2:
            return None
        incidents = [
            incident
            for incident in incidents
            if isinstance(incident, dict)
            and str(
                (incident.get("task") or {}).get("id")
                if isinstance(incident.get("task"), dict)
                else ""
            ) in failed_task_ids
        ]
    return detect_scheduler_environment_circuit(incidents, now=now)


def task_payload_with_meta(task: dict[str, Any]) -> dict[str, Any]:
    payload = scheduled_task_payload_with_meta(task)
    payload["__scheduler_attempt_id"] = str(task.get("attempt_id") or "")
    return payload


def scheduler_task_dispatch_level(task: dict[str, Any]) -> int:
    try:
        return max(0, min(5, int(task.get("dispatch_level") or 0)))
    except (TypeError, ValueError):
        return 0


def scheduler_task_retry_delay_seconds(task: dict[str, Any]) -> int:
    """Return retry delay for a technical Cell/trigger failure only.

    A normal business miss is not a Scheduler failure.  The task must persist
    the next_time selected by that business branch (defer, next cycle, None,
    or explicitly due again) and return normally, so the Scheduler records the
    attempt as ``success``.  This function is reserved for raised program,
    infrastructure, or contract exceptions where the Cell did not complete
    its scheduling contract.  An explicit interrupt restores the attempt's
    original trigger and does not use this retry policy.
    """

    raw_configured = task.get("error_retry_delay_seconds")
    if raw_configured not in {None, ""}:
        try:
            return max(0, int(raw_configured))
        except (TypeError, ValueError):
            pass
    if scheduler_task_dispatch_level(task) > 0:
        return 0
    try:
        configured = int(raw_configured or 0)
    except (TypeError, ValueError):
        configured = 0
    return configured if configured > 0 else 600


def scheduler_task_retry_time(task: dict[str, Any], finished: datetime) -> str:
    delay_seconds = scheduler_task_retry_delay_seconds(task)
    retry_at = finished + timedelta(seconds=delay_seconds)
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    start = payload.get("daily_start_time")
    end = payload.get("daily_end_time")
    if start and end:
        retry_at = clip_daily_retry_to_window(
            retry_at,
            now=finished,
            start=str(start),
            end=str(end),
        )
    return retry_at.strftime("%Y-%m-%d %H:%M:%S")


def scheduler_task_retry_ends_at_daily_close(
    task: dict[str, Any],
    finished: datetime,
) -> bool:
    """Whether this technical retry has crossed a one-day activity close.

    Most windowed Jobs roll a failed attempt to tomorrow's opening.  Dynamic
    one-off event tails such as Xianmeng challenge instead stop scheduling;
    tomorrow's activity-list discovery owns tomorrow's trigger.
    """

    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    if str(payload.get("error_retry_after_daily_end") or "").strip().lower() != "none":
        return False
    end = str(payload.get("daily_end_time") or "").strip()
    if not end:
        return False
    try:
        end_clock = datetime.strptime(end, "%H:%M").time()
    except ValueError:
        return False
    retry_at = finished + timedelta(seconds=scheduler_task_retry_delay_seconds(task))
    close_at = finished.replace(
        hour=end_clock.hour,
        minute=end_clock.minute,
        second=end_clock.second,
        microsecond=0,
    )
    return finished >= close_at or retry_at >= close_at


def schedule_failed_task_retry(
    task: dict[str, Any],
    finished: datetime,
) -> None:
    """Recover a technical Cell failure through the single trigger fact.

    Do not call this for a business outcome.  Business code owns ``next_time``
    and returns normally even when the desired in-game result was not reached.
    """

    if scheduler_task_retry_ends_at_daily_close(task, finished):
        task["next_time"] = None
        return

    # Login is a manual Job in the product surface, but a successful MuMu
    # recovery turns it into the mandatory continuation of the invalidated GUI
    # transaction.  The Jupyter boundary deliberately preserves the exception
    # type in ``last_message``.  Clearing this trigger under the generic manual
    # Job rule would allow ordinary due Jobs to run on a freshly restarted,
    # not-yet-reconciled game and would also skip the post-login bubble hide.
    if (
        str(task.get("id") or "") == LOGIN_GAME_SCHEDULER_TASK_ID
        and str(task.get("last_message") or "").startswith(
            "FanxiuEmulatorRestartRequired:"
        )
        and "已完整重启 MuMu" in str(task.get("last_message") or "")
    ):
        task["next_time"] = finished.strftime("%Y-%m-%d %H:%M:%S")
        return

    # A pure manual Job has no autonomous recurrence. ``0`` means "do not
    # install a technical retry" for that trigger kind; treating it as an
    # immediate timestamp turns one explicit run into an endless due loop.
    # Timed/urgent Jobs keep the existing zero-delay immediate retry meaning.
    if (
        str(task.get("trigger_description") or "").strip() == "手动"
        and scheduler_task_retry_delay_seconds(task) == 0
    ):
        task["next_time"] = None
        return
    if scheduler_task_retry_delay_seconds(task) == 0:
        task["next_time"] = finished.strftime("%Y-%m-%d %H:%M:%S")
        return
    task["next_time"] = scheduler_task_retry_time(task, finished)


def sort_scheduler_tasks_for_dispatch(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply hard level, trigger cohort, retry policy, then configured soft order."""
    return sorted(tasks, key=data_annotation_scheduler_dispatch_sort_key)


def scheduler_tasks_for_dispatch(
    tasks: list[dict[str, Any]],
    *,
    scheduler_settings_path: Path | None = None,
) -> list[dict[str, Any]]:
    settings = read_scheduler_settings(
        scheduler_settings_path=scheduler_settings_path
    )
    sequence = settings["time_sequence"]
    return [
        scheduler_task_time_view(task, tasks, sequence)
        for task in tasks
    ]


def select_due_data_annotation_scheduler_tasks(
    tasks: list[dict[str, Any]],
    *,
    scheduler_settings_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Select due tasks by derived time while preserving their original fact."""

    return sorted(
        (
            task
            for task in scheduler_tasks_for_dispatch(
                tasks,
                scheduler_settings_path=scheduler_settings_path,
            )
            if data_annotation_task_due(task) and task_supported(task)
        ),
        key=data_annotation_scheduler_dispatch_sort_key,
    )


def _higher_level_due_task_for_attempt(
    running_task_id: str,
    attempt_id: str,
    *,
    exclude_task_ids: set[str] | None = None,
    scheduler_state_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any] | None:
    """Return the highest newly-due preemptor for one live Scheduler attempt."""
    settings = read_scheduler_settings(
        scheduler_settings_path=scheduler_settings_path,
    )
    if not bool(settings.get("job_group_enabled", True)):
        # Priority preemption belongs to the engineering Scheduler.  A manual
        # scheduler-task can still own an attempt while AI has runtime control,
        # but engineering due facts must be inert in that mode.
        return None
    tasks = read_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
    )
    running_task = next((item for item in tasks if str(item.get("id") or "") == running_task_id), None)
    if running_task is None or running_task.get("attempt_id") != attempt_id:
        return None
    if str(running_task.get("last_result") or "") != "running":
        return None
    running_level = scheduler_task_dispatch_level(running_task)
    excluded_ids = {
        str(task_id or "").strip()
        for task_id in (exclude_task_ids or set())
        if str(task_id or "").strip()
    }
    candidates = select_due_data_annotation_scheduler_tasks(
        tasks,
        scheduler_settings_path=scheduler_settings_path,
    )
    candidates = [
        item
        for item in candidates
        if str(item.get("id") or "") != running_task_id
        and str(item.get("id") or "") not in excluded_ids
        and not is_deprecated_data_annotation_job_type(str(item.get("task_type") or ""))
        and scheduler_task_dispatch_level(item) > running_level
    ]
    ordered = sort_scheduler_tasks_for_dispatch(candidates)
    return ordered[0] if ordered else None


def _task_terminal_payload_from_cell(result: dict[str, Any]) -> dict[str, Any]:
    if str(result.get("status") or "error") != "success":
        return {}
    raw = str(result.get("result_text") or "").strip()
    if raw:
        try:
            payload = ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            payload = None
        if isinstance(payload, dict):
            return payload
    return {}


def _task_result_from_cell(result: dict[str, Any]) -> tuple[str, str]:
    if str(result.get("status") or "error") != "success":
        return "error", str(result.get("error") or result.get("message") or "Cell 执行失败")
    payload = _task_terminal_payload_from_cell(result)
    return "success", str(
        payload.get("message") or result.get("message") or "Cell 执行完成"
    ).strip()


def _scheduler_submission_timeout_is_live_task(
    task: dict[str, Any],
    *,
    kernel_generation: Any,
) -> bool:
    """Return True when a submit-side timeout raced with an already-running task Cell."""

    from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_manager_status

    try:
        kernel = fanxiu_kernel_manager_status(timeout_seconds=2.0)
    except Exception:
        return False
    if str(kernel.get("execution_state") or "") != "busy":
        return False
    if kernel_generation is not None and kernel.get("generation") != kernel_generation:
        return False
    try:
        runtime = behavior_tree_runtime_runner_status()
    except Exception:
        return False
    runtime_attempt_id = str(runtime.get("scheduler_attempt_id") or "")
    expected_attempt_id = str(task.get("attempt_id") or "")
    return (
        str(runtime.get("status") or "") == "running"
        and str(runtime.get("current_task_id") or "") == str(task.get("id") or "")
        # A Cell started by the immediately previous Kernel code has no
        # attempt field yet.  Same task + same generation remains sufficient
        # for that one rolling-upgrade window; once present, the id must match.
        and (not runtime_attempt_id or not expected_attempt_id or runtime_attempt_id == expected_attempt_id)
    )


def _scheduler_submit_exception_is_caller_timeout(exc: Exception) -> bool:
    """Classify only transport/wait timeouts, never business task failures."""

    if isinstance(exc, TimeoutError):
        return True
    message = str(exc)
    return "Kernel didn't respond" in message or "Jupyter cell 执行超时" in message


def _matching_scheduler_runtime_terminal(
    runtime: dict[str, Any],
    task: dict[str, Any],
) -> tuple[str, str] | None:
    """Return an authoritative terminal only when task and attempt identities match."""

    task_id = str(task.get("id") or "")
    attempt_id = str(task.get("attempt_id") or "")
    if not task_id or not attempt_id:
        return None
    if str(runtime.get("scheduler_task_id") or "") != task_id:
        return None
    if str(runtime.get("scheduler_attempt_id") or "") != attempt_id:
        return None
    result = str(runtime.get("scheduler_terminal_result") or "")
    if result not in {"success", "error", "interrupted"}:
        return None
    return result, str(
        runtime.get("scheduler_terminal_message")
        or runtime.get("message")
        or ""
    ).strip()


def reconcile_stale_scheduler_attempts(
    tasks: list[dict[str, Any]],
    *,
    scheduler_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> bool:
    """Invalidate orphaned attempts; recovery is a later whole-job Scheduler retry."""
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_manager_status

    kernel = fanxiu_kernel_manager_status()
    kernel_alive = bool(kernel.get("alive", True))
    kernel_busy = str(kernel.get("execution_state") or "") == "busy"
    kernel_generation = kernel.get("generation")
    # Runtime keeps the last scheduler terminal together with its attempt id.
    # Read it even after the Kernel becomes idle so a detached caller can be
    # reconciled without replaying the business Cell.
    try:
        runtime = behavior_tree_runtime_runner_status()
    except Exception:
        runtime = {}
    runtime_task_id = (
        str(runtime.get("current_task_id") or "")
        if runtime.get("running")
        else ""
    )
    now = datetime.now()
    changed = False
    dirty = False
    dirty_ids: set[str] = set()
    dirty_attempt_ids: dict[str, str | None] = {}
    for task in tasks:
        task_id = str(task.get("id") or "")
        if (
            str(task.get("last_result") or "") == "error"
            and str(task.get("last_message") or "").startswith(
                "先前 Cell/Kernel 执行尝试已作废；保留原触发时间"
            )
        ):
            schedule_failed_task_retry(task, now)
            task["last_message"] = "先前 Cell/Kernel 执行尝试已作废；已按失败策略安排整单重试"
            task["finished_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
            record_scheduler_task_fact(task, "error", world_facts_path=world_facts_path)
            changed = True
            dirty = True
            dirty_ids.add(task_id)
            continue
        if str(task.get("last_result") or "") != "running":
            continue
        attempt_generation = task.get("attempt_kernel_generation")
        generation_changed = (
            attempt_generation is not None
            and kernel_generation is not None
            and attempt_generation != kernel_generation
        )
        same_live_generation = (
            kernel_busy
            and not generation_changed
            and (not runtime_task_id or task_id == runtime_task_id)
        )
        if same_live_generation:
            if task.pop("attempt_kernel_idle_since", None) is not None:
                dirty = True
                dirty_ids.add(task_id)
                dirty_attempt_ids[task_id] = str(task.get("attempt_id") or "") or None
            continue
        terminal = None if generation_changed else _matching_scheduler_runtime_terminal(runtime, task)
        if terminal is not None:
            terminal_result, terminal_message = terminal
            terminal_attempt_id = str(task.get("attempt_id") or "") or None
            if terminal_result == "success":
                task["last_result"] = "success"
                task["last_message"] = terminal_message or "Cell 执行完成（已回收 Runtime 终态）"
            elif terminal_result == "interrupted":
                task["last_result"] = "interrupted"
                task["last_message"] = terminal_message or "Cell 执行已中断"
                task["next_time"] = task.get("attempt_original_trigger")
            else:
                task["last_result"] = "error"
                task["last_message"] = terminal_message or "Cell 权威终态为失败"
                schedule_failed_task_retry(task, now)
            task["finished_at"] = datetime.fromtimestamp(
                float(runtime.get("scheduler_terminal_at") or time.time())
            ).strftime("%Y-%m-%d %H:%M:%S")
            task["attempt_id"] = None
            task["attempt_original_trigger"] = None
            task["attempt_kernel_generation"] = None
            task["attempt_kernel_idle_since"] = None
            record_scheduler_task_fact(
                task,
                str(task.get("last_result") or terminal_result),
                world_facts_path=world_facts_path,
            )
            changed = True
            dirty = True
            dirty_ids.add(task_id)
            dirty_attempt_ids[task_id] = terminal_attempt_id
            continue
        if kernel_alive and not generation_changed:
            idle_since = task.get("attempt_kernel_idle_since")
            if not idle_since:
                task["attempt_kernel_idle_since"] = now.strftime("%Y-%m-%d %H:%M:%S")
                dirty = True
                dirty_ids.add(task_id)
                dirty_attempt_ids[task_id] = str(task.get("attempt_id") or "") or None
                continue
            try:
                idle_age = (now - datetime.strptime(str(idle_since), "%Y-%m-%d %H:%M:%S")).total_seconds()
            except (TypeError, ValueError):
                idle_age = 0.0
            # Cell 已结束与外部 Scheduler 写回终态之间存在极短竞态窗口。
            # 先给终态记录器一个租约宽限期；持续 idle 才视为外部调用已丢失。
            if idle_age < 30.0:
                continue
        stale_attempt_id = str(task.get("attempt_id") or "") or None
        task["last_result"] = "error"
        task["last_message"] = "先前 Cell/Kernel 执行尝试已作废；按失败策略等待 Scheduler 整单重试"
        schedule_failed_task_retry(task, now)
        task["finished_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        task["attempt_id"] = None
        task["attempt_original_trigger"] = None
        task["attempt_kernel_generation"] = None
        task["attempt_kernel_idle_since"] = None
        record_scheduler_task_fact(task, "error", world_facts_path=world_facts_path)
        changed = True
        dirty = True
        dirty_ids.add(task_id)
        dirty_attempt_ids[task_id] = stale_attempt_id
    if dirty:
        write_scheduler_tasks(
            tasks,
            scheduler_state_path=scheduler_state_path,
            runtime_update_ids=dirty_ids,
            expected_runtime_attempt_ids=dirty_attempt_ids or None,
        )
    return changed


def _run_scheduler_task_cell_and_record_terminal_owned(
    *,
    entry: Any,
    entry_id: str,
    task: dict[str, Any],
    preemption_exclude_task_ids: set[str] | None = None,
    scheduler_state_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    scheduled_attempt: bool = False,
) -> dict[str, Any]:
    """Submit one ordinary task Cell and keep Scheduler state orthogonal and terminal."""
    started = datetime.now()
    started_text = started.strftime("%Y-%m-%d %H:%M:%S")
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_manager_status

    kernel_generation = fanxiu_kernel_manager_status().get("generation")
    attempt_id = uuid.uuid4().hex
    tasks = read_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
    )
    state_task = next((item for item in tasks if item.get("id") == task.get("id")), None)
    if state_task is None:
        raise LookupError(f"Scheduler 任务不存在：{task.get('id') or ''}")
    observed_attempt_id = str(state_task.get("attempt_id") or "") or None
    if str(state_task.get("last_result") or "") == "running" and observed_attempt_id:
        return {
            "status": "running",
            "phase": "scheduler_attempt_already_claimed",
            "message": "目标作业已有外部 Scheduler attempt，未重复提交 Cell",
            "attempt_id": observed_attempt_id,
        }
    original_next_time = state_task.get("next_time")
    state_task["last_run_at"] = started_text
    state_task["last_result"] = "running"
    state_task["last_message"] = "已向 Fanxiu Kernel 提交普通 Cell"
    state_task["started_at"] = started_text
    state_task["finished_at"] = None
    state_task["attempt_id"] = attempt_id
    state_task["attempt_original_trigger"] = original_next_time
    state_task["attempt_kernel_generation"] = kernel_generation
    state_task["attempt_kernel_idle_since"] = None
    # Incidents describe this submission, not the task's previous terminal
    # projection.  Keeping the old snapshot here made a later retry inherit an
    # earlier started_at/message and look as if it had occupied the Kernel
    # across several unrelated jobs.
    attempt_task_state = deepcopy(state_task)
    # ``task`` can be a non-persistent run-now copy whose payload carries
    # per-attempt fields such as ``effective_now``.  The fresh disk read above
    # is authoritative for ownership/runtime fields, but it deliberately does
    # not contain those transient overrides.  Keep the two concerns separate:
    # persist attempt metadata from ``state_task`` while submitting the exact
    # payload from the requested task copy.  Do not write the transient payload
    # back into the Scheduler's durable task configuration.
    attempt_task_state["payload"] = deepcopy(
        task.get("payload") if isinstance(task.get("payload"), dict) else {}
    )
    claimed = write_scheduler_tasks(
        tasks,
        scheduler_state_path=scheduler_state_path,
        runtime_update_ids={str(state_task.get("id") or "")},
        expected_runtime_attempt_ids={str(state_task.get("id") or ""): observed_attempt_id},
    )
    if claimed is False:
        return {
            "status": "running",
            "phase": "scheduler_attempt_already_claimed",
            "message": "另一派发者已原子领取目标作业，本轮未提交 Cell",
        }
    record_scheduler_task_fact(state_task, "running", world_facts_path=world_facts_path)

    preempting_task: dict[str, Any] | None = None
    preemption_detected_at: datetime | None = None
    preemption_interrupt_attempts = 0
    preemption_last_error = ""

    def submit_cell() -> dict[str, Any]:
        try:
            return submit_runtime_task_cell(
                entry=entry,
                entry_id=entry_id,
                task_type=str(task.get("task_type") or ""),
                payload=task_payload_with_meta(attempt_task_state),
            )
        except Exception as exc:
            if (
                _scheduler_submit_exception_is_caller_timeout(exc)
                and _scheduler_submission_timeout_is_live_task(
                    attempt_task_state,
                    kernel_generation=kernel_generation,
                )
            ):
                return {
                    "status": "running",
                    "phase": "accepted_after_submit_timeout",
                    "message": "提交端等待 Kernel ready 超时，但 manager/runtime 已确认同一任务 Cell 正在运行",
                }
            return {"status": "error", "phase": "error", "message": str(exc), "error": str(exc)}

    # Cell execution remains synchronous from the caller's perspective, but the
    # external Scheduler must keep observing trigger facts while it waits.  A
    # higher-level task can therefore become due during this Cell and interrupt
    # it without introducing any queue or priority state inside the Kernel.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="fanxiu-scheduler-cell") as executor:
        future = executor.submit(submit_cell)
        while True:
            try:
                result = future.result(timeout=0.5)
                break
            except FutureTimeoutError:
                if preempting_task is not None:
                    continue
                candidate = _higher_level_due_task_for_attempt(
                    str(task.get("id") or ""),
                    attempt_id,
                    exclude_task_ids=preemption_exclude_task_ids,
                    scheduler_state_path=scheduler_state_path,
                    scheduler_settings_path=scheduler_settings_path,
                    world_facts_path=world_facts_path,
                )
                if candidate is None:
                    continue
                if preemption_detected_at is None:
                    preemption_detected_at = datetime.now()
                from backend.core.fanxiu.behavior_tree.jupyter_kernel import send_fanxiu_kernel_manager_command

                preemption_interrupt_attempts += 1
                try:
                    interrupt_result = send_fanxiu_kernel_manager_command("interrupt", timeout_seconds=5.0)
                except (OSError, EOFError, TimeoutError) as exc:
                    preemption_last_error = f"{type(exc).__name__}: {exc}"
                    continue
                if bool(interrupt_result.get("ok")):
                    preempting_task = candidate
                    preemption_last_error = ""
                else:
                    preemption_last_error = str(
                        interrupt_result.get("error")
                        or interrupt_result.get("message")
                        or "interrupt command was not acknowledged"
                    )

    tasks = read_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
    )
    finished = datetime.now()
    state_task = next((item for item in tasks if item.get("id") == task.get("id")), None) or dict(task)
    if state_task.get("attempt_id") != attempt_id:
        return result
    if str(result.get("status") or "") == "running":
        return result
    terminal_payload = _task_terminal_payload_from_cell(result)
    task_result, task_message = _task_result_from_cell(result)
    preempted = (
        preempting_task is not None
        and str(result.get("status") or "error") != "success"
    )
    if preempted:
        task_result = "interrupted"
        task_message = (
            f"被更高级作业 {preempting_task.get('label') or preempting_task.get('id') or preempting_task.get('task_type')} "
            f"(级别 {scheduler_task_dispatch_level(preempting_task)}) 打断；本次作废并整单重跑"
        )
    if task_result == "success":
        # ``success`` is a Scheduler/trigger result: the submitted Cell ran to
        # a normal return and made a fresh scheduling decision.  It does NOT
        # mean the in-game objective succeeded.  Business misses, cooldowns,
        # exhausted rewards and dynamic rechecks all stay on this path; the
        # task expresses their future effect only through the next_time chosen
        # by that business branch: defer, next cycle, None, or (only when the
        # business itself requires it) immediately due again.
        # Only exceptions, interrupts and infrastructure loss use the error
        # path below and activate the Scheduler's technical retry policy.
        state_task["last_result"] = "success"
        state_task["last_run_at"] = started_text
        state_task["last_message"] = task_message or "Cell 执行完成"
    elif task_result == "interrupted":
        state_task["last_result"] = "interrupted"
        state_task["last_run_at"] = started_text
        state_task["last_message"] = task_message or "Cell 执行已中断"
        state_task["next_time"] = state_task.get("attempt_original_trigger")
    else:
        state_task["last_result"] = "error"
        state_task["last_run_at"] = started_text
        state_task["last_message"] = task_message or "Cell 执行失败"
        schedule_failed_task_retry(state_task, finished)
    state_task["finished_at"] = finished.strftime("%Y-%m-%d %H:%M:%S")
    state_task["attempt_id"] = None
    state_task["attempt_original_trigger"] = None
    state_task["attempt_kernel_generation"] = None
    state_task["attempt_kernel_idle_since"] = None
    replaced = False
    for index, item in enumerate(tasks):
        if item.get("id") == state_task.get("id"):
            tasks[index] = state_task
            replaced = True
            break
    if not replaced:
        tasks.append(state_task)
    terminal_written = write_scheduler_tasks(
        tasks,
        scheduler_state_path=scheduler_state_path,
        runtime_update_ids={str(state_task.get("id") or "")},
        expected_runtime_attempt_ids={str(state_task.get("id") or ""): attempt_id},
    )
    if terminal_written is False:
        return {
            **result,
            "status": "running",
            "phase": "scheduler_attempt_ownership_lost",
            "message": "attempt 所有权已变化，忽略当前调用方的迟到终态",
            "attempt_id": attempt_id,
        }
    record_scheduler_task_fact(state_task, str(state_task.get("last_result") or "error"), world_facts_path=world_facts_path)
    incident = terminal_payload.get("scheduler_incident")
    if task_result == "success" and isinstance(incident, dict):
        incident_runtime_status = (
            read_behavior_tree_runtime_status(runtime_state_path)
            if runtime_state_path is not None
            else {}
        )
        record_scheduler_incident(
            task=attempt_task_state,
            original_next_time=original_next_time,
            next_time=state_task.get("next_time"),
            incident=incident,
            attempt_id=attempt_id,
            entry_id=entry_id,
            occurred_at=finished,
            runtime_status=incident_runtime_status,
            scheduler_state_path=scheduler_state_path,
        )
    elif task_result == "interrupted":
        interrupted_runtime_status = (
            read_behavior_tree_runtime_status(runtime_state_path)
            if runtime_state_path is not None
            else {}
        )
        record_scheduler_incident(
            task=attempt_task_state,
            original_next_time=original_next_time,
            next_time=state_task.get("next_time"),
            incident={
                "kind": "attempt_interrupted",
                "cycle_kind": "scheduler",
                "reason": state_task.get("last_message") or task_message,
                "preemption": {
                    "candidate_task_id": str((preempting_task or {}).get("id") or ""),
                    "detected_at": (
                        preemption_detected_at.strftime("%Y-%m-%d %H:%M:%S.%f")
                        if preemption_detected_at is not None
                        else None
                    ),
                    "interrupt_attempts": preemption_interrupt_attempts,
                    "last_error": preemption_last_error,
                },
            },
            attempt_id=attempt_id,
            entry_id=entry_id,
            occurred_at=finished,
            runtime_status=interrupted_runtime_status,
            scheduler_state_path=scheduler_state_path,
        )
    elif task_result == "error" and scheduled_attempt:
        failure_runtime_status = (
            read_behavior_tree_runtime_status(runtime_state_path)
            if runtime_state_path is not None
            else {}
        )
        record_scheduler_incident(
            task=attempt_task_state,
            original_next_time=original_next_time,
            next_time=state_task.get("next_time"),
            incident={
                "kind": "attempt_failed",
                "cycle_kind": "scheduler",
                "reason": state_task.get("last_message") or task_message,
            },
            attempt_id=attempt_id,
            entry_id=entry_id,
            occurred_at=finished,
            runtime_status=failure_runtime_status,
            scheduler_state_path=scheduler_state_path,
        )
    if runtime_state_path is not None:
        runtime_terminal = read_behavior_tree_runtime_status(runtime_state_path)
        runtime_terminal.update({
            "running": False,
            "guard_running": False,
            "status": task_result,
            "phase": (
                "done"
                if task_result == "success"
                else ("interrupted" if task_result == "interrupted" else "error")
            ),
            "task_type": "",
            "current_task": "",
            "current_task_id": "",
            "message": task_message or str(result.get("message") or ""),
            "error": (
                str(result.get("error") or task_message or "")
                if task_result == "error"
                else ""
            ),
            "finished_at": time.time(),
            "updated_at": time.time(),
        })
        persist_behavior_tree_runtime_status(
            runtime_terminal,
            runtime_state_path=runtime_state_path,
            world_facts_path=world_facts_path,
        )
    return {
        **result,
        "status": task_result,
        "phase": (
            "done"
            if task_result == "success"
            else ("interrupted" if task_result == "interrupted" else "error")
        ),
        "message": task_message or str(result.get("message") or ""),
        "task_result": task_result,
        "preemption": {
            "candidate_task_id": str((preempting_task or {}).get("id") or ""),
            "detected_at": (
                preemption_detected_at.strftime("%Y-%m-%d %H:%M:%S.%f")
                if preemption_detected_at is not None
                else None
            ),
            "interrupt_attempts": preemption_interrupt_attempts,
            "last_error": preemption_last_error,
        },
    }


def _run_scheduler_task_cell_and_record_terminal(
    *,
    entry: Any,
    entry_id: str,
    task: dict[str, Any],
    preemption_exclude_task_ids: set[str] | None = None,
    scheduler_state_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    scheduled_attempt: bool = False,
) -> dict[str, Any]:
    """Own the one external Scheduler Cell submission lane cross-process.

    Runtime preparation and interrupt happen before this entry. Once the old
    Cell has yielded, API run-now and watch-doctor may observe the same idle
    boundary in different processes. A non-blocking lease lets exactly one of
    them submit; the loser returns to its caller instead of queueing a second
    ordinary Cell behind Jupyter's ready handshake.
    """

    state_path = scheduler_state_path or fanxiu_data_annotation_scheduler_state_path()
    dispatch_lock_path = state_path.with_name("scheduler_cell_dispatch.lock")
    dispatch_lock = FileLock(str(dispatch_lock_path), timeout=0)
    try:
        with dispatch_lock:
            return _run_scheduler_task_cell_and_record_terminal_owned(
                entry=entry,
                entry_id=entry_id,
                task=task,
                preemption_exclude_task_ids=preemption_exclude_task_ids,
                scheduler_state_path=scheduler_state_path,
                scheduler_settings_path=scheduler_settings_path,
                runtime_state_path=runtime_state_path,
                world_facts_path=world_facts_path,
                scheduled_attempt=scheduled_attempt,
            )
    except FileLockTimeout:
        return {
            "status": "running",
            "phase": "scheduler_dispatch_already_owned",
            "message": "另一外部派发者已占用 Scheduler Cell 提交通道，本轮未提交 Cell",
        }


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
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_manager_status

    kernel = fanxiu_kernel_manager_status()
    if str(kernel.get("execution_state") or "") == "busy":
        status = behavior_tree_runtime_status(
            runtime_state_path=runtime_state_path,
            world_facts_path=world_facts_path,
        )
        status.update({
            "phase": "scheduler_wait_kernel_busy",
            "message": f"Kernel 正在执行 Cell，{task.get('id') or task.get('label') or task.get('task_type')} 等待空闲",
            "updated_at": time.time(),
        })
        persist_behavior_tree_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
        return status
    status = behavior_tree_runtime_runner_status()
    if not status.get("running"):
        return None
    has_running_attempt = any(
        str(item.get("last_result") or "") == "running"
        or bool(item.get("attempt_id"))
        for item in tasks
        if isinstance(item, dict)
    )
    if _is_scheduler_behavior_tree_runtime_status(status) and not has_running_attempt:
        recovered = {
            **status,
            "running": False,
            "guard_running": False,
            "status": "idle",
            "phase": "scheduler_stale_runtime_recovered",
            "task_type": "",
            "current_task": "",
            "current_task_id": "",
            "message": "Kernel 已空闲且 Scheduler 无运行中 attempt，已清理陈旧运行状态",
            "error": "",
            "updated_at": time.time(),
        }
        persist_behavior_tree_runtime_status(
            recovered,
            runtime_state_path=runtime_state_path,
            world_facts_path=world_facts_path,
        )
        return None
    if interrupt_same_group and _is_scheduler_behavior_tree_runtime_status(status):
        if not bool(status.get("interruptible", True)):
            message = f"当前作业不可中断，{task.get('id') or task.get('label') or task.get('task_type')} 暂不触发"
            status.update({"message": message, "updated_at": time.time()})
            persist_behavior_tree_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
            return status
        stop_fanxiu_behavior_tree_current_task(str(entry_id or status.get("entry_id") or ""))
        if _wait_runtime_idle(wait_timeout_seconds):
            return None
        status = behavior_tree_runtime_runner_status()
        message = f"已请求中断当前作业，{task.get('id') or task.get('label') or task.get('task_type')} 等待运行时空闲"
        status.update({"message": message, "updated_at": time.time()})
        persist_behavior_tree_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
        return status
    task_id = str(task.get("id") or "")
    message = f"当前有任务运行，{task_id or task.get('label') or task.get('task_type')} 暂不触发"
    status.update({"message": message, "updated_at": time.time()})
    persist_behavior_tree_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
    return status


def _is_scheduler_behavior_tree_runtime_status(status: dict[str, Any]) -> bool:
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
        status = behavior_tree_runtime_runner_status()
        if not status.get("running") and str(status.get("status") or "") != "stopping":
            return True
        time.sleep(0.1)
    status = behavior_tree_runtime_runner_status()
    return not status.get("running") and str(status.get("status") or "") != "stopping"


def ensure_scheduler_kernel_code_current(
    *,
    entry: Any,
    entry_id: str,
    timeout_seconds: float = 45.0,
) -> dict[str, Any]:
    """Make the next Scheduler Cell run on the current behavior-tree code."""

    from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_manager_status
    from backend.core.fanxiu.behavior_tree.kernel import FanxiuKernel

    expected_signature = fanxiu_behavior_tree_code_signature()
    kernel_status = fanxiu_kernel_manager_status()
    if str(kernel_status.get("execution_state") or "") == "busy":
        return {
            "ready": False,
            "restarted": False,
            "reason": "kernel_busy",
            "kernel": kernel_status,
        }

    loaded_signature = str(kernel_status.get("behavior_tree_code_signature") or "")
    if bool(kernel_status.get("alive")) and loaded_signature == expected_signature:
        return {
            "ready": True,
            "restarted": False,
            "reason": "code_current",
            "kernel": kernel_status,
        }

    kernel = FanxiuKernel(entry_id=str(entry_id))
    if bool(kernel_status.get("alive")) and not loaded_signature:
        # Managers started before code-signature support cannot report what
        # their child loaded. Replace that one legacy process once; future
        # code updates only need a native Kernel restart.
        if kernel_status.get("manager_pid") is None:
            return {
                "ready": True,
                "restarted": False,
                "reason": "signature_unavailable",
                "kernel": kernel_status,
            }
        kernel.shutdown(timeout_seconds=min(15.0, max(1.0, timeout_seconds)))
        ensure_fanxiu_behavior_tree_service(entry, entry_id)
    elif kernel_status.get("manager_pid") is not None and loaded_signature != expected_signature:
        # A native child restart reuses the already imported manager module.
        # That is insufficient when the manager/bootstrap code itself moved or
        # changed (for example runtime.jupyter_kernel ->
        # behavior_tree.jupyter_kernel): the old manager would keep emitting
        # the stale bootstrap Cell forever. Replace the manager process so both
        # the control plane and the child Kernel load the current source tree.
        kernel.shutdown(timeout_seconds=min(15.0, max(1.0, timeout_seconds)))
        ensure_fanxiu_behavior_tree_service(entry, entry_id)
    elif bool(kernel_status.get("alive")):
        kernel.restart(timeout_seconds=max(1.0, timeout_seconds))
    elif kernel_status.get("manager_pid") is not None:
        # The manager process can still be healthy after its Jupyter child
        # exits.  ``alive`` describes the child, not the manager endpoint.
        # Reuse that endpoint to create a fresh generation; starting another
        # manager would collide with the existing control listener.
        kernel.restart(timeout_seconds=max(1.0, timeout_seconds))
    else:
        ensure_fanxiu_behavior_tree_service(entry, entry_id)

    refreshed = fanxiu_kernel_manager_status(timeout_seconds=3.0)
    refreshed_signature = str(refreshed.get("behavior_tree_code_signature") or "")
    if (
        not bool(refreshed.get("alive"))
        or str(refreshed.get("execution_state") or "") != "idle"
        or refreshed_signature != expected_signature
    ):
        raise RuntimeError("Fanxiu Kernel 未能加载最新版行为树代码")
    return {
        "ready": True,
        "restarted": True,
        "reason": "code_refreshed",
        "kernel": refreshed,
    }


def run_now_scheduler_task(
    *,
    entry: Any,
    entry_id: str,
    task_id: str,
    payload_override: dict[str, Any] | None = None,
    business_time_mode: Literal["planned", "current"] = "planned",
    interrupt_same_group: bool = True,
    scheduler_state_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    asset_tree_path: Path | None = None,
) -> dict[str, Any]:
    if business_time_mode not in {"planned", "current"}:
        raise ValueError("business_time_mode 必须是 planned 或 current")
    maintain_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
    )
    tasks = read_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
    )
    reconcile_stale_scheduler_attempts(
        tasks,
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
    )
    state_task = next((item for item in tasks if item.get("id") == task_id), None)
    payload = deepcopy(payload_override or {})
    if business_time_mode == "planned" and "effective_now" not in payload and state_task is not None:
        next_time = parse_data_annotation_task_time(state_task.get("next_time"))
        if next_time is None:
            raise ValueError("该作业没有计划时间，不能提前运行")
        if next_time <= time.time():
            raise ValueError("该作业已经到期，不能提前运行；请使用立即运行")
        payload["effective_now"] = (
            datetime.fromtimestamp(next_time) + timedelta(minutes=1)
        ).strftime("%Y-%m-%d %H:%M:%S")
    run_task = data_annotation_scheduler_run_now_task(tasks, task_id, payload)
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
    kernel_code = ensure_scheduler_kernel_code_current(
        entry=entry,
        entry_id=entry_id,
    )
    if not bool(kernel_code.get("ready")):
        return {
            **behavior_tree_runtime_status(
                runtime_state_path=runtime_state_path,
                world_facts_path=world_facts_path,
            ),
            "phase": "scheduler_wait_kernel_busy",
            "message": "Kernel 正在执行 Cell，等待空闲后再运行作业",
        }
    return _run_scheduler_task_cell_and_record_terminal(
        entry=entry,
        entry_id=entry_id,
        task=run_task,
        scheduler_state_path=scheduler_state_path,
        scheduler_settings_path=scheduler_settings_path,
        runtime_state_path=runtime_state_path or fanxiu_behavior_tree_runtime_state_path(),
        world_facts_path=world_facts_path,
        # AI/manual dispatch of a task whose business timestamp is already due
        # is still a real Scheduler attempt.  Keep the relaxed run-now behavior
        # only for an intentional early/future probe.
        scheduled_attempt=data_annotation_task_due(state_task),
    )


def run_due_scheduler_tasks(
    *,
    entry: Any,
    entry_id: str,
    exclude_task_ids: set[str] | None = None,
    scheduler_state_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
    asset_tree_path: Path | None = None,
) -> dict[str, Any]:
    settings = read_scheduler_settings(scheduler_settings_path=scheduler_settings_path)
    ensure_fanxiu_behavior_tree_service(entry, entry_id, asset_tree_path=asset_tree_path or data_annotation_asset_tree_path(entry_id))
    behavior_enabled = bool(settings.get("behavior_tree_enabled", True))
    job_group_enabled = bool(settings.get("job_group_enabled", True))
    if not behavior_enabled or not job_group_enabled:
        status = behavior_tree_runtime_status(
            scheduler_settings_path=scheduler_settings_path,
            runtime_state_path=runtime_state_path,
            world_facts_path=world_facts_path,
        )
        disabled_reason = (
            "自动调度已关闭，到期作业暂不自动执行"
            if not behavior_enabled
            else "AI 调度器占用运行权，工程不自动提交到期作业"
        )
        status.update({
            "message": disabled_reason,
            "phase": "scheduler_job_group_disabled",
            "updated_at": time.time(),
        })
        persist_behavior_tree_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
        return status
    maintain_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
    )
    tasks = read_scheduler_tasks(
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
    )
    reconcile_stale_scheduler_attempts(
        tasks,
        scheduler_state_path=scheduler_state_path,
        world_facts_path=world_facts_path,
    )
    due_tasks = select_due_data_annotation_scheduler_tasks(
        tasks,
        scheduler_settings_path=scheduler_settings_path,
    )
    if due_tasks:
        excluded_ids = {str(task_id or "").strip() for task_id in (exclude_task_ids or set())}
        due_tasks = [
            task
            for task in due_tasks
            if (
                str(task.get("id") or "") not in excluded_ids
                and not is_deprecated_data_annotation_job_type(str(task.get("task_type") or ""))
            )
        ]
        due_tasks = sort_scheduler_tasks_for_dispatch(due_tasks)
    if not due_tasks:
        status = behavior_tree_runtime_status(runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
        status.update({"message": "没有可执行的到期任务", "updated_at": time.time()})
        persist_behavior_tree_runtime_status(status, runtime_state_path=runtime_state_path, world_facts_path=world_facts_path)
        return status
    selected = due_tasks[0]
    latest_settings = read_scheduler_settings(scheduler_settings_path=scheduler_settings_path)
    if not bool(latest_settings.get("behavior_tree_enabled", True)) or not bool(
        latest_settings.get("job_group_enabled", True)
    ):
        status = behavior_tree_runtime_status(
            scheduler_settings_path=scheduler_settings_path,
            runtime_state_path=runtime_state_path,
            world_facts_path=world_facts_path,
        )
        status.update({
            "phase": "scheduler_job_group_disabled",
            "message": "AI 调度器已取得运行权，本轮不再提交新的到期 Cell",
            "updated_at": time.time(),
        })
        persist_behavior_tree_runtime_status(
            status,
            runtime_state_path=runtime_state_path,
            world_facts_path=world_facts_path,
        )
        return status
    blocked_status = prepare_runtime_for_scheduler_task(
        selected,
        tasks,
        scheduler_state_path=scheduler_state_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )
    if blocked_status is not None:
        return blocked_status
    kernel_code = ensure_scheduler_kernel_code_current(
        entry=entry,
        entry_id=entry_id,
    )
    if not bool(kernel_code.get("ready")):
        return {
            **behavior_tree_runtime_status(
                runtime_state_path=runtime_state_path,
                world_facts_path=world_facts_path,
            ),
            "phase": "scheduler_wait_kernel_busy",
            "message": "Kernel 正在执行 Cell，等待空闲后再运行到期作业",
        }
    latest_settings = read_scheduler_settings(scheduler_settings_path=scheduler_settings_path)
    if not bool(latest_settings.get("behavior_tree_enabled", True)) or not bool(
        latest_settings.get("job_group_enabled", True)
    ):
        status = behavior_tree_runtime_status(
            scheduler_settings_path=scheduler_settings_path,
            runtime_state_path=runtime_state_path,
            world_facts_path=world_facts_path,
        )
        status.update({
            "phase": "scheduler_job_group_disabled",
            "message": "AI 调度器已取得运行权，本轮不再提交新的到期 Cell",
            "updated_at": time.time(),
        })
        persist_behavior_tree_runtime_status(
            status,
            runtime_state_path=runtime_state_path,
            world_facts_path=world_facts_path,
        )
        return status
    result = _run_scheduler_task_cell_and_record_terminal(
        entry=entry,
        entry_id=entry_id,
        task=selected,
        preemption_exclude_task_ids=excluded_ids,
        scheduler_state_path=scheduler_state_path,
        scheduler_settings_path=scheduler_settings_path,
        runtime_state_path=runtime_state_path or fanxiu_behavior_tree_runtime_state_path(),
        world_facts_path=world_facts_path,
        scheduled_attempt=True,
    )
    return {
        **result,
        "dispatched_task_id": str(selected.get("id") or ""),
    }


