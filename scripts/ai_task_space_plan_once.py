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
    build_automation_directive,
    build_execution_packet,
    mutate_task_space,
    run_planner_check,
    task_space_fingerprint,
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


def _selected_task(payload: dict[str, Any]) -> dict[str, Any] | None:
    latest_log = (payload.get("plannerLogs") or [{}])[0]
    selected_task_id = latest_log.get("selectedTaskId")
    if not selected_task_id:
        return None
    return next((task for task in payload.get("tasks", []) if task.get("id") == selected_task_id), None)


def main() -> None:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Run one AI task-space planning check.")
    parser.add_argument("--username", help="CodeYun username. Defaults to the first active superuser.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    user = _find_user(args.username)
    path = user_task_space_path(user.id)
    after = mutate_task_space(path, run_planner_check)
    latest_log = (after.get("plannerLogs") or [{}])[0]
    selected = _selected_task(after)
    execution_packet = build_execution_packet(after, selected.get("id") if selected else None, username=user.username)
    audit = audit_task_space(after)
    automation_directive = build_automation_directive(execution_packet, audit)
    planning_decision = (
        execution_packet.get("planningDecision")
        if isinstance(execution_packet.get("planningDecision"), dict)
        else {}
    )
    skipped = (
        planning_decision.get("skipped")
        if isinstance(planning_decision.get("skipped"), list)
        else []
    )
    skipped_count = planning_decision.get("skippedCount", len(skipped))
    open_suggestions = [
        suggestion
        for suggestion in after.get("plannerSuggestions", [])
        if isinstance(suggestion, dict) and suggestion.get("status") == "open"
    ]

    result = {
        "ok": True,
        "username": user.username,
        "task_space_path": str(path),
        "current_fingerprint": task_space_fingerprint(after),
        "latest_log": latest_log,
        "planner_state": {
            "selectedTaskId": planning_decision.get("selectedTaskId"),
            "selectedReason": planning_decision.get("selectedReason"),
            "candidateCount": planning_decision.get("candidateCount", 0),
            "blockerCount": skipped_count if isinstance(skipped_count, int) else len(skipped),
            "blockers": skipped[:5],
        },
        "planner_suggestions": open_suggestions,
        "selected_task": selected,
        "execution_packet": execution_packet,
        "audit": audit,
        "automation_directive": automation_directive,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"AI task-space planning check: {user.username}")
    print(f"Task space: {path}")
    print(f"Fingerprint: {result['current_fingerprint']}")
    print(f"Summary: {latest_log.get('summary') or ''}")
    planner_state = result["planner_state"]
    print(f"Planner: {planner_state.get('selectedReason') or ''}")
    print(
        "Candidates: "
        f"{planner_state.get('candidateCount', 0)}, "
        f"blockers: {planner_state.get('blockerCount', 0)}"
    )
    for blocker in planner_state.get("blockers") or []:
        reasons = " / ".join(str(reason) for reason in blocker.get("reasons", []))
        print(f"Blocker: {blocker.get('title')}: {reasons}")
    for action in latest_log.get("actions") or []:
        print(f"- {action}")
    for suggestion in open_suggestions[:5]:
        print(f"Suggestion: [{suggestion.get('kind')}] {suggestion.get('title')}")
    if selected:
        print(f"Selected: {selected.get('title')} [{selected.get('executionPolicy')}, {selected.get('risk')}]")
        print(f"Execution mode: {execution_packet.get('decision', {}).get('mode')}")
        print(f"Reason: {execution_packet.get('decision', {}).get('reason')}")
        print(f"Directive: {automation_directive.get('action')}")
    print(
        "Audit: "
        f"{'ok' if audit.get('ok') else 'needs attention'} "
        f"({audit.get('summary', {}).get('errors', 0)} errors, "
        f"{audit.get('summary', {}).get('warnings', 0)} warnings)"
    )
    for issue in audit.get("issues") or []:
        print(f"- [{issue.get('severity')}] {issue.get('message')}")


if __name__ == "__main__":
    main()
