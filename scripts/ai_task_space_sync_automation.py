from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.ai_task_space import build_automation_prompt
from backend.core.ai_task_space_automation import (
    default_automation_toml_path,
    render_automation_toml,
    validate_automation_toml,
    write_automation_toml,
)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Generate or write the Codex automation.toml for AI task-space automation execution.")
    parser.add_argument("--username", default="", help="CodeYun username to pass to the planning check script.")
    parser.add_argument("--path", default=str(default_automation_toml_path()), help="automation.toml path to write or preview.")
    parser.add_argument("--cwd", default=str(ROOT_DIR), help="Repository cwd for the automation.")
    parser.add_argument("--rrule", default="FREQ=HOURLY;INTERVAL=1", help="Cron RRULE for the automation.")
    parser.add_argument("--model", default="gpt-5.4", help="Codex model for the automation.")
    parser.add_argument("--reasoning-effort", default="medium", help="Codex reasoning effort.")
    parser.add_argument("--status", default="ACTIVE", choices=["ACTIVE", "PAUSED"], help="Automation status.")
    parser.add_argument("--dry-run", action="store_true", help="Print the generated TOML without writing it.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    path = Path(args.path)
    cwd = Path(args.cwd)
    toml_text = render_automation_toml(
        username=args.username,
        cwd=cwd,
        rrule=args.rrule,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        status=args.status,
    )

    written = False
    if not args.dry_run:
        write_automation_toml(
            path,
            username=args.username,
            cwd=cwd,
            rrule=args.rrule,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            status=args.status,
        )
        written = True

    expected_prompt = build_automation_prompt(args.username)
    validation = (
        validate_automation_toml(path, expected_prompt=expected_prompt, expected_cwd=cwd)
        if written
        else {"path": str(path), "exists": path.exists(), "config": None, "failures": []}
    )
    result = {
        "ok": not validation.get("failures"),
        "written": written,
        "path": str(path),
        "cwd": str(cwd.resolve(strict=False)),
        "username": args.username,
        "validation": validation,
        "toml": toml_text if args.dry_run else "",
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["ok"] else 1)

    if args.dry_run:
        print(toml_text)
        return

    print(f"AI task-space automation synced: {path}")
    if result["ok"]:
        print("Validation: ok")
        return
    print("Validation: failed", file=sys.stderr)
    for failure in validation.get("failures") or []:
        print(f"- {failure.get('code')}: {failure.get('message')}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()

