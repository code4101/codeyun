from __future__ import annotations

import threading
from pathlib import Path
from typing import Any


class MiscActionTaskMixin:
    def _open_settings_page(self, runtime: Any):
        """从当前 #34/#35/#49 层级以最短确定路径打开设置页。"""

        scene_id, _score, _frame = runtime.current_scene([34, 35, 49], update=True)
        if scene_id == 49:
            self._log("success", "打开设置页：当前已在 #49")
            return 49
        if scene_id not in {34, 35}:
            yield from runtime.goto_view(34)
            scene_id = 34
        if scene_id == 34:
            yield from runtime.click_shape_center_then_view(
                34,
                "打开下方菜单",
                35,
                label="打开设置页：等待下方菜单 #35",
            )
        yield from runtime.click_shape_center_then_view(
            35,
            "设置",
            49,
            label="打开设置页：等待设置页 #49",
        )
        self._log("success", "打开设置页：已到达 #49")
        return 49

    def _leave_settings_page(self, runtime: Any):
        """沿正式 shape 从 #49/#35 确定性返回世界 #34。"""

        scene_id, score, _frame = runtime.current_scene([34, 35, 49], update=True)
        if scene_id == 34:
            self._log("success", "离开设置页：当前已在 #34")
            return 34
        if scene_id not in {35, 49}:
            raise RuntimeError(
                f"离开设置页需要 fresh #49/#35/#34，当前 "
                f"{f'#{scene_id}' if scene_id is not None else 'unknown'} {float(score):.0f}%"
            )
        if scene_id == 49:
            landed = yield from runtime.click_shape_center_then_view(
                49,
                "回退",
                34,
                35,
                label="离开设置页：等待世界 #34 或下方菜单 #35",
            )
            landed_id = int(getattr(landed, "id", landed))
            if landed_id == 34:
                self._log("success", "离开设置页：#49 回退已直接到达 #34")
                return 34
        yield from runtime.click_shape_center_then_view(
            35,
            "关闭下方菜单",
            34,
            label="离开设置页：等待世界 #34",
        )
        self._log("success", "离开设置页：已到达 #34")
        return 34

    def _execute_hide_floating_window(self, ctx: dict[str, Any], stop_event: threading.Event) -> bool:
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(
            ctx,
            asset_tree_path if isinstance(asset_tree_path, Path) else None,
            stop_event=stop_event,
        )
        frame = runtime.cur_frame(update=True)
        threshold = self.scene_thresholds.get("hide_floating", 55)
        for scene_id, source_title, target_title in (
            (421, "气泡", "拖拽隐藏"),
            (58, "图标", "隐藏区"),
        ):
            score = runtime.shape_score(scene_id, source_title, frame_data_url=frame)
            if score < threshold:
                continue
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"隐藏浮动窗：#{scene_id} {source_title}匹配 {score:.0f}%",
                    phase="hide_floating",
                    current_scene=scene_id,
                )
            runtime.drag_shape_to_shape(
                scene_id,
                source_title,
                target_title,
                duration=0.35,
                frame_data_url=frame,
            )
            for _ in runtime.wait_action_settle(0.8):
                pass
            return True
        return False
