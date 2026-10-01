from __future__ import annotations

"""Optional idle observation, importable by an already-running Kernel."""

import time
from typing import Any

from pyxllib.prog import read_json_state_dict
from backend.core.fanxiu.info_window import (
    fanxiu_info_window_settings_path,
    fanxiu_info_window_state,
)

FANXIU_INFO_WINDOW_STALE_SECONDS = 10.0


def info_window_refresh_due(settings: dict[str, Any], snapshot: dict[str, Any], *, now: float) -> bool:
    """Only committed observations reset the 10-second inactivity deadline."""
    if not settings.get("enabled") or not settings.get("auto_refresh"):
        return False
    observed = float(snapshot.get("committed_at") or snapshot.get("observed_at") or 0.0)
    return now - observed >= FANXIU_INFO_WINDOW_STALE_SECONDS


def refresh_info_window_in_kernel(binding: Any, *, requested_at: float) -> dict[str, Any]:
    """Run inside the sole Kernel; recheck settings/freshness after any queue delay.

    Capture once, explicitly register an account handoff before generic scene
    recognition, and never dismiss a popup. The handoff owns Scheduler deferral;
    ordinary diagnostic ``scene`` queries remain observation-only.
    """
    now = time.time()
    if now - requested_at > FANXIU_INFO_WINDOW_STALE_SECONDS:
        return {"status": "skipped", "reason": "expired"}
    if not info_window_refresh_due(dict(read_json_state_dict(fanxiu_info_window_settings_path())), fanxiu_info_window_state.read(), now=now):
        return {"status": "skipped", "reason": "disabled_or_fresh"}
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_scheduler_settings
    if not read_scheduler_settings().get("job_group_enabled", True):
        return {"status": "skipped", "reason": "ai_control"}
    frame = binding.ctx.frame()
    handoff = binding.context.observe_external_login_notice(frame_data_url=frame)
    if handoff:
        return {"scene_id": 909, "score": 100.0, "external_login_handoff": handoff}
    return binding.scene(frame)

