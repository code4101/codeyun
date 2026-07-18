from __future__ import annotations

import threading
from pathlib import Path
from typing import Any


class DaofaTaskMixin:
    def _run_daofa_challenge_round(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        challenge_point: tuple[float, float] | None = None,
        prompt_timeout: float = 15.0,
        result_timeout: float = 60.0,
        return_timeout: float = 45.0,
    ):
        """Complete one #376 -> (#377) -> #378 -> #376 challenge round.

        ``#377`` is an optional business confirmation.  The game skips it
        after "本次登录不再提示" has been selected, so the first wait must
        accept both #377 and #378.  When #377 does appear, this business flow
        owns its confirmation; popup guards must not be relied on to click it.

        The helper is resumable from #377 or #378.  ``challenge_point`` is
        required only when starting from #376 because the target row is
        dynamic and is selected by the caller from current ranking evidence.
        """
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(
            ctx,
            asset_tree_path if isinstance(asset_tree_path, Path) else None,
            stop_event=stop_event,
        )

        scene_id, _score, _frame = runtime.current_scene([376, 377, 378], update=True)
        prompt_seen = scene_id == 377

        if scene_id == 376:
            if challenge_point is None:
                raise RuntimeError("道法争锋：从 #376 开始挑战时必须提供目标挑战按钮落点")
            click_x, click_y = float(challenge_point[0]), float(challenge_point[1])
            image376 = (ctx.get("images") or {}).get(376)
            if not isinstance(image376, dict):
                raise RuntimeError("道法争锋：缺少 #376 资产标注")
            width, height = self._frame_size(image376)
            if not (0.0 <= click_x <= width and 0.0 <= click_y <= height):
                raise ValueError(
                    f"道法争锋：挑战落点越界 ({click_x:.1f}, {click_y:.1f})，"
                    f"画面尺寸 {width:.0f}x{height:.0f}"
                )

            runtime.click_frame_point(376, click_x, click_y)
            landed = yield from runtime.wait_scene(
                377,
                378,
                timeout=float(prompt_timeout),
                label="道法争锋：等待挑战确认或挑战结果",
            )
            scene_id = int(getattr(landed, "id", landed))
            prompt_seen = scene_id == 377
        elif scene_id not in {377, 378}:
            raise RuntimeError(
                "道法争锋：小闭环只能从 #376 挑战页、#377 确认框或 #378 结果页开始"
            )

        if scene_id == 377:
            runtime.click_shape_center(377, "确认")
            yield from runtime.wait_scene(
                378,
                timeout=float(result_timeout),
                label="道法争锋：确认挑战后等待结果",
            )

        result_text = runtime.ocr_text(update=True)
        runtime.click_shape_center(378, "继续")
        yield from runtime.wait_scene(
            376,
            timeout=float(return_timeout),
            label="道法争锋：结果页继续并返回挑战页",
        )
        return {
            "status": "success",
            "prompt_seen": prompt_seen,
            "result_text": result_text,
            "final_scene": 376,
        }
