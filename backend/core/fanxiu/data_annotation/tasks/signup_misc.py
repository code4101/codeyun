from __future__ import annotations

import time
from typing import Any


def daily_signup_traversal_limits(payload: dict[str, Any] | None = None) -> tuple[float, int, int]:
    """Return the absolute flow budget, item cap, and scroll cap."""

    options = payload if isinstance(payload, dict) else {}
    timeout_seconds = max(30.0, float(options.get("signup_flow_timeout_seconds") or 300.0))
    max_items = max(1, int(options.get("signup_max_items") or 20))
    max_scrolls = max(1, int(options.get("signup_max_scrolls") or 30))
    return timeout_seconds, max_items, max_scrolls


def ensure_daily_signup_traversal_budget(
    *,
    deadline: float,
    now: float,
    claimed: int,
    scrolls: int,
    max_items: int,
    max_scrolls: int,
    phase: str,
    before_item: bool = False,
    before_scroll: bool = False,
) -> None:
    """Fail closed when the one-attempt signup traversal budget is exhausted."""

    if float(now) >= float(deadline):
        raise TimeoutError(
            f"日常_报名：{phase}超过本次绝对截止时间；"
            f"已领取 {claimed} 项、已滚动 {scrolls} 次"
        )
    if before_item and int(claimed) >= int(max_items):
        raise RuntimeError(
            f"日常_报名：报名项超过单次上限 {max_items}；"
            "拒绝继续点击，防止重复条目长期占用 Kernel"
        )
    if before_scroll and int(scrolls) >= int(max_scrolls):
        raise RuntimeError(
            f"日常_报名：报名列已滚动 {scrolls} 次，达到单次上限 {max_scrolls}；"
            "仍未确认列表底部，拒绝继续滚动"
        )


