from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterable


class WeeklyHanliTaskMixin:
    """执行“周常_韩立”的当前可验证闭环。"""

    def _wait_and_click_weekly_hanli_ocr_target(
        self,
        runtime: Any,
        *,
        alternatives: Iterable[str],
        label: str,
        timeout_seconds: float,
        poll_seconds: float,
    ):
        """按底层 OCR token 框精确点击窗口中的唯一目标。"""

        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        keywords = tuple(str(item).strip() for item in alternatives if str(item).strip())
        last_matches: list[tuple[float, float, str]] = []
        while time.monotonic() < deadline:
            for keyword in keywords:
                matches = runtime.ocr_centers_in_shape(334, "窗口", include=(keyword,))
                if not matches:
                    continue
                last_matches = matches
                if len(matches) != 1:
                    raise RuntimeError(f"{label}：OCR 目标“{keyword}”匹配到 {len(matches)} 项，停止点击")
                x, y, text = matches[0]
                runtime.click_frame_point(334, x, y)
                self._log("action", f"{label}：点击 OCR 目标“{keyword}”，识别文本：{text[:80]}")
                yield from runtime.wait_action_settle(poll_seconds)
                return {"keyword": keyword, "text": text, "x": x, "y": y}
            yield from runtime.wait_action_settle(poll_seconds)
        raise TimeoutError(f"{label}：等待 OCR 目标超时，最后匹配：{last_matches}")

    def _execute_weekly_hanli_task(
        self,
        ctx: dict[str, Any],
        stop_event: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """从世界进入韩立私聊页，暂略领取礼物后返回世界。"""

        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少周常_韩立资产树路径，无法执行作业")

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        transition_timeout = float(payload.get("transition_timeout_seconds") or 15.0)
        ocr_timeout = float(payload.get("ocr_timeout_seconds") or 15.0)
        poll_seconds = max(0.2, float(payload.get("poll_seconds") or 0.8))

        yield from runtime.click_shape_center_then_view(
            34,
            "聊天",
            332,
            timeout=transition_timeout,
            label="周常_韩立：等待聊天页 #332",
        )
        yield from runtime.click_shape_center_then_view(
            332,
            "通讯录",
            333,
            timeout=transition_timeout,
            label="周常_韩立：等待通讯录 #333",
        )
        yield from runtime.click_shape_center_then_view(
            333,
            "仙缘",
            334,
            timeout=transition_timeout,
            label="周常_韩立：等待仙缘列表 #334",
        )

        hanli = yield from self._wait_and_click_weekly_hanli_ocr_target(
            runtime,
            alternatives=("韩立",),
            label="周常_韩立：选择韩立",
            timeout_seconds=ocr_timeout,
            poll_seconds=poll_seconds,
        )
        private_chat = yield from self._wait_and_click_weekly_hanli_ocr_target(
            runtime,
            alternatives=("私聊", "传音"),
            label="周常_韩立：进入私聊",
            timeout_seconds=ocr_timeout,
            poll_seconds=poll_seconds,
        )
        yield from runtime.wait_view(379, timeout=transition_timeout, label="周常_韩立：等待私聊页 #379")

        # 当前游戏的领取礼物交互存在已知异常。闭环暂时只验证能进入私聊，
        # 随后按已标注返回链安全回到世界，不尝试任何领取动作。
        yield from runtime.click_shape_center_then_view(
            379,
            "返回",
            334,
            timeout=transition_timeout,
            label="周常_韩立：从私聊返回 #334",
        )
        yield from runtime.click_shape_center_then_view(
            334,
            "返回",
            34,
            timeout=transition_timeout,
            label="周常_韩立：返回世界 #34",
        )
        self._log("success", "周常_韩立：已进入韩立私聊并安全返回世界；本版暂不领取礼物")
        return {
            "result": "success",
            "message": "已进入韩立私聊并返回世界；当前版本按业务约束暂不领取礼物",
            "current_scene": 34,
            "hanli_text": hanli["text"],
            "private_chat_text": private_chat["text"],
        }
