from __future__ import annotations

import re
import threading
import time
from typing import Any, Iterable

YUANDING_ACTIVITY_NAME = "缘定三生"
YUANDING_MAIN_SCENE_ID = 249


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("：", ":")


def exact_fragment(
    fragments: Iterable[dict[str, Any]],
    target: str,
) -> dict[str, Any] | None:
    normalized_target = _normalized_text(target)
    candidates = [
        item
        for item in fragments
        if isinstance(item, dict)
        and _normalized_text(item.get("text")) == normalized_target
        and float(item.get("w") or 0) > 0
        and float(item.get("h") or 0) > 0
    ]
    return candidates[0] if len(candidates) == 1 else None


def fragment_center(fragment: dict[str, Any]) -> tuple[float, float]:
    return (
        float(fragment["x"]) + float(fragment["w"]) / 2,
        float(fragment["y"]) + float(fragment["h"]) / 2,
    )


def gift_tab_fragment(
    fragments: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    candidates = [
        item
        for item in fragments
        if isinstance(item, dict)
        and _normalized_text(item.get("text")) == "礼包"
        and float(item.get("x") or 0) >= 700
        and float(item.get("y") or 0) >= 1350
        and float(item.get("w") or 0) > 0
        and float(item.get("h") or 0) > 0
    ]
    return candidates[0] if len(candidates) == 1 else None


def yuanding_store_state(
    fragments: Iterable[dict[str, Any]],
    full_text: str,
) -> str:
    items = list(fragments)
    text = _normalized_text(full_text)
    if "冲榜商店" not in text:
        return "loading"
    if exact_fragment(items, "免费") is not None and "每日限购:1" in text:
        return "claimable"
    if "每日限购:0" in text and exact_fragment(items, "免费") is None:
        return "claimed"
    paid_goods_loaded = "VIP3特惠灵石礼包" in text and "适度娱乐" in text and "理性消费" in text
    if paid_goods_loaded and "免费冲榜礼包" not in text and exact_fragment(items, "免费") is None:
        # On a fresh visit after claiming, the game removes the whole free
        # card instead of retaining the transient 每日限购：0 view.
        return "claimed"
    return "loading"


def yuanding_page_state(
    scene_id: int | None,
    fragments: Iterable[dict[str, Any]],
    full_text: str,
) -> str:
    items = list(fragments)
    text = _normalized_text(full_text)
    if scene_id == 34:
        return "world"
    if "冲榜商店" in text and (
        "免费冲榜礼包" in text
        or ("VIP3特惠灵石礼包" in text and "适度娱乐" in text and "理性消费" in text)
    ):
        return "store"
    if exact_fragment(items, "查看详情") is not None and "活动时间" in text:
        return "intro"
    if gift_tab_fragment(items) is not None and ("缘宠三生" in text or YUANDING_ACTIVITY_NAME in text):
        return "main"
    if scene_id == 66 and "日程" in text:
        return "schedule"
    return "unknown"


class YuandingSanshengTaskMixin:
    def _yuanding_result(
        self,
        payload: dict[str, Any],
        *,
        outcome: str,
        message: str,
    ) -> dict[str, Any]:
        return {
            "result": "success",
            "outcome": outcome,
            "message": message,
            "current_scene": 34,
        }

    def _wait_yuanding_page(
        self,
        runtime: Any,
        stop_event: threading.Event,
        expected_state: str,
        *,
        timeout_seconds: float,
    ):
        deadline = time.monotonic() + max(0.5, float(timeout_seconds))
        last_text = ""
        while time.monotonic() < deadline:
            self._raise_if_stopped(stop_event)
            frame = runtime.cur_frame(update=True)
            fragments = runtime.ocr_fragments(frame)
            last_text = runtime.ocr_text(frame)
            scene_id, _score, _frame = runtime.current_scene(
                [34, 66, YUANDING_MAIN_SCENE_ID],
                update=False,
            )
            if yuanding_page_state(scene_id, fragments, last_text) == expected_state:
                return frame, fragments
            yield from runtime.wait_action_settle(0.35)
        raise RuntimeError(
            f"缘定三生_每日礼包：等待 {expected_state} 超时，末帧={_normalized_text(last_text)[:180]}"
        )

    def _execute_yuanding_sansheng_daily_gift_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        payload = dict(payload or {})
        payload.pop("__scheduler_task_id", None)
        payload["manage_schedule"] = False
        runtime = self._fanxiu_runtime(ctx, ctx["asset_tree_path"], stop_event=stop_event)
        page_timeout = float(payload.get("page_timeout_seconds") or 20.0)
        entry_timeout = float(payload.get("entry_timeout_seconds") or 30.0)

        frame = runtime.cur_frame(update=True)
        fragments = runtime.ocr_fragments(frame)
        text = runtime.ocr_text(frame)
        scene_id, _score, _frame = runtime.current_scene(
            [34, 66, YUANDING_MAIN_SCENE_ID],
            update=False,
        )
        state = yuanding_page_state(scene_id, fragments, text)
        if state == "unknown":
            raise RuntimeError(
                f"缘定三生_每日礼包：当前场景 #{scene_id} 不是可安全恢复的世界/日程/活动页，拒绝点击"
            )

        if state == "world":
            yield from runtime.goto_view(66)
            state = "schedule"

        if state == "schedule":
            deadline = time.monotonic() + max(1.0, entry_timeout)
            entry: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                self._raise_if_stopped(stop_event)
                frame = runtime.cur_frame(update=True)
                entry = exact_fragment(runtime.ocr_fragments(frame), YUANDING_ACTIVITY_NAME)
                if entry is not None:
                    break
                yield from runtime.wait_action_settle(0.5)
            if entry is None:
                runtime.click_shape_center(66, "返回")
                yield from runtime.wait_view(34, timeout=page_timeout, label="缘定三生：无活动时返回世界")
                return self._yuanding_result(
                    payload,
                    outcome="activity_unavailable",
                    message="缘定三生_每日礼包：当前日程未发现活动入口",
                )
            runtime.click_frame_point(66, *fragment_center(entry))
            _frame, fragments = yield from self._wait_yuanding_page(
                runtime,
                stop_event,
                "intro",
                timeout_seconds=page_timeout,
            )
            state = "intro"

        if state == "intro":
            details = exact_fragment(fragments, "查看详情")
            if details is None:
                raise RuntimeError("缘定三生_每日礼包：活动介绍层未唯一识别到“查看详情”")
            runtime.click_frame_point(66, *fragment_center(details))
            _frame, fragments = yield from self._wait_yuanding_page(
                runtime,
                stop_event,
                "main",
                timeout_seconds=page_timeout,
            )
            state = "main"

        if state == "main":
            gift_tab = gift_tab_fragment(fragments)
            if gift_tab is None:
                raise RuntimeError("缘定三生_每日礼包：活动主页未唯一识别到右下角“礼包”页签")
            runtime.click_frame_point(YUANDING_MAIN_SCENE_ID, *fragment_center(gift_tab))
            yield from self._wait_yuanding_page(
                runtime,
                stop_event,
                "store",
                timeout_seconds=page_timeout,
            )
            state = "store"

        if state != "store":
            raise RuntimeError(f"缘定三生_每日礼包：无法处理页面状态 {state}")

        deadline = time.monotonic() + max(1.0, page_timeout)
        stable_state = ""
        stable_count = 0
        free: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            self._raise_if_stopped(stop_event)
            frame = runtime.cur_frame(update=True)
            fragments = runtime.ocr_fragments(frame)
            text = runtime.ocr_text(frame)
            store_state = yuanding_store_state(fragments, text)
            if store_state != "loading" and store_state == stable_state:
                stable_count += 1
            else:
                stable_state = store_state
                stable_count = 1 if store_state != "loading" else 0
            if stable_count >= 2:
                free = exact_fragment(fragments, "免费")
                break
            yield from runtime.wait_action_settle(0.35)
        if stable_count < 2:
            raise RuntimeError("缘定三生_每日礼包：礼包页未形成连续两帧稳定状态")

        claimed_now = False
        if stable_state == "claimable":
            if free is None:
                raise RuntimeError("缘定三生_每日礼包：存在每日限购 1，但未唯一识别到“免费”")
            runtime.click_frame_point(YUANDING_MAIN_SCENE_ID, *fragment_center(free))
            claimed_count = 0
            verify_deadline = time.monotonic() + max(5.0, page_timeout)
            while time.monotonic() < verify_deadline:
                self._raise_if_stopped(stop_event)
                frame = runtime.cur_frame(update=True)
                fragments = runtime.ocr_fragments(frame)
                if yuanding_store_state(fragments, runtime.ocr_text(frame)) == "claimed":
                    claimed_count += 1
                else:
                    claimed_count = 0
                if claimed_count >= 2:
                    claimed_now = True
                    break
                yield from runtime.wait_action_settle(0.35)
            if not claimed_now:
                raise RuntimeError("缘定三生_每日礼包：点击免费礼包后未确认每日限购 0")

        runtime.click_shape_center(YUANDING_MAIN_SCENE_ID, "返回")
        yield from runtime.wait_view(66, timeout=page_timeout, label="缘定三生：返回日程")
        runtime.click_shape_center(66, "返回")
        yield from runtime.wait_view(34, timeout=page_timeout, label="缘定三生：返回世界")
        return self._yuanding_result(
            payload,
            outcome=("claimed" if claimed_now else "already_claimed"),
            message=(
                "缘定三生_每日礼包：免费冲榜礼包已领取并确认每日限购 0"
                if claimed_now
                else "缘定三生_每日礼包：今日免费冲榜礼包已领取"
            ),
        )


__all__ = [
    "YUANDING_ACTIVITY_NAME",
    "YUANDING_MAIN_SCENE_ID",
    "YuandingSanshengTaskMixin",
    "exact_fragment",
    "fragment_center",
    "gift_tab_fragment",
    "yuanding_page_state",
    "yuanding_store_state",
]
