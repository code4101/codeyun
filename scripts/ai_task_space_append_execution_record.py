from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlmodel import Session, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.ai_task_space import (
    ExecutionPacketReplayConflict,
    ExecutionSnapshotMismatch,
    append_execution_record,
    mutate_task_space,
    task_space_fingerprint,
    user_task_space_path,
)
from backend.db import engine
from backend.models import User


class WritebackCliError(Exception):
    def __init__(self, code: str, message: str, **extra: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _find_user(username: str | None) -> User:
    with Session(engine) as session:
        statement = select(User).where(User.is_active == True)  # noqa: E712
        if username:
            statement = statement.where(User.username == username)
        else:
            statement = statement.order_by(User.is_superuser.desc(), User.id)
        user = session.exec(statement).first()
        if user is None:
            raise SystemExit(f"未找到可用账号：{username or '<默认账号>'}")
        return user


def _resolve_task_id(task_space: dict, task_id: str, task_title: str) -> str:
    if bool(task_id) == bool(task_title):
        raise WritebackCliError("invalid_target", "必须且只能提供 --task-id 或 --task-title。")
    if task_id:
        return task_id

    title = task_title.strip()
    matches = [task for task in task_space.get("tasks", []) if str(task.get("title") or "").strip() == title]
    if not matches:
        raise WritebackCliError("task_title_not_found", f"未找到标题为「{title}」的任务。", task_title=title)
    if len(matches) > 1:
        candidates = "\n".join(
            f"- {task.get('id')} [{task.get('status')}] {task.get('title')}"
            for task in matches[:12]
        )
        raise WritebackCliError(
            "ambiguous_task_title",
            f"标题「{title}」匹配到多个任务，请改用 --task-id：\n{candidates}",
            task_title=title,
            candidates=[{"id": task.get("id"), "status": task.get("status")} for task in matches[:12]],
        )
    return str(matches[0]["id"])


def _enforce_budget_limit(label: str, used: int, limit: int | None) -> None:
    if limit is None:
        return
    if limit < 0:
        raise WritebackCliError("negative_budget_limit", f"{label} 上限不能为负数：{limit}", label=label, limit=limit)
    if used > limit:
        raise WritebackCliError(
            "budget_overrun",
            f"{label} 超出执行包预算：实际 {used}，上限 {limit}。请停止本轮并重新运行规划检查。",
            label=label,
            used=used,
            limit=limit,
        )


def _abort(args: argparse.Namespace, code: str, message: str, **extra: object) -> None:
    if args.json:
        print(json.dumps({"ok": False, "code": code, "message": message, **extra}, ensure_ascii=False, indent=2))
    else:
        print(message, file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Append an AI task-space execution record.")
    parser.add_argument("--username", help="CodeYun username. Defaults to the first active superuser.")
    parser.add_argument("--task-id", default="", help="Task id to update. Use this for execution-packet writeback.")
    parser.add_argument(
        "--task-title",
        default="",
        help="Exact task title to update. Intended for manual/system progress writeback; fails if not unique.",
    )
    parser.add_argument("--summary", required=True, help="Current board-state summary after this execution.")
    parser.add_argument("--verification", default="", help="Verification command, result, or reason it was not run.")
    parser.add_argument("--remaining-risk", default="", help="Remaining risk, dependency, or review need.")
    parser.add_argument("--next-step", default="", help="Next smallest step for a future planning check.")
    parser.add_argument("--status", choices=["progress", "done", "blocked"], default="progress")
    parser.add_argument("--packet-id", default="", help="Execution packet id used for this writeback.")
    parser.add_argument(
        "--expected-task-updated-at",
        default="",
        help="Abort if the task has changed since the execution packet snapshot.",
    )
    parser.add_argument("--steps-done", type=int, default=0, help="Number of execution steps used.")
    parser.add_argument("--commands-run", type=int, default=0, help="Number of commands run.")
    parser.add_argument("--files-changed", type=int, default=0, help="Number of files changed.")
    parser.add_argument("--max-steps", type=int, default=None, help="Execution packet maxSteps guard.")
    parser.add_argument("--max-commands", type=int, default=None, help="Execution packet maxCommands guard.")
    parser.add_argument("--max-files-changed", type=int, default=None, help="Execution packet maxFilesChanged guard.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    try:
        _enforce_budget_limit("执行步骤", args.steps_done, args.max_steps)
        _enforce_budget_limit("命令数", args.commands_run, args.max_commands)
        _enforce_budget_limit("文件改动数", args.files_changed, args.max_files_changed)
    except WritebackCliError as exc:
        _abort(args, exc.code, exc.message, **exc.extra)

    user = _find_user(args.username)
    path = user_task_space_path(user.id)
    task_id = ""

    def _append_record(current: dict) -> dict:
        nonlocal task_id
        task_id = _resolve_task_id(current, args.task_id.strip(), args.task_title)
        return append_execution_record(
            current,
            task_id,
            summary=args.summary,
            verification=args.verification,
            remaining_risk=args.remaining_risk,
            next_step=args.next_step,
            status=args.status,
            packet_id=args.packet_id,
            expected_task_updated_at=args.expected_task_updated_at,
            steps_done=args.steps_done,
            commands_run=args.commands_run,
            files_changed=args.files_changed,
        )

    try:
        saved = mutate_task_space(path, _append_record)
    except WritebackCliError as exc:
        _abort(args, exc.code, exc.message, **exc.extra)
    except KeyError as exc:
        _abort(args, "task_missing", f"任务不存在：{task_id}", task_id=task_id)
    except ExecutionPacketReplayConflict as exc:
        _abort(args, "packet_replay_conflict", f"执行包重复回写冲突：{exc}", task_id=task_id, packet_id=args.packet_id)
    except ExecutionSnapshotMismatch as exc:
        _abort(args, "snapshot_mismatch", f"执行包已过期：{exc}", task_id=task_id, packet_id=args.packet_id)
    task = next(item for item in saved["tasks"] if item["id"] == task_id)
    result = {
        "ok": True,
        "username": user.username,
        "task_space_path": str(path),
        "current_fingerprint": task_space_fingerprint(saved),
        "task_id": task_id,
        "task_title": task["title"],
        "task_status": task["status"],
        "latest_execution_record": task.get("executionRecords", [None])[0],
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"AI task execution record appended: {task['title']}")
    print(f"Task status: {task['status']}")
    print(f"Fingerprint: {result['current_fingerprint']}")
    print(f"Task space: {path}")


if __name__ == "__main__":
    main()

