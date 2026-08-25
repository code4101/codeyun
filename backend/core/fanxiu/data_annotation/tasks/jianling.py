from __future__ import annotations

import threading
from pathlib import Path
from typing import Any


class JianlingTaskMixin:
    def _execute_jianling_cuiling_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        """Raise 剑灵淬灵 to level 1000 inside the #349/#351 layer0 context."""
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(
            ctx,
            asset_tree_path if isinstance(asset_tree_path, Path) else None,
            stop_event=stop_event,
        )
        target_level = max(1, int(payload.get("target_level") or 1000))
        max_rounds = max(1, min(2000, int(payload.get("max_rounds") or 500)))
        max_unknown_rounds = max(1, min(30, int(payload.get("max_unknown_rounds") or 8)))
        press_seconds = max(0.1, min(3.0, float(payload.get("press_seconds") or 3.0)))
        ocr_every_presses = max(1, min(20, int(payload.get("ocr_every_presses") or 5)))
        unknown_rounds = 0
        presses_since_ocr = ocr_every_presses
        last_level: int | None = None

        cuiling_shapes = [
            shape
            for shape in runtime.view(349).get_shapes()
            if shape.title == "淬灵" and not bool(shape.raw.get("isSceneIdentity"))
        ]
        if len(cuiling_shapes) != 1:
            raise RuntimeError(f"剑灵_淬灵：#349 应有且仅有一个非场景身份的「淬灵」操作按钮，实际 {len(cuiling_shapes)} 个")
        cuiling_action = cuiling_shapes[0]

        def read_level(frame: str) -> tuple[int | None, str, bool]:
            numbers, text = runtime.ocr_numbers_in_shapes(
                349,
                ("等级",),
                frame_data_url=frame,
            )
            valid_levels = [int(value) for value in numbers if 0 <= int(value) <= 10000]
            return (
                max(valid_levels) if valid_levels else None,
                str(text),
                "等级" in str(text) or "圆满" in str(text),
            )

        def finish_if_full(level: int | None, level_context: bool, scene_id: int | None) -> str | None:
            if level is None or level < target_level or (scene_id != 349 and not level_context):
                return None
            message = f"剑灵_淬灵：凝炼等级已达 {level}（圆满）"
            runtime.set_completion_message(message)
            self._log("success", message)
            return "success"

        for round_index in range(1, max_rounds + 1):
            self._raise_if_stopped(stop_event)
            frame = runtime.cur_frame(update=True)
            scene_id, _score, _frame = runtime.current_scene([351, 349], frame_data_url=frame)

            if scene_id == 351:
                unknown_rounds = 0
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "剑灵_淬灵：点击突破弹窗「继续」",
                        phase="jianling_cuiling_continue",
                        current_scene=351,
                    )
                yield from runtime.wait_click(351, "继续")
                yield from runtime.wait_action_settle(0.8)
                continue

            should_read_level = scene_id != 349 or presses_since_ocr >= ocr_every_presses
            if should_read_level:
                level, level_text, level_context = read_level(frame)
                presses_since_ocr = 0
                if level is not None:
                    last_level = level
            else:
                level = last_level
                level_text = ""
                level_context = False

            # At level 1000 the [淬灵] identity/button disappears. The page is
            # still the terminal state of this layer0 transaction, so its level
            # region is the authoritative completion condition.
            terminal_result = finish_if_full(level, level_context, scene_id)
            if terminal_result is not None:
                return terminal_result

            if scene_id != 349:
                unknown_rounds += 1
                if unknown_rounds > max_unknown_rounds:
                    raise RuntimeError(
                        "剑灵_淬灵：限定 #349/#351 layer0 上下文连续未命中，"
                        f"最后 OCR={str(level_text)[:120]!r}；不会回退到默认候选猜测"
                    )
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"剑灵_淬灵：等待 #349/#351 当前事务状态 {unknown_rounds}/{max_unknown_rounds}",
                        phase="jianling_cuiling_wait_layer0",
                        current_scene=None,
                    )
                yield from runtime.wait_action_settle(0.5)
                continue

            if level is None:
                unknown_rounds += 1
                if unknown_rounds > max_unknown_rounds:
                    raise RuntimeError(f"剑灵_淬灵：无法识别 #349[等级]，OCR={str(level_text)[:120]!r}")
                yield from runtime.wait_action_settle(0.5)
                continue

            unknown_rounds = 0
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"剑灵_淬灵：当前 {level}/{target_level}，长按「淬灵」({round_index}/{max_rounds})",
                    phase="jianling_cuiling_press",
                    current_scene=349,
                )
            try:
                runtime.long_press_shape(349, cuiling_action, duration=press_seconds)
            except RuntimeError:
                # The action shape disappears as soon as level 1000 is reached.
                # Any action failure gets one authoritative terminal check; only
                # a confirmed full level can convert that failure into success.
                terminal_frame = runtime.cur_frame(update=True)
                terminal_level, _terminal_text, terminal_context = read_level(terminal_frame)
                terminal_result = finish_if_full(terminal_level, terminal_context, 349)
                if terminal_result is not None:
                    return terminal_result
                raise
            presses_since_ocr += 1

        raise RuntimeError(f"剑灵_淬灵：达到最大轮数 {max_rounds}，仍未到 {target_level} 级")
