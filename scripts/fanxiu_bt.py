from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.fanxiu_behavior_tree import (
    DEFAULT_FANXIU_ENTRY_ID,
    FanxiuLocalEnqueueRequest,
    FanxiuLocalRunRequest,
    FanxiuLocalServiceRequest,
    acquire_fanxiu_job_group_isolation,
    cancel_fanxiu_local_manual_job,
    clear_fanxiu_local_manual_jobs,
    clear_stale_fanxiu_job_group_isolation,
    enqueue_fanxiu_local_manual_job,
    clear_fanxiu_data_annotation_runtime_logs,
    fanxiu_data_annotation_manual_job_catalog,
    fanxiu_data_annotation_manual_jobs,
    fanxiu_data_annotation_runtime_logs,
    fanxiu_data_annotation_runtime_status,
    fanxiu_local_task_should_enqueue,
    read_fanxiu_job_group_isolation,
    read_fanxiu_behavior_tree_service_owner,
    release_fanxiu_job_group_isolation,
    request_fanxiu_behavior_tree_stop,
    run_fanxiu_local_service,
    run_fanxiu_local_task,
    wait_fanxiu_local_manual_job,
)
from backend.core.fanxiu_data_annotation_jobs import parse_data_annotation_scene_id


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
    if args.command == "task":
        return str(args.task_type), payload
    raise SystemExit(f"未知命令：{args.command}")


def _print_log_entries(entries: list[dict[str, Any]]) -> None:
    for item in entries:
        kind = item.get("kind") or "info"
        scope = item.get("scope") or ""
        item_id = item.get("item_id") or ""
        prefix = " ".join(part for part in [str(item.get("time") or ""), str(kind), str(scope), str(item_id)] if part)
        message = item.get("message") or ""
        print(f"{prefix}: {message}".strip())


def _print_status(status: dict[str, Any]) -> None:
    print(json.dumps(
        {
            "status": status.get("status"),
            "phase": status.get("phase"),
            "task_type": status.get("task_type"),
            "current_scene": status.get("current_scene"),
            "message": status.get("message"),
            "error": status.get("error"),
            "queued_job": status.get("queued_job") or {},
        },
        ensure_ascii=False,
        indent=2,
    ))
    logs = [item for item in status.get("logs") or [] if isinstance(item, dict)]
    _print_log_entries(logs[-12:])


def _task_should_enqueue(run_mode: str) -> bool:
    return fanxiu_local_task_should_enqueue(run_mode)


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


def _print_manual_jobs(jobs: list[dict[str, Any]]) -> None:
    if not jobs:
        print("手动作业队列为空")
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


def _wait_and_print_queued_job(status: dict[str, Any], timeout_seconds: float) -> int:
    queued_job = status.get("queued_job") if isinstance(status.get("queued_job"), dict) else {}
    job_id = str(queued_job.get("id") or "")
    if not job_id:
        print("没有 queued_job.id，无法等待")
        return 1
    result = wait_fanxiu_local_manual_job(job_id, timeout_seconds=float(timeout_seconds or 300.0))
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


def _add_task_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--run-mode",
        choices=["auto", "direct", "enqueue"],
        default=argparse.SUPPRESS,
        help="执行方式：auto 在已有 resident owner 时排队，否则直跑",
    )
    parser.add_argument("--wait", action="store_true", default=argparse.SUPPRESS, help="如果任务进入队列，则等待 queued job 完成")
    parser.add_argument("--wait-timeout-seconds", type=float, default=argparse.SUPPRESS)


