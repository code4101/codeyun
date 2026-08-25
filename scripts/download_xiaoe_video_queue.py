from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.xiaoe_video_archive import download_hls_video


HELPER_DIR_NAME = "_下载辅助"
STATE_FILE_NAME = "current-state.json"


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def _wait_for_completion(path: Path, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if payload.get("status") in {"completed", "completed_with_failures"}:
                return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"等待前置队列完成超时：{path}")
        time.sleep(10)


def _default_state_path(output_dir: Path) -> Path:
    """返回成品目录下统一的滚动状态文件路径。"""
    return output_dir / HELPER_DIR_NAME / STATE_FILE_NAME


def _cleanup_stale_temp_files(queue_path: Path) -> None:
    """清理同一临时队列目录中上一次运行遗留的辅助文件。"""
    temp_root = Path(tempfile.gettempdir()).resolve()
    resolved_queue = queue_path.resolve()
    if not resolved_queue.is_relative_to(temp_root):
        return

    current_files = {
        resolved_queue,
        resolved_queue.with_suffix(".stdout.log"),
        resolved_queue.with_suffix(".stderr.log"),
    }
    for pattern in ("page*-item*.json", "page*-item*.stdout.log", "page*-item*.stderr.log"):
        for path in resolved_queue.parent.glob(pattern):
            if path.resolve() not in current_files:
                path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="顺序下载小鹅通视频队列")
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--wait-for", type=Path)
    parser.add_argument("--wait-timeout", type=float, default=2 * 60 * 60)
    args = parser.parse_args()
    state_path = args.state or _default_state_path(args.output_dir)
    _cleanup_stale_temp_files(args.queue)

    queue = json.loads(args.queue.read_text(encoding="utf-8"))
    if not isinstance(queue, list):
        raise ValueError("队列文件必须是数组")

    state: dict[str, Any] = {
        "status": "waiting" if args.wait_for else "running",
        "started_at": datetime.now().astimezone().isoformat(),
        "queue_path": str(args.queue),
        "output_dir": str(args.output_dir),
        "completed": [],
        "failures": [],
    }
    _write_state(state_path, state)

    if args.wait_for:
        _wait_for_completion(args.wait_for, timeout_seconds=args.wait_timeout)

    state["status"] = "running"
    _write_state(state_path, state)
    for index, item in enumerate(queue):
        state["current_index"] = index
        state["current_title"] = str(item["title"])
        _write_state(state_path, state)
        try:
            result = download_hls_video(
                playlist_url=str(item["playlist_url"]),
                title=str(item["title"]),
                published_at=str(item["published_at"]),
                output_dir=args.output_dir,
            )
        except Exception as exc:
            state["failures"].append(
                {
                    "index": index,
                    "title": str(item.get("title") or ""),
                    "error": str(exc),
                }
            )
        else:
            state["completed"].append(
                {
                    "index": index,
                    "title": str(item["title"]),
                    **result,
                }
            )
        _write_state(state_path, state)

    state["status"] = "completed_with_failures" if state["failures"] else "completed"
    state["finished_at"] = datetime.now().astimezone().isoformat()
    state.pop("current_index", None)
    state.pop("current_title", None)
    _write_state(state_path, state)
    if not state["failures"]:
        try:
            if args.queue.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
                args.queue.unlink(missing_ok=True)
        except OSError:
            pass
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
