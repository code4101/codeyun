from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.fanxiu.runtime.behavior_tree import (
    DEFAULT_FANXIU_ENTRY_ID,
    FANXIU_EMBEDDED_SERVICE_ENV,
    FanxiuLocalServiceRequest,
    acquire_fanxiu_job_group_isolation,
    cancel_fanxiu_task_cell,
    clear_fanxiu_task_cells,
    clear_stale_fanxiu_job_group_isolation,
    clear_fanxiu_data_annotation_runtime_logs,
    fanxiu_data_annotation_task_cells,
    fanxiu_data_annotation_task_cell_catalog,
    fanxiu_data_annotation_runtime_logs,
    fanxiu_data_annotation_runtime_status,
    fanxiu_data_annotation_dir,
    data_annotation_asset_tree_path,
    read_fanxiu_job_group_isolation,
    read_fanxiu_behavior_tree_service_owner,
    release_fanxiu_job_group_isolation,
    request_fanxiu_behavior_tree_stop,
    resolve_fanxiu_entry,
    run_fanxiu_local_service,
    start_fanxiu_local_service,
    stop_fanxiu_local_service,
    wait_fanxiu_task_cell,
)
from backend.core.fanxiu.runtime.kernel import FanxiuKernel
from backend.core.fanxiu.runtime.jupyter_kernel import run_fanxiu_jupyter_kernel_service
from backend.core.fanxiu.data_annotation.runner import create_fanxiu_runtime_runner
from backend.core.fanxiu.data_annotation.jobs import parse_data_annotation_scene_id
from backend.core.fanxiu.data_annotation.runtime_control import (
    build_scheduler_plan,
    read_doctor_watch_latest,
    read_scheduler_tasks,
    reset_scheduler_task_runs,
    run_due_scheduler_tasks,
)


_DOCTOR_LOG_KEYWORDS = (
    "开始到期任务",
    "到期任务",
    "Scheduler",
    "失败",
    "错误",
    "超时",
    "error",
    "日常_游历",
    "日常_灵祖",
    "#186",
    "奖励浮层",
    "宝魄",
)


def _payload_from_args(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] = {}
    if args.payload:
        try:
            payload.update(json.loads(args.payload))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--payload 不是合法 JSON：{exc}") from exc
    if args.timeout_seconds:
        payload["timeout_seconds"] = float(args.timeout_seconds)
    if args.command == "go-scene":
        payload["target_scene_id"] = parse_data_annotation_scene_id(args.scene_id)
        return "go_scene", payload
    if args.command == "mail-check":
        payload.update(
            {
                "observe_only": bool(args.observe_only),
                "scan_mode": args.scan_mode,
                "skip_capture": bool(args.skip_capture),
                "max_actions": int(args.max_actions or 0),
            }
        )
        return "mail_cleanup", payload
    if args.command in {"task", "run"}:
        if str(args.task_type) == "go_scene" and getattr(args, "target_scene_id", ""):
            payload["target_scene_id"] = parse_data_annotation_scene_id(args.target_scene_id)
        return str(args.task_type), payload
    raise SystemExit(f"未知命令：{args.command}")