class SignupMiscTaskMixin:
    def 日常报名流程(self, context: Any):
        payload = getattr(context, "payload", {})
        payload = payload if isinstance(payload, dict) else {}
        timeout_seconds, max_items, max_scrolls = daily_signup_traversal_limits(payload)
        deadline = time.monotonic() + timeout_seconds
        起点状态 = yield from self._日常报名进入日常页(context)
        ensure_daily_signup_traversal_budget(
            deadline=deadline,
            now=time.monotonic(),
            claimed=0,
            scrolls=0,
            max_items=max_items,
            max_scrolls=max_scrolls,
            phase="进入日常页",
        )
        if 起点状态 == "活动页":
            yield from self._日常报名返回世界(context)
            context.set_next_time(self._next_daily_boss_reset_time_text())
            return {"result": "success", "claimed": 1, "activity_opened": True, "evidence": "activity_page"}
        if 起点状态 != "报名页":
            yield from self._日常报名打开活动报名(context)

        报名结果 = yield from self._日常报名处理报名列(
            context,
            deadline=deadline,
            max_items=max_items,
            max_scrolls=max_scrolls,
        )
        领取数量 = int(报名结果.get("claimed") or 0)
        yield from self._日常报名返回日常页(context)
        yield from self._日常报名返回世界(context)
        if 领取数量 <= 0:
            if 报名结果.get("bottom_confirmed") and 报名结果.get("saw_signed_item"):
                context.set_next_time(self._next_daily_boss_reset_time_text())
                return {
                    "result": "success",
                    "claimed": 0,
                    "signup_page_opened": True,
                    "evidence": "all_items_already_signed",
                }
            context.set_next_time(self._next_daily_boss_reset_time_text())
            return {
                "result": "success",
                "claimed": 0,
                "message": "日常_报名：未领取任何报名项，不能确认最后两条已处理，稍后重试",
            }
        context.set_next_time(self._next_daily_boss_reset_time_text())
        return {"result": "success", "claimed": 领取数量, "signup_page_opened": True, "evidence": "claimed_rewards"}

    def _日常报名进入日常页(self, context: Any):
        scene_id, _score, frame = (yield from context.current_scene([69, 34], update=True))
        text = context.ocr_text(frame)
        if self._日常报名文本是报名后活动页(text):
            return "活动页"
        if self._日常报名文本是报名页(text):
            return "报名页"
        if scene_id == 69:
            return "日常页"
        if scene_id == 34:
            yield from context.wait_click_then_scene(34, "日常", 69, label="日常_报名：从世界进入日常 #69")
            return "日常页"
        yield from context.go_scene(69)
        return "日常页"

    def _日常报名文本是报名页(self, text: str) -> bool:
        normalized = str(text or "")
        return "报名" in normalized and "活动时间" in normalized and ("已报名" in normalized or "待报名" in normalized)

    def _日常报名文本是报名后活动页(self, text: str) -> bool:
        normalized = str(text or "")
        compact = "".join(normalized.split())
        return bool(
            ("道法争锋" in compact and ("当前排名" in compact or "剩余挑战次数" in compact))
            or ("活动将于每周日" in compact and "可挑战" in compact and "当前排名" in compact)
        )

    def _日常报名打开活动报名(self, context: Any) -> str:
        current_text = context.ocr_text(context.cur_frame(update=True))
        if self._日常报名文本是报名页(current_text):
            return "报名页"
        # #75 is a reference frame for the bottom activity strip rendered on
        # the real #69 daily page; it is not an independently recognizable
        # scene.  Guard #69, locate/click the #75 Shape on that fresh frame,
        # then verify the real successor #23 explicitly.
        for attempt in range(2):
            scene_id, _score, frame = yield from context.current_scene(
                [69, 23],
                update=True,
            )
            if int(scene_id) == 23:
                break
            if int(scene_id) != 69:
                raise RuntimeError(
                    "日常_报名：活动报名参考帧只允许在 #69 使用，"
                    f"当前 #{scene_id or 'unknown'}"
                )
            context.click_shape(75, "活动报名", frame_data_url=frame)
            landed = yield from context.wait_scene(
                [23],
                wait=20.0,
                label="日常_报名：打开活动报名 #23",
            )
            landed_id = int(getattr(landed, "id", landed))
            if landed_id == 23:
                break
            if landed_id != 69 or attempt >= 1:
                raise RuntimeError(
                    "日常_报名：点击活动报名后未进入 #23，"
                    f"实际 #{landed_id}"
                )
            yield from context.wait_action_settle(1.0)
        yield from context.wait_any(
            {
                "scene": context.scene_visible(23),
                "text": context.ocr_matches(self._日常报名文本是报名页, label="日常_报名：报名列表 OCR"),
            },
            label="日常_报名：等待报名列表 #23",
        )
        return "报名页"

    def _日常报名处理报名列(
        self,
        context: Any,
        *,
        deadline: float,
        max_items: int,
        max_scrolls: int,
    ) -> dict[str, Any]:
        领取数量 = 0
        滚动次数 = 0
        无变化确认次数 = 0
        看到已报名项 = False
        payload = getattr(context, "payload", {})
        payload = payload if isinstance(payload, dict) else {}
        底部确认轮数 = max(
            1,
            min(3, int(payload.get("signup_bottom_confirmations", 2) or 2)),
        )
        # The initial top row can be hidden by the announcement overlay.
        # Probe its annotated button once; scrolling later retains overlap.
        已处理行: set[int] = set()
        领取成功 = yield from self._日常报名点击并处理(
            context, lambda: context.click_shape_center(23, "第1个报名"),
        )
        领取数量 += int(领取成功)
        看到已报名项 = not 领取成功
        while True:
            ensure_daily_signup_traversal_budget(
                deadline=deadline,
                now=time.monotonic(),
                claimed=领取数量,
                scrolls=滚动次数,
                max_items=max_items,
                max_scrolls=max_scrolls,
                phase="扫描报名列",
            )
            frame = context.cur_frame(update=True)
            已报名项 = context.ocr_row_clicks_in_shape(
                23,
                "报名列",
                include=("已报名",),
                frame_data_url=frame,
            )
            看到已报名项 = 看到已报名项 or bool(已报名项)
            matches = context.ocr_row_clicks_in_shape(
                23,
                "报名列",
                include=("报名",),
                exclude=("已报名",),
                click_target="unoccluded_text",
                frame_data_url=frame,
            )
            matches = [m for m in matches if not any(abs(round(m[1]) - y) <= 24 for y in 已处理行)]
            if matches:
                ensure_daily_signup_traversal_budget(
                    deadline=deadline,
                    now=time.monotonic(),
                    claimed=领取数量,
                    scrolls=滚动次数,
                    max_items=max_items,
                    max_scrolls=max_scrolls,
                    phase="领取报名项",
                    before_item=True,
                )
                x, y, text = matches[0]
                领取成功 = yield from self._日常报名点击并处理(
                    context, lambda: context.click_frame_point(23, x, y),
                )
                已处理行.add(round(y))
                领取数量 += int(领取成功)
                看到已报名项 = 看到已报名项 or not 领取成功
                无变化确认次数 = 0
                continue

            # Once a no-change probe is observed, allow the configured second
            # bottom confirmation even when the first probe landed exactly on
            # the traversal cap. The absolute deadline still bounds that
            # confirmation and any changed result is rejected next round.
            if 无变化确认次数 == 0:
                ensure_daily_signup_traversal_budget(
                    deadline=deadline,
                    now=time.monotonic(),
                    claimed=领取数量,
                    scrolls=滚动次数,
                    max_items=max_items,
                    max_scrolls=max_scrolls,
                    phase="查找报名列底部",
                    before_scroll=True,
                )
            滚动次数 += 1
            滚动有变化 = yield from context.scroll_shape_content(23, "报名列", ratio=0.7)
            if 滚动有变化:
                已处理行.clear()
                无变化确认次数 = 0
                continue
            无变化确认次数 += 1
            if 无变化确认次数 >= max(1, 底部确认轮数):
                break
        return {
            "claimed": 领取数量,
            "bottom_confirmed": True,
            "saw_signed_item": 看到已报名项,
        }

    def _日常报名点击并处理(self, context: Any, click: Any) -> bool:
        """A signed row toggles details; an unsigned row opens reward #24.

        Wait the full five-second window before treating #23 as the detail
        branch. Reuse the exact click to dismiss it, never retry opening #24.
        """
        click()
        match = yield from context.wait_scene([24], wait=5)
        scene_id = int(match.scene_id)
        if scene_id == 24:
            yield from context.wait_click(24, "领取")
            landing = yield from self._日常报名等待领取后落点(context)
            if landing != "报名页":
                raise RuntimeError(f"日常_报名：领取后落点为{landing}，未返回报名页")
            return True
        if scene_id != 23:
            raise RuntimeError(f"日常_报名：点击报名后意外到达 #{scene_id}")
        click()
        yield from context.wait_action_settle(0.8)
        match = yield from context.wait_scene([23], wait=5)
        if int(match.scene_id) != 23:
            raise RuntimeError("日常_报名：收起活动详情后未恢复 #23")
        return False

    def _日常报名等待领取后落点(self, context: Any) -> str:
        return (
            yield from context.wait_any(
                {
                    "报名页": context.scene_visible(23),
                    "日常页": context.scene_visible(69),
                    "世界": context.scene_visible(34),
                    "绿瓶": context.scene_visible(20),
                    "报名文本": context.ocr_matches(self._日常报名文本是报名页, label="日常_报名：领取后报名页 OCR"),
                },
                label="日常_报名：等待领取后落点",
            )
        )

    def _日常报名返回日常页(self, context: Any):
        scene_id, _score, frame = (yield from context.current_scene([23, 69, 34], update=True))
        text = context.ocr_text(frame)
        if scene_id in (69, 34):
            return
        if scene_id != 23 and not self._日常报名文本是报名页(text):
            return
        if all(hasattr(context, name) for name in ("click_shape_center", "wait_action_settle")):
            context.click_shape_center(23, "返回")
            yield from context.wait_action_settle(1.0)
            return
        yield from context.wait_click(23, "返回")

    def _日常报名返回世界(self, context: Any):
        scene_id, _score, frame = (yield from context.current_scene([69, 34], update=True))
        text = context.ocr_text(frame)
        if scene_id == 34:
            return
        if self._日常报名文本是报名后活动页(text):
            for _attempt in range(3):
                context.click_frame_point(23, 80.0, 1482.0)
                if hasattr(context, "wait_action_settle"):
                    yield from context.wait_action_settle(1.0)
                else:
                    yield from context.wait_any(
                        {
                            "scene": context.scene_visible(34),
                            "daily": context.scene_visible(69),
                        },
                        label="日常_报名：等待活动页返回",
                    )
                scene_id, _score, frame = (yield from context.current_scene([69, 34], update=True))
                text = context.ocr_text(frame)
                if scene_id == 34:
                    return
                if scene_id == 69 or ("日常" in text and "活跃度" in text):
                    break
                if not self._日常报名文本是报名后活动页(text):
                    break
            if self._日常报名文本是报名后活动页(text):
                raise RuntimeError("日常_报名：活动页返回后仍停留在道法争锋，请检查该页返回标注")
        if scene_id == 69 or ("日常" in text and "活跃度" in text):
            if all(hasattr(context, name) for name in ("click_shape_center", "wait_action_settle")):
                context.click_shape_center(69, "退出")
                yield from context.wait_action_settle(1.0)
            else:
                yield from context.wait_click(69, "退出")
            yield from context.wait_any(
                {
                    "scene": context.scene_visible(34),
                },
                label="日常_报名：等待返回世界 #34",
            )
            return
        yield from context.go_scene(34)
