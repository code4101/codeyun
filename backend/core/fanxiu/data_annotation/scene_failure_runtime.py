"""Targeted, secondary Runtime evidence beside the already saved first frame."""
from __future__ import annotations

import json
import time
from pathlib import Path


def capture_scene_failure_runtime(
    *, scene_id: int | None, evidence_frame_path: str | None,
    task_id: str, attempt_id: str,
) -> dict | None:
    """Persist one bounded read for #698; never replace the navigation failure.

    The observation occurs AFTER the saved frame, so its identity and timestamp
    are explicit. A missing panel or failed read is evidence of uncertainty,
    not proof that the earlier click closed the panel. No replay or GUI action.
    Other scenes perform no Runtime read. Failure to publish remains secondary.
    """
    if scene_id != 698 or not evidence_frame_path:
        return None
    frame = Path(evidence_frame_path)
    if not frame.is_file():
        return None
    payload = {"scene_id": scene_id, "task_id": task_id, "attempt_id": attempt_id,
               "frame_path": str(frame), "observation_started_at_epoch": time.time(),
               "same_instant_as_frame": False, "exit_repair_verified": False}
    try:
        from backend.core.fanxiu.instrumentation.magic_invasion_auto_settings import (
            read_magic_invasion_auto_exit_snapshot,
        )
        payload["runtime"] = read_magic_invasion_auto_exit_snapshot()
    except Exception as exc:
        payload["diagnostic_error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
    path = frame.with_suffix(".runtime.json")
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["artifact_path"] = str(path)
    except Exception as exc:
        payload["publication_error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
    return payload
