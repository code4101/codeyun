"""跨日常玩法的外层世界确认与内部区域离场。

这里只处理共用的世界/内部区域/日常返回关系，不决定任何玩法是否完成。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from pyxllib.prog import BehaviorTreeStatus
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text


class WorldReturnMixin:
    def _ensure_outer_world(self, ctx: dict[str, Any], stop_event: threading.Event, *, label: str):
        """确认外层世界；OCR 只能否决世界结论，不能单独生成场景身份。

        内部区域必须由正式识别确认为 #85 才点击离开；缺少标注或识别与
        文本冲突均报告失败，调用方不能把 skipped 当成已回世界。
        离开后的超时在完整识别之后、下一次点击之前检查，覆盖所有返回分支。
        """
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        match = yield from context.wait_scene([34, 85], wait=5.0, label=f"{label}：确认外层世界")
        scene_id = match.scene_id
        text = context.ocr_text(match.frame_data_url)
        if scene_id == 34 and not self._world_text_is_internal_area(text):
            with self._lock:
                self._status.update({"current_scene": 34, "updated_at": time.time()})
            return "success"
        if scene_id != 85:
            raise RuntimeError(f"{label}：未确认外层世界或内部区域，实际 #{scene_id}，文本：{text[:120]}")
        image85 = (ctx.get("images") or {}).get(85)
        if not isinstance(image85, dict):
            raise RuntimeError(f"{label}：缺少 #85 内部区域标注，无法安全离开")
        with self._lock:
            self._set_status_locked("running", f"{label}：当前仍在宗门内部，点击离开", phase="world_return_leave_internal_area", current_scene=85)
            self._log_locked("action", f"{label}：点击 #85「离开」")
        yield from context.wait_click(85, "离开")

        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from context.wait_scene([34, 204, 69], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, score, text
            if scene_id == 34 and not self._world_text_is_internal_area(text):
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                    self._log_locked("success", f"{label}：已离开宗门内部并回到外层世界 #34 {score:.0f}%")
                return "success"
            if time.monotonic() - start >= 20:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise RuntimeError(f"{label}：离开宗门内部超时，最后 {scene_text} {last_score:.0f}%，文本：{last_text[:120]}")
            if scene_id == 204:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"{label}：离开后落到小助手清单，返回日常页",
                        phase="world_return_leave_assistant_return",
                        current_scene=204,
                    )
                    self._log_locked("action", f"{label}：点击 #204「返回」")
                yield from context.wait_click(204, "返回")
                yield from context.wait_action_settle(2.0)
                continue
            if scene_id == 69:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"{label}：从日常页退出到世界",
                        phase="world_return_leave_daily_exit",
                        current_scene=69,
                    )
                    self._log_locked("action", f"{label}：点击 #69「退出」")
                yield from context.wait_click(69, "退出")
                yield from context.wait_action_settle(2.0)
                continue
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}：等待离开宗门内部，当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    phase="world_return_wait_outer_world",
                    current_scene=scene_id,
                )

    def _world_text_is_internal_area(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        if "离开" not in normalized:
            return False
        return any(fragment in normalized for fragment in ("社团管事", "贺圣朴", "创建队伍", "加入队伍", "组队"))
