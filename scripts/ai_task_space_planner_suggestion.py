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
    apply_planner_suggestion,
    dismiss_planner_suggestion,
    mutate_task_space,
    task_space_fingerprint,
    user_task_space_path,
)
from backend.db import engine
from backend.models import User


class SuggestionCliError(Exception):
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


def _abort(args: argparse.Namespace, code: str, message: str, **extra: object) -> None:
    if args.json:
        print(json.dumps({"ok": False, "code": code, "message": message, **extra}, ensure_ascii=False, indent=2))
    else:
        print(message, file=sys.stderr)
    raise SystemExit(1)


def _find_suggestion(task_space: dict, suggestion_id: str) -> dict:
    suggestion = next(
        (item for item in task_space.get("plannerSuggestions", []) if item.get("id") == suggestion_id),
        None,
    )
    if suggestion is None:
        raise KeyError(suggestion_id)
    return suggestion


def main() -> None:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Apply or dismiss an AI task-space planner suggestion.")
    parser.add_argument("--username", help="CodeYun username. Defaults to the first active superuser.")
    parser.add_argument("--suggestion-id", required=True, help="Planner suggestion id from planning check output.")
    parser.add_argument("--action", choices=["apply", "dismiss"], required=True, help="Suggestion review action.")
    parser.add_argument(
        "--expected-fingerprint",
        default="",
        help="Optional stale-write guard. Fails if the task space changed before this action.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    user = _find_user(args.username)
    path = user_task_space_path(user.id)
    before_suggestion: dict | None = None

    def _act(current: dict) -> dict:
        nonlocal before_suggestion
        if args.expected_fingerprint and args.expected_fingerprint != task_space_fingerprint(current):
            raise SuggestionCliError(
                "stale_fingerprint",
                "任务空间已被其他采集、规划检查或回写更新，请重新读取后再处理建议。",
            )
        before_suggestion = _find_suggestion(current, args.suggestion_id)
        if args.action == "apply":
            return apply_planner_suggestion(current, args.suggestion_id)
        return dismiss_planner_suggestion(current, args.suggestion_id)

    try:
        saved = mutate_task_space(path, _act)
    except SuggestionCliError as exc:
        _abort(args, exc.code, exc.message, **exc.extra)
    except KeyError as exc:
        _abort(args, "suggestion_missing", f"建议或任务不存在：{args.suggestion_id}", suggestion_id=args.suggestion_id)
    except ValueError as exc:
        _abort(args, "suggestion_action_rejected", str(exc), suggestion_id=args.suggestion_id)

    suggestion = _find_suggestion(saved, args.suggestion_id)
    result = {
        "ok": True,
        "username": user.username,
        "task_space_path": str(path),
        "current_fingerprint": task_space_fingerprint(saved),
        "suggestion": suggestion,
        "previous_status": before_suggestion.get("status") if before_suggestion else None,
        "action": args.action,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"AI task planner suggestion {args.action}: {suggestion.get('title')}")
    print(f"Suggestion status: {suggestion.get('status')}")
    print(f"Fingerprint: {result['current_fingerprint']}")
    print(f"Task space: {path}")


if __name__ == "__main__":
    main()

