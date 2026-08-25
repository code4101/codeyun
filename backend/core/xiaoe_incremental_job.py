from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from backend.core.jobs.local_runtime import submit_local_job_once
from backend.core.services.launcher import run_quiet


XIAOE_INCREMENTAL_UPDATE_TASK_KEY = "xiaoe_incremental_update"
XIAOE_INCREMENTAL_UPDATE_RUN_TIME = "08:00"
XIAOE_INCREMENTAL_UPDATE_WEEKDAYS = (7,)
XIAOE_ARCHIVE_ROOT = Path(r"E:\data\m2311禅课合辑")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_last_json_line(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {"status": "completed"}


def _run_script(script_name: str, *arguments: str) -> dict[str, Any]:
    command = [sys.executable, str(REPOSITORY_ROOT / "scripts" / script_name), *arguments]
    options: dict[str, Any] = {
        "cwd": REPOSITORY_ROOT,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "check": False,
    }
    completed = run_quiet(command, **options)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        message = detail[-1] if detail else f"退出码 {completed.returncode}"
        raise RuntimeError(f"{script_name} 执行失败：{message}")
    return _parse_last_json_line(completed.stdout)


def _full_archive_completed(output_dir: Path, state_file_name: str) -> bool:
    state = _read_json(output_dir / "_下载辅助" / state_file_name)
    return state.get("status") == "completed"


def run_xiaoe_incremental_update(
    output_dir: Path = XIAOE_ARCHIVE_ROOT,
) -> dict[str, Any]:
    """按视频、音频、图文顺序执行一次小鹅通增量归档。"""
    output_dir = Path(output_dir)
    common = ("--output-dir", str(output_dir))
    result: dict[str, Any] = {
        "status": "completed",
        "video": _run_script("download_xiaoe_video_incremental.py", *common),
    }

    if _full_archive_completed(output_dir, "audio-current-state.json"):
        result["audio"] = _run_script(
            "download_xiaoe_audio.py", *common, "--mode", "incremental"
        )
    else:
        result["audio"] = {"status": "skipped", "reason": "full_archive_incomplete"}

    if _full_archive_completed(output_dir, "text-current-state.json"):
        result["text"] = _run_script(
            "download_xiaoe_text.py", *common, "--mode", "incremental"
        )
    else:
        result["text"] = {"status": "skipped", "reason": "full_archive_incomplete"}
    return result


def enqueue_xiaoe_incremental_update_job() -> str:
    run, _created = submit_local_job_once(
        job_type="archive.xiaoe-incremental-update",
        payload={},
    )
    return run.id
