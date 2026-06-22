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
    CAPTURE_CONTEXT_KINDS,
    add_capture,
    mutate_task_space,
    save_capture_attachment_file,
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


def _read_capture_text(args: argparse.Namespace) -> str:
    if args.text:
        return str(args.text)
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.buffer.read().decode("utf-8", errors="replace")
    raise SystemExit("采集内容不能为空：请使用 --text、--file，或通过 stdin 输入。")


def main() -> None:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Capture text into the AI task-space Inbox.")
    parser.add_argument("--username", help="CodeYun username. Defaults to the first active superuser.")
    parser.add_argument("--text", help="Raw task/context/constraint text to capture.")
    parser.add_argument("--file", help="UTF-8 text file to capture. Use this for long context.")
    parser.add_argument("--source", default="Codex 当前会话", help="Capture source label.")
    parser.add_argument("--tag", action="append", default=[], help="Optional capture tag. Can be repeated.")
    parser.add_argument("--image", action="append", default=[], help="Image file to preserve with this capture. Can be repeated.")
    parser.add_argument(
        "--context-kind",
        default="task",
        choices=sorted(CAPTURE_CONTEXT_KINDS),
        help="Capture kind.",
    )
    parser.add_argument("--project-path", default="", help="Optional project path related to this capture.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()
    raw_text = _read_capture_text(args)
    if not raw_text.strip():
        raise SystemExit("采集内容不能为空。")
    attachments = []
    for image_path in args.image:
        try:
            attachments.append(save_capture_attachment_file(image_path))
        except FileNotFoundError as exc:
            raise SystemExit(f"图片附件不存在：{image_path}") from exc
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    user = _find_user(args.username)
    path = user_task_space_path(user.id)

    def _append_capture(current):
        return add_capture(
            current,
            raw_text,
            args.source,
            tags=args.tag,
            context_kind=args.context_kind,
            project_path=args.project_path,
            attachments=attachments,
        )

    after = mutate_task_space(path, _append_capture)
    capture = after["captures"][0] if after.get("captures") else None
    result = {
        "ok": True,
        "username": user.username,
        "task_space_path": str(path),
        "current_fingerprint": task_space_fingerprint(after),
        "capture": capture,
        "inbox_count": len([item for item in after["captures"] if item.get("status") == "inbox"]),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print("AI task capture appended.")
    print(f"Inbox count: {result['inbox_count']}")
    print(f"Fingerprint: {result['current_fingerprint']}")
    print(f"Task space: {path}")


if __name__ == "__main__":
    main()
