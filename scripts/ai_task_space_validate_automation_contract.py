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
    build_automation_prompt,
    load_task_space,
    run_planner_check,
    task_space_fingerprint,
    user_task_space_path,
)
from backend.core.ai_task_space_automation import (
    default_automation_toml_path,
    validate_automation_toml,
    validate_contract,
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


def main() -> None:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Validate the AI task-space automation execution contract without mutating task space.")
    parser.add_argument("--username", help="CodeYun username. Defaults to the first active superuser.")
    parser.add_argument(
        "--use-current",
        action="store_true",
        help="Validate the current task space directly. By default, simulate one planning check in memory.",
    )
    parser.add_argument(
        "--automation-toml",
        nargs="?",
        const=str(default_automation_toml_path()),
        default="",
        help="Also validate a Codex automation.toml against the generated prompt. Omit value to use the default ai automation.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    user = _find_user(args.username)
    path = user_task_space_path(user.id)
    original = load_task_space(path)
    validated_space = original if args.use_current else run_planner_check(original)
    current_fingerprint = task_space_fingerprint(original)
    validated_fingerprint = task_space_fingerprint(validated_space)
    prompt = build_automation_prompt(user.username)
    contract = validate_contract(validated_space, username=user.username, prompt=prompt)
    automation = (
        validate_automation_toml(Path(args.automation_toml), expected_prompt=prompt, expected_cwd=ROOT_DIR)
        if args.automation_toml
        else None
    )
    failures = [*contract["failures"], *((automation or {}).get("failures") or [])]
    result = {
        "username": user.username,
        "task_space_path": str(path),
        "current_fingerprint": current_fingerprint,
        "validated_fingerprint": validated_fingerprint,
        "mutated": False,
        "mode": "current" if args.use_current else "simulated_plan",
        **contract,
        "automation_toml": automation,
        "failures": failures,
        "ok": not failures,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"AI task automation contract: {'ok' if result['ok'] else 'failed'}")
        print(f"User: {user.username}")
        print(f"Mode: {result['mode']}")
        print(f"Task space: {path}")
        print(f"Current fingerprint: {current_fingerprint}")
        for failure in result["failures"]:
            print(f"- [{failure['code']}] {failure['message']}")

    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