def main() -> int:
    parser = argparse.ArgumentParser(description="本地运行凡修行为树任务，不经过前端手动作业队列。")
    parser.add_argument("--entry-id", default=os.environ.get("FANXIU_ENTRY_ID") or DEFAULT_FANXIU_ENTRY_ID)
    parser.add_argument("--no-isolate-jobs", action="store_true", help="本次运行期间不隔离普通作业组")
    parser.add_argument("--timeout-seconds", type=float, default=0)
    parser.add_argument("--payload", default="", help="附加 payload JSON")
    parser.add_argument("--wait", action="store_true", help="如果任务进入队列，则等待 queued job 完成")
    parser.add_argument("--wait-timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--run-mode",
        choices=["auto", "direct", "enqueue"],
        default="auto",
        help="go-scene/mail-check/task 的执行方式：auto 在已有 resident owner 时排队，否则直跑",
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
    _add_task_run_options(task)

    tasks = subparsers.add_parser("tasks", help="查看本地已注册作业类型")
    tasks.add_argument("--json", action="store_true", help="输出 JSON")

    service = subparsers.add_parser("service", help="启动本地前台常驻行为树服务")
    service.add_argument("--tick-seconds", type=float, default=1.0)
    service.add_argument("--duration-seconds", type=float, default=0.0, help="默认一直运行，直到 Ctrl+C")

    stop = subparsers.add_parser("stop", help="请求 resident service 停止当前任务")
    stop.add_argument("--reason", default="local_cli")

    enqueue = subparsers.add_parser("enqueue", help="写入本地手动作业队列，由 resident service 串行执行")
    enqueue.add_argument("task_type")
    enqueue.add_argument("--label", default="")
    enqueue.add_argument("--target-scene-id", default="")
    enqueue.add_argument("--interruptible", action="store_true")
    enqueue.add_argument("--non-interruptible", action="store_true")
    enqueue.add_argument("--isolation-ttl-seconds", type=float, default=300.0)
    enqueue.add_argument("--wait", action="store_true", help="等待 queued job 完成")
    enqueue.add_argument("--wait-timeout-seconds", type=float, default=300.0)

    queue = subparsers.add_parser("queue", help="查看本地手动作业队列")
    queue.add_argument("--json", action="store_true", help="输出 JSON")

    cancel = subparsers.add_parser("cancel", help="取消本地手动作业队列中的任务")
    cancel.add_argument("job_id")
    cancel.add_argument("--force", action="store_true", help="允许删除 running 记录；停止执行仍应优先用 stop")

    clear_queue = subparsers.add_parser("clear-queue", help="清空本地手动作业队列")
    clear_queue.add_argument("--force", action="store_true", help="同时删除 running 记录")

    status_parser = subparsers.add_parser("status", help="查看本地 Runtime 状态")
    status_parser.add_argument("--raw", action="store_true", help="输出完整 JSON")

    logs_parser = subparsers.add_parser("logs", help="查看本地 Runtime 日志")
    logs_parser.add_argument("--limit", type=int, default=80)
    logs_parser.add_argument("--scope", default="")
    logs_parser.add_argument("--item-id", default="")
    logs_parser.add_argument("--json", action="store_true", help="输出 JSON")

    owner_parser = subparsers.add_parser("owner", help="查看行为树全局单例 owner")
    owner_parser.add_argument("--stale-after-seconds", type=float, default=120.0)
    owner_parser.add_argument("--json", action="store_true", help="输出完整 JSON")

    isolation = subparsers.add_parser("isolation", help="查看普通作业组隔离锁")
    isolation.add_argument("--json", action="store_true", help="输出 JSON")
    isolation.add_argument("--clear-stale", action="store_true", help="清理已过期隔离锁")

    isolate = subparsers.add_parser("isolate", help="手动隔离普通作业组")
    isolate.add_argument("--reason", default="local_cli")
    isolate.add_argument("--ttl-seconds", type=float, default=300.0)

    release_isolation = subparsers.add_parser("release-isolation", help="按 token 释放普通作业组隔离锁")
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
        jobs = fanxiu_data_annotation_manual_jobs()
        if args.json:
            print(json.dumps(jobs, ensure_ascii=False, indent=2, default=str))
        else:
            _print_manual_jobs(jobs)
        return 0
    if args.command == "tasks":
        items = fanxiu_data_annotation_manual_job_catalog()
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
        result = cancel_fanxiu_local_manual_job(str(args.job_id), force=bool(args.force))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if bool(result.get("cancelled")) else 1
    if args.command == "clear-queue":
        result = clear_fanxiu_local_manual_jobs(force=bool(args.force))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "enqueue":
        payload: dict[str, Any] = {}
        if args.payload:
            try:
                payload.update(json.loads(args.payload))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"--payload 不是合法 JSON：{exc}") from exc
        if args.target_scene_id:
            payload["target_scene_id"] = parse_data_annotation_scene_id(args.target_scene_id)
        if args.timeout_seconds:
            payload["timeout_seconds"] = float(args.timeout_seconds)
        interruptible = None
        if bool(args.interruptible) and bool(args.non_interruptible):
            raise SystemExit("--interruptible 和 --non-interruptible 不能同时使用")
        if bool(args.interruptible):
            interruptible = True
        if bool(args.non_interruptible):
            interruptible = False
        status = enqueue_fanxiu_local_manual_job(FanxiuLocalEnqueueRequest(
            entry_id=str(args.entry_id),
            task_type=str(args.task_type),
            payload=payload,
            label=str(args.label or ""),
            interruptible=interruptible,
            isolate_jobs=not bool(args.no_isolate_jobs),
            isolation_ttl_seconds=float(args.isolation_ttl_seconds or 300.0),
        ))
        _print_status(status)
        if bool(args.wait):
            return _wait_and_print_queued_job(status, float(args.wait_timeout_seconds or 300.0))
        return 0 if str(status.get("status") or "") not in {"error"} else 1
    if args.command == "clear-logs":
        clear_fanxiu_data_annotation_runtime_logs()
        print("Runtime 日志已清空")
        return 0
    if args.command == "service":
        status = run_fanxiu_local_service(FanxiuLocalServiceRequest(
            entry_id=str(args.entry_id),
            tick_seconds=float(args.tick_seconds or 1.0),
            duration_seconds=float(args.duration_seconds or 0.0),
        ))
        _print_status(status)
        if bool(args.wait):
            return _wait_and_print_queued_job(status, float(args.wait_timeout_seconds or 300.0))
        return 0 if str(status.get("status") or "") not in {"error"} else 1
    task_type, payload = _payload_from_args(args)
    if _task_should_enqueue(str(args.run_mode)):
        status = enqueue_fanxiu_local_manual_job(FanxiuLocalEnqueueRequest(
            task_type=task_type,
            payload=payload,
            entry_id=str(args.entry_id),
            label="",
            isolate_jobs=not bool(args.no_isolate_jobs),
        ))
        _print_status(status)
        if bool(args.wait):
            return _wait_and_print_queued_job(status, float(args.wait_timeout_seconds or 300.0))
        return 0 if str(status.get("status") or "") not in {"error"} else 1
    status = run_fanxiu_local_task(FanxiuLocalRunRequest(
        task_type=task_type,
        payload=payload,
        entry_id=str(args.entry_id),
        isolate_jobs=not bool(args.no_isolate_jobs),
    ))
    _print_status(status)
    return 0 if str(status.get("status") or "") not in {"error", "stopped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
