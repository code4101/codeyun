from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any


WEEKLY_SHENGZU_WEEKDAY = 6  # Python weekday: Sunday
WEEKLY_SHENGZU_TRIGGER_TIME = dt_time(20, 0)
WEEKLY_SHENGZU_WINDOW_END = dt_time(20, 5)


def _now() -> datetime:
    return datetime.now()


def _next_weekly_shengzu_trigger(now: datetime) -> datetime:
    for day_offset in range(8):
        candidate_date = now.date() + timedelta(days=day_offset)
        if candidate_date.weekday() != WEEKLY_SHENGZU_WEEKDAY:
            continue
        candidate = datetime.combine(candidate_date, WEEKLY_SHENGZU_TRIGGER_TIME)
        if candidate > now:
            return candidate
    raise RuntimeError("无法计算周常_圣祖下次触发时间")


def _weekly_shengzu_in_window(now: datetime) -> bool:
    return (
        now.weekday() == WEEKLY_SHENGZU_WEEKDAY
        and WEEKLY_SHENGZU_TRIGGER_TIME <= now.time() <= WEEKLY_SHENGZU_WINDOW_END
    )


class WeeklyShengzuTaskMixin:
    """执行周日 20:00-20:05 的“周常_圣祖”闭环。"""

    def _open_weekly_shengzu_from_daily(
        self,
        runtime: Any,
        *,
        max_scrolls: int,
        transition_timeout: float,
    ):
        yield from runtime.goto_view(69)
        for scroll_index in range(max(0, int(max_scrolls)) + 1):
            frame = runtime.cur_frame(update=True)
            items = runtime.find_floating_items_by_anchor_text(
                69,
                "任务块模板",
                "标题",
                "圣祖",
                container_shape="滚动窗口",
                frame_data_url=frame,
                match_mode="contains",
            )
            if len(items) > 1:
                raise RuntimeError("周常_圣祖：#69 中“圣祖”任务块不唯一，停止点击")
            if len(items) == 1:
                item = items[0]
                if not runtime.floating_item_is_fully_inside(item, "滚动窗口"):
                    raise RuntimeError("周常_圣祖：#69“圣祖”任务块位于滚动窗口边缘，停止点击")
                if not runtime.floating_item_field_is_inside(item, "任务状态", "滚动窗口"):
                    raise RuntimeError("周常_圣祖：#69“圣祖”任务块的前往按钮不在滚动窗口内，停止点击")
                status_text = runtime.read_floating_item_field(
                    item,
                    "任务状态",
                    frame_data_url=frame,
                )
                if "前往" not in status_text:
                    raise RuntimeError(f"周常_圣祖：#69“圣祖”任务状态不是前往：{status_text!r}")
                runtime.click_floating_item_field(item, "任务状态")
                yield from runtime.wait_view(
                    384,
                    timeout=transition_timeout,
                    label="周常_圣祖：等待入口页 #384",
                )
                return
            if scroll_index >= max_scrolls:
                break
            changed = yield from runtime.scroll_shape_content(69, "滚动窗口")
            if not changed:
                break
        raise RuntimeError("周常_圣祖：滚动 #69 后仍未找到“圣祖”任务块")

    def _goto_weekly_shengzu(
        self,
        runtime: Any,
        *,
        max_scrolls: int,
        transition_timeout: float,
    ):
        scene_id, _score, _frame = runtime.current_scene([383, 384, 385, 69, 34], update=True)
        if scene_id == 385:
            return
        if scene_id == 383:
            yield from runtime.goto_view(385)
            return
        if scene_id != 384:
            yield from self._open_weekly_shengzu_from_daily(
                runtime,
                max_scrolls=max_scrolls,
                transition_timeout=transition_timeout,
            )
        yield from runtime.click_shape_center_then_view(
            384,
            "前往",
            385,
            timeout=transition_timeout,
            label="周常_圣祖：等待挑战页 #385",
        )

    def _execute_weekly_shengzu_task(
        self,
        ctx: dict[str, Any],
        stop_event: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(payload or {})
        now = _now()
        next_time = _next_weekly_shengzu_trigger(now).strftime("%Y-%m-%d %H:%M:%S")
        if not _weekly_shengzu_in_window(now):
            return {
                "result": "success",
                "message": "周常_圣祖：当前不在周日 20:00:00-20:05:00 窗口，未执行游戏操作",
                "next_time": next_time,
                "current_scene": None,
            }

        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少周常_圣祖资产树路径，无法执行作业")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        transition_timeout = float(payload.get("transition_timeout_seconds") or 20.0)
        max_scrolls = max(0, int(payload.get("max_daily_scrolls") or 30))
        challenge_wait_seconds = max(30.0, float(payload.get("challenge_wait_seconds") or 30.0))

        yield from self._goto_weekly_shengzu(
            runtime,
            max_scrolls=max_scrolls,
            transition_timeout=transition_timeout,
        )
        yield from runtime.wait_click(385, "前往挑战")
        yield from runtime.wait_action_settle(challenge_wait_seconds)
        yield from runtime.goto_view(34)
        self._log("success", "周常_圣祖：已挑战并等待 30 秒，安全返回世界 #34")
        return {
            "result": "success",
            "message": "周常_圣祖完成，已返回世界 #34",
            "next_time": next_time,
            "current_scene": 34,
        }
