from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from backend.core.settings import get_settings


def local_job_result_snapshot_path(run_id: str) -> Path:
    normalized = str(run_id or "").strip()
    if not normalized or any(char not in "0123456789abcdefABCDEF-" for char in normalized):
        raise ValueError("Local Job 结果快照 ID 无效。")
    root = get_settings().data_dir / "local-jobs" / "result-snapshots"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{normalized}.json"


def write_local_job_result_snapshot(run_id: str, result: dict[str, Any]) -> Path:
    path = local_job_result_snapshot_path(run_id)
    temporary = path.with_suffix(f".{os.getpid()}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(temporary, path)
    return path


def read_local_job_result_snapshot(run_id: str) -> dict[str, Any] | None:
    try:
        result = json.loads(local_job_result_snapshot_path(run_id).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return result if isinstance(result, dict) else None
