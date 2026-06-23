from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, select
from pyxllib.prog import process_runtime
from pyxllib.prog.schedule_policy import schedule_policy_label

from backend.api.task_manager import task_manager
from backend.db import engine
from backend.core.runtime.background_task_queue import background_task_queue
from backend.core.devices.device import device_manager, get_device_id
from backend.core.runtime.codeyun_watchdog import (
    CODEYUN_WATCHDOG_SERVICE_KEY,
    CodeYunWatchdogError,
    build_codeyun_watchdog_log_lines,
    disable_codeyun_watchdog_startup,
    enable_codeyun_watchdog_startup,
    get_codeyun_watchdog_status,
    start_codeyun_watchdog,
    stop_codeyun_watchdog,
)
from backend.core.runtime.proxy_traffic_audit_runtime import (
    PROXY_TRAFFIC_AUDIT_SERVICE_KEY,
    ProxyTrafficAuditError,
    build_proxy_traffic_audit_log_lines,
    get_proxy_traffic_audit_status,
    start_proxy_traffic_audit,
    stop_proxy_traffic_audit,
)
from backend.core.runtime.game_window_service import (
    GAME_WINDOW_SERVICE_KEY,
    GameWindowServiceError,
    get_game_window_service_status,
    start_game_window_service,
    stop_game_window_service,
)
from backend.core.ocr.preview import OcrPreviewError
from backend.core.runtime.ocr_service import (
    get_ocr_service_status,
    start_ocr_service,
    stop_ocr_service,
)
from backend.core.runtime.public_frontend_deploy import (
    PUBLIC_FRONTEND_DEPLOY_TASK_KEY,
    build_public_frontend_deploy_log_lines,
)
from backend.core.attendance.behavior_tree_service import (
    ATTENDANCE_BEHAVIOR_TREE_SERVICE_KEY,
    build_attendance_behavior_tree_log_lines,
    get_attendance_behavior_tree_status,
    is_attendance_behavior_tree_service_enabled,
    start_attendance_behavior_tree_service,
    stop_attendance_behavior_tree_service,
)
from backend.core.fanxiu.runtime.capture_runtime import (
    FANXIU_CAPTURE_RUNTIME_SERVICE_KEY,
    FANXIU_CAPTURE_RUNTIME_BEHAVIOR_TREE_REASON,
    ensure_fanxiu_capture_runtime_backstop,
    fanxiu_capture_runtime_service,
)
from backend.core.fanxiu.packet.insight_worker import fanxiu_packet_insight_worker
from backend.core.fanxiu.runtime.behavior_tree import (
    ensure_fanxiu_behavior_tree_service,
    fanxiu_data_annotation_runtime_dir,
    fanxiu_data_annotation_runtime_status,
    fanxiu_data_annotation_runtime_state_path,
    fanxiu_data_annotation_world_facts_path,
    stop_fanxiu_behavior_tree_current_task,
)
from backend.core.fanxiu.data_annotation.runtime_control import ensure_doctor_watch_background
from backend.core.runtime.units import (
    command_runtime_group,
    command_runtime_queue_name,
    infer_command_runtime_kind,
    is_legacy_codeyun_command_task,
    resolve_builtin_job_runtime_policy,
    resolve_command_runtime_policy,
    runtime_policy_payload,
)
from backend.core.settings import get_settings
from backend.models import Task as TaskModel, UserDevice

BUILTIN_OCR_SERVICE_KEY = "ocr"
FANXIU_BEHAVIOR_TREE_SERVICE_KEY = "fanxiu-behavior-tree"
CRITICAL_LOCAL_COMMAND_SERVICE_NAMES = {"sync", "syncthing", "frpc", "nginx"}


