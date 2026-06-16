from __future__ import annotations

import threading
from pathlib import Path
from typing import Any


class MiscActionTaskMixin:
    def _execute_hide_floating_window(self, ctx: dict[str, Any], stop_event: threading.Event) -> None:
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(
            ctx,
            asset_tree_path if isinstance(asset_tree_path, Path) else None,
            stop_event=stop_event,
        )
        frame = runtime.cur_frame(update=True)
        score = runtime.shape_score(58, "图标", frame_data_url=frame)
        if score < self.scene_thresholds.get("hide_floating", 55):
            self._log("info", f"浮动窗未明显出现，图标匹配 {score:.0f}%")
            return
        with self._lock:
            self._set_status_locked("running", f"隐藏浮动窗：图标匹配 {score:.0f}%", phase="hide_floating", current_scene=58)
        runtime.drag_shape_to_shape(58, "图标", "隐藏区", duration=0.35, frame_data_url=frame)
        for _ in runtime.wait_action_settle(0.8):
            pass
