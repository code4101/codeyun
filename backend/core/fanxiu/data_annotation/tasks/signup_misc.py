from __future__ import annotations

from typing import Any


class SignupMiscTaskMixin:
    def 日常报名流程(self, context: Any):
        起点状态 = yield from self._日常报名进入日常页(context)
        if 起点状态 == "活动页":
            yield from self._日常报名返回世界(context)
            context.set_next_time(self._next_daily_boss_reset_time_text())
            return {"result": "success", "claimed": 1, "activity_opened": True, "evidence": "activity_page"}
        if 起点状态 != "报名页":
            yield from self._日常报名打开活动报名(context)

        报名结果 = yield from self._日常报名处理报名列(context)
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
        scene_id, _score, frame = context.current_scene([69, 34], update=True)
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
        if hasattr(context, "wait_click_then_scene"):
            yield from context.wait_click_then_scene(75, "活动报名", 23, label="日常_报名：打开活动报名 #23")
        else:
            yield from context.wait_click(75, "活动报名")
            yield from context.wait_scene(23)
        yield from context.wait_any(
            {
                "scene": context.scene_visible(23),
                "text": context.ocr_matches(self._日常报名文本是报名页, label="日常_报名：报名列表 OCR"),
            },
            label="日常_报名：等待报名列表 #23",
        )
        return "报名页"

    def _日常报名处理报名列(self, context: Any) -> dict[str, Any]:
        领取数量 = 0
        无变化确认次数 = 0
        看到已报名项 = False
        payload = getattr(context, "payload", {})
        payload = payload if isinstance(payload, dict) else {}
        底部确认轮数 = int(payload.get("signup_bottom_confirmations", 2) or 2)
        同项打开上限 = max(1, int(payload.get("signup_claim_open_attempts", 3) or 3))
        上次报名项: tuple[int, str] | None = None
        同项打开次数 = 0
        while True:
            已报名项 = context.ocr_row_clicks_in_shape(
                23,
                "报名列",
                include=("已报名",),
            )
            看到已报名项 = 看到已报名项 or bool(已报名项)
            matches = context.ocr_row_clicks_in_shape(
                23,
                "报名列",
                include=("报名",),
                exclude=("已报名",),
                click_target="unoccluded_text",
            )
            if matches:
                x, y, text = matches[0]
                当前报名项 = (round(y), str(text or "").strip())
                if 当前报名项 == 上次报名项:
                    同项打开次数 += 1
                else:
                    上次报名项 = 当前报名项
                    同项打开次数 = 1
                context.click_frame_point(23, x, y)
                if not (yield from self._日常报名等待领取页(context)):
                    if 同项打开次数 >= 同项打开上限:
                        raise RuntimeError(
                            f"日常_报名：同一报名项连续 {同项打开次数} 次未打开领取页 #24："
                            f"{text!r}；可能存在公告遮挡或入口状态异常"
                        )
                    if hasattr(context, "wait_action_settle"):
                        yield from context.wait_action_settle(1.0)
                    continue
                yield from context.wait_click(24, "领取")
                领取数量 += 1
                无变化确认次数 = 0
                上次报名项 = None
                同项打开次数 = 0
                领取后落点 = yield from self._日常报名等待领取后落点(context)
                if 领取后落点 != "报名页":
                    return {
                        "claimed": 领取数量,
                        "bottom_confirmed": False,
                        "saw_signed_item": 看到已报名项,
                    }
                continue

            滚动有变化 = yield from context.scroll_shape_content(23, "报名列")
            if 滚动有变化:
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

    def _日常报名等待领取页(self, context: Any) -> bool:
        try:
            yield from context.wait_scene(24)
            return True
        except TimeoutError:
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
        scene_id, _score, frame = context.current_scene([23, 69, 34], update=True)
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
        scene_id, _score, frame = context.current_scene([69, 34], update=True)
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
                scene_id, _score, frame = context.current_scene([69, 34], update=True)
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
