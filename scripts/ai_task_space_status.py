from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.ai_task_space import (
    audit_task_space,
    load_task_space,
    task_space_fingerprint,
    task_waits_for_user_confirmation,
    user_task_space_path,
)
from backend.db import engine
from backend.models import User


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


def _compact_task(task: dict[str, Any]) -> dict[str, Any]:
    document = task.get("document") if isinstance(task.get("document"), dict) else {}
    return {
        "id": task.get("id"),
        "title": task.get("title"),
        "status": task.get("status"),
        "kind": task.get("kind"),
        "executionPolicy": task.get("executionPolicy"),
        "risk": task.get("risk"),
        "updatedAt": task.get("updatedAt"),
        "currentState": document.get("currentState", ""),
        "nextStep": document.get("nextStep", ""),
    }


def _base_argv(username: str, script: str) -> list[str]:
    return ["uv", "run", "python", script, "--username", username]


def _confirm_hint(username: str, task: dict[str, Any], fingerprint: str) -> dict[str, Any]:
    task_id = str(task.get("id") or "")
    return {
        "kind": "confirm_waiting_task",
        "taskId": task_id,
        "taskTitle": task.get("title"),
        "fingerprint": fingerprint,
        "requiresApproval": False,
        "argvTemplate": [
            *_base_argv(username, "scripts/ai_task_space_confirm_user_ready.py"),
            "--task-id",
            task_id,
            "--expected-fingerprint",
            fingerprint,
            "--note",
            "<用户确认的范围或条件>",
            "--json",
        ],
    }


def _suggestion_hints(username: str, suggestion: dict[str, Any], fingerprint: str) -> list[dict[str, Any]]:
    suggestion_id = str(suggestion.get("id") or "")
    hints: list[dict[str, Any]] = []
    for action in ("apply", "dismiss"):
        hints.append(
            {
                "kind": "planner_suggestion",
                "suggestionId": suggestion_id,
                "suggestionTitle": suggestion.get("title"),
                "action": action,
                "fingerprint": fingerprint,
                "requiresApproval": False,
                "argvTemplate": [
                    *_base_argv(username, "scripts/ai_task_space_planner_suggestion.py"),
                    "--suggestion-id",
                    suggestion_id,
                    "--action",
                    action,
                    "--expected-fingerprint",
                    fingerprint,
                    "--json",
                ],
            }
        )
    return hints


def _archive_review_hints(username: str, task: dict[str, Any], fingerprint: str) -> list[dict[str, Any]]:
    task_id = str(task.get("id") or "")
    hints: list[dict[str, Any]] = []
    for action in ("keep_unarchived", "archive"):
        hints.append(
            {
                "kind": "archive_review",
                "taskId": task_id,
                "taskTitle": task.get("title"),
                "action": action,
                "fingerprint": fingerprint,
                "requiresApproval": False,
                "argvTemplate": [
                    *_base_argv(username, "scripts/ai_task_space_review_action.py"),
                    "--task-id",
                    task_id,
                    "--action",
                    action,
                    "--expected-fingerprint",
                    fingerprint,
                    "--json",
                ],
            }
        )
    return hints


def main() -> None:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Read the current saved AI task-space status without mutating it.")
    parser.add_argument("--username", help="CodeYun username. Defaults to the first active superuser.")
    parser.add_argument("--limit", type=int, default=8, help="Maximum open suggestions / waiting tasks to include.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    user = _find_user(args.username)
    path = user_task_space_path(user.id)
    task_space = load_task_space(path)
    audit = audit_task_space(task_space)
    tasks = [task for task in task_space.get("tasks", []) if isinstance(task, dict)]
    open_suggestions = [
        suggestion
        for suggestion in task_space.get("plannerSuggestions", [])
        if isinstance(suggestion, dict) and suggestion.get("status") == "open"
    ]
    waiting_tasks = [task for task in tasks if task_waits_for_user_confirmation(task)]
    archive_review_tasks = [task for task in tasks if task.get("status") == "review_for_archive"]
    latest_log = (task_space.get("plannerLogs") or [None])[0]
    planning_decision = (
        latest_log.get("planningDecision")
        if isinstance(latest_log, dict) and isinstance(latest_log.get("planningDecision"), dict)
        else {}
    )
    selected_task_id = latest_log.get("selectedTaskId") if isinstance(latest_log, dict) else None
    selected_task = next((task for task in tasks if task.get("id") == selected_task_id), None)
    limit = max(0, args.limit)
    current_fingerprint = task_space_fingerprint(task_space)
    action_hints = [
        *[_confirm_hint(user.username, task, current_fingerprint) for task in waiting_tasks[:limit]],
        *[
            hint
            for suggestion in open_suggestions[:limit]
            for hint in _suggestion_hints(user.username, suggestion, current_fingerprint)
        ],
        *[
            hint
            for task in archive_review_tasks[:limit]
            for hint in _archive_review_hints(user.username, task, current_fingerprint)
        ],
    ]

    result = {
        "ok": True,
        "username": user.username,
        "task_space_path": str(path),
        "current_fingerprint": current_fingerprint,
        "mutated": False,
        "summary": {
            **audit.get("summary", {}),
            "openSuggestionCount": len(open_suggestions),
            "waitingConfirmationCount": len(waiting_tasks),
            "archiveReviewCount": len(archive_review_tasks),
        },
        "latest_planner_log": (
            {
                "id": latest_log.get("id"),
                "ranAt": latest_log.get("ranAt"),
                "summary": latest_log.get("summary"),
                "selectedTaskId": selected_task_id,
                "selectedReason": planning_decision.get("selectedReason"),
                "candidateCount": planning_decision.get("candidateCount", 0),
                "skippedCount": planning_decision.get("skippedCount", 0),
            }
            if isinstance(latest_log, dict)
            else None
        ),
        "selected_task": _compact_task(selected_task) if isinstance(selected_task, dict) else None,
        "waiting_tasks": [_compact_task(task) for task in waiting_tasks[:limit]],
        "archive_review_tasks": [_compact_task(task) for task in archive_review_tasks[:limit]],
        "open_planner_suggestions": open_suggestions[:limit],
        "action_hint_contract": {
            "requiresApproval": False,
            "fingerprint": current_fingerprint,
            "staleAfterAnyWrite": True,
            "reloadAfterSuccess": True,
            "note": "action_hints 基于同一当前快照，可直接执行；执行任一写操作后请重新运行 status，不要继续复用旧 hint。",
        },
        "action_hints": action_hints,
        "audit": audit,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"AI task-space status: {user.username}")
    print(f"Task space: {path}")
    print(f"Fingerprint: {result['current_fingerprint']}")
    print(
        "Summary: "
        f"{result['summary'].get('activeTasks', 0)} active tasks, "
        f"{result['summary'].get('inboxCaptures', 0)} inbox captures, "
        f"{result['summary'].get('openSuggestionCount', 0)} open suggestions, "
        f"{result['summary'].get('waitingConfirmationCount', 0)} waiting confirmations"
    )
    if result["latest_planner_log"]:
        latest = result["latest_planner_log"]
        print(f"Latest planning check: {latest.get('summary') or ''}")
        print(f"Planner: {latest.get('selectedReason') or ''}")
    for task in result["waiting_tasks"]:
        print(f"Waiting: {task.get('id')} {task.get('title')}")
    for suggestion in result["open_planner_suggestions"]:
        print(f"Suggestion: [{suggestion.get('kind')}] {suggestion.get('title')}")
    print(f"Action hints: {len(result['action_hints'])}")


if __name__ == "__main__":
    main()

