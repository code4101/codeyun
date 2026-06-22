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
    confirm_task_user_ready,
    mutate_task_space,
    task_space_fingerprint,
    user_task_space_path,
)
from backend.db import engine
from backend.models import User


class ConfirmCliError(Exception):
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
        raise ConfirmCliError("invalid_target", "必须且只能提供 --task-id 或 --task-title。")
    if task_id:
        return task_id

    title = task_title.strip()
    matches = [task for task in task_space.get("tasks", []) if str(task.get("title") or "").strip() == title]
    if not matches:
        raise ConfirmCliError("task_title_not_found", f"未找到标题为「{title}」的任务。", task_title=title)
    if len(matches) > 1:
        candidates = "\n".join(
            f"- {task.get('id')} [{task.get('status')}] {task.get('title')}"
            for task in matches[:12]
        )
        raise ConfirmCliError(
            "ambiguous_task_title",
            f"标题「{title}」匹配到多个任务，请改用 --task-id：\n{candidates}",
            task_title=title,
            candidates=[{"id": task.get("id"), "status": task.get("status")} for task in matches[:12]],
        )
    return str(matches[0]["id"])


def _abort(args: argparse.Namespace, code: str, message: str, **extra: object) -> None:
    if args.json:
        print(json.dumps({"ok": False, "code": code, "message": message, **extra}, ensure_ascii=False, indent=2))
    else:
        print(message, file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Confirm that a waiting AI task-space task may continue.")
    parser.add_argument("--username", help="CodeYun username. Defaults to the first active superuser.")
    parser.add_argument("--task-id", default="", help="Task id to confirm.")
    parser.add_argument(
        "--task-title",
        default="",
        help="Exact task title to confirm; fails if not unique.",
    )
    parser.add_argument("--note", default="", help="Optional user confirmation note.")
    parser.add_argument(
        "--expected-fingerprint",
        default="",
        help="Optional stale-write guard. Fails if the task space changed before this confirmation.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    user = _find_user(args.username)
    path = user_task_space_path(user.id)
    task_id = ""

    def _confirm(current: dict) -> dict:
        nonlocal task_id
        if args.expected_fingerprint and args.expected_fingerprint != task_space_fingerprint(current):
            raise ConfirmCliError(
                "stale_fingerprint",
                "任务空间已被其他采集、规划检查或回写更新，请重新读取后再确认继续。",
            )
        task_id = _resolve_task_id(current, args.task_id.strip(), args.task_title)
        return confirm_task_user_ready(current, task_id, note=args.note)

    try:
        saved = mutate_task_space(path, _confirm)
    except ConfirmCliError as exc:
        _abort(args, exc.code, exc.message, **exc.extra)
    except KeyError as exc:
        _abort(args, "task_missing", f"任务不存在：{task_id}", task_id=task_id)
    except ValueError as exc:
        _abort(args, "task_not_waiting_confirmation", str(exc), task_id=task_id)

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

    print(f"AI task confirmed for next planning check: {task['title']}")
    print(f"Task status: {task['status']}")
    print(f"Fingerprint: {result['current_fingerprint']}")
    print(f"Task space: {path}")


if __name__ == "__main__":
    main()