def _env_enabled(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _fanxiu_runtime_service_enabled(service_key: str, aliases: set[str], env_name: str) -> bool:
    configured = _env_enabled(os.getenv(env_name))
    if configured is not None:
        return configured

    services_text = os.getenv("FX_RUNTIME_SERVICES")
    if services_text is None:
        return False
    services = {item.strip().lower() for item in services_text.split(",") if item.strip()}
    return bool(services & {"*", "all", "fanxiu", service_key, *aliases})


def _fanxiu_capture_runtime_service_enabled() -> bool:
    return _fanxiu_runtime_service_enabled(
        FANXIU_CAPTURE_RUNTIME_SERVICE_KEY,
        {"fanxiu_capture_runtime", "capture_runtime", "capture", "凡修抓包"},
        "FX_CAPTURE_RUNTIME_SERVICE_ENABLED",
    )


def _fanxiu_game_window_service_enabled() -> bool:
    return _fanxiu_runtime_service_enabled(
        GAME_WINDOW_SERVICE_KEY,
        {"fanxiu_game_window", "game_window", "screen", "stream", "凡修画面流", "凡修游戏画面流"},
        "FX_GAME_WINDOW_SERVICE_ENABLED",
    )


def is_fanxiu_behavior_tree_service_enabled() -> bool:
    return _fanxiu_runtime_service_enabled(
        FANXIU_BEHAVIOR_TREE_SERVICE_KEY,
        {"fanxiu_behavior_tree", "behavior_tree", "runtime", "scheduler", "凡修行为树"},
        "FX_BEHAVIOR_TREE_SERVICE_ENABLED",
    )


def _fanxiu_behavior_tree_service_enabled() -> bool:
    return is_fanxiu_behavior_tree_service_enabled()


def _model_dump(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return dict(value)
    return {}


def _is_command_job(task: TaskModel) -> bool:
    return infer_command_runtime_kind(task) == "job"


def _command_group_for(task: TaskModel, kind: str) -> tuple[str, str]:
    return command_runtime_group(task, "job" if kind == "job" else "service")


def _runtime_group(kind: str, group_id: str, title: str) -> dict[str, Any]:
    return {
        "id": group_id,
        "kind": kind,
        "title": title,
        "queue_key": group_id if kind == "job" else None,
        "is_default": group_id in {"service:default", "job:default"},
    }


def _command_next_run_at(task: TaskModel) -> str | None:
    if task.next_run_at:
        return str(task.next_run_at)
    state = task.schedule_state or {}
    next_trigger_at = state.get("next_trigger_at") if isinstance(state, dict) else None
    if next_trigger_at:
        return str(next_trigger_at)
    task_id = task.id
    job = task_manager.scheduler.get_job(task_id)
    if not job or not job.next_run_time:
        return None
    return job.next_run_time.isoformat()


def _extract_command_option(command: str, option: str) -> str:
    pattern = rf'(?:^|\s){re.escape(option)}(?:=|\s+)("[^"]+"|\'[^\']+\'|\S+)'
    match = re.search(pattern, command)
    if not match:
        return ""
    return match.group(1).strip().strip('"\'')


def _short_device_name(value: str) -> str:
    name = value.strip()
    return name.removeprefix("codepc_") or name


def _command_runtime_title(task: TaskModel) -> str:
    title = task.name or task.id
    command = task.command or ""
    if "sync_rime_config.py" in command and (title == "小狼毫配置同步" or title.startswith("rime_")):
        target_name = _extract_command_option(command, "--target-name")
        target_entry_id = _extract_command_option(command, "--target-entry-id")
        target = _short_device_name(target_name or target_entry_id)
        return f"小狼毫到{target}" if target else "小狼毫命令同步"
    return title


def _find_queue_snapshot(queue: dict[str, Any] | None, task_name: str) -> dict[str, Any] | None:
    if not queue:
        return None
    running = queue.get("running")
    if isinstance(running, dict) and running.get("name") == task_name:
        return running
    for item in queue.get("pending") or []:
        if isinstance(item, dict) and item.get("name") == task_name:
            return item
    return None


def _runtime_queue_name_for_item(item: dict[str, Any]) -> str:
    source = str(item.get("source") or "")
    kind = str(item.get("kind") or "")
    key = str(item.get("key") or "")
    if source == "command" and kind == "job" and key:
        return command_runtime_queue_name(key)
    if source == "builtin" and key:
        return key
    return ""


def _enrich_runtime_queue(queue: dict[str, Any] | None, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(queue, dict):
        return queue

    title_by_queue_name = {
        queue_name: str(item.get("title") or item.get("key") or queue_name)
        for item in items
        if (queue_name := _runtime_queue_name_for_item(item))
    }

    def enrich_item(item: Any) -> Any:
        if not isinstance(item, dict):
            return item
        result = dict(item)
        title = title_by_queue_name.get(str(result.get("name") or ""))
        if title:
            metadata = dict(result.get("metadata") or {})
            metadata["title"] = title
            result["metadata"] = metadata
            result["display_name"] = title
        return result

    result = dict(queue)
    result["running"] = enrich_item(queue.get("running"))
    result["pending"] = [enrich_item(item) for item in queue.get("pending") or []]
    result["recent"] = [enrich_item(item) for item in queue.get("recent") or []]
    return result


def _queue_records_for_name(queue: dict[str, Any] | None, task_name: str, limit: int = 500) -> list[dict[str, Any]]:
    if not queue or not task_name:
        return []

    records: list[dict[str, Any]] = []

    def append_record(item: Any, section: str) -> None:
        if not isinstance(item, dict) or item.get("name") != task_name:
            return
        result = dict(item)
        result["queue_section"] = section
        records.append(result)

    append_record(queue.get("running"), "running")
    for item in queue.get("pending") or []:
        append_record(item, "pending")
    for item in queue.get("recent") or []:
        append_record(item, "recent")

    return records[: max(1, int(limit or 500))]


def _format_epoch(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return str(value)
    return dt.datetime.fromtimestamp(timestamp).replace(microsecond=0).isoformat(sep=" ")


def _format_record_time(record: dict[str, Any]) -> str:
    return (
        _format_epoch(record.get("finished_at"))
        or _format_epoch(record.get("started_at"))
        or _format_epoch(record.get("queued_at"))
        or "-"
    )


def _format_record_duration(record: dict[str, Any]) -> str:
    try:
        started_at = float(record.get("started_at") or 0)
        finished_at = float(record.get("finished_at") or 0)
    except (TypeError, ValueError):
        return ""
    if started_at <= 0 or finished_at <= 0 or finished_at < started_at:
        return ""
    seconds = max(0, int(round(finished_at - started_at)))
    if seconds < 60:
        return f"{seconds}秒"
    minutes, remain = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}分{remain}秒" if remain else f"{minutes}分"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}小时{minutes}分" if minutes else f"{hours}小时"


def _runtime_status_label(status: Any) -> str:
    value = str(status or "").lower()
    return {
        "pending": "等待",
        "running": "运行中",
        "completed": "完成",
        "failed": "失败",
        "cancelled": "取消",
    }.get(value, str(status or "未知"))


def _format_runtime_record_line(record: dict[str, Any]) -> str:
    status_label = _runtime_status_label(record.get("status"))
    duration = _format_record_duration(record)
    suffix = f" · {duration}" if duration else ""
    return f"{_format_record_time(record)} · {status_label}{suffix}"


def _build_builtin_runtime_log_lines(item: dict[str, Any], records: list[dict[str, Any]]) -> list[str]:
    lines = [
        f"名称：{item.get('title') or item.get('key')}",
        f"状态：{'运行中' if item.get('active') else '停止'}",
    ]
    schedule_label = item.get("schedule_label") or item.get("schedule")
    if schedule_label:
        lines.append(f"调度：{schedule_label}")
    next_run_at = item.get("next_run_at") or (item.get("status") or {}).get("next_run_at")
    if next_run_at:
        lines.append(f"下次触发：{next_run_at}")

    latest_run = (item.get("status") or {}).get("latest_run")
    if isinstance(latest_run, dict):
        lines.append("")
        lines.append("最近运行：")
        stage = latest_run.get("stage_label") or latest_run.get("stage")
        if stage:
            lines.append(f"- 阶段：{stage}")
        status = latest_run.get("status")
        if status:
            lines.append(f"- 状态：{_runtime_status_label(status)}")
        for label, key in (
            ("创建", "created_at"),
            ("开始", "started_at"),
            ("更新", "updated_at"),
            ("结束", "finished_at"),
        ):
            formatted = _format_epoch(latest_run.get(key))
            if formatted:
                lines.append(f"- {label}：{formatted}")
        for label, key in (
            ("来源会话", "source_thread_count"),
            ("来源消息", "source_turn_count"),
            ("生成笔记", "created_note_count"),
            ("数据旧文件", "old_data_file_count"),
            ("源码旧文件", "old_source_file_count"),
        ):
            value = latest_run.get(key)
            if value not in (None, ""):
                lines.append(f"- {label}：{value}")
        report_path = latest_run.get("report_path")
        if report_path:
            lines.append(f"- 报告：{report_path}")
        error_message = latest_run.get("error_message")
        if error_message:
            lines.append(f"- 错误：{error_message}")
        result_text = str(latest_run.get("result_text") or "").strip()
        if result_text:
            lines.append("")
            lines.append("结果摘要：")
            summary_lines = [line.rstrip() for line in result_text.splitlines() if line.strip()]
            for line in summary_lines[:80]:
                lines.append(line)
            if len(summary_lines) > 80:
                lines.append(f"... 还有 {len(summary_lines) - 80} 行，查看报告文件获取完整内容")

    lines.append("")
    lines.append("队列记录：")
    if records:
        for record in records:
            lines.append(f"- {_format_runtime_record_line(record)}")
            error_message = record.get("error_message")
            if error_message:
                lines.append(f"  {error_message}")
    else:
        lines.append("- 暂无队列记录")
    return lines


def _seconds_label(seconds: Any) -> str:
    try:
        value = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    if value < 60:
        return f"{value}秒"
    minutes, remain = divmod(value, 60)
    if minutes < 60:
        return f"{minutes}分{remain}秒" if remain else f"{minutes}分"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}小时{minutes}分" if minutes else f"{hours}小时"


def _ocr_state_label(state: str) -> str:
    return {
        "running": "运行中",
        "idle": "已加载",
        "cold": "冷启动",
        "starting": "启动中",
        "stopped": "已停止",
        "unreachable": "不可达",
    }.get(state, state or "未知")


def _serialize_ocr_service_item(status: dict[str, Any]) -> dict[str, Any]:
    state = str(status.get("state") or "cold")
    running = bool(status.get("running"))
    loaded = bool(status.get("loaded"))
    idle_timeout = _seconds_label(status.get("idle_timeout_seconds"))
    description_parts = [
        str(status.get("url") or ""),
        "独立进程",
        f"空闲{idle_timeout}释放" if idle_timeout else "",
    ]
    description = " · ".join(part for part in description_parts if part)
    return {
        "id": f"builtin:{BUILTIN_OCR_SERVICE_KEY}",
        "key": BUILTIN_OCR_SERVICE_KEY,
        "kind": "service",
        "source": "builtin",
        "group_id": "service:default",
        "group_title": "默认服务",
        "title": status.get("title") or "OCR",
        "description": description,
        "command": "",
        "cwd": "",
        "schedule": "",
        "schedule_policy": None,
        "schedule_label": "",
        "next_run_at": None,
        "timeout": None,
        "order": 0,
        "enabled": True,
        "active": running,
        "status": {
            "running": running,
            "state": state,
            "state_label": status.get("state_label") or _ocr_state_label(state),
            "loaded": loaded,
            "instance_count": status.get("instance_count"),
            "idle_instance_count": status.get("idle_instance_count"),
            "active_instance_count": status.get("active_instance_count"),
            "idle_timeout_seconds": status.get("idle_timeout_seconds"),
            "idle_remaining_seconds": status.get("idle_remaining_seconds"),
            "acquire_timeout_seconds": status.get("acquire_timeout_seconds"),
            "call_count": status.get("call_count"),
            "error_count": status.get("error_count"),
            "last_loaded_at": status.get("last_loaded_at"),
            "last_used_at": status.get("last_used_at"),
            "last_error": status.get("last_error"),
            "url": status.get("url"),
            "host": status.get("host"),
            "port": status.get("port"),
            "log_path": status.get("log_path"),
            "process_count": status.get("process_count") or 0,
            "pids": status.get("pids") or [],
        },
        "actions": ["trigger", "stop", "logs", "configure"],
        "raw": status,
        "schedule_kind": "manual",
        "timeout_policy": "none",
        "timeout_seconds": None,
        "concurrency_scope": "unit",
        "concurrency_key": BUILTIN_OCR_SERVICE_KEY,
        "overlap_policy": "queue",
        "queue_key": None,
    }


def _serialize_codeyun_watchdog_service_item(status: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(status or get_codeyun_watchdog_status())
    running = bool(payload.get("running"))
    state = str(payload.get("state") or ("running" if running else "stopped"))
    interval = payload.get("interval_seconds") or 60
    description = " · ".join(
        part
        for part in (
            f"每 {interval} 秒巡检",
            "独立进程",
            "异常时重启 dev.py",
            "开机自启" if (payload.get("startup") or {}).get("enabled") else "",
        )
        if part
    )
    return {
        "id": f"builtin:{CODEYUN_WATCHDOG_SERVICE_KEY}",
        "key": CODEYUN_WATCHDOG_SERVICE_KEY,
        "kind": "service",
        "source": "builtin",
        "group_id": "service:default",
        "group_title": "默认服务",
        "title": payload.get("title") or "CodeYun 本机守护",
        "description": description,
        "command": payload.get("script_path") or "",
        "cwd": payload.get("cwd") or "",
        "schedule": "",
        "schedule_policy": None,
        "schedule_label": "",
        "next_run_at": None,
        "timeout": None,
        "order": 1,
        "enabled": True,
        "active": running,
        "status": {
            "running": running,
            "state": state,
            "state_label": payload.get("state_label") or state,
            "interval_seconds": interval,
            "backend_url": payload.get("backend_url"),
            "frontend_url": payload.get("frontend_url"),
            "script_path": payload.get("script_path"),
            "cwd": payload.get("cwd"),
            "log_path": payload.get("log_path"),
            "process_count": payload.get("process_count") or 0,
            "pids": payload.get("pids") or [],
            "last_error": payload.get("last_error") or "",
            "startup": payload.get("startup") or {},
            "controllable": True,
        },
        "actions": ["trigger", "stop", "logs", "configure"],
        "raw": payload,
        "schedule_kind": "manual",
        "timeout_policy": "none",
        "timeout_seconds": None,
        "concurrency_scope": "unit",
        "concurrency_key": CODEYUN_WATCHDOG_SERVICE_KEY,
        "overlap_policy": "replace",
        "queue_key": None,
    }


def _serialize_proxy_traffic_audit_service_item(status: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(status or get_proxy_traffic_audit_status(include_summary=False))
    running = bool(payload.get("running"))
    state = str(payload.get("state") or ("running" if running else "stopped"))
    interval = payload.get("interval_seconds") or 2
    description = " · ".join(
        part
        for part in (
            f"每 {interval} 秒采样",
            "只统计非 DIRECT",
            "SQLite 落库",
        )
        if part
    )
    return {
        "id": f"builtin:{PROXY_TRAFFIC_AUDIT_SERVICE_KEY}",
        "key": PROXY_TRAFFIC_AUDIT_SERVICE_KEY,
        "kind": "service",
        "source": "builtin",
        "group_id": "service:default",
        "group_title": "默认服务",
        "title": payload.get("title") or "代理流量审计",
        "description": description,
        "command": payload.get("module") or "",
        "cwd": payload.get("cwd") or "",
        "schedule": "",
        "schedule_policy": None,
        "schedule_label": "",
        "next_run_at": None,
        "timeout": None,
        "order": 2,
        "enabled": True,
        "active": running,
        "status": {
            "running": running,
            "state": state,
            "state_label": payload.get("state_label") or state,
            "interval_seconds": interval,
            "db_path": payload.get("db_path"),
            "log_path": payload.get("log_path"),
            "last_sample_at": payload.get("last_sample_at") or "",
            "last_sample_summary": payload.get("last_sample_summary") or "",
            "top_hosts": payload.get("top_hosts") or [],
            "process_count": payload.get("process_count") or 0,
            "pids": payload.get("pids") or [],
            "controllable": True,
        },
        "actions": ["trigger", "stop", "logs", "configure"],
        "raw": payload,
        "schedule_kind": "manual",
        "timeout_policy": "none",
        "timeout_seconds": None,
        "concurrency_scope": "unit",
        "concurrency_key": PROXY_TRAFFIC_AUDIT_SERVICE_KEY,
        "overlap_policy": "replace",
        "queue_key": None,
    }


def _behavior_tree_service_description(status: dict[str, Any]) -> str:
    parts = [str(status.get("state_label") or "")]
    if status.get("pid"):
        parts.append(f"PID {status.get('pid')}")
    process_count = int(status.get("process_count") or 0)
    if process_count > 1:
        parts.append(f"{process_count} 个行为树")
    child_process_count = int(status.get("child_process_count") or 0)
    if child_process_count:
        parts.append(f"子进程 {child_process_count}")
    next_run_at = status.get("next_run_at")
    if next_run_at:
        parts.append(f"下次 {next_run_at}")
    heartbeat_age = status.get("heartbeat_age_seconds")
    if isinstance(heartbeat_age, int):
        parts.append(f"心跳 {heartbeat_age}s 前")
    return " · ".join(part for part in parts if part)


def _serialize_attendance_behavior_tree_service_item(status: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(status or get_attendance_behavior_tree_status())
    running = bool(payload.get("running"))
    state = str(payload.get("state") or ("running" if running else "stopped"))
    next_run_at = payload.get("next_run_at")
    return {
        "id": f"builtin:{ATTENDANCE_BEHAVIOR_TREE_SERVICE_KEY}",
        "key": ATTENDANCE_BEHAVIOR_TREE_SERVICE_KEY,
        "kind": "service",
        "source": "builtin",
        "group_id": "service:attendance",
        "group_title": "考勤服务",
        "title": payload.get("title") or "考勤行为树",
        "description": _behavior_tree_service_description(payload),
        "command": payload.get("script_path") or "",
        "cwd": payload.get("cwd") or payload.get("root") or "",
        "schedule": "",
        "schedule_policy": None,
        "schedule_label": "",
        "next_run_at": next_run_at,
        "timeout": None,
        "order": 0,
        "enabled": True,
        "active": running,
        "status": {
            "running": running,
            "state": state,
            "state_label": payload.get("state_label") or state,
            "pid": payload.get("pid"),
            "process_count": payload.get("process_count") or 0,
            "child_process_count": payload.get("child_process_count") or 0,
            "total_process_count": payload.get("total_process_count") or payload.get("process_count") or 0,
            "started_at": payload.get("started_at"),
            "next_run_at": next_run_at,
            "last_error": payload.get("last_error") or "",
            "controllable": True,
        },
        "actions": ["trigger", "stop", "logs", "configure"],
        "raw": payload,
        "schedule_kind": "manual",
        "timeout_policy": "none",
        "timeout_seconds": None,
        "concurrency_scope": "unit",
        "concurrency_key": ATTENDANCE_BEHAVIOR_TREE_SERVICE_KEY,
        "overlap_policy": "replace",
        "queue_key": None,
    }


def _read_json_file(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _data_annotation_runtime_dir() -> Path:
    return fanxiu_data_annotation_runtime_dir()


def _get_data_annotation_behavior_tree_status() -> dict[str, Any]:
    runtime_dir = _data_annotation_runtime_dir()
    live_status: dict[str, Any] = {}
    try:
        live_status = fanxiu_data_annotation_runtime_status()
    except Exception as exc:
        live_status = {"last_error": str(exc)}
    runtime_state = live_status or _read_json_file(fanxiu_data_annotation_runtime_state_path(), {})
    world_facts = _read_json_file(fanxiu_data_annotation_world_facts_path(), {})
    if not isinstance(runtime_state, dict):
        runtime_state = {}
    if not isinstance(world_facts, dict):
        world_facts = {}
    facts_runtime = world_facts.get("runtime") if isinstance(world_facts.get("runtime"), dict) else {}
    facts_guard = world_facts.get("guard") if isinstance(world_facts.get("guard"), dict) else {}

    current_scene = runtime_state.get("current_scene", facts_runtime.get("current_scene"))
    current_task = runtime_state.get("current_task") or facts_runtime.get("current_task") or ""
    phase = runtime_state.get("phase") or facts_runtime.get("phase") or ""
    message = runtime_state.get("message") or facts_runtime.get("message") or ""
    entry_id = runtime_state.get("entry_id") or facts_runtime.get("entry_id") or ""
    guard_entry_id = runtime_state.get("guard_entry_id") or facts_guard.get("entry_id") or ""
    guard_enabled = bool(facts_guard.get("enabled"))
    guard_running = bool(facts_guard.get("running"))
    service_running = bool(runtime_state.get("service_running") or facts_runtime.get("service_running"))
    task_running = bool(runtime_state.get("running") or facts_runtime.get("running"))
    running = service_running
    raw_status = str(runtime_state.get("status") or facts_runtime.get("status") or "")
    if raw_status == "error":
        state = "error"
        state_label = "错误"
    elif task_running:
        state = "running"
        state_label = "运行中"
    elif service_running:
        state = "idle"
        state_label = "常驻"
    elif guard_enabled or guard_running:
        state = "pending"
        state_label = "待恢复"
    else:
        state = "idle"
        state_label = "空闲"
    return {
        "key": FANXIU_BEHAVIOR_TREE_SERVICE_KEY,
        "title": "凡修行为树",
        "running": running,
        "state": state,
        "state_label": state_label,
        "current_scene": current_scene,
        "entry_id": entry_id,
        "guard_entry_id": guard_entry_id,
        "current_task": current_task,
        "phase": phase,
        "message": message,
        "guard_enabled": guard_enabled,
        "guard_running": guard_running,
        "service_running": service_running,
        "task_running": task_running,
        "updated_at": runtime_state.get("updated_at") or facts_runtime.get("updated_at") or world_facts.get("updated_at"),
        "runtime_state_path": os.fspath(fanxiu_data_annotation_runtime_state_path()),
        "world_facts_path": os.fspath(fanxiu_data_annotation_world_facts_path()),
        "route_path": "/fanxiu/data-annotation/runtime",
        "logs": runtime_state.get("logs") if isinstance(runtime_state.get("logs"), list) else [],
    }


def _resolve_data_annotation_runtime_entry(session: Session) -> UserDevice:
    status = _get_data_annotation_behavior_tree_status()
    entry_candidates = [
        status.get("entry_id"),
        status.get("guard_entry_id"),
    ]
    runtime_state = _read_json_file(fanxiu_data_annotation_runtime_state_path(), {})
    if isinstance(runtime_state, dict):
        entry_candidates.extend([
            runtime_state.get("entry_id"),
            runtime_state.get("guard_entry_id"),
        ])
    world_facts = _read_json_file(fanxiu_data_annotation_world_facts_path(), {})
    if isinstance(world_facts, dict):
        runtime = world_facts.get("runtime") if isinstance(world_facts.get("runtime"), dict) else {}
        guard = world_facts.get("guard") if isinstance(world_facts.get("guard"), dict) else {}
        entry_candidates.extend([runtime.get("entry_id"), guard.get("entry_id")])

    for entry_id in entry_candidates:
        if not entry_id:
            continue
        entry = session.get(UserDevice, str(entry_id))
        if entry is not None and entry.is_active:
            return entry

    stmt = (
        select(UserDevice)
        .where(UserDevice.is_active == True)  # noqa: E712
        .where(UserDevice.mode == "local")
        .order_by(UserDevice.order_index, UserDevice.created_at)
    )
    entry = session.exec(stmt).first()
    if entry is not None:
        return entry
    raise HTTPException(status_code=404, detail="未找到可用于凡修行为树的本地设备入口")


def ensure_data_annotation_behavior_tree_service(session: Session) -> dict[str, Any]:
    entry = _resolve_data_annotation_runtime_entry(session)
    ensure_fanxiu_behavior_tree_service(entry=entry, entry_id=entry.entry_id)
    result: dict[str, Any] = {"status": "started", "service": _get_data_annotation_behavior_tree_status()}
    try:
        result["capture_runtime"] = ensure_fanxiu_capture_runtime_backstop(
            FANXIU_CAPTURE_RUNTIME_BEHAVIOR_TREE_REASON,
        )
    except Exception as exc:
        result["capture_runtime"] = {"ok": False, "ensured": False, "error": str(exc)}
    if _fanxiu_doctor_watch_autostart_enabled():
        try:
            result["doctor_watch"] = ensure_doctor_watch_background()
        except Exception as exc:
            result["doctor_watch"] = {"ok": False, "started": False, "error": str(exc)}
    return result


def ensure_data_annotation_behavior_tree_service_on_startup() -> dict[str, Any] | None:
    if not _fanxiu_behavior_tree_service_enabled():
        return None
    with Session(engine) as session:
        return ensure_data_annotation_behavior_tree_service(session)


def _fanxiu_doctor_watch_autostart_enabled() -> bool:
    configured = _env_enabled(os.getenv("FANXIU_DOCTOR_WATCH_AUTOSTART"))
    return True if configured is None else configured


def _local_builtin_service_autostart_enabled(env_name: str) -> bool:
    configured = _env_enabled(os.getenv(env_name))
    return True if configured is None else configured


def ensure_local_builtin_services_on_startup() -> dict[str, Any]:
    results: dict[str, Any] = {}
    if _local_builtin_service_autostart_enabled("CODEYUN_WATCHDOG_AUTOSTART"):
        try:
            results[CODEYUN_WATCHDOG_SERVICE_KEY] = start_codeyun_watchdog()
        except CodeYunWatchdogError as exc:
            results[CODEYUN_WATCHDOG_SERVICE_KEY] = {"status": "error", "error": str(exc)}

    if _local_builtin_service_autostart_enabled("CODEYUN_PROXY_TRAFFIC_AUDIT_AUTOSTART"):
        try:
            results[PROXY_TRAFFIC_AUDIT_SERVICE_KEY] = start_proxy_traffic_audit()
        except ProxyTrafficAuditError as exc:
            results[PROXY_TRAFFIC_AUDIT_SERVICE_KEY] = {"status": "error", "error": str(exc)}

    if _local_builtin_service_autostart_enabled("CODEYUN_CRITICAL_COMMAND_SERVICES_AUTOSTART"):
        results["critical-command-services"] = ensure_local_critical_command_services()

    return results


def ensure_local_critical_command_services() -> dict[str, Any]:
    """Keep local always-on command services alive.

    These are user-configured command rows, not built-in services.  We guard only
    the small network/sync allowlist because other command services may be
    intentionally stopped.
    """

    local_device_id = get_device_id()
    started: list[dict[str, Any]] = []
    already_running: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    task_manager.scan_running_tasks()
    with Session(engine) as session:
        stmt = (
            select(TaskModel)
            .where(TaskModel.device_id == local_device_id)
            .order_by(TaskModel.order, TaskModel.created_at)
        )
        tasks = [
            task
            for task in session.exec(stmt).all()
            if str(task.name or "").strip().lower() in CRITICAL_LOCAL_COMMAND_SERVICE_NAMES
            and infer_command_runtime_kind(task) == "service"
            and not is_legacy_codeyun_command_task(task)
        ]

    for task in tasks:
        status = task_manager.get_task_status(task.id)
        item = {"id": task.id, "name": task.name, "pid": status.pid}
        if status.running:
            already_running.append(item)
            continue

        executable_match = _find_running_process_by_command_executable(task.command)
        if executable_match:
            device = device_manager.get_device(local_device_id)
            if device is not None:
                try:
                    device.associate_process(task.id, int(executable_match["pid"]))
                except Exception:
                    pass
            already_running.append({**item, **executable_match})
            continue

        try:
            result = task_manager.start_task(
                task.id,
                replace_running=False,
                trigger_reason="critical_command_service_guard",
            )
            started.append({"id": task.id, "name": task.name, "result": result})
        except Exception as exc:
            errors.append({"id": task.id, "name": task.name, "error": str(exc)})

    return {
        "status": "error" if errors else ("started" if started else "ok"),
        "started": started,
        "already_running": already_running,
        "errors": errors,
    }


def _find_running_process_by_command_executable(command: str) -> dict[str, Any] | None:
    try:
        args = process_runtime.parse_cmdline(command)
    except Exception:
        args = []
    if not args:
        return None

    executable = Path(str(args[0]).strip('"')).expanduser()
    if not executable.is_absolute():
        return None

    executable_key = os.path.normcase(os.path.abspath(os.fspath(executable)))
    for proc in process_runtime.process_candidates_by_name({executable.name}):
        try:
            proc_exe = proc.exe()
        except Exception:
            continue
        if os.path.normcase(os.path.abspath(proc_exe)) == executable_key:
            return {"pid": proc.pid, "matched_by": "executable", "exe": proc_exe}
    return None


def start_behavior_tree_service(*, replace_existing: bool = True) -> dict[str, Any]:
    del replace_existing
    with Session(engine) as session:
        return ensure_data_annotation_behavior_tree_service(session)


def stop_data_annotation_behavior_tree_current_task(session: Session) -> dict[str, Any]:
    entry = _resolve_data_annotation_runtime_entry(session)
    stop_fanxiu_behavior_tree_current_task(entry.entry_id)
    return {"status": "stopped", "service": _get_data_annotation_behavior_tree_status()}


def _fanxiu_behavior_tree_description(status: dict[str, Any]) -> str:
    parts = [str(status.get("state_label") or "")]
    current_scene = status.get("current_scene")
    if current_scene is not None:
        parts.append(f"场景 #{current_scene}")
    if status.get("guard_enabled"):
        parts.append("守护开启")
    if status.get("current_task"):
        parts.append(str(status.get("current_task")))
    if status.get("phase"):
        parts.append(str(status.get("phase")))
    if status.get("message"):
        parts.append(str(status.get("message")))
    return " · ".join(part for part in parts if part)


def _serialize_fanxiu_behavior_tree_service_item(status: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(status or _get_data_annotation_behavior_tree_status())
    running = bool(payload.get("running"))
    state = str(payload.get("state") or ("running" if running else "idle"))
    return {
        "id": f"builtin:{FANXIU_BEHAVIOR_TREE_SERVICE_KEY}",
        "key": FANXIU_BEHAVIOR_TREE_SERVICE_KEY,
        "kind": "service",
        "source": "builtin",
        "group_id": "service:game",
        "group_title": "游戏服务",
        "title": "凡修行为树",
        "description": _fanxiu_behavior_tree_description(payload),
        "command": "CodeYun backend /fanxiu/data-annotation/runtime",
        "cwd": "",
        "schedule": "",
        "schedule_policy": None,
        "schedule_label": "",
        "next_run_at": None,
        "timeout": None,
        "order": 0,
        "enabled": True,
        "active": running,
        "status": {
            "running": running,
            "state": state,
            "state_label": payload.get("state_label") or state,
            "current_scene": payload.get("current_scene"),
            "current_task": payload.get("current_task") or "",
            "phase": payload.get("phase") or "",
            "guard_enabled": bool(payload.get("guard_enabled")),
            "guard_running": bool(payload.get("guard_running")),
            "service_running": bool(payload.get("service_running")),
            "task_running": bool(payload.get("task_running")),
            "updated_at": payload.get("updated_at"),
            "runtime_state_path": payload.get("runtime_state_path") or "",
            "world_facts_path": payload.get("world_facts_path") or "",
            "route_path": payload.get("route_path") or "",
            "last_error": payload.get("last_error") or "",
            "controllable": True,
        },
        "actions": ["trigger", "stop", "logs", "configure"],
        "raw": payload,
        "schedule_kind": "manual",
        "timeout_policy": "none",
        "timeout_seconds": None,
        "concurrency_scope": "unit",
        "concurrency_key": FANXIU_BEHAVIOR_TREE_SERVICE_KEY,
        "overlap_policy": "replace",
        "queue_key": None,
    }


def _fanxiu_capture_runtime_description(status: dict[str, Any]) -> str:
    parts = [str(status.get("state") or "stopped")]
    reasons = status.get("active_reasons") or []
    if reasons:
        parts.append(f"consumer {', '.join(str(item) for item in reasons)}")
    if status.get("current_pcap_path"):
        parts.append(Path(str(status.get("current_pcap_path"))).name)
    if status.get("watchdog_running"):
        interval = status.get("watchdog_interval_seconds") or 0
        parts.append(f"守护巡检 {interval:g}s")
    if status.get("last_error"):
        parts.append(f"错误 {status.get('last_error')}")
    return " · ".join(part for part in parts if part)


def _serialize_fanxiu_capture_runtime_service_item(status: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(status or fanxiu_capture_runtime_service.status())
    running = bool(payload.get("running"))
    state = str(payload.get("state") or ("running" if running else "stopped"))
    reasons = payload.get("active_reasons") or []
    active = running or bool(reasons)
    state_label = {
        "stopped": "已停止",
        "waiting_game": "等待游戏",
        "recovering": "恢复中",
        "running": "采集中",
    }.get(state, state)
    return {
        "id": f"builtin:{FANXIU_CAPTURE_RUNTIME_SERVICE_KEY}",
        "key": FANXIU_CAPTURE_RUNTIME_SERVICE_KEY,
        "kind": "service",
        "source": "builtin",
        "group_id": "service:game",
        "group_title": "游戏服务",
        "title": "凡修抓包",
        "description": _fanxiu_capture_runtime_description(payload),
        "command": "adb tcpdump -> fanxiu/tcp-flow/live-captures",
        "cwd": "",
        "schedule": "",
        "schedule_policy": None,
        "schedule_label": "",
        "next_run_at": None,
        "timeout": None,
        "order": 0,
        "enabled": True,
        "active": active,
        "status": {
            "running": running,
            "state": state,
            "state_label": state_label,
            "game_running": bool(payload.get("game_running")),
            "adb_connected": bool(payload.get("adb_connected")),
            "root_ready": bool(payload.get("root_ready")),
            "tcpdump_ready": bool(payload.get("tcpdump_ready")),
            "active_reasons": reasons,
            "current_pcap_path": payload.get("current_pcap_path") or "",
            "current_pcap_size": payload.get("current_pcap_size") or 0,
            "started_at": payload.get("started_at") or "",
            "last_error": payload.get("last_error") or "",
            "last_recover_at": payload.get("last_recover_at") or "",
            "watchdog_running": bool(payload.get("watchdog_running")),
            "watchdog_interval_seconds": payload.get("watchdog_interval_seconds") or 0,
            "watchdog_last_check_at": payload.get("watchdog_last_check_at") or "",
            "watchdog_last_action": payload.get("watchdog_last_action") or "",
            "watchdog_last_error": payload.get("watchdog_last_error") or "",
            "controllable": True,
        },
        "actions": ["trigger", "stop", "logs"],
        "raw": payload,
        "schedule_kind": "manual",
        "timeout_policy": "none",
        "timeout_seconds": None,
        "concurrency_scope": "unit",
        "concurrency_key": FANXIU_CAPTURE_RUNTIME_SERVICE_KEY,
        "overlap_policy": "replace",
        "queue_key": None,
    }


def _game_window_service_description(status: dict[str, Any]) -> str:
    parts = [str(status.get("url") or "")]
    target_title = str(status.get("target_title") or "")
    if target_title:
        parts.append(f"窗口 {target_title}")
    if status.get("running"):
        pids = ", ".join(str(pid) for pid in status.get("pids") or [])
        parts.append(f"PID {pids}" if pids else "端口已连接")
    elif status.get("process_count"):
        parts.append("进程不可达")
    else:
        parts.append("独立进程未启动")
    return " · ".join(part for part in parts if part)


def _serialize_game_window_service_item(status: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(status or get_game_window_service_status())
    running = bool(payload.get("running"))
    state = str(payload.get("state") or ("running" if running else "stopped"))
    state_label = str(payload.get("state_label") or ("运行中" if running else "已停止"))
    return {
        "id": f"builtin:{GAME_WINDOW_SERVICE_KEY}",
        "key": GAME_WINDOW_SERVICE_KEY,
        "kind": "service",
        "source": "builtin",
        "group_id": "service:game",
        "group_title": "游戏服务",
        "title": "凡修画面流",
        "description": _game_window_service_description(payload),
        "command": "python -m backend.services.game_window_daemon",
        "cwd": "",
        "schedule": "",
        "schedule_policy": None,
        "schedule_label": "",
        "next_run_at": None,
        "timeout": None,
        "order": 0,
        "enabled": True,
        "active": running,
        "status": {
            "running": running,
            "state": state,
            "state_label": state_label,
            "target_title": payload.get("target_title") or "",
            "url": payload.get("url") or "",
            "host": payload.get("host") or "",
            "port": payload.get("port"),
            "process_count": payload.get("process_count") or 0,
            "pids": payload.get("pids") or [],
            "log_path": payload.get("log_path") or "",
            "last_error": payload.get("last_error") or "",
            "controllable": True,
        },
        "actions": ["trigger", "stop", "logs", "configure"],
        "raw": payload,
        "schedule_kind": "manual",
        "timeout_policy": "none",
        "timeout_seconds": None,
        "concurrency_scope": "unit",
        "concurrency_key": GAME_WINDOW_SERVICE_KEY,
        "overlap_policy": "replace",
        "queue_key": None,
    }


def _build_builtin_service_log_lines(item: dict[str, Any]) -> list[str]:
    if item.get("key") == ATTENDANCE_BEHAVIOR_TREE_SERVICE_KEY:
        return build_attendance_behavior_tree_log_lines()
    if item.get("key") == FANXIU_BEHAVIOR_TREE_SERVICE_KEY:
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        lines = [
            f"名称：{item.get('title') or item.get('key')}",
            f"状态：{(item.get('status') or {}).get('state_label') or '-'}",
            f"入口：{raw.get('route_path') or '/fanxiu/data-annotation/runtime'}",
        ]
        for entry in raw.get("logs") or []:
            if isinstance(entry, dict):
                lines.append(f"{entry.get('time') or ''} {entry.get('kind') or ''} {entry.get('message') or ''}".strip())
        return lines
    if item.get("key") == FANXIU_CAPTURE_RUNTIME_SERVICE_KEY:
        worker_status = fanxiu_packet_insight_worker.status()
        mail_sync = {}
        for decoded_item in worker_status.get("decoded") or []:
            if isinstance(decoded_item, dict) and isinstance(decoded_item.get("mail_packet_sync"), dict):
                mail_sync = decoded_item["mail_packet_sync"]
                break
        lines = [
            f"名称：{item.get('title') or item.get('key')}",
            f"状态：{(item.get('status') or {}).get('state_label') or '-'}",
            (
                "解析队列："
                f"{worker_status.get('updated_at') or '-'}；"
                f"本轮解码 {worker_status.get('decoded_count') or 0}；"
                f"跳过 {worker_status.get('skipped_count') or 0}；"
                f"错误 {worker_status.get('error_count') or 0}"
            ),
        ]
        if mail_sync:
            lines.append(
                "邮件落库："
                f"记录 {mail_sync.get('record_count') or 0}；"
                f"新增 {mail_sync.get('inserted') or 0}；"
                f"更新 {mail_sync.get('updated') or 0}；"
                f"源包 {mail_sync.get('source_packets') or 0}；"
                f"动作包 {mail_sync.get('action_packets') or 0}"
            )
        for error in worker_status.get("errors") or []:
            if isinstance(error, dict):
                lines.append(f"解析错误：{error.get('path') or '-'}；{error.get('error') or '-'}")
        lines.extend(fanxiu_capture_runtime_service.log_lines())
        return lines
    if item.get("key") == GAME_WINDOW_SERVICE_KEY:
        return _build_game_window_service_log_lines(item)
    if item.get("key") == CODEYUN_WATCHDOG_SERVICE_KEY:
        return build_codeyun_watchdog_log_lines()
    if item.get("key") == PROXY_TRAFFIC_AUDIT_SERVICE_KEY:
        return build_proxy_traffic_audit_log_lines()

    status = item.get("status") or {}
    raw = item.get("raw") or {}
    lines = [
        f"名称：{item.get('title') or item.get('key')}",
        f"状态：{status.get('state_label') or _ocr_state_label(str(status.get('state') or ''))}",
        f"地址：{status.get('url') or raw.get('url') or '-'}",
        f"引擎：{raw.get('engine') or 'paddleocr'}",
        f"运行设备：{raw.get('device') or '-'} · {raw.get('lang') or '-'}",
        f"实例：{status.get('instance_count') or 0}",
        f"识别中：{status.get('active_instance_count') or 0}",
    ]
    pids = status.get("pids") or []
    if pids:
        lines.append(f"PID：{', '.join(str(pid) for pid in pids)}")
    if status.get("log_path"):
        lines.append(f"日志：{status.get('log_path')}")
    idle_timeout = _seconds_label(status.get("idle_timeout_seconds"))
    if idle_timeout:
        lines.append(f"空闲释放：{idle_timeout}")
    acquire_timeout = _seconds_label(status.get("acquire_timeout_seconds"))
    if acquire_timeout:
        lines.append(f"排队等待：{acquire_timeout}")
    lines.extend([
        f"调用：{status.get('call_count') or 0}",
        f"错误：{status.get('error_count') or 0}",
    ])
    if status.get("last_loaded_at"):
        lines.append(f"最近加载：{_format_epoch(status.get('last_loaded_at'))}")
    if status.get("last_used_at"):
        lines.append(f"最近调用：{_format_epoch(status.get('last_used_at'))}")
    if status.get("last_error"):
        lines.extend(["", f"最近错误：{status.get('last_error')}"])
    return lines


def _build_game_window_service_log_lines(item: dict[str, Any]) -> list[str]:
    status = item.get("status") or {}
    raw = item.get("raw") or {}
    lines = [
        f"名称：{item.get('title') or item.get('key')}",
        f"状态：{status.get('state_label') or '-'}",
        f"地址：{status.get('url') or raw.get('url') or '-'}",
        f"窗口标题：{status.get('target_title') or raw.get('target_title') or '-'}",
    ]
    pids = status.get("pids") or []
    if pids:
        lines.append(f"PID：{', '.join(str(pid) for pid in pids)}")
    if status.get("log_path"):
        lines.append(f"日志：{status.get('log_path')}")
    if status.get("last_error"):
        lines.extend(["", f"最近错误：{status.get('last_error')}"])

    processes = raw.get("processes") if isinstance(raw, dict) else None
    if processes:
        lines.append("")
        lines.append("进程：")
        for process in processes[:20]:
            if not isinstance(process, dict):
                continue
            lines.append(
                f"- PID {process.get('pid')} · {process.get('name') or '-'} · "
                f"{process.get('cmdline') or '-'}"
            )

    lines.append("")
    lines.append("这个运行单元只负责守护 codepc_mf 本机的 MuMu/凡修窗口画面流；凡修 data-annotation 不再使用 mi15 旧云手机画面流。")
    return lines


def _serialize_command_runtime_item(task: TaskModel, queue: dict[str, Any] | None = None) -> dict[str, Any]:
    kind = "job" if _is_command_job(task) else "service"
    group_id, group_title = _command_group_for(task, kind)
    policy = resolve_command_runtime_policy(task)
    status = task_manager.get_task_status(task.id)
    status_payload = _model_dump(status)
    active = bool(status_payload.get("running"))
    next_run_at = _command_next_run_at(task)
    if next_run_at:
        status_payload["next_run_at"] = next_run_at
    queue_name = command_runtime_queue_name(task.id) if kind == "job" else ""
    queue_snapshot = _find_queue_snapshot(queue, queue_name)
    if queue_snapshot:
        status_payload["queued"] = True
        status_payload["queue_status"] = queue_snapshot.get("status")
        status_payload["queue_task_id"] = queue_snapshot.get("id")
    else:
        status_payload["queued"] = False

    return {
        "id": f"command:{task.id}",
        "key": task.id,
        "kind": kind,
        "source": "command",
        "group_id": group_id,
        "group_title": group_title,
        "title": _command_runtime_title(task),
        "description": task.description,
        "command": task.command,
        "cwd": task.cwd,
        "runtime_kind": kind,
        "schedule": task.schedule,
        "schedule_policy": task.schedule_policy,
        "schedule_state": task.schedule_state,
        "schedule_label": schedule_policy_label(task.schedule_policy) or task.schedule or "",
        "next_run_at": next_run_at,
        "timeout": task.timeout,
        "order": task.order or 0,
        "active": active,
        "status": status_payload,
        "actions": (
            ["trigger", "stop", "logs", "delete"]
            if kind == "job"
            else ["start", "stop", "logs", "delete", "reorder"]
        ),
        "raw": task.model_dump(),
        **runtime_policy_payload(policy),
    }


def _serialize_builtin_job_item(item: dict[str, Any]) -> dict[str, Any]:
    category = str(item.get("category") or "默认")
    group_id = f"job:{category}"
    policy = resolve_builtin_job_runtime_policy()
    return {
        "id": f"builtin:{item.get('key')}",
        "key": item.get("key"),
        "kind": "job",
        "source": "builtin",
        "group_id": group_id,
        "group_title": category,
        "title": item.get("title") or item.get("key"),
        "description": item.get("description") or "",
        "command": "",
        "cwd": "",
        "schedule": item.get("cron_expression") or "",
        "schedule_policy": item.get("schedule_policy"),
        "schedule_label": item.get("schedule_label") or "",
        "next_run_at": item.get("next_run_at"),
        "timeout": None,
        "order": 0,
        "enabled": bool(item.get("enabled")),
        "active": bool(item.get("active")),
        "status": {
            "running": bool(item.get("active")),
            "enabled": bool(item.get("enabled")),
            "runner_running": bool(item.get("runner_running")),
            "next_run_at": item.get("next_run_at"),
            "latest_run": item.get("latest_run"),
            "retry_policy": item.get("retry_policy"),
            "trigger_warning": item.get("trigger_warning"),
        },
        "actions": ["trigger", "toggle", "delete", "reset_schedule"],
        "raw": item,
        **runtime_policy_payload(policy),
    }


def _collect_builtin_jobs(session: Session) -> dict[str, Any]:
    from backend.api.admin import get_background_task_status

    status = get_background_task_status(session)
    payload = status.model_dump() if hasattr(status, "model_dump") else dict(status)
    items = [
        _serialize_builtin_job_item(item)
        for item in payload.get("tasks", [])
        if isinstance(item, dict)
    ]
    return {
        "items": items,
        "queue": payload.get("queue"),
        "runner_running": payload.get("runner_running"),
        "next_wake_at": payload.get("next_wake_at"),
        "runner_error": payload.get("runner_error"),
    }


def _collect_builtin_services() -> dict[str, Any]:
    items = [
        _serialize_ocr_service_item(get_ocr_service_status()),
        _serialize_codeyun_watchdog_service_item(),
        _serialize_proxy_traffic_audit_service_item(),
    ]
    if is_attendance_behavior_tree_service_enabled():
        items.append(_serialize_attendance_behavior_tree_service_item())
    if _fanxiu_behavior_tree_service_enabled():
        items.append(_serialize_fanxiu_behavior_tree_service_item())
    if _fanxiu_capture_runtime_service_enabled():
        items.append(_serialize_fanxiu_capture_runtime_service_item())
    if _fanxiu_game_window_service_enabled():
        items.append(_serialize_game_window_service_item())
    return {
        "items": items,
    }


def build_runtime_status(session: Session, device_id: str | None = None) -> dict[str, Any]:
    target_device_id = device_id or get_device_id()
    local_device_id = get_device_id()

    if target_device_id == local_device_id:
        task_manager.scan_running_tasks()

    stmt = (
        select(TaskModel)
        .where(TaskModel.device_id == target_device_id)
        .order_by(TaskModel.order, TaskModel.created_at)
    )
    builtin = _collect_builtin_jobs(session) if target_device_id == local_device_id else {
        "items": [],
        "queue": None,
        "runner_running": False,
        "next_wake_at": None,
        "runner_error": None,
    }
    builtin_services = _collect_builtin_services() if target_device_id == local_device_id else {"items": []}
    queue = builtin["queue"] if target_device_id == local_device_id else None
    command_items = [
        _serialize_command_runtime_item(task, queue=queue)
        for task in session.exec(stmt).all()
        if not (target_device_id == local_device_id and is_legacy_codeyun_command_task(task))
    ]

    items = command_items + builtin_services["items"] + builtin["items"]
    runtime_queue = _enrich_runtime_queue(builtin["queue"], items) if target_device_id == local_device_id else None
    group_by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        group_by_id[item["group_id"]] = _runtime_group(item["kind"], item["group_id"], item["group_title"])

    device = device_manager.get_device(target_device_id)
    return {
        "device_id": target_device_id,
        "device": device.to_dict() if device else {"id": target_device_id, "name": target_device_id},
        "groups": sorted(group_by_id.values(), key=lambda group: (group["kind"], group["title"])),
        "items": items,
        "queue": runtime_queue,
        "runner_running": builtin["runner_running"],
        "next_wake_at": builtin["next_wake_at"],
        "runner_error": builtin["runner_error"],
    }


def get_runtime_item_logs(
    source: str,
    item_key: str,
    session: Session | None,
    limit: int = 500,
    device_id: str | None = None,
) -> dict[str, Any]:
    normalized_source = str(source or "").strip()
    normalized_key = str(item_key or "").strip()
    if not normalized_key:
        raise HTTPException(status_code=404, detail="运行单元不存在")

    if normalized_source == "command":
        if session is None:
            with Session(engine) as owned_session:
                return get_runtime_item_logs(
                    normalized_source,
                    normalized_key,
                    owned_session,
                    limit,
                    device_id=device_id,
                )
        task = session.get(TaskModel, normalized_key)
        if task is None or (device_id and task.device_id != device_id):
            raise HTTPException(status_code=404, detail="运行单元不存在")
        queue = background_task_queue.snapshot()
        item = _serialize_command_runtime_item(task, queue=queue)
        queue_name = _runtime_queue_name_for_item(item)
        records = _queue_records_for_name(_enrich_runtime_queue(queue, [item]), queue_name, limit)
        return {
            "source": "command",
            "key": normalized_key,
            "kind": item.get("kind"),
            "title": item.get("title"),
            "description": item.get("description") or "",
            "command": item.get("command") or "",
            "cwd": item.get("cwd") or "",
            "schedule": item.get("schedule") or "",
            "schedule_label": item.get("schedule_label") or "",
            "next_run_at": item.get("next_run_at"),
            "timeout": item.get("timeout"),
            "status": item.get("status") or {},
            "records": records,
            "logs": task_manager.get_logs(normalized_key, limit),
        }

    if normalized_source == "builtin":
        if device_id and device_id != get_device_id():
            raise HTTPException(status_code=404, detail="运行单元不存在")
        builtin_services = _collect_builtin_services()
        service_item = next(
            (item for item in builtin_services.get("items", []) if item.get("key") == normalized_key),
            None,
        )
        if service_item is not None:
            return {
                "source": "builtin",
                "key": normalized_key,
                "kind": service_item.get("kind"),
                "title": service_item.get("title"),
                "description": service_item.get("description") or "",
                "command": service_item.get("command") or "",
                "cwd": service_item.get("cwd") or "",
                "schedule": service_item.get("schedule") or "",
                "schedule_label": service_item.get("schedule_label") or "",
                "next_run_at": service_item.get("next_run_at"),
                "timeout": service_item.get("timeout"),
                "status": service_item.get("status") or {},
                "records": [],
                "logs": _build_builtin_service_log_lines(service_item),
            }
        if session is None:
            with Session(engine) as owned_session:
                return get_runtime_item_logs(
                    normalized_source,
                    normalized_key,
                    owned_session,
                    limit,
                    device_id=device_id,
                )
        builtin = _collect_builtin_jobs(session)
        items = (builtin_services.get("items") or []) + (builtin.get("items") or [])
        item = next((item for item in items if item.get("key") == normalized_key), None)
        if item is None:
            raise HTTPException(status_code=404, detail="运行单元不存在")
        queue = _enrich_runtime_queue(builtin.get("queue"), [item])
        records = _queue_records_for_name(queue, normalized_key, limit)
        return {
            "source": "builtin",
            "key": normalized_key,
            "kind": item.get("kind"),
            "title": item.get("title"),
            "description": item.get("description") or "",
            "command": item.get("command") or "",
            "cwd": item.get("cwd") or "",
            "schedule": item.get("schedule") or "",
            "schedule_label": item.get("schedule_label") or "",
            "next_run_at": item.get("next_run_at"),
            "timeout": item.get("timeout"),
            "status": item.get("status") or {},
            "records": records,
            "logs": (
                _build_builtin_service_log_lines(item)
                if item.get("kind") == "service"
                else build_public_frontend_deploy_log_lines()
                if normalized_key == PUBLIC_FRONTEND_DEPLOY_TASK_KEY
                else _build_builtin_runtime_log_lines(item, records)
            ),
        }

    raise HTTPException(status_code=400, detail="不支持的运行单元来源")


def trigger_builtin_runtime_job(task_key: str, session: Session) -> dict[str, Any]:
    from backend.api.admin import trigger_background_task

    result = trigger_background_task(task_key, session=session)
    return result.model_dump() if hasattr(result, "model_dump") else dict(result)


def list_builtin_runtime_job_catalog(session: Session) -> dict[str, Any]:
    from backend.api.admin import get_background_task_catalog

    result = get_background_task_catalog(session=session)
    return result.model_dump() if hasattr(result, "model_dump") else dict(result)


def add_builtin_runtime_job(task_key: str) -> dict[str, Any]:
    from backend.api.admin import add_background_task

    return add_background_task(task_key)


def trigger_builtin_runtime_item(task_key: str, session: Session) -> dict[str, Any]:
    normalized_key = str(task_key or "").strip()
    if normalized_key == BUILTIN_OCR_SERVICE_KEY:
        try:
            return start_ocr_service(replace_existing=False)
        except OcrPreviewError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    if normalized_key == CODEYUN_WATCHDOG_SERVICE_KEY:
        try:
            return start_codeyun_watchdog()
        except CodeYunWatchdogError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    if normalized_key == PROXY_TRAFFIC_AUDIT_SERVICE_KEY:
        try:
            return start_proxy_traffic_audit()
        except ProxyTrafficAuditError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    if normalized_key == GAME_WINDOW_SERVICE_KEY:
        if not _fanxiu_game_window_service_enabled():
            raise HTTPException(status_code=404, detail="凡修画面流未在当前机器启用")
        try:
            return start_game_window_service(replace_existing=False)
        except GameWindowServiceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    if normalized_key == FANXIU_CAPTURE_RUNTIME_SERVICE_KEY:
        if not _fanxiu_capture_runtime_service_enabled():
            raise HTTPException(status_code=404, detail="凡修抓包未在当前机器启用")
        return fanxiu_capture_runtime_service.ensure_running("runtime-manual")
    if normalized_key == ATTENDANCE_BEHAVIOR_TREE_SERVICE_KEY:
        if not is_attendance_behavior_tree_service_enabled():
            raise HTTPException(status_code=404, detail="考勤行为树只在 mi15 执行主机上管理")
        try:
            return start_attendance_behavior_tree_service(replace_existing=True)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    if normalized_key == FANXIU_BEHAVIOR_TREE_SERVICE_KEY:
        if not _fanxiu_behavior_tree_service_enabled():
            raise HTTPException(status_code=404, detail="凡修行为树只在 mi15 执行主机上管理")
        return ensure_data_annotation_behavior_tree_service(session)
    return trigger_builtin_runtime_job(normalized_key, session)


def trigger_command_runtime_item(task_key: str, session: Session) -> dict[str, Any]:
    task = session.get(TaskModel, task_key)
    if task is None:
        raise HTTPException(status_code=404, detail="运行单元不存在")
    if task.device_id == get_device_id() and is_legacy_codeyun_command_task(task):
        return trigger_builtin_runtime_item(CODEYUN_WATCHDOG_SERVICE_KEY, session)
    policy = resolve_command_runtime_policy(task)
    if policy.kind == "job":
        return task_manager.enqueue_task_run(task_key, trigger_reason="manual_runtime")
    return task_manager.start_task(
        task_key,
        replace_running=policy.overlap_policy == "replace",
        trigger_reason="manual_runtime",
    )


def stop_command_runtime_item(task_key: str, session: Session) -> dict[str, Any]:
    task = session.get(TaskModel, task_key)
    if task is None:
        raise HTTPException(status_code=404, detail="运行单元不存在")
    return task_manager.stop_task(task_key)


def stop_builtin_runtime_item(task_key: str) -> dict[str, Any]:
    normalized_key = str(task_key or "").strip()
    if normalized_key == BUILTIN_OCR_SERVICE_KEY:
        return stop_ocr_service()
    if normalized_key == CODEYUN_WATCHDOG_SERVICE_KEY:
        return stop_codeyun_watchdog()
    if normalized_key == PROXY_TRAFFIC_AUDIT_SERVICE_KEY:
        return stop_proxy_traffic_audit()
    if normalized_key == GAME_WINDOW_SERVICE_KEY:
        if not _fanxiu_game_window_service_enabled():
            raise HTTPException(status_code=404, detail="凡修画面流未在当前机器启用")
        return stop_game_window_service()
    if normalized_key == FANXIU_CAPTURE_RUNTIME_SERVICE_KEY:
        if not _fanxiu_capture_runtime_service_enabled():
            raise HTTPException(status_code=404, detail="凡修抓包未在当前机器启用")
        return fanxiu_capture_runtime_service.force_stop("runtime-manual")
    if normalized_key == ATTENDANCE_BEHAVIOR_TREE_SERVICE_KEY:
        if not is_attendance_behavior_tree_service_enabled():
            raise HTTPException(status_code=404, detail="考勤行为树只在 mi15 执行主机上管理")
        return stop_attendance_behavior_tree_service()
    if normalized_key == FANXIU_BEHAVIOR_TREE_SERVICE_KEY:
        if not _fanxiu_behavior_tree_service_enabled():
            raise HTTPException(status_code=404, detail="凡修行为树未在当前机器启用")
        with Session(engine) as session:
            return stop_data_annotation_behavior_tree_current_task(session)
    raise HTTPException(status_code=400, detail="该内置运行单元不支持停止")


def configure_builtin_runtime_item_autostart(task_key: str, enabled: bool) -> dict[str, Any]:
    normalized_key = str(task_key or "").strip()
    if normalized_key != CODEYUN_WATCHDOG_SERVICE_KEY:
        raise HTTPException(status_code=400, detail="该运行单元不支持开机自启配置")
    try:
        return enable_codeyun_watchdog_startup() if enabled else disable_codeyun_watchdog_startup()
    except CodeYunWatchdogError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def toggle_builtin_runtime_job(task_key: str, enabled: bool, session: Session) -> dict[str, Any]:
    from backend.api.admin import BackgroundTaskToggleRequest, toggle_background_task

    return toggle_background_task(
        task_key,
        BackgroundTaskToggleRequest(enabled=enabled),
        session=session,
    )


def configure_builtin_runtime_job_schedule(
    task_key: str,
    schedule_policy: dict[str, Any] | None,
    session: Session,
    *,
    next_run_at: str | None = None,
    next_run_at_provided: bool = False,
) -> dict[str, Any]:
    from backend.api.admin import BackgroundTaskScheduleRequest, configure_background_task_schedule

    payload: dict[str, Any] = {"schedule_policy": schedule_policy}
    if next_run_at_provided:
        payload["next_run_at"] = next_run_at
    return configure_background_task_schedule(
        task_key,
        BackgroundTaskScheduleRequest(**payload),
        session=session,
    )


def delete_builtin_runtime_job(task_key: str) -> dict[str, Any]:
    from backend.api.admin import delete_background_task

    return delete_background_task(task_key)


def delete_builtin_runtime_queue_task(task_id: str) -> dict[str, Any]:
    from backend.api.admin import delete_background_queue_task

    return delete_background_queue_task(task_id)


def reset_builtin_runtime_job_schedule(task_key: str) -> dict[str, Any]:
    from backend.api.admin import reset_background_task_schedule_api

    return reset_background_task_schedule_api(task_key)


def ensure_builtin_source(source: str) -> None:
    if source != "builtin":
        raise HTTPException(status_code=400, detail="该操作仅支持内置作业")