def _apply_wait_timeout_as_runtime_budget(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    if "timeout_seconds" in payload or "max_runtime_seconds" in payload:
        return
    wait_timeout = float(getattr(args, "wait_timeout_seconds", 0.0) or 0.0)
    if wait_timeout > 300.0:
        payload["timeout_seconds"] = wait_timeout


def _print_log_entries(entries: list[dict[str, Any]]) -> None:
    for item in entries:
        kind = item.get("kind") or "info"
        scope = item.get("scope") or ""
        item_id = item.get("item_id") or ""
        prefix = " ".join(part for part in [str(item.get("time") or ""), str(kind), str(scope), str(item_id)] if part)
        message = item.get("message") or ""
        print(f"{prefix}: {message}".strip())


def _configure_stdout() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(errors="replace")


def _print_status(status: dict[str, Any]) -> None:
    print(json.dumps(
        {
            "status": status.get("status"),
            "phase": status.get("phase"),
            "task_type": status.get("task_type"),
            "current_scene": status.get("current_scene"),
            "message": status.get("message"),
            "error": status.get("error"),
            "queued_cell": status.get("queued_cell") or status.get("queued_job") or {},
        },
        ensure_ascii=False,
        indent=2,
    ))
    logs = [item for item in status.get("logs") or [] if isinstance(item, dict)]
    _print_log_entries(logs[-12:])


def _print_owner(owner: dict[str, Any]) -> None:
    print(json.dumps(
        {
            "active": bool(owner.get("active")),
            "stale": bool(owner.get("stale")),
            "pid": owner.get("pid"),
            "entry_id": owner.get("entry_id"),
            "step": owner.get("step"),
            "age_seconds": owner.get("age_seconds"),
            "path": owner.get("path"),
            "error": owner.get("error") or "",
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    ))


def _print_task_cells(jobs: list[dict[str, Any]]) -> None:
    if not jobs:
        print("task cell 队列为空")
        return
    for job in jobs:
        print(json.dumps(
            {
                "id": job.get("id"),
                "status": job.get("status"),
                "task_type": job.get("task_type"),
                "label": job.get("label"),
                "created_at": job.get("created_at"),
                "started_at": job.get("started_at"),
            },
            ensure_ascii=False,
            default=str,
        ))


def _wait_and_print_queued_cell(status: dict[str, Any], timeout_seconds: float) -> int:
    queued_cell = status.get("queued_cell") if isinstance(status.get("queued_cell"), dict) else {}
    if not queued_cell:
        queued_cell = status.get("queued_job") if isinstance(status.get("queued_job"), dict) else {}
    job_id = str(queued_cell.get("id") or "")
    if not job_id:
        print("没有 queued_cell.id，无法等待")
        return 1
    result = wait_fanxiu_task_cell(job_id, timeout_seconds=float(timeout_seconds or 300.0))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    runtime_status = result.get("runtime_status") if isinstance(result.get("runtime_status"), dict) else {}
    if not bool(result.get("done")):
        return 1
    return 0 if str(runtime_status.get("status") or "") not in {"error", "stopped"} else 1


def _print_job_catalog(items: list[dict[str, Any]]) -> None:
    if not items:
        print("没有已注册作业类型")
        return
    for item in items:
        flags = []
        if item.get("scheduler_supported"):
            flags.append("scheduler")
        if item.get("interruptible"):
            flags.append("interruptible")
        print(f"{item.get('task_type')}  {item.get('label')}  {'/'.join(flags)}".rstrip())


def _runtime_status_summary(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "service_running": status.get("service_running"),
        "running": status.get("running"),
        "status": status.get("status"),
        "phase": status.get("phase"),
        "current_scene": status.get("current_scene"),
        "task_type": status.get("task_type"),
        "current_task": status.get("current_task"),
        "current_task_id": status.get("current_task_id"),
        "message": status.get("message"),
        "error": status.get("error") or "",
    }


def _scheduler_task_summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task.get("id"),
        "task_type": task.get("task_type"),
        "label": task.get("label"),
        "enabled": task.get("enabled"),
        "next_time": task.get("next_time"),
        "retry_after": task.get("retry_after"),
        "last_run_at": task.get("last_run_at"),
        "last_result": task.get("last_result"),
        "last_message": task.get("last_message"),
    }


def _doctor_relevant_logs(limit: int) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for scope in ("job", "manual_job", "guard", ""):
        for item in fanxiu_data_annotation_runtime_logs(limit=limit, scope=scope):
            if not isinstance(item, dict):
                continue
            key = (item.get("time"), item.get("kind"), item.get("scope"), item.get("item_id"), item.get("message"))
            if key in seen:
                continue
            seen.add(key)
            message = str(item.get("message") or "")
            if any(keyword in message for keyword in _DOCTOR_LOG_KEYWORDS):
                entries.append(item)
    return entries[-limit:]


def _doctor_screenshot() -> dict[str, Any]:
    from backend.core.fanxiu.runtime.mumu_control import screencap_mumu_adb_png
    from backend.core.temp_paths import codeyun_temp_root

    out_dir = codeyun_temp_root("fanxiu-evidence")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data, meta = screencap_mumu_adb_png()
    path = out_dir / f"doctor_{stamp}.png"
    path.write_bytes(data)
    return {"path": str(path), "bytes": len(data), "meta": meta}


def _doctor_blocking_overlays(screenshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(screenshot, dict):
        return []
    path_text = str(screenshot.get("path") or "")
    if not path_text:
        return []
    path = Path(path_text)
    if not path.exists():
        return []
    try:
        def shape_titles(image: dict[str, Any] | None) -> list[str]:
            if not isinstance(image, dict):
                return []
            titles: list[str] = []
            for shape in image.get("shapes") or []:
                if isinstance(shape, dict):
                    title = str(shape.get("title") or "").strip()
                    if title:
                        titles.append(title)
            return titles

        def game_announcement_action_titles(image: dict[str, Any] | None) -> list[str]:
            if not isinstance(image, dict):
                return []
            actions: list[str] = []
            if runner._find_shape(image, "关闭公告"):
                actions.append("关闭公告")
            return actions

        data_url = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
        runner = create_fanxiu_runtime_runner()
        text = runner._ocr_text(runner._ocr_lines(data_url))
        asset_tree_path = data_annotation_asset_tree_path(DEFAULT_FANXIU_ENTRY_ID)
        tree = runner._load_asset_tree(asset_tree_path)
        ctx = {
            "asset_tree": tree,
            "asset_tree_path": asset_tree_path,
            "images": runner._index_images(tree),
        }
        if "游戏公告" in text or ("更新公告" in text and "风险提醒" in text):
            image = runner._find_asset_image_by_title(ctx, "游戏公告")
            action_titles = game_announcement_action_titles(image)
            message = "检测到游戏公告遮挡"
            if not action_titles:
                message += "；资产树「游戏公告」缺少「关闭公告」动作标注，自动作业无法安全进入游戏"
            return [{
                "scene_id": None,
                "title": "游戏公告",
                "keywords": [keyword for keyword in ("游戏公告", "更新公告", "风险提醒") if keyword in text],
                "all_shapes": shape_titles(image),
                "action_shapes": action_titles,
                "blocking": not bool(action_titles),
                "message": message,
            }]
        compact = re.sub(r"\s+", "", text)
        if "破界符" in compact and "购买并使用" in compact and ("剩余限购次数" in compact or "价格" in compact):
            image = (ctx.get("images") or {}).get(224)
            image225 = (ctx.get("images") or {}).get(225)
            has_purchase = bool(runner._find_shape(image, "购买并使用"))
            has_return_blank = bool(runner._find_shape(image225, "空白"))
            action_titles = []
            if has_purchase:
                action_titles.append("购买并使用")
            if has_return_blank:
                action_titles.append("#225 空白")
            missing = []
            if not has_purchase:
                missing.append("#224「购买并使用」")
            if not has_return_blank:
                missing.append("#225「空白」")
            message = "检测到 #224「购买破界符」弹窗"
            if missing:
                message += f"；资产树缺少 {'、'.join(missing)}，自动作业无法按 #224 连续购买到 #225 后回退"
            return [{
                "scene_id": 224,
                "title": "购买破界符",
                "keywords": [keyword for keyword in ("破界符", "购买并使用", "剩余限购次数", "价格") if keyword in text],
                "all_shapes": shape_titles(image),
                "action_shapes": action_titles,
                "blocking": bool(missing),
                "message": message,
            }]
        return []
    except Exception as exc:
        return [{"error": str(exc), "blocking": False, "message": "阻断浮层巡检失败"}]


def _doctor_annotation_target(blocker: dict[str, Any], entry_id: str | None) -> dict[str, Any] | None:
    title = str(blocker.get("title") or "")
    normalized = str(title or "").strip()
    if normalized == "游戏公告":
        focus_title = "游戏公告"
        acceptable_shapes = ["关闭公告"]
        description = "在资产树「游戏公告」补充「关闭公告」动作标注"
    elif normalized == "灵祖奖励浮层":
        focus_title = "灵祖奖励浮层"
        acceptable_shapes = ["关闭", "空白", "返回", "退出"]
        description = "在 #186「灵祖奖励浮层」补充可安全关闭的动作标注"
    elif normalized == "购买破界符":
        focus_title = "购买破界符"
        acceptable_shapes = ["购买并使用", "#225 空白"]
        description = "补齐 #224「购买并使用」和 #225「空白」，用于连续购买到 #225 后回退"
    else:
        return None

    existing_shapes = [
        str(shape)
        for shape in (blocker.get("action_shapes") or [])
        if str(shape)
    ]
    all_shapes = [
        str(shape)
        for shape in (blocker.get("all_shapes") or [])
        if str(shape)
    ]
    missing_shapes = [
        shape
        for shape in acceptable_shapes
        if shape not in existing_shapes
    ]
    query = {
        "entry_id": entry_id or DEFAULT_FANXIU_ENTRY_ID,
        "focus_image_title": focus_title,
    }
    path = "/fanxiu/data-annotation"
    return {
        "title": focus_title,
        "path": path,
        "query": query,
        "url": f"{path}?{urlencode(query)}",
        "acceptable_shapes": acceptable_shapes,
        "existing_shapes": existing_shapes,
        "all_shapes": all_shapes,
        "missing_shapes": missing_shapes,
        "required_shapes": acceptable_shapes,
        "description": description,
    }


def _runtime_error_requires_human_annotation(message: str) -> bool:
    text = str(message or "")
    if not text:
        return False
    markers = (
        "请人工补标/修标",
        "人工补标",
        "缺少可靠标注",
        "场景跳转缺少可靠标注",
        "缺少安全推进动作标注",
    )
    return any(marker in text for marker in markers)


def _runtime_error_task_label(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    match = re.match(r"([^：:]{2,40})[：:]", text)
    if not match:
        return ""
    label = match.group(1).strip()
    if not label.startswith(("日常_", "邮件_", "作业", "task cell", "AI显式提交")):
        return ""
    return label


def _parse_local_time_to_ts(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).timestamp()
        except ValueError:
            pass
    return 0.0


def _build_maintenance_summary(report: dict[str, Any]) -> dict[str, Any]:
    runtime = report.get("runtime") if isinstance(report.get("runtime"), dict) else {}
    scheduler = report.get("scheduler") if isinstance(report.get("scheduler"), dict) else {}
    daily_audit = scheduler.get("daily_audit") if isinstance(scheduler.get("daily_audit"), dict) else {}
    due_tasks = [task for task in (scheduler.get("due_tasks") or []) if isinstance(task, dict)]
    screenshot_blockers = [item for item in (report.get("blocking_overlays") or []) if isinstance(item, dict)]
    plan_blockers = [item for item in (scheduler.get("blocking_overlays") or []) if isinstance(item, dict)]
    blockers = screenshot_blockers or plan_blockers
    blocking_items = [item for item in blockers if bool(item.get("blocking"))]
    runtime_error_text = str(runtime.get("error") or runtime.get("message") or "")
    runtime_active_or_error = bool(runtime.get("running")) or str(runtime.get("status") or "") == "error"
    runtime_annotation_blockers: list[dict[str, Any]] = []
    runtime_error_label = _runtime_error_task_label(runtime_error_text)
    if _runtime_error_requires_human_annotation(runtime_error_text):
        runtime_active_or_error = True
        blocker: dict[str, Any] = {
            "id": "runtime_annotation",
            "title": "场景跳转标注缺失",
            "blocking": True,
            "message": runtime_error_text,
        }
        if runtime_error_label:
            blocker["task_label"] = runtime_error_label
        runtime_annotation_blockers.append(blocker)
    enabled_by_id = {
        str(task.get("id") or ""): task
        for task in (scheduler.get("enabled_tasks") or [])
        if isinstance(task, dict)
    }
    due_ids = {str(task.get("id") or "") for task in due_tasks}
    due_labels_by_id = {str(task.get("id") or ""): str(task.get("label") or "") for task in due_tasks}
    due_state = [
        {
            "id": task_id,
            "label": str(task.get("label") or task_id),
            "next_time": task.get("next_time"),
            "retry_after": task.get("retry_after"),
            "last_run_at": task.get("last_run_at"),
            "last_result": task.get("last_result"),
        }
        for task_id, task in enabled_by_id.items()
        if task_id in due_ids
    ]
    stale_due = [
        item
        for item in due_state
        if item.get("next_time") and item.get("last_run_at") != item.get("next_time")
    ]
    stale_due_success = [
        item
        for item in stale_due
        if str(item.get("last_result") or "") == "success"
    ]
    blocked_due = [
        item
        for item in due_state
        if str(item.get("last_result") or "") == "blocked"
    ]
    runtime_annotation_blocked_due_ids = {
        task_id
        for task_id, label in due_labels_by_id.items()
        if runtime_error_label and label == runtime_error_label
    }
    global_human_blocking_items = [*blocking_items]
    if (
        runtime_annotation_blockers
        and runtime_active_or_error
        and runtime_error_label
        and not runtime_annotation_blocked_due_ids
    ):
        global_human_blocking_items.extend(runtime_annotation_blockers)
    human_blocking_items = [*global_human_blocking_items, *runtime_annotation_blockers]
    audit_updated_at = float(daily_audit.get("updated_at") or 0) if daily_audit else 0.0
    visual_incomplete_rows = []
    for item in (daily_audit.get("mapped_incomplete") or []):
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "")
        if not task_id:
            continue
        task = enabled_by_id.get(task_id) or {}
        last_run_at = _parse_local_time_to_ts(task.get("last_run_at"))
        if audit_updated_at and last_run_at and audit_updated_at <= last_run_at:
            continue
        visual_incomplete_rows.append(item)
    visual_incomplete_ids = {str(item.get("task_id") or "") for item in visual_incomplete_rows}
    visual_incomplete_tasks = [
        {
            **_scheduler_task_summary(task),
            "audit_row": next(
                (row for row in visual_incomplete_rows if str(row.get("task_id") or "") == str(task.get("id") or "")),
                {},
            ),
        }
        for task in (scheduler.get("enabled_tasks") or [])
        if isinstance(task, dict) and str(task.get("id") or "") in visual_incomplete_ids
    ]
    visual_unmapped_incomplete = [
        item
        for item in (daily_audit.get("unmapped_incomplete") or [])
        if isinstance(item, dict)
    ]
    now_dt = datetime.now()
    critical_failed_tasks: list[dict[str, Any]] = []
    critical_task_ids = {"mail-cleanup"}
    for task_id in critical_task_ids:
        task = enabled_by_id.get(task_id)
        if not isinstance(task, dict):
            continue
        result = str(task.get("last_result") or "")
        retry_after = str(task.get("retry_after") or "").strip()
        last_run_at = str(task.get("last_run_at") or "").strip()
        schedule_times = task.get("schedule_times") if isinstance(task.get("schedule_times"), list) else []
        schedule_passed_today = False
        for schedule_time in schedule_times:
            try:
                hour_text, minute_text = str(schedule_time).split(":", 1)
                scheduled_dt = now_dt.replace(hour=int(hour_text), minute=int(minute_text), second=0, microsecond=0)
            except (TypeError, ValueError):
                continue
            if scheduled_dt <= now_dt:
                schedule_passed_today = True
                break
        ran_today = last_run_at.startswith(now_dt.strftime("%Y-%m-%d"))
        stale_running = result == "running" and not bool(runtime.get("running"))
        failed_after_trigger = result in {"error", "stopped", "blocked"} and (
            bool(retry_after) or ran_today or schedule_passed_today
        )
        if not (failed_after_trigger or stale_running):
            continue
        critical_failed_tasks.append(
            {
                "id": task_id,
                "label": str(task.get("label") or task_id),
                "last_result": result,
                "last_run_at": task.get("last_run_at"),
                "retry_after": task.get("retry_after"),
                "next_time": task.get("next_time"),
                "reason": "stale_running" if stale_running else "failed_after_trigger",
            }
        )
    if global_human_blocking_items:
        blocked_by_id = {str(item.get("id") or ""): item for item in blocked_due}
        for item in due_state:
            task_id = str(item.get("id") or "")
            if task_id and task_id not in blocked_by_id:
                blocked_by_id[task_id] = item
        blocked_due = list(blocked_by_id.values())
        stale_due_success = [
            item
            for item in stale_due_success
            if str(item.get("id") or "") not in blocked_by_id
        ]
    elif runtime_annotation_blocked_due_ids:
        blocked_by_id = {str(item.get("id") or ""): item for item in blocked_due}
        for item in due_state:
            task_id = str(item.get("id") or "")
            if task_id in runtime_annotation_blocked_due_ids and task_id not in blocked_by_id:
                blocked_by_id[task_id] = item
        blocked_due = list(blocked_by_id.values())
    action_required: list[str] = []
    blocking_action_items = global_human_blocking_items or runtime_annotation_blockers
    if blocking_action_items and (global_human_blocking_items or len(blocked_due) >= len(due_state or [])):
        for item in blocking_action_items:
            title = str(item.get("title") or "阻断浮层")
            if title == "游戏公告":
                action_required.append("在资产树「游戏公告」补充「关闭公告」动作标注")
            elif title == "灵祖奖励浮层":
                action_required.append("在 #186「灵祖奖励浮层」补充可安全关闭的「关闭/空白/返回/退出」动作标注")
            elif title == "场景跳转标注缺失":
                action_required.append(str(item.get("message") or "修复 Runtime 报告的场景跳转标注缺失"))
            else:
                action_required.append(str(item.get("message") or f"处理阻断项：{title}"))
    elif scheduler.get("next_action") == "job_group_disabled" and due_tasks:
        action_required.append("当前有到期任务但工程作业组已关闭；等待 AI 显式提交 cell")
    elif scheduler.get("next_action") == "run_due" and due_tasks:
        action_required.append("当前有到期任务且未发现阻断，等待 resident service 执行或检查服务调度日志")
    elif critical_failed_tasks:
        labels = "、".join(str(item.get("label") or item.get("id")) for item in critical_failed_tasks)
        action_required.append(f"关键作业今日失败或残留：{labels}；需要立即诊断日志、清理运行残留并按公开入口监督重跑或 observe-only 验证")
    elif visual_incomplete_tasks or visual_unmapped_incomplete:
        action_required.append("日常页复核发现任务次数未满；已映射任务应重新到期执行，未映射任务需要补 Scheduler 能力或映射规则")
    elif not due_tasks:
        action_required.append("当前没有到期任务")

    owner = report.get("owner") if isinstance(report.get("owner"), dict) else {}
    entry_id = str(owner.get("entry_id") or DEFAULT_FANXIU_ENTRY_ID)
    annotation_targets = [
        target
        for target in (
            _doctor_annotation_target(item, entry_id)
            for item in blocking_items
        )
        if target is not None
    ]

    unblocked_due_count = max(0, len(due_state) - len(blocked_due))
    report_blocked_by = human_blocking_items if (global_human_blocking_items or unblocked_due_count == 0) else global_human_blocking_items
    if global_human_blocking_items or (runtime_annotation_blockers and unblocked_due_count == 0):
        severity = "blocked"
        summary = str((blocking_action_items[0] if blocking_action_items else {}).get("message") or scheduler.get("message") or runtime.get("message") or "检测到阻断项")
    elif str(runtime.get("status") or "") == "error":
        severity = "error"
        summary = str(runtime.get("error") or runtime.get("message") or "Runtime 错误")
    elif due_tasks and scheduler.get("next_action") == "job_group_disabled":
        severity = "attention"
        summary = f"{len(due_tasks)} 个任务已到期；AI 调度器占用运行权，工程不自动执行"
    elif due_tasks and scheduler.get("next_action") == "run_due":
        severity = "attention"
        summary = f"{len(due_tasks)} 个任务已到期，等待自动执行"
    elif critical_failed_tasks:
        severity = "attention"
        labels = "、".join(str(item.get("label") or item.get("id")) for item in critical_failed_tasks)
        summary = f"关键作业失败或残留：{labels}"
    elif visual_incomplete_tasks or visual_unmapped_incomplete:
        severity = "attention"
        summary = (
            f"日常页复核发现 {len(visual_incomplete_tasks)} 个已映射任务、"
            f"{len(visual_unmapped_incomplete)} 个未映射条目次数未满"
        )
    else:
        severity = "ok"
        summary = str(scheduler.get("message") or runtime.get("message") or "巡检未发现阻断")

    return {
        "severity": severity,
        "summary": summary,
        "automation_safe": not bool(global_human_blocking_items) and (str(runtime.get("status") or "") != "error" or unblocked_due_count > 0),
        "needs_human_annotation": bool(global_human_blocking_items) or (bool(runtime_annotation_blockers) and unblocked_due_count == 0),
        "blocked_by": report_blocked_by,
        "due_task_count": len(due_tasks),
        "due_task_ids": [str(task.get("id") or "") for task in due_tasks],
        "due_task_state": due_state,
        "state_clean": not any(str(item.get("last_result") or "") == "running" for item in due_state),
        "stale_due_count": len(stale_due),
        "stale_due_success_count": len(stale_due_success),
        "blocked_due_count": len(blocked_due),
        "blocked_due_ids": [str(item.get("id") or "") for item in blocked_due],
        "critical_failed_count": len(critical_failed_tasks),
        "critical_failed_ids": [str(item.get("id") or "") for item in critical_failed_tasks],
        "critical_failed_tasks": critical_failed_tasks,
        "visual_incomplete_count": len(visual_incomplete_tasks),
        "visual_incomplete_ids": [str(item.get("id") or "") for item in visual_incomplete_tasks],
        "visual_unmapped_incomplete_count": len(visual_unmapped_incomplete),
        "visual_unmapped_incomplete": visual_unmapped_incomplete,
        "action_required": action_required,
        "annotation_targets": annotation_targets,
        "retry_condition": "阻断浮层消失且对应资产树已有安全处理动作标注" if human_blocking_items else "无需特殊条件",
    }


def _doctor_exit_code(report: dict[str, Any], *, strict: bool) -> int:
    owner = report.get("owner") if isinstance(report.get("owner"), dict) else {}
    runtime_status = report.get("runtime") if isinstance(report.get("runtime"), dict) else {}
    maintenance = report.get("maintenance") if isinstance(report.get("maintenance"), dict) else {}
    due_task_count = int(maintenance.get("due_task_count") or 0)
    if str(runtime_status.get("status") or "") in {"error", "stopped"}:
        return 1
    if due_task_count > 0 and not bool(owner.get("active")):
        return 1
    if not strict:
        return 0
    severity = str(maintenance.get("severity") or "")
    if severity in {"blocked", "error"}:
        return 2
    if severity == "attention":
        return 1
    return 0


def _print_doctor_summary(report: dict[str, Any]) -> None:
    maintenance = report.get("maintenance") if isinstance(report.get("maintenance"), dict) else {}
    owner = report.get("owner") if isinstance(report.get("owner"), dict) else {}
    runtime_status = report.get("runtime") if isinstance(report.get("runtime"), dict) else {}
    scheduler = report.get("scheduler") if isinstance(report.get("scheduler"), dict) else {}
    lines = [
        f"checked_at: {report.get('checked_at') or ''}",
        f"severity: {maintenance.get('severity') or 'unknown'}",
        f"summary: {maintenance.get('summary') or ''}",
        "owner: "
        f"active={bool(owner.get('active'))} "
        f"pid={owner.get('pid') or ''} "
        f"step={owner.get('step') or ''}",
        "runtime: "
        f"status={runtime_status.get('status') or ''} "
        f"phase={runtime_status.get('phase') or ''} "
        f"scene={runtime_status.get('current_scene') or ''}",
        "scheduler: "
        f"next_action={scheduler.get('next_action') or ''} "
        f"due_task_count={maintenance.get('due_task_count') or 0} "
        f"stale_due_count={maintenance.get('stale_due_count') or 0} "
        f"blocked_due_count={maintenance.get('blocked_due_count') or 0} "
        f"stale_due_success_count={maintenance.get('stale_due_success_count') or 0} "
        f"visual_incomplete_count={maintenance.get('visual_incomplete_count') or 0}",
        f"automation_safe: {bool(maintenance.get('automation_safe'))}",
        f"needs_human_annotation: {bool(maintenance.get('needs_human_annotation'))}",
    ]
    blockers = [item for item in (maintenance.get("blocked_by") or []) if isinstance(item, dict)]
    for item in blockers:
        lines.append(f"blocked_by: {item.get('title') or '阻断项'} - {item.get('message') or ''}")
    actions = [str(item) for item in (maintenance.get("action_required") or []) if str(item)]
    for action in actions:
        lines.append(f"action_required: {action}")
    annotation_targets = [item for item in (maintenance.get("annotation_targets") or []) if isinstance(item, dict)]
    for target in annotation_targets:
        acceptable_shapes = target.get("acceptable_shapes") or target.get("required_shapes") or []
        missing_shapes = target.get("missing_shapes") or []
        existing_shapes = target.get("existing_shapes") or []
        all_shapes = target.get("all_shapes") or []
        lines.append(
            "annotation_target: "
            f"{target.get('title') or ''} "
            f"url={target.get('url') or ''} "
            f"all_shapes={','.join(str(shape) for shape in all_shapes)} "
            f"existing_safe_shapes={','.join(str(shape) for shape in existing_shapes)} "
            f"acceptable_shapes={','.join(str(shape) for shape in acceptable_shapes)} "
            f"missing_shapes={','.join(str(shape) for shape in missing_shapes)}"
        )
    retry_condition = str(maintenance.get("retry_condition") or "")
    if retry_condition:
        lines.append(f"retry_condition: {retry_condition}")
    screenshot = report.get("screenshot") if isinstance(report.get("screenshot"), dict) else {}
    if screenshot.get("path"):
        lines.append(f"screenshot: {screenshot.get('path')}")
    elif report.get("screenshot_error"):
        lines.append(f"screenshot_error: {report.get('screenshot_error')}")
    print("\n".join(lines))


def _doctor_watch_event(report: dict[str, Any], *, iteration: int) -> dict[str, Any]:
    maintenance = report.get("maintenance") if isinstance(report.get("maintenance"), dict) else {}
    owner = report.get("owner") if isinstance(report.get("owner"), dict) else {}
    runtime_status = report.get("runtime") if isinstance(report.get("runtime"), dict) else {}
    scheduler = report.get("scheduler") if isinstance(report.get("scheduler"), dict) else {}
    screenshot = report.get("screenshot") if isinstance(report.get("screenshot"), dict) else {}
    return {
        "iteration": iteration,
        "checked_at": report.get("checked_at"),
        "severity": maintenance.get("severity") or "unknown",
        "summary": maintenance.get("summary") or "",
        "owner_active": bool(owner.get("active")),
        "owner_pid": owner.get("pid"),
        "owner_step": owner.get("step"),
        "runtime_status": runtime_status.get("status"),
        "runtime_phase": runtime_status.get("phase"),
        "runtime_scene": runtime_status.get("current_scene"),
        "scheduler_next_action": scheduler.get("next_action"),
        "due_task_count": maintenance.get("due_task_count") or 0,
        "due_task_ids": maintenance.get("due_task_ids") or [],
        "stale_due_count": maintenance.get("stale_due_count") or 0,
        "stale_due_success_count": maintenance.get("stale_due_success_count") or 0,
        "blocked_due_count": maintenance.get("blocked_due_count") or 0,
        "blocked_due_ids": maintenance.get("blocked_due_ids") or [],
        "visual_incomplete_count": maintenance.get("visual_incomplete_count") or 0,
        "visual_incomplete_ids": maintenance.get("visual_incomplete_ids") or [],
        "visual_unmapped_incomplete_count": maintenance.get("visual_unmapped_incomplete_count") or 0,
        "automation_safe": bool(maintenance.get("automation_safe")),
        "needs_human_annotation": bool(maintenance.get("needs_human_annotation")),
        "blocked_by": maintenance.get("blocked_by") or [],
        "action_required": maintenance.get("action_required") or [],
        "annotation_targets": maintenance.get("annotation_targets") or [],
        "retry_condition": maintenance.get("retry_condition") or "",
        "screenshot_path": screenshot.get("path") or "",
        "screenshot_error": report.get("screenshot_error") or "",
        "auto_run_due": report.get("auto_run_due") or {},
    }


def _watch_should_auto_run_due(report: dict[str, Any]) -> bool:
    scheduler = report.get("scheduler") if isinstance(report.get("scheduler"), dict) else {}
    maintenance = report.get("maintenance") if isinstance(report.get("maintenance"), dict) else {}
    owner = report.get("owner") if isinstance(report.get("owner"), dict) else {}
    runtime_status = report.get("runtime") if isinstance(report.get("runtime"), dict) else {}
    isolation = report.get("isolation") if isinstance(report.get("isolation"), dict) else {}
    next_action = str(scheduler.get("next_action") or "")
    if next_action in {"job_group_disabled", "run_due"}:
        report["auto_run_due_blocked_reason"] = next_action
        return False
    if next_action != "manual_ai_cell":
        return False
    if not [item for item in (scheduler.get("due_tasks") or []) if isinstance(item, dict)]:
        return False
    if [item for item in (report.get("task_cells") or []) if isinstance(item, dict)]:
        return False
    if bool(isolation.get("active")):
        return False
    if str(maintenance.get("severity") or "") in {"blocked", "error"}:
        return False
    if not bool(maintenance.get("automation_safe")):
        return False
    if str(runtime_status.get("status") or "") == "running":
        return False
    return True


def _watch_wait_for_queued_cell(status: dict[str, Any], *, entry_id: str, timeout_seconds: float) -> dict[str, Any]:
    queued_cell = status.get("queued_cell") if isinstance(status.get("queued_cell"), dict) else {}
    if not queued_cell:
        queued_cell = status.get("queued_job") if isinstance(status.get("queued_job"), dict) else {}
    job_id = str(queued_cell.get("id") or "")
    if not job_id:
        return {"waited": False, "error": "missing_queued_cell_id"}
    previous_embedded = os.environ.get(FANXIU_EMBEDDED_SERVICE_ENV)
    result: dict[str, Any] | None = None
    try:
        os.environ[FANXIU_EMBEDDED_SERVICE_ENV] = "1"
        service_status = start_fanxiu_local_service(
            FanxiuLocalServiceRequest(entry_id=entry_id, tick_seconds=0.5)
        )
        result = wait_fanxiu_task_cell(
            job_id,
            timeout_seconds=max(1.0, float(timeout_seconds or 900.0)),
            poll_seconds=0.5,
        )
        return {
            "waited": True,
            "job_id": job_id,
            "done": bool(result.get("done")),
            "result": result.get("result"),
            "runtime_status": result.get("runtime_status"),
            "service_status": service_status,
        }
    finally:
        if result is not None and bool(result.get("done")):
            stop_fanxiu_local_service()
        if previous_embedded is None:
            os.environ.pop(FANXIU_EMBEDDED_SERVICE_ENV, None)
        else:
            os.environ[FANXIU_EMBEDDED_SERVICE_ENV] = previous_embedded


def _watch_auto_run_due(report: dict[str, Any], *, wait_timeout_seconds: float = 900.0) -> dict[str, Any]:
    owner = report.get("owner") if isinstance(report.get("owner"), dict) else {}
    entry_id = str(owner.get("entry_id") or DEFAULT_FANXIU_ENTRY_ID)
    status = run_due_scheduler_tasks(
        entry=resolve_fanxiu_entry(entry_id),
        entry_id=entry_id,
        asset_tree_path=data_annotation_asset_tree_path(entry_id),
    )
    wait_result: dict[str, Any] = {}
    queued_cell = status.get("queued_cell") if isinstance(status.get("queued_cell"), dict) else {}
    if not queued_cell:
        queued_cell = status.get("queued_job") if isinstance(status.get("queued_job"), dict) else {}
    if str(status.get("phase") or "") == "scheduler_due_queued" and queued_cell.get("id"):
        wait_result = _watch_wait_for_queued_cell(
            status,
            entry_id=entry_id,
            timeout_seconds=wait_timeout_seconds,
        )
    return {
        "triggered": True,
        "entry_id": entry_id,
        "status": status.get("status"),
        "phase": status.get("phase"),
        "message": status.get("message"),
        "current_task_id": status.get("current_task_id"),
        "blocking_overlays": status.get("blocking_overlays") or [],
        "queued_cell": queued_cell or None,
        "wait_result": wait_result,
    }


def _watch_auto_run_due_batch(
    report: dict[str, Any],
    *,
    log_limit: int,
    include_screenshot: bool,
    take_screenshot: bool,
    min_interval_seconds: float,
    last_due_key: str,
    last_due_at: float,
    max_runs: int = 10,
    wait_timeout_seconds: float = 900.0,
) -> tuple[dict[str, Any], float, str]:
    auto_run_due_results: list[dict[str, Any]] = []
    seen_due_keys: set[str] = set()
    min_interval = max(1.0, float(min_interval_seconds or 300.0))

    while len(auto_run_due_results) < max(1, int(max_runs or 1)) and _watch_should_auto_run_due(report):
        scheduler = report.get("scheduler") if isinstance(report.get("scheduler"), dict) else {}
        due_ids = sorted(str(item.get("id") or "") for item in (scheduler.get("due_tasks") or []) if isinstance(item, dict))
        due_key = "|".join(due_ids)
        if not due_key:
            break

        now_mono = time.monotonic()
        if not auto_run_due_results and due_key == last_due_key and now_mono - last_due_at < min_interval:
            break
        if due_key in seen_due_keys:
            report["auto_run_due_repeated_due_key"] = due_key
            break
        seen_due_keys.add(due_key)

        try:
            auto_run_due_result = _watch_auto_run_due(report, wait_timeout_seconds=wait_timeout_seconds)
        except Exception as exc:
            auto_run_due_result = {"triggered": False, "error": str(exc)}
        auto_run_due_results.append(auto_run_due_result)
        last_due_at = now_mono
        last_due_key = due_key

        if not auto_run_due_result.get("triggered") or auto_run_due_result.get("error"):
            break

        report = _build_doctor_report(log_limit=log_limit, include_screenshot=take_screenshot)
        auto_run_blockers = [
            item
            for item in (auto_run_due_result.get("blocking_overlays") or [])
            if isinstance(item, dict)
        ]
        if auto_run_blockers and not [
            item
            for item in (report.get("blocking_overlays") or [])
            if isinstance(item, dict)
        ]:
            report["blocking_overlays"] = auto_run_blockers
            report["maintenance"] = _build_maintenance_summary(report)
        maintenance = report.get("maintenance") if isinstance(report.get("maintenance"), dict) else {}
        if bool(include_screenshot) and not take_screenshot and str(maintenance.get("severity") or "") in {"blocked", "error"}:
            report = _build_doctor_report(log_limit=log_limit, include_screenshot=True)

    if auto_run_due_results:
        report["auto_run_due"] = {
            **auto_run_due_results[-1],
            "run_count": len(auto_run_due_results),
            "runs": auto_run_due_results,
        }
        if len(auto_run_due_results) >= max(1, int(max_runs or 1)) and _watch_should_auto_run_due(report):
            report["auto_run_due_limit_reached"] = True
    return report, last_due_at, last_due_key


def _asset_tree_signature_for_entry(entry_id: str | None) -> tuple[int, int]:
    try:
        path = data_annotation_asset_tree_path(entry_id or DEFAULT_FANXIU_ENTRY_ID)
        stat = path.stat()
        return int(stat.st_mtime_ns), int(stat.st_size)
    except Exception:
        return 0, 0


def _watch_has_annotation_blocker(report: dict[str, Any]) -> bool:
    maintenance = report.get("maintenance") if isinstance(report.get("maintenance"), dict) else {}
    if str(maintenance.get("severity") or "") != "blocked":
        return False
    return any(isinstance(item, dict) for item in (maintenance.get("annotation_targets") or []))


def _watch_sleep_until_next_check(report: dict[str, Any], *, interval_seconds: float) -> None:
    interval = max(1.0, float(interval_seconds or 60.0))
    if not _watch_has_annotation_blocker(report):
        time.sleep(interval)
        return

    owner = report.get("owner") if isinstance(report.get("owner"), dict) else {}
    entry_id = str(owner.get("entry_id") or DEFAULT_FANXIU_ENTRY_ID)
    start_signature = _asset_tree_signature_for_entry(entry_id)
    deadline = time.monotonic() + interval
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(2.0, max(0.1, remaining)))
        if _asset_tree_signature_for_entry(entry_id) != start_signature:
            return


def _default_doctor_watch_path() -> Path:
    from backend.core.temp_paths import codeyun_temp_root

    out_dir = codeyun_temp_root("fanxiu-watch")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return out_dir / f"doctor_watch_{stamp}.ndjson"


def _stable_doctor_watch_latest_path() -> Path:
    from backend.core.temp_paths import codeyun_temp_root

    return codeyun_temp_root("fanxiu-watch") / "doctor_watch_latest.json"


def _doctor_watch_heartbeat_path() -> Path:
    from backend.core.temp_paths import codeyun_temp_root

    return codeyun_temp_root("fanxiu-watch") / "doctor_watch_heartbeat.json"


def _write_doctor_watch_latest(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(event, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp_path.replace(path)


def _write_doctor_watch_heartbeat(
    *,
    output_path: Path,
    latest_path: Path,
    stable_latest_path: Path,
    iteration: int,
    event: dict[str, Any],
) -> None:
    path = _doctor_watch_heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "updated_at": time.time(),
        "updated_at_text": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "output_path": str(output_path),
        "latest_path": str(latest_path),
        "stable_latest_path": str(stable_latest_path),
        "iteration": iteration,
        "severity": event.get("severity"),
        "auto_run_due_enabled": bool(event.get("auto_run_due_enabled")),
        "due_task_count": event.get("due_task_count"),
        "stale_due_count": event.get("stale_due_count"),
        "stale_due_success_count": event.get("stale_due_success_count"),
        "blocked_due_count": event.get("blocked_due_count"),
        "visual_incomplete_count": event.get("visual_incomplete_count"),
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp_path.replace(path)


def _read_doctor_watch_heartbeat() -> dict[str, Any]:
    path = _doctor_watch_heartbeat_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"exists": path.exists(), "path": str(path), "active": False, "age_seconds": None}
    if not isinstance(payload, dict):
        return {"exists": True, "path": str(path), "active": False, "age_seconds": None}
    try:
        age_seconds = max(0.0, time.time() - float(payload.get("updated_at") or 0))
    except (TypeError, ValueError):
        age_seconds = None
    expected_stable_latest = str(_stable_doctor_watch_latest_path())
    actual_stable_latest = str(payload.get("stable_latest_path") or "")
    runtime_consistent = bool(actual_stable_latest) and actual_stable_latest == expected_stable_latest
    return {
        **payload,
        "exists": True,
        "path": str(path),
        "age_seconds": age_seconds,
        "runtime_consistent": runtime_consistent,
        "expected_stable_latest_path": expected_stable_latest,
    }


def _ensure_doctor_watch_background(
    *,
    interval_seconds: float,
    duration_seconds: float,
    log_limit: int,
    include_screenshot: bool,
    screenshot_every: int,
    stale_after_seconds: float,
    auto_run_due: bool,
) -> dict[str, Any]:
    heartbeat = _read_doctor_watch_heartbeat()
    age = heartbeat.get("age_seconds")
    capability_consistent = (not auto_run_due) or bool(heartbeat.get("auto_run_due_enabled"))
    if (
        isinstance(age, (int, float))
        and age <= stale_after_seconds
        and bool(heartbeat.get("runtime_consistent"))
        and capability_consistent
    ):
        return {
            "started": False,
            "reason": "heartbeat_recent",
            "heartbeat": heartbeat,
            "latest": read_doctor_watch_latest(),
        }

    from backend.core.temp_paths import codeyun_temp_root

    watch_dir = codeyun_temp_root("fanxiu-watch")
    watch_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = watch_dir / f"doctor_watch_background_{stamp}.ndjson"
    stdout_path = watch_dir / f"doctor_watch_background_{stamp}.stdout.log"
    stderr_path = watch_dir / f"doctor_watch_background_{stamp}.stderr.log"
    python_executable = Path(sys.executable)
    if os.name == "nt" and python_executable.name.lower() == "python.exe":
        pythonw_executable = python_executable.with_name("pythonw.exe")
        if pythonw_executable.is_file():
            python_executable = pythonw_executable
    command = [
        str(python_executable),
        str(Path(__file__).resolve()),
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
        command.append("--screenshot")
    if auto_run_due:
        command.append("--auto-run-due")

    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    stdout_fh = stdout_path.open("ab")
    stderr_fh = stderr_path.open("ab")
    child_env = os.environ.copy()
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    child_env.setdefault("PYTHONUTF8", "1")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=stdout_fh,
            stderr=stderr_fh,
            env=child_env,
            close_fds=(os.name != "nt"),
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
    finally:
        stdout_fh.close()
        stderr_fh.close()
    return {
        "started": True,
        "pid": process.pid,
        "reason": "heartbeat_missing_or_stale",
        "previous_heartbeat": heartbeat,
        "output_path": str(output_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "command": command,
    }


def _run_doctor_watch(
    *,
    interval_seconds: float,
    duration_seconds: float,
    max_iterations: int,
    log_limit: int,
    include_screenshot: bool,
    screenshot_every: int,
    output_path: Path | None,
    latest_json_path: Path | None,
    stop_on_blocked: bool,
    stop_on_ok_no_due: bool,
    auto_run_due: bool,
    auto_run_due_min_interval_seconds: float,
    auto_run_due_wait_timeout_seconds: float,
) -> int:
    path = output_path or _default_doctor_watch_path()
    latest_path = latest_json_path or path.with_suffix(".latest.json")
    stable_latest_path = _stable_doctor_watch_latest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    iteration = 0
    worst_code = 0
    last_auto_run_due_at = 0.0
    last_auto_run_due_key = ""
    while True:
        iteration += 1
        take_screenshot = bool(include_screenshot) and (screenshot_every <= 1 or (iteration - 1) % screenshot_every == 0)
        report = _build_doctor_report(log_limit=log_limit, include_screenshot=take_screenshot)
        maintenance = report.get("maintenance") if isinstance(report.get("maintenance"), dict) else {}
        if bool(include_screenshot) and not take_screenshot and str(maintenance.get("severity") or "") in {"blocked", "error"}:
            report = _build_doctor_report(log_limit=log_limit, include_screenshot=True)
        if auto_run_due and _watch_should_auto_run_due(report):
            report, last_auto_run_due_at, last_auto_run_due_key = _watch_auto_run_due_batch(
                report,
                log_limit=log_limit,
                include_screenshot=include_screenshot,
                take_screenshot=take_screenshot,
                min_interval_seconds=auto_run_due_min_interval_seconds,
                last_due_key=last_auto_run_due_key,
                last_due_at=last_auto_run_due_at,
                wait_timeout_seconds=auto_run_due_wait_timeout_seconds,
            )
        event = _doctor_watch_event(report, iteration=iteration)
        event["auto_run_due_enabled"] = bool(auto_run_due)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        _write_doctor_watch_latest(latest_path, event)
        if stable_latest_path != latest_path:
            _write_doctor_watch_latest(stable_latest_path, event)
        _write_doctor_watch_heartbeat(
            output_path=path,
            latest_path=latest_path,
            stable_latest_path=stable_latest_path,
            iteration=iteration,
            event=event,
        )
        print(
            "watch "
            f"#{iteration} "
            f"severity={event['severity']} "
            f"due={event['due_task_count']} "
            f"stale={event['stale_due_count']} "
            f"blocked_due={event['blocked_due_count']} "
            f"next={event['scheduler_next_action']} "
            f"auto_run_due={bool((event.get('auto_run_due') or {}).get('triggered'))} "
            f"path={path} "
            f"latest={latest_path} "
            f"stable_latest={stable_latest_path}",
            flush=True,
        )
        code = _doctor_exit_code(report, strict=True)
        worst_code = max(worst_code, code)
        if stop_on_blocked and str(event["severity"]) in {"blocked", "error"}:
            return code
        if stop_on_ok_no_due and str(event["severity"]) == "ok" and int(event["due_task_count"] or 0) == 0:
            return worst_code
        if max_iterations > 0 and iteration >= max_iterations:
            return worst_code
        if duration_seconds > 0 and time.monotonic() - started >= duration_seconds:
            return worst_code
        _watch_sleep_until_next_check(report, interval_seconds=interval_seconds)


def _build_doctor_report(*, log_limit: int, include_screenshot: bool) -> dict[str, Any]:
    owner = read_fanxiu_behavior_tree_service_owner()
    runtime_status = fanxiu_data_annotation_runtime_status()
    jobs = fanxiu_data_annotation_task_cells()
    isolation = read_fanxiu_job_group_isolation()
    entry_id = str(owner.get("entry_id") or DEFAULT_FANXIU_ENTRY_ID)
    scheduler_plan = build_scheduler_plan(
        entry=resolve_fanxiu_entry(entry_id),
        entry_id=entry_id,
        asset_tree_path=data_annotation_asset_tree_path(entry_id),
    )
    enabled_tasks = [
        _scheduler_task_summary(task)
        for task in read_scheduler_tasks()
        if bool(task.get("enabled"))
    ]
    report: dict[str, Any] = {
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "owner": owner,
        "runtime": _runtime_status_summary(runtime_status),
        "task_cells": jobs,
        "isolation": isolation,
        "scheduler": {
            "next_action": scheduler_plan.get("next_action"),
            "message": scheduler_plan.get("message"),
            "job_group_enabled": scheduler_plan.get("job_group_enabled"),
            "due_tasks": scheduler_plan.get("due_tasks") or [],
            "enabled_tasks": enabled_tasks,
            "daily_audit": scheduler_plan.get("daily_audit") or {},
        },
        "relevant_logs": _doctor_relevant_logs(max(1, int(log_limit or 80))),
    }
    if include_screenshot:
        try:
            report["screenshot"] = _doctor_screenshot()
            report["blocking_overlays"] = _doctor_blocking_overlays(report.get("screenshot"))
        except Exception as exc:
            report["screenshot_error"] = str(exc)
    if "blocking_overlays" not in report:
        plan_blockers = scheduler_plan.get("blocking_overlays")
        if isinstance(plan_blockers, list):
            report["blocking_overlays"] = plan_blockers
    report["maintenance"] = _build_maintenance_summary(report)
    return report


def _add_task_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--run-mode",
        choices=["auto", "direct"],
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--wait", action="store_true", default=argparse.SUPPRESS, help="如果任务进入队列，则等待 queued job 完成")
    parser.add_argument("--wait-timeout-seconds", type=float, default=argparse.SUPPRESS)
    parser.add_argument("--tick-seconds", type=float, default=0.2, help=argparse.SUPPRESS)


def main() -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(description="本地提交凡修行为树任务到 resident kernel。")
    parser.add_argument("--entry-id", default=os.environ.get("FANXIU_ENTRY_ID") or DEFAULT_FANXIU_ENTRY_ID)
    parser.add_argument("--no-isolate-jobs", action="store_true", help="本次运行期间不隔离工程作业")
    parser.add_argument("--timeout-seconds", type=float, default=0)
    parser.add_argument("--payload", default="", help="附加 payload JSON")
    parser.add_argument("--wait", action="store_true", help="如果任务进入队列，则等待 queued job 完成")
    parser.add_argument("--wait-timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--run-mode",
        choices=["auto", "direct"],
        default="auto",
        help=argparse.SUPPRESS,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    go_scene = subparsers.add_parser("go-scene", help="到达指定场景")
    go_scene.add_argument("scene_id")
    _add_task_run_options(go_scene)

    mail_check = subparsers.add_parser("mail-check", help="运行邮件_清理")
    mail_check.add_argument("--observe-only", action="store_true")
    mail_check.add_argument("--scan-mode", default="incremental")
    mail_check.add_argument("--skip-capture", action="store_true")
    mail_check.add_argument("--max-actions", type=int, default=0)
    _add_task_run_options(mail_check)

    task = subparsers.add_parser("task", help="运行任意已注册任务类型")
    task.add_argument("task_type")
    task.add_argument("--target-scene-id", default="", help="task_type=go_scene 时的目标场景")
    _add_task_run_options(task)

    run_task = subparsers.add_parser("run", help="提交并等待一个 task cell 完成")
    run_task.add_argument("task_type")
    run_task.add_argument("--target-scene-id", default="", help="task_type=go_scene 时的目标场景")
    run_task.add_argument("--wait-timeout-seconds", type=float, default=300.0)

    code_cell = subparsers.add_parser("code-cell", help="提交一段 Python code cell 到 Runtime kernel")
    code_cell.add_argument("code", nargs="?", default="", help="要执行的 Python 代码；也可用 --file")
    code_cell.add_argument("--file", default="", help="从文件读取 Python 代码")
    code_cell.add_argument("--mode", choices=["readonly", "act"], default="readonly")
    code_cell.add_argument("--max-output-chars", type=int, default=4000)
    code_cell.add_argument("--isolation-ttl-seconds", type=float, default=300.0, help=argparse.SUPPRESS)
    code_cell.add_argument("--wait", action="store_true", help="等待 code cell 完成")
    code_cell.add_argument("--wait-timeout-seconds", type=float, default=300.0)

    cell = subparsers.add_parser("cell", help="提交并等待一段 Python cell；凡修调试的默认入口")
    cell.add_argument("code", nargs="?", default="", help="要执行的 Python 代码；也可用 --file")
    cell.add_argument("--file", default="", help="从文件读取 Python 代码")
    cell.add_argument("--mode", choices=["readonly", "act"], default="readonly")
    cell.add_argument("--max-output-chars", type=int, default=4000)
    cell.add_argument("--isolation-ttl-seconds", type=float, default=300.0, help=argparse.SUPPRESS)
    cell.add_argument("--wait-timeout-seconds", type=float, default=300.0)

    py_cell = subparsers.add_parser("py", help="提交并等待一段 Python code cell 完成")
    py_cell.add_argument("code", nargs="?", default="", help="要执行的 Python 代码；也可用 --file")
    py_cell.add_argument("--file", default="", help="从文件读取 Python 代码")
    py_cell.add_argument("--mode", choices=["readonly", "act"], default="readonly")
    py_cell.add_argument("--max-output-chars", type=int, default=4000)
    py_cell.add_argument("--isolation-ttl-seconds", type=float, default=300.0, help=argparse.SUPPRESS)
    py_cell.add_argument("--wait-timeout-seconds", type=float, default=300.0)

    tasks = subparsers.add_parser("tasks", help="查看本地已注册作业类型")
    tasks.add_argument("--json", action="store_true", help="输出 JSON")

    service = subparsers.add_parser("service", help="启动本地前台常驻行为树服务")
    service.add_argument("--tick-seconds", type=float, default=1.0)
    service.add_argument("--duration-seconds", type=float, default=0.0, help="默认一直运行，直到 Ctrl+C")

    stop = subparsers.add_parser("stop", help="请求 resident service 停止当前任务")
    stop.add_argument("--reason", default="local_cli")

    queue = subparsers.add_parser("queue", help="查看本地 task cell 队列")
    queue.add_argument("--json", action="store_true", help="输出 JSON")

    cancel = subparsers.add_parser("cancel", help="取消本地 task cell 队列中的任务")
    cancel.add_argument("job_id")
    cancel.add_argument("--force", action="store_true", help="允许删除 running 记录；停止执行仍应优先用 stop")

    clear_queue = subparsers.add_parser("clear-queue", help="清空本地 task cell 队列")
    clear_queue.add_argument("--force", action="store_true", help="同时删除 running 记录")

    status_parser = subparsers.add_parser("status", help="查看本地 Runtime 状态")
    status_parser.add_argument("--raw", action="store_true", help="输出完整 JSON")

    logs_parser = subparsers.add_parser("logs", help="查看本地 Runtime 日志")
    logs_parser.add_argument("--limit", type=int, default=80)
    logs_parser.add_argument("--scope", default="")
    logs_parser.add_argument("--item-id", default="")
    logs_parser.add_argument("--json", action="store_true", help="输出 JSON")

    doctor = subparsers.add_parser("doctor", help="只读巡检 owner/runtime/task cell 队列/Scheduler/关键日志")
    doctor.add_argument("--log-limit", type=int, default=80)
    doctor.add_argument("--screenshot", action="store_true", help="额外保存一张真实 ADB 当前帧")
    doctor.add_argument("--json", action="store_true", help="输出完整 JSON")
    doctor.add_argument("--summary", action="store_true", help="输出适合巡检脚本读取的摘要")
    doctor.add_argument("--exit-code", action="store_true", help="按 maintenance.severity 返回退出码：ok=0 attention=1 blocked/error=2")

    watch_doctor = subparsers.add_parser("watch-doctor", help="持续巡检并写入 NDJSON 留痕")
    watch_doctor.add_argument("--interval-seconds", type=float, default=60.0)
    watch_doctor.add_argument("--duration-seconds", type=float, default=0.0, help="默认不按时间停止")
    watch_doctor.add_argument("--max-iterations", type=int, default=0, help="默认不按次数停止")
    watch_doctor.add_argument("--log-limit", type=int, default=80)
    watch_doctor.add_argument("--screenshot", action="store_true", help="巡检时保存真实 ADB 当前帧")
    watch_doctor.add_argument("--screenshot-every", type=int, default=10, help="启用 --screenshot 后每 N 次保存一张，默认 10")
    watch_doctor.add_argument("--output", default="", help="NDJSON 输出路径，默认写入系统临时目录")
    watch_doctor.add_argument("--latest-json", default="", help="最新巡检快照 JSON 路径，默认与 NDJSON 同名 .latest.json")
    watch_doctor.add_argument("--stop-on-blocked", action="store_true", help="发现 blocked/error 立即退出")
    watch_doctor.add_argument("--stop-on-ok-no-due", action="store_true", help="severity=ok 且无到期任务时退出")
    watch_doctor.add_argument("--auto-run-due", action="store_true", help="无阻断且有到期任务时调用 Scheduler run-due")
    watch_doctor.add_argument("--auto-run-due-min-interval-seconds", type=float, default=300.0, help="同一批到期任务自动 run-due 的最小间隔")
    watch_doctor.add_argument("--auto-run-due-wait-timeout-seconds", type=float, default=900.0, help="AI 保底提交 queued job 后等待真实执行完成的超时")

    ensure_watch_doctor = subparsers.add_parser("ensure-watch-doctor", help="确保后台巡检进程存在，心跳过期则自动拉起")
    ensure_watch_doctor.add_argument("--interval-seconds", type=float, default=60.0)
    ensure_watch_doctor.add_argument("--duration-seconds", type=float, default=0.0, help="后台巡检运行时长，默认一直运行")
    ensure_watch_doctor.add_argument("--log-limit", type=int, default=80)
    ensure_watch_doctor.add_argument("--screenshot", action="store_true", help="后台巡检保存真实 ADB 当前帧")
    ensure_watch_doctor.add_argument("--screenshot-every", type=int, default=10)
    ensure_watch_doctor.add_argument("--stale-after-seconds", type=float, default=180.0, help="心跳超过该时长视为后台巡检失效")
    ensure_watch_doctor.add_argument("--auto-run-due", action="store_true", help="允许后台巡检自动触发 run-due；默认只观察")
    ensure_watch_doctor.add_argument("--no-auto-run-due", action="store_true", help=argparse.SUPPRESS)

    reset_scheduler_runs = subparsers.add_parser("reset-scheduler-runs", help="重置 Scheduler 作业运行结论，让作业重新按到期规则验收")
    reset_scheduler_runs.add_argument("--task-id", action="append", default=[], help="只重置指定 task id；可重复传入")
    reset_scheduler_runs.add_argument("--include-disabled", action="store_true", help="同时重置未启用作业的运行结论；不会启用它们")
    reset_scheduler_runs.add_argument("--include-manual", action="store_true", help="同时重置 manual schedule_kind 作业")
    reset_scheduler_runs.add_argument("--keep-next-time", action="store_true", help=argparse.SUPPRESS)
    reset_scheduler_runs.add_argument("--clear-next-time", action="store_true", help="同时清空 next_time，让作业重新按到期规则验收")
    reset_scheduler_runs.add_argument("--force", action="store_true", help="确认执行重置")
    reset_scheduler_runs.add_argument("--json", action="store_true", help="输出 JSON")

    owner_parser = subparsers.add_parser("owner", help="查看行为树全局单例 owner")
    owner_parser.add_argument("--stale-after-seconds", type=float, default=120.0)
    owner_parser.add_argument("--json", action="store_true", help="输出完整 JSON")

    isolation = subparsers.add_parser("isolation", help="查看工程作业隔离锁")
    isolation.add_argument("--json", action="store_true", help="输出 JSON")
    isolation.add_argument("--clear-stale", action="store_true", help="清理已过期隔离锁")

    isolate = subparsers.add_parser("isolate", help="手动隔离工程作业")
    isolate.add_argument("--reason", default="local_cli")
    isolate.add_argument("--ttl-seconds", type=float, default=300.0)

    release_isolation = subparsers.add_parser("release-isolation", help="按 token 释放工程作业隔离锁")
    release_isolation.add_argument("token")

    subparsers.add_parser("clear-logs", help="清空本地 Runtime 日志")

    args = parser.parse_args()
    if args.command == "status":
        status = fanxiu_data_annotation_runtime_status()
        if args.raw:
            print(json.dumps(status, ensure_ascii=False, indent=2, default=str))
        else:
            _print_status(status)
        return 0
    if args.command == "logs":
        entries = fanxiu_data_annotation_runtime_logs(
            limit=int(args.limit or 80),
            scope=str(args.scope or ""),
            item_id=str(args.item_id or ""),
        )
        if args.json:
            print(json.dumps(entries, ensure_ascii=False, indent=2, default=str))
        else:
            _print_log_entries(entries)
        return 0
    if args.command == "doctor":
        report = _build_doctor_report(log_limit=int(args.log_limit or 80), include_screenshot=bool(args.screenshot))
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        elif args.summary:
            _print_doctor_summary(report)
        else:
            print(json.dumps(
                {
                    "checked_at": report.get("checked_at"),
                    "owner": {
                        "active": bool((report.get("owner") or {}).get("active")),
                        "stale": bool((report.get("owner") or {}).get("stale")),
                        "pid": (report.get("owner") or {}).get("pid"),
                        "step": (report.get("owner") or {}).get("step"),
                    },
                    "runtime": report.get("runtime"),
                    "task_cell_queue_size": len(report.get("task_cells") or []),
                    "isolation_active": bool((report.get("isolation") or {}).get("active")),
                    "scheduler": report.get("scheduler"),
                    "screenshot": report.get("screenshot") or report.get("screenshot_error") or "",
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ))
            _print_log_entries([item for item in report.get("relevant_logs") or [] if isinstance(item, dict)][-12:])
        return _doctor_exit_code(report, strict=bool(args.exit_code))
    if args.command == "watch-doctor":
        output_path = Path(str(args.output)).expanduser() if str(args.output or "") else None
        latest_json_path = Path(str(args.latest_json)).expanduser() if str(args.latest_json or "") else None
        return _run_doctor_watch(
            interval_seconds=float(args.interval_seconds or 60.0),
            duration_seconds=float(args.duration_seconds or 0.0),
            max_iterations=int(args.max_iterations or 0),
            log_limit=int(args.log_limit or 80),
            include_screenshot=bool(args.screenshot),
            screenshot_every=max(1, int(args.screenshot_every or 1)),
            output_path=output_path,
            latest_json_path=latest_json_path,
            stop_on_blocked=bool(args.stop_on_blocked),
            stop_on_ok_no_due=bool(args.stop_on_ok_no_due),
            auto_run_due=bool(args.auto_run_due),
            auto_run_due_min_interval_seconds=max(1.0, float(args.auto_run_due_min_interval_seconds or 300.0)),
            auto_run_due_wait_timeout_seconds=max(1.0, float(args.auto_run_due_wait_timeout_seconds or 900.0)),
        )
    if args.command == "ensure-watch-doctor":
        result = _ensure_doctor_watch_background(
            interval_seconds=float(args.interval_seconds or 60.0),
            duration_seconds=float(args.duration_seconds or 0.0),
            log_limit=int(args.log_limit or 80),
            include_screenshot=bool(args.screenshot),
            screenshot_every=max(1, int(args.screenshot_every or 1)),
            stale_after_seconds=max(1.0, float(args.stale_after_seconds or 180.0)),
            auto_run_due=bool(args.auto_run_due) and not bool(args.no_auto_run_due),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "reset-scheduler-runs":
        if not bool(args.force):
            print("拒绝执行：reset-scheduler-runs 需要 --force 明确确认")
            return 2
        result = reset_scheduler_task_runs(
            task_ids=[str(item) for item in (args.task_id or [])],
            include_disabled=bool(args.include_disabled),
            include_manual=bool(args.include_manual),
            clear_next_time=bool(args.clear_next_time) and not bool(args.keep_next_time),
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            print(
                f"已重置 {result.get('reset_count')} 个 Scheduler 作业运行结论；"
                f"backup={result.get('backup_path')}"
            )
            for task_id in result.get("reset_ids") or []:
                print(f"- {task_id}")
        return 0
    if args.command == "owner":
        owner = read_fanxiu_behavior_tree_service_owner(stale_after_seconds=float(args.stale_after_seconds or 30.0))
        if args.json:
            print(json.dumps(owner, ensure_ascii=False, indent=2, default=str))
        else:
            _print_owner(owner)
        return 0 if bool(owner.get("active")) else 1
    if args.command == "isolation":
        status = clear_stale_fanxiu_job_group_isolation() if bool(args.clear_stale) else read_fanxiu_job_group_isolation()
        print(json.dumps(status, ensure_ascii=False, indent=2, default=str))
        return 0 if bool(status.get("active")) or bool(status.get("cleared")) or not bool(status.get("exists")) else 1
    if args.command == "isolate":
        token = acquire_fanxiu_job_group_isolation(
            reason=str(args.reason or "local_cli"),
            ttl_seconds=float(args.ttl_seconds or 300.0),
        )
        print(json.dumps(read_fanxiu_job_group_isolation(), ensure_ascii=False, indent=2, default=str))
        print(f"token={token}")
        return 0
    if args.command == "release-isolation":
        release_fanxiu_job_group_isolation(str(args.token or ""))
        print(json.dumps(read_fanxiu_job_group_isolation(), ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "queue":
        jobs = fanxiu_data_annotation_task_cells()
        if args.json:
            print(json.dumps(jobs, ensure_ascii=False, indent=2, default=str))
        else:
            _print_task_cells(jobs)
        return 0
    if args.command == "tasks":
        items = fanxiu_data_annotation_task_cell_catalog()
        if args.json:
            print(json.dumps(items, ensure_ascii=False, indent=2, default=str))
        else:
            _print_job_catalog(items)
        return 0
    if args.command == "stop":
        request = request_fanxiu_behavior_tree_stop(entry_id=str(args.entry_id), reason=str(args.reason or "local_cli"))
        print(json.dumps(request, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "cancel":
        result = cancel_fanxiu_task_cell(str(args.job_id), force=bool(args.force))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if bool(result.get("cancelled")) else 1
    if args.command == "clear-queue":
        result = clear_fanxiu_task_cells(force=bool(args.force))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command in {"cell", "code-cell", "py"}:
        code = str(args.code or "")
        if args.file:
            code = Path(args.file).read_text(encoding="utf-8")
        if not code.strip():
            raise SystemExit(f"{args.command} 需要提供代码或 --file")
        kernel = FanxiuKernel(
            entry_id=str(args.entry_id),
            isolate_jobs=not bool(args.no_isolate_jobs),
        )
        cell_factory = kernel.cell if args.command == "cell" else kernel.code
        cell = cell_factory(
            code,
            mode=str(args.mode or "readonly"),
            timeout_seconds=float(args.timeout_seconds or 120.0),
            max_output_chars=int(args.max_output_chars or 4000),
        )
        if args.command in {"cell", "py"}:
            status = cell.run(timeout_seconds=float(args.wait_timeout_seconds or 300.0))
        else:
            status = cell.run(timeout_seconds=float(args.wait_timeout_seconds or 300.0)) if bool(args.wait) else cell.submit()
        _print_status(status)
        return 0 if str(status.get("status") or "") not in {"error", "stopped"} else 1
    if args.command == "clear-logs":
        clear_fanxiu_data_annotation_runtime_logs()
        print("Runtime 日志已清空")
        return 0
    if args.command == "service":
        if float(args.duration_seconds or 0.0) > 0:
            raise SystemExit("Jupyter kernel service 不支持 --duration-seconds；请通过 stop/restart 控制生命周期")
        run_fanxiu_jupyter_kernel_service(
            entry_id=str(args.entry_id),
            tick_seconds=float(args.tick_seconds or 1.0),
        )
        return 0
    task_type, payload = _payload_from_args(args)
    _apply_wait_timeout_as_runtime_budget(args, payload)
    kernel = FanxiuKernel(
        entry_id=str(args.entry_id),
        isolate_jobs=not bool(args.no_isolate_jobs),
    )
    if args.command == "run":
        status = kernel.task(task_type, payload).run(timeout_seconds=float(args.wait_timeout_seconds or 300.0))
        _print_status(status)
        return 0 if str(status.get("status") or "") not in {"error", "stopped"} else 1
    run_mode = str(args.run_mode or "auto")
    wait = bool(args.wait) or run_mode == "direct"
    cell = kernel.task(task_type, payload)
    status = cell.run(timeout_seconds=float(args.wait_timeout_seconds or 300.0)) if wait else cell.submit()
    if wait and run_mode != "direct":
        print(json.dumps(status, ensure_ascii=False, indent=2, default=str))
        runtime_status = status.get("runtime_status") if isinstance(status.get("runtime_status"), dict) else {}
        return 0 if bool(status.get("done")) and str(runtime_status.get("status") or "") not in {"error", "stopped"} else 1
    _print_status(status)
    if run_mode == "direct":
        return 0 if str(status.get("status") or "") not in {"error", "stopped"} else 1
    return 0 if str(status.get("status") or "") not in {"error"} else 1


if __name__ == "__main__":
    raise SystemExit(main())


