from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from backend.core.fanxiu.data_annotation.job_times import next_business_time


class WeeklyHanliTaskMixin:
    """执行“周常_韩立”的当前可验证闭环。"""

    def _record_weekly_hanli_done(
        self,
        payload: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> str:
        next_time = next_business_time(
            ("05:00",),
            now=now,
            weekdays=(0,),
        )
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "weekly-hanli"),
            next_time,
        )
        return next_time

    def _wait_and_click_weekly_hanli_ocr_target(
        self,
        runtime: Any,
        *,
        scene_id: int = 334,
        shape_title: str = "窗口",
        alternatives: Iterable[str],
        label: str,
        timeout_seconds: float,
        poll_seconds: float,
        require_unique: bool = True,
    ):
        """按底层 OCR token 框精确点击窗口中的唯一目标。"""

        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        keywords = tuple(str(item).strip() for item in alternatives if str(item).strip())
        last_matches: list[tuple[float, float, str]] = []
        while time.monotonic() < deadline:
            for keyword in keywords:
                matches = runtime.ocr_centers_in_shape(scene_id, shape_title, include=(keyword,))
                if not matches:
                    continue
                last_matches = matches
                if require_unique and len(matches) != 1:
                    raise RuntimeError(f"{label}：OCR 目标“{keyword}”匹配到 {len(matches)} 项，停止点击")
                x, y, text = matches[0]
                runtime.click_frame_point(scene_id, x, y)
                self._log("action", f"{label}：点击 OCR 目标“{keyword}”，识别文本：{text[:80]}")
                yield from runtime.wait_action_settle(poll_seconds)
                return {"keyword": keyword, "text": text, "x": x, "y": y}
            yield from runtime.wait_action_settle(poll_seconds)
        raise TimeoutError(f"{label}：等待 OCR 目标超时，最后匹配：{last_matches}")

    def _claim_weekly_hanli_gifts(
        self,
        runtime: Any,
        *,
        max_claims: int,
        reward_wait_seconds: float,
        transition_timeout: float,
    ):
        """逐个领取韩立礼物；稳定空白列表与“空空如也”均是幂等完成态。"""

        claimed: list[dict[str, Any]] = []
        while True:
            deadline = time.monotonic() + max(1.0, float(transition_timeout))
            matches: list[tuple[float, float, str]] = []
            empty_matches: list[tuple[float, float, str]] = []
            blank_frame_count = 0
            while time.monotonic() < deadline:
                frame = runtime.cur_frame(update=True)
                matches = runtime.ocr_centers_in_shape(
                    379,
                    "礼物",
                    include=("点击领取",),
                    frame_data_url=frame,
                )
                if matches:
                    break
                empty_matches = runtime.ocr_centers_in_shape(
                    379,
                    "空状态",
                    include=("空空如也",),
                    frame_data_url=frame,
                )
                if empty_matches:
                    self._log(
                        "success",
                        f"周常_韩立：奖励列表为空，已识别幂等完成态“{empty_matches[0][2]}”",
                    )
                    return claimed
                blank_frame_count += 1
                if blank_frame_count >= 3:
                    yield from runtime.wait_view(
                        379,
                        timeout=transition_timeout,
                        label="周常_韩立：复核空白奖励列表仍在私聊页 #379",
                    )
                    self._log(
                        "success",
                        "周常_韩立：私聊页奖励区连续 3 帧无“点击领取”，"
                        "按稳定空白列表确认本周已完成",
                    )
                    return claimed
                yield from runtime.wait_action_settle(0.5)

            if not matches:
                raise TimeoutError("周常_韩立：私聊页奖励状态在等待期内始终无法稳定分类")
            if len(claimed) >= max_claims:
                raise RuntimeError(f"周常_韩立：已领取 {max_claims} 次后仍有“点击领取”，停止避免无限循环")

            x, y, text = matches[0]
            runtime.click_frame_point(379, x, y)
            claimed.append({"text": text, "x": x, "y": y})
            self._log(
                "action",
                f"周常_韩立：点击第 {len(claimed)} 个“点击领取”，本轮识别到 {len(matches)} 个匹配",
            )
            yield from runtime.wait_action_settle(reward_wait_seconds)
            yield from runtime.wait_view(
                379,
                timeout=transition_timeout,
                label="周常_韩立：等待奖励弹窗结束并回到 #379",
            )

    def _execute_weekly_hanli_task(
        self,
        ctx: dict[str, Any],
        stop_event: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """进入韩立私聊页，领完全部礼物或确认列表为空后返回世界。"""

        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少周常_韩立资产树路径，无法执行作业")

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        transition_timeout = float(payload.get("transition_timeout_seconds") or 15.0)
        ocr_timeout = float(payload.get("ocr_timeout_seconds") or 15.0)
        poll_seconds = max(0.2, float(payload.get("poll_seconds") or 0.8))
        reward_wait_seconds = max(5.0, float(payload.get("reward_wait_seconds") or 5.0))
        max_gift_claims = max(1, min(100, int(payload.get("max_gift_claims") or 20)))

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

        gifts = yield from self._claim_weekly_hanli_gifts(
            runtime,
            max_claims=max_gift_claims,
            reward_wait_seconds=reward_wait_seconds,
            transition_timeout=transition_timeout,
        )
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
        next_time = self._record_weekly_hanli_done(payload)
        self._log(
            "success",
            f"周常_韩立：已领取 {len(gifts)} 个礼物并安全返回世界，下次 {next_time}",
        )
        return {
            "result": "success",
            "message": f"已领取 {len(gifts)} 个韩立礼物并返回世界",
            "current_scene": 34,
            "hanli_text": hanli["text"],
            "private_chat_text": private_chat["text"],
            "gift_count": len(gifts),
            "gifts": gifts,
        }
