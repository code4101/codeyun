from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation.ocr_spatial import (
    find_text_matches,
    locate_text_box,
    query_spatial_ocr,
    select_text_match,
)
from backend.core.fanxiu.data_annotation.redpacket_state import (
    QMCH_REWARD_EVENT_KEY,
    classify_redpacket_runtime_snapshot,
    classify_redpacket_runtime_routes,
    read_current_redpacket_state,
    recover_redpacket_runtime_snapshot,
)
from backend.core.fanxiu.instrumentation.chat import (
    read_chat_channel_gui_target,
    read_repeated_chat_phrase,
    select_chat_channel_title_patterns,
    select_chat_row_anchors,
)
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
from backend.core.fanxiu.client.mumu_control import keyevents_mumu_adb, text_mumu_adb


REDPACKET_OCR_PATTERN = re.compile(r"首领[累猎]杀|奖赏|第一|获赠|红包")
REDPACKET_HISTORY_PATTERN = re.compile(r"你领取了|已领取")
REDPACKET_SELF_CHECK_INTERVAL_SECONDS = 12 * 60 * 60


def _now() -> datetime:
    return datetime.now()


class DailyRedpacketTaskMixin:
    """执行由巡检调度、由当前画面独立授权的“日常_红包”领取闭环。"""

    # Runtime decides whether work exists; scenes and Shapes locate controls.
    # A missing badge/card is an observation failure, never proof of completion.
    # Completion requires fresh Runtime evidence; leave through annotated exits.

    def _prepare_daily_redpacket_world(self, context: Any, *, transition_timeout: float):
        """Unwind an interrupted chat input layer through formal GUI shapes."""

        scene_id, _score, _frame = (yield from context.current_scene(
            [390, 30, 332, 34],
            update=True,
        ))
        if scene_id in {390, 30}:
            self._log(
                "action",
                f"日常_红包：从中断遗留 #{scene_id} 仅通过聊天背景 [返回] 退出",
            )
            landing = yield from self._return_daily_redpacket_group_to_list(
                context,
                transition_timeout=transition_timeout,
            )
            if int(landing.id or 0) == 34:
                return landing
        return (yield from context.go_scene(34))

    def _daily_redpacket_record_next_check(self, payload: dict[str, Any], message: str) -> str:
        interval_seconds = max(
            300,
            int(payload.get("interval_seconds") or REDPACKET_SELF_CHECK_INTERVAL_SECONDS),
        )
        next_time = (_now() + timedelta(seconds=interval_seconds)).strftime("%Y-%m-%d %H:%M:%S")
        task_id = str(payload.get("__scheduler_task_id") or "daily-redpacket")
        self._persist_scheduler_task_next_time(
            task_id,
            next_time,
        )
        self._log("success", f"日常_红包：{message}，下次 {next_time}")
        return next_time

    def _daily_redpacket_result(
        self,
        payload: dict[str, Any],
        message: str,
        *,
        opened_count: int = 0,
        current_scene: int = 34,
        unclaimable_uids: list[str] | None = None,
    ) -> dict[str, Any]:
        next_time = self._daily_redpacket_record_next_check(payload, message)
        return {
            "result": "success",
            "message": f"{message}，下次 {next_time}",
            "current_scene": int(current_scene),
            "opened_count": int(opened_count),
            "unclaimable_uids": list(unclaimable_uids or []),
        }

    @staticmethod
    def _daily_redpacket_runtime_candidates() -> dict[str, Any]:
        """Return fresh trigger facts without granting any GUI action."""

        current = read_current_redpacket_state()
        if (current.get("evidence_levels") or {}).get("structural"):
            return current
        return classify_redpacket_runtime_snapshot(
            recover_redpacket_runtime_snapshot()
        )

    def _daily_redpacket_require_fresh_uid_snapshot(self, *, phase: str) -> dict[str, Any]:
        snapshot = self._daily_redpacket_runtime_candidates()
        levels = snapshot.get("evidence_levels") or {}
        if not levels.get("structural") or not levels.get("semantic"):
            raise RuntimeError(
                f"日常_红包：{phase} Runtime 结构或语义不完整，拒绝以不新鲜事实继续："
                f"{snapshot.get('reason') or snapshot.get('trigger_reason') or 'unknown'}"
            )
        return {
            "uids": frozenset(str(uid) for uid in snapshot.get("pending_uids") or []),
            "pending_count": int(snapshot.get("pending_count") or 0),
            "snapshot": snapshot,
        }

    def _daily_redpacket_runtime_route_plan(self) -> dict[str, Any]:
        """Select the business route before either route performs a GUI action."""

        snapshot = self._daily_redpacket_runtime_candidates()
        plan = {
            **classify_redpacket_runtime_routes(snapshot),
            # Route and trigger must come from the same current snapshot.
            "trigger_ready": bool(snapshot.get("trigger_ready")),
        }
        if plan.get("status") != "ready":
            raise RuntimeError(
                "日常_红包：fresh Runtime 无法唯一分流，拒绝进入旧普通红包流程："
                f"{plan.get('reason') or 'unknown'}"
            )
        terminal_items = self._daily_qmch_terminal_items(snapshot)
        terminal_uids = {str(item["uid"]) for item in terminal_items}
        if plan.get("route") == QMCH_REWARD_EVENT_KEY:
            active_items = [
                item
                for item in plan.get("qmch_reward_items") or []
                if str(item.get("uid") or "") not in terminal_uids
            ]
            if not active_items:
                deferred_ordinary_uids = [
                    str(uid)
                    for uid in plan.get("deferred_ordinary_uids") or []
                    if str(uid).strip()
                ]
                if deferred_ordinary_uids:
                    return {
                        **plan,
                        "route": "ordinary_chat",
                        "uids": deferred_ordinary_uids,
                        "qmch_reward_items": [],
                        "terminal_qmch_items": terminal_items,
                        "deferred_ordinary_uids": [],
                    }
                return {
                    **plan,
                    "route": "qmch_reward_terminal",
                    "uids": sorted(terminal_uids),
                    "qmch_reward_items": terminal_items,
                }
            plan = {
                **plan,
                "uids": [str(item["uid"]) for item in active_items],
                "qmch_reward_items": active_items,
            }
        elif plan.get("route") == "none" and terminal_items:
            return {
                **plan,
                "route": "qmch_reward_terminal",
                "uids": sorted(terminal_uids),
                "qmch_reward_items": terminal_items,
            }
        return plan

    def _wait_daily_redpacket_runtime_route_plan(
        self,
        context: Any,
        *,
        timeout_seconds: float,
        poll_seconds: float,
    ):
        """Wait for chat-opened Runtime data without initializing it ourselves."""

        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        last_error: FanxiuRuntimeMemoryError | None = None
        while time.monotonic() < deadline:
            try:
                return self._daily_redpacket_runtime_route_plan()
            except FanxiuRuntimeMemoryError as exc:
                if exc.code != "data_not_loaded":
                    raise
                last_error = exc
            yield from context.wait_action_settle(poll_seconds)
        raise RuntimeError(
            "日常_红包：打开聊天后 Chat.ChatGroup 仍未自然加载，拒绝在分流未知时继续"
        ) from last_error

    @staticmethod
    def _daily_qmch_terminal_items(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        """Return exact, fresh 9033 items whose claimed terminal is proven."""

        chat = (snapshot.get("sources") or {}).get("chat") or {}
        raw_items = [
            *(chat.get("special_event_items") or []),
            *(chat.get("items") or []),
            *(snapshot.get("items") or []),
        ]
        terminals: dict[str, dict[str, Any]] = {}
        for raw_item in raw_items:
            item = dict(raw_item) if isinstance(raw_item, dict) else {}
            uid = str(item.get("uid") or "").strip()
            reasons = {str(reason) for reason in item.get("exclusion_reasons") or []}
            if not (
                uid
                and item.get("id") == 5022
                and item.get("event_type") == 9033
                and item.get("event_key") == QMCH_REWARD_EVENT_KEY
                and item.get("channel") == 101
                and item.get("detail_loaded") is True
                and reasons.intersection({"server_rewarded", "detail_rewarded"})
            ):
                continue
            terminals[uid] = item
        return list(terminals.values())

    def _wait_daily_qmch_uid_terminal(
        self,
        context: Any,
        uid: str,
        *,
        timeout_seconds: float,
        poll_seconds: float,
    ):
        """Require a fresh same-UID rewarded terminal after the one-shot open."""

        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        while time.monotonic() < deadline:
            snapshot = self._daily_redpacket_runtime_candidates()
            levels = snapshot.get("evidence_levels") or {}
            if levels.get("structural") and levels.get("semantic"):
                terminal = next(
                    (
                        item
                        for item in self._daily_qmch_terminal_items(snapshot)
                        if str(item.get("uid") or "") == uid
                    ),
                    None,
                )
                if terminal is not None:
                    return terminal
            yield from context.wait_action_settle(poll_seconds)
        raise TimeoutError(
            f"日常_红包：鸿运福签打开后 fresh Runtime 未出现同 UID rewarded 终态：{uid}"
        )

    def _dispatch_daily_redpacket_runtime_route(
        self,
        context: Any,
        ctx: dict[str, Any],
        stop_event: Any,
        payload: dict[str, Any],
        route_plan: dict[str, Any],
    ):
        """Dispatch a non-ordinary route; missing business handlers fail closed."""

        route = str(route_plan.get("route") or "")
        if route != QMCH_REWARD_EVENT_KEY:
            raise RuntimeError(f"日常_红包：不支持的专用 Runtime route={route or 'unknown'}")
        handler = getattr(self, "_execute_daily_qmch_reward_route", None)
        if not callable(handler):
            raise RuntimeError(
                "日常_红包：识别到 9033/qmch_reward，专用福入口 handler 未配置；"
                "已硬禁止回落旧普通群聊红包流程"
            )
        return (yield from handler(
            context,
            ctx,
            stop_event,
            payload,
            route_plan,
        ))

    def _wait_daily_qmch_activity_row(
        self,
        context: Any,
        ctx: dict[str, Any],
        *,
        timeout_seconds: float,
        poll_seconds: float,
        anchors: list[str] | None = None,
        max_scrolls: int = 8,
    ):
        """Align the exact Runtime channel to one current #332 row."""

        image = (ctx.get("images") or {}).get(332)
        window = self._find_shape(image, "窗口") if isinstance(image, dict) else None
        if not isinstance(image, dict) or not isinstance(window, dict):
            raise RuntimeError("日常_红包：缺少 #332[窗口]，无法局部定位鸿运福签")
        window_box = self._box(window, image)
        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        last_text = ""
        scroll_count = 0
        while time.monotonic() < deadline:
            frame = context.cur_frame(update=True)
            cached = self._shared_spatial_ocr_result(ctx, frame)
            spatial = query_spatial_ocr(cached.get("tokens") or [], window_box)
            tokens = spatial.get("tokens") if isinstance(spatial.get("tokens"), list) else []
            last_text = "".join(str(token.get("text") or "") for token in tokens)
            for anchor in list(anchors or ("鸿运福签",)):
                match = select_text_match(
                    find_text_matches(tokens, anchor),
                    anchor,
                )
                if match is not None:
                    return frame, match
            if scroll_count >= max(0, int(max_scrolls)):
                break
            changed = yield from context.scroll_shape_content(
                332,
                "窗口",
                direction="down",
                ratio=0.5,
                unchanged_confirmations=2,
            )
            scroll_count += 1
            if not changed:
                break
            yield from context.wait_action_settle(poll_seconds)
        raise TimeoutError(
            "日常_红包：#332[窗口] 未唯一对齐到 Runtime 指定聊天行，"
            f"anchors={list(anchors or ())}，OCR={last_text[:200]}"
        )

    def _execute_daily_qmch_reward_route(
        self,
        context: Any,
        ctx: dict[str, Any],
        stop_event: Any,
        payload: dict[str, Any],
        route_plan: dict[str, Any],
    ):
        """Enter the exact 9033/5022 activity chat without using ordinary claims."""

        del stop_event
        uids = [str(uid) for uid in route_plan.get("uids") or [] if str(uid)]
        if len(uids) != 1:
            raise RuntimeError(f"日常_红包：qmch_reward 要求唯一 fresh UID，实际={uids}")
        uid = uids[0]
        channel = int(route_plan.get("channel") or 0)
        sub_id = int(route_plan.get("sub_id") or 0)
        if channel != 101 or sub_id <= 0:
            raise RuntimeError(
                f"日常_红包：qmch_reward route 非法，channel={channel}, sub_id={sub_id}"
            )
        transition_timeout = max(3.0, float(payload.get("transition_timeout_seconds") or 15.0))
        poll_seconds = max(0.2, float(payload.get("poll_seconds") or 0.8))

        # 9033/5022/channel=101 is already the dedicated activity-channel
        # contract proven by the fresh route plan above.  The GUI target is
        # therefore #332[活动] by business identity; consulting the optional
        # DBMgr.ChatGroup table again only duplicates that fact and breaks on
        # clients where the config table has not been naturally materialized.
        tab_label = "活动"
        phrase_fact = read_repeated_chat_phrase(channel, sub_id)
        phrase = str(phrase_fact.get("phrase") or "").strip()
        if not phrase_fact.get("ready") or not phrase:
            raise RuntimeError(
                "日常_红包：当前活动频道 Runtime 未形成唯一重复话术，拒绝发送："
                f"{phrase_fact.get('reason') or 'unknown'}"
            )
        # World auto-battle can temporarily replace the whole HUD with a
        # cinematic frame.  Require a fresh #34 identity before clicking the
        # annotated chat action.  Do not image-match the chat icon itself here:
        # its unread/red-packet corner badge is dynamic and can make the action
        # Shape score low even though the stable world HUD is fully available.
        entry_timeout = max(
            transition_timeout,
            float(payload.get("redpacket_confirm_seconds") or 60.0),
        )
        yield from context.wait_scene(
            [34],
            wait=entry_timeout,
            label="鸿运福签：等待世界战斗过场结束并确认世界页",
        )
        landing = yield from context.click_shape_center_then_scene(
            34,
            "聊天",
            332,
            333,
            timeout=transition_timeout,
            label="鸿运福签：等待聊天容器",
        )
        if int(landing.id or 0) == 333:
            yield from context.click_shape_center_then_scene(
                333,
                "聊天",
                332,
                timeout=transition_timeout,
                label="鸿运福签：从通讯录切回聊天页",
            )
        yield from context.click_shape_center_then_scene(
            332,
            tab_label,
            332,
            timeout=transition_timeout,
            label="鸿运福签：幂等切到活动消息",
        )
        _frame, row = yield from self._wait_daily_qmch_activity_row(
            context,
            ctx,
            timeout_seconds=transition_timeout,
            poll_seconds=poll_seconds,
            # 列表行身份与发送话术是两个正交真值。祝贺话术会出现在多个
            # 活动预览中，不能用作会话行定位；专用活动标题才是唯一入口。
            anchors=["鸿运福签"],
        )
        context.click_frame_point(332, float(row.x + row.w / 2), float(row.y + row.h / 2))
        yield from context.wait_scene(
            [30],
            wait=transition_timeout,
            label="鸿运福签：等待活动聊天页",
        )
        yield from context.wait_shape(
            673,
            "鸿运福签",
            timeout=transition_timeout,
            label="鸿运福签：确认专用活动聊天身份",
        )
        # Entering the exact activity chat naturally materializes its detail.
        # Re-read the same UID before sending: a previous attempt may already
        # have completed the claim even if the world-level snapshot still
        # exposed only the active summary.  This is the idempotency boundary
        # that prevents repeated phrase sends and repeated result overlays.
        entered_snapshot = self._daily_redpacket_runtime_candidates()
        entered_terminal = next(
            (
                item
                for item in self._daily_qmch_terminal_items(entered_snapshot)
                if str(item.get("uid") or "") == uid
            ),
            None,
        )
        if entered_terminal is not None:
            return_view = yield from self._return_daily_redpacket_group_to_list(
                context,
                transition_timeout=transition_timeout,
            )
            return_scene = int(return_view.id or 0)
            if return_scene == 332:
                yield from self._close_daily_redpacket_chat_to_world(
                    context,
                    transition_timeout=transition_timeout,
                )
            elif return_scene != 34:
                yield from context.go_scene(34)
            return self._daily_redpacket_result(
                payload,
                f"鸿运福签 UID={uid} 进入频道后确认已 rewarded，幂等零发送",
                opened_count=0,
                current_scene=34,
            )
        self._log(
            "success",
            f"鸿运福签：已按 fresh route channel={channel}, sub_id={sub_id} 进入活动聊天 #30",
        )
        # Runtime 已经给出当前活动频道的唯一话术；GUI 只负责进入对应聊天、
        # 输入、发送和领取。输入框可能保留上次中断的话术，因此先用现有
        # 文本编辑原语幂等清空；禁止再依赖复制图标或“输入框必须为空”。
        context.click_shape_center(673, "输入框空态")
        yield from context.wait_action_settle(0.5)
        keyevents_mumu_adb([
            "KEYCODE_MOVE_END",
            *["KEYCODE_DEL" for _ in range(64)],
        ])
        yield from context.wait_action_settle(0.25)
        text_mumu_adb(phrase)
        yield from context.wait_action_settle(0.5)
        # 输入法打开时，第一次点击发送热区只收起输入层。收起后先 OCR
        # 回读输入框，确认 Runtime 话术确实落入 GUI，再授权真正发送。
        context.click_shape_center_fast(673, "发送")
        yield from context.wait_action_settle(0.8)
        # Runtime 是当前频道唯一话术的权威来源；输入框会被遮挡并横向滚动，
        # 不得用局部 OCR 反向否定 Runtime，也不建立第二套话术真值。
        yield from context.wait_click_then_shape(
            673,
            "发送",
            397,
            "开",
            timeout=transition_timeout,
            max_clicks=1,
            label="鸿运福签：发送一次并等待真实开包弹窗",
        )

        # Sending can race with a claim performed elsewhere.  Re-check the
        # passive same-UID fact before authorizing the irreversible open.
        pre_open_snapshot = self._daily_redpacket_runtime_candidates()
        already_terminal = next(
            (
                item
                for item in self._daily_qmch_terminal_items(pre_open_snapshot)
                if str(item.get("uid") or "") == uid
            ),
            None,
        )
        opened_count = 0
        if already_terminal is None:
            yield from context.wait_click(397, "开", timeout=transition_timeout)
            opened_count = 1
            yield from context.wait_action_settle(poll_seconds)
            yield from self._wait_daily_qmch_uid_terminal(
                context,
                uid,
                timeout_seconds=transition_timeout,
                poll_seconds=poll_seconds,
            )
        # The same-UID rewarded fact above is the irreversible business
        # postcondition.  Do not keep clicking through the group/chat stack
        # after success: that creates no business value and can hit a message
        # context menu.  Only close the reward-detail overlay that this attempt
        # itself opened, then finish in the activity chat.
        detail_close_error = ""
        final_scene = 30
        if opened_count:
            try:
                context.click_shape_center(672, "弹窗外背景", x_ratio=0.1)
                yield from context.wait_scene(
                    [30],
                    wait=transition_timeout,
                    label="鸿运福签：关闭奖励详情回到活动聊天",
                )
            except Exception as exc:
                detail_close_error = f"{type(exc).__name__}: {exc}"
                final_scene = 672
                self._log(
                    "warning",
                    (
                        f"日常_红包：鸿运福签 UID={uid} 已确认 rewarded；"
                        f"奖励详情关闭失败但不回滚业务成功，保留当前现场：{detail_close_error}"
                    ),
                )
        message = f"鸿运福签 UID={uid} 已确认 rewarded 终态"
        if detail_close_error:
            message += "；奖励详情未关闭，已保留当前现场"
        else:
            message += "；已停在活动聊天，不再执行群聊离场点击"
        return self._daily_redpacket_result(
            payload,
            message,
            opened_count=opened_count,
            current_scene=final_scene,
        )

    def _daily_redpacket_verify_uid_postcondition(
        self,
        before: dict[str, Any],
        *,
        phase: str,
        legal_unclaimable: bool = False,
        require_reduction: bool = False,
    ) -> dict[str, Any]:
        after = self._daily_redpacket_require_fresh_uid_snapshot(phase=f"{phase}后")
        before_uids = set(before.get("uids") or ())
        after_uids = set(after.get("uids") or ())
        removed = sorted(before_uids - after_uids)
        added = sorted(after_uids - before_uids)
        if require_reduction and not removed:
            raise RuntimeError(
                f"日常_红包：{phase}后 fresh Runtime UID 集合未减少，拒绝假成功："
                f"before={sorted(before_uids)}, after={sorted(after_uids)}"
            )
        outcome = (
            "legal_unclaimable"
            if legal_unclaimable
            else "reduced"
            if removed
            else "unchanged"
        )
        self._log(
            "diagnostic",
            (
                f"日常_红包：{phase} Runtime 后验={outcome}，"
                f"removed={removed}，added={added}，pending={after['pending_count']}"
            ),
        )
        return {**after, "removed_uids": removed, "added_uids": added, "outcome": outcome}

    def _daily_redpacket_ocr_targets(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        frame: str,
    ) -> list[dict[str, Any]]:
        window = self._find_shape(image, "窗口")
        if not isinstance(window, dict):
            raise RuntimeError("日常_红包：#30 缺少 [窗口] 标注")
        window_box = self._box(window, image)
        cached = self._shared_spatial_ocr_result(ctx, frame)
        spatial = query_spatial_ocr(cached.get("tokens") or [], window_box)
        tokens = spatial.get("tokens") if isinstance(spatial.get("tokens"), list) else []
        fragments = [
            fragment
            for fragment in spatial.get("fragments") or []
            if isinstance(fragment, dict)
        ]
        matches: list[dict[str, Any]] = []
        for fragment in fragments:
            line_text = str(fragment.get("text") or "")
            matched = REDPACKET_OCR_PATTERN.search(line_text)
            if matched is None or REDPACKET_HISTORY_PATTERN.search(line_text):
                continue
            parent_line_id = fragment.get("parent_line_id")
            line_tokens = [
                token
                for token in tokens
                if parent_line_id is None or str(token.get("parent_line_id")) == str(parent_line_id)
            ]
            target_box = locate_text_box(line_tokens, matched.group(0))
            if target_box is None:
                continue
            target_center_x = float(target_box["x"]) + float(target_box["w"]) / 2
            target_center_y = float(target_box["y"]) + float(target_box["h"]) / 2
            target_height = max(1.0, float(target_box["h"]))
            claimed_nearby = any(
                "已领取" in str(candidate.get("text") or "")
                and 0.0
                <= (
                    float(candidate.get("y") or 0)
                    + float(candidate.get("h") or 0) / 2
                    - target_center_y
                )
                <= max(120.0, target_height * 3.0)
                and abs(
                    float(candidate.get("x") or 0)
                    + float(candidate.get("w") or 0) / 2
                    - target_center_x
                )
                <= 320.0
                for candidate in fragments
            )
            if claimed_nearby:
                continue
            matches.append({
                "matched_text": matched.group(0),
                "line_text": line_text,
                "box": target_box,
                "x": target_center_x,
                "y": target_center_y,
            })

        return sorted(matches, key=lambda item: (float(item["y"]), float(item["x"])))

    @staticmethod
    def _select_daily_redpacket_ocr_target(matches: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not matches:
            return None
        return max(matches, key=lambda item: (float(item["y"]), float(item["x"])))

    def _daily_redpacket_card_click_point(
        self,
        image: dict[str, Any],
        target: dict[str, Any],
    ) -> tuple[float, float]:
        """Map a text anchor to the clickable red-envelope lane on its card."""

        window = self._find_shape(image, "窗口")
        if not isinstance(window, dict):
            raise RuntimeError("日常_红包：#30 缺少 [窗口] 标注")
        window_box = self._box(window, image)
        # Red-packet messages use a stable card layout: the envelope button is
        # 35% across the chat window, while OCR text is farther to the right.
        # Keep the OCR-derived y so wrapped and vertically moving cards still
        # click their own row.
        click_x = float(window_box["x"]) + float(window_box["w"]) * 0.35
        click_y = float(target["y"])
        return click_x, click_y

    def _daily_redpacket_card_click_points(
        self,
        image: dict[str, Any],
        target: dict[str, Any],
    ) -> list[tuple[float, float]]:
        """Return safe hotspots within one red-packet card, in preferred order."""

        window = self._find_shape(image, "窗口")
        if not isinstance(window, dict):
            raise RuntimeError("日常_红包：#30 缺少 [窗口] 标注")
        window_box = self._box(window, image)
        y = float(target["y"])
        candidates = [
            self._daily_redpacket_card_click_point(image, target),
            (
                float(window_box["x"]) + float(window_box["w"]) * 0.62,
                y,
            ),
            (float(target["x"]), y),
        ]
        unique: list[tuple[float, float]] = []
        for point in candidates:
            if not any(abs(point[0] - old[0]) < 2.0 and abs(point[1] - old[1]) < 2.0 for old in unique):
                unique.append(point)
        return unique

    def _wait_daily_redpacket_ocr_targets(
        self,
        context: Any,
        ctx: dict[str, Any],
        *,
        timeout_seconds: float,
        poll_seconds: float,
    ):
        image = (ctx.get("images") or {}).get(30)
        if not isinstance(image, dict):
            raise RuntimeError("日常_红包：缺少 #30 标注")
        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        last_text = ""
        previous_signature: tuple[tuple[str, int, int], ...] | None = None
        while time.monotonic() < deadline:
            frame = context.cur_frame(update=True)
            matches = self._daily_redpacket_ocr_targets(ctx, image, frame)
            if matches:
                signature = tuple(
                    (
                        str(item.get("matched_text") or ""),
                        round(float(item.get("x") or 0)),
                        round(float(item.get("y") or 0)),
                    )
                    for item in matches
                )
                if signature == previous_signature:
                    return frame, matches
                previous_signature = signature
            else:
                previous_signature = None
            cached = ctx.get("_ocr_tokens_cache") if isinstance(ctx.get("_ocr_tokens_cache"), dict) else {}
            last_text = "".join(str(token.get("text") or "") for token in cached.get("tokens") or [])
            yield from context.wait_action_settle(poll_seconds)
        raise TimeoutError(f"日常_红包：#30[窗口] 未找到 {REDPACKET_OCR_PATTERN.pattern}，OCR={last_text[:160]}")

    def _find_and_click_daily_redpacket_group(
        self,
        context: Any,
        ctx: dict[str, Any],
        *,
        max_scrolls: int,
        settle_seconds: float,
        exclude_group_keys: set[str] | None = None,
    ):
        """Click the exact Runtime-selected chat row using OCR only for alignment."""

        snapshot = self._daily_redpacket_require_fresh_uid_snapshot(
            phase="#332 Runtime 群列表对齐"
        )
        runtime_snapshot = (
            snapshot.get("snapshot")
            if isinstance(snapshot.get("snapshot"), dict)
            else snapshot
        )
        route_plan = classify_redpacket_runtime_routes(runtime_snapshot)
        excluded = set(exclude_group_keys or ())
        items = [
            item
            for item in route_plan.get("ordinary_chat_items") or ()
            if f"{int(item.get('channel') or 0)}_{int(item.get('sub_channel_id') or 0)}"
            not in excluded
        ]
        if not items:
            return None
        target = items[0]
        channel = int(target.get("channel") or 0)
        sub_id = int(target.get("sub_channel_id") or 0)
        direct_anchors = select_chat_row_anchors(target)
        direct_title_patterns = select_chat_channel_title_patterns(channel)
        gui_target = read_chat_channel_gui_target(channel, sub_id)
        anchors = list(dict.fromkeys([
            *direct_anchors,
            *(gui_target.get("anchors") or ()),
        ]))
        title_patterns = list(dict.fromkeys([
            *direct_title_patterns,
            *(gui_target.get("title_patterns") or ()),
        ]))
        if not anchors and not title_patterns:
            raise RuntimeError(
                f"日常_红包：Runtime 群 {channel}_{sub_id} 缺少 GUI 对齐锚点"
            )
        tab_label = str(gui_target.get("tab_label") or "")
        if tab_label not in {"全部", "活动", "群聊", "私聊", "系统"}:
            raise RuntimeError(
                f"日常_红包：Runtime 群 {channel}_{sub_id} 缺少受支持的 GUI Tab"
            )

        window_shape = context.shape(332, "窗口")
        window_box = window_shape.box()
        last_text = ""

        for scroll_index in range(max(0, int(max_scrolls)) + 1):
            frame = context.cur_frame(update=True)
            cached = self._shared_spatial_ocr_result(ctx, frame)
            spatial = query_spatial_ocr(cached.get("tokens") or [], window_box)
            tokens = spatial.get("tokens") if isinstance(spatial.get("tokens"), list) else []
            fragments = spatial.get("fragments") if isinstance(spatial.get("fragments"), list) else []
            last_text = "".join(str(token.get("text") or "") for token in tokens)
            resolved_y = None
            matched_anchor = ""
            for anchor in anchors:
                candidate = select_text_match(find_text_matches(tokens, anchor), anchor)
                if candidate is not None:
                    resolved_y = float(candidate.y + candidate.h / 2)
                    matched_anchor = anchor
                    break
            if resolved_y is None:
                for pattern in title_patterns:
                    fragment = next((
                        item
                        for item in fragments
                        if re.search(pattern, str(item.get("text") or ""))
                    ), None)
                    if fragment is not None:
                        resolved_y = float(fragment.get("y") or 0) + float(fragment.get("h") or 0) / 2
                        matched_anchor = f"/{pattern}/"
                        break
            if resolved_y is not None:
                click_x = float(window_box["x"]) + float(window_box["w"]) * 0.55
                click_y = resolved_y
                context.click_frame_point(332, click_x, click_y)
                self._log(
                    "action",
                    (
                        f"日常_红包：Runtime 群 {channel}_{sub_id} 通过列表锚点"
                        f"「{matched_anchor}」对齐，scroll={scroll_index}"
                    ),
                )
                # The caller immediately waits for the destination #30 state;
                # do not add a blind settle before that state-based wait.
                return {
                    "x": click_x,
                    "y": click_y,
                    "scroll_index": scroll_index,
                    "channel": channel,
                    "sub_channel_id": sub_id,
                    "anchor": matched_anchor,
                }
            if scroll_index >= max(0, int(max_scrolls)):
                break
            context.drag_shape_content(window_shape, direction="down")
            yield from context.wait_action_settle(settle_seconds)
        self._log(
            "diagnostic",
            (
                f"日常_红包：Runtime 群 {channel}_{sub_id} 未对齐，"
                f"tab={tab_label}，anchors={anchors}，OCR={last_text[:240]}"
            ),
        )
        return None

    def _select_daily_redpacket_group_tab(
        self,
        context: Any,
        *,
        transition_timeout: float,
        exclude_group_keys: set[str] | None = None,
    ) -> dict[str, Any] | None:
        """Select the exact category tab for the next fresh Runtime candidate."""

        snapshot = self._daily_redpacket_require_fresh_uid_snapshot(
            phase="#332 Runtime 群分类"
        )
        runtime_snapshot = (
            snapshot.get("snapshot")
            if isinstance(snapshot.get("snapshot"), dict)
            else snapshot
        )
        excluded = set(exclude_group_keys or ())
        items = [
            item
            for item in classify_redpacket_runtime_routes(runtime_snapshot).get(
                "ordinary_chat_items"
            )
            or ()
            if f"{int(item.get('channel') or 0)}_{int(item.get('sub_channel_id') or 0)}"
            not in excluded
        ]
        if not items:
            return None
        target = items[0]
        channel = int(target.get("channel") or 0)
        sub_id = int(target.get("sub_channel_id") or 0)
        gui_target = read_chat_channel_gui_target(channel, sub_id)
        tab_label = str(gui_target.get("tab_label") or "")
        if tab_label not in {"全部", "活动", "群聊", "私聊", "系统"}:
            raise RuntimeError(
                f"日常_红包：Runtime 群 {channel}_{sub_id} 无法映射 GUI Tab"
            )
        yield from context.click_shape_center_then_scene(
            332,
            tab_label,
            332,
            timeout=transition_timeout,
            label=(
                f"日常_红包：Runtime 群 {channel}_{sub_id} "
                f"按 groupType={gui_target.get('group_type')} 切到{tab_label}"
            ),
        )
        self._log(
            "action",
            (
                f"日常_红包：Runtime 群 {channel}_{sub_id} 精确分流到"
                f"[{tab_label}]，再做 GUI 行对齐"
            ),
        )
        return gui_target

    def _wait_and_click_daily_redpacket_group(
        self,
        context: Any,
        ctx: dict[str, Any],
        *,
        timeout_seconds: float,
        poll_seconds: float,
        max_scrolls: int = 0,
        exclude_group_keys: set[str] | None = None,
    ):
        """Wait for Runtime-selected group alignment, then boundedly scan the list.

        ``_find_and_click_daily_redpacket_group`` keeps the action gate on the
        current frame: scrolling never authorizes a row click by itself.  A
        positive ``max_scrolls`` means one complete bounded list scan; once it
        is exhausted, waiting longer on the bottom window cannot reveal a row
        that the scan already disproved.
        """

        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        while time.monotonic() < deadline:
            group = yield from self._find_and_click_daily_redpacket_group(
                context,
                ctx,
                max_scrolls=max_scrolls,
                settle_seconds=poll_seconds,
                exclude_group_keys=exclude_group_keys,
            )
            if group is not None:
                return group
            if max_scrolls > 0:
                break
            yield from context.wait_action_settle(poll_seconds)
        raise TimeoutError("日常_红包：#332[窗口] 未对齐到 Runtime 指定红包群")

    def _claim_daily_redpackets(
        self,
        context: Any,
        *,
        transition_timeout: float,
        max_open_count: int,
        current: Any | None = None,
    ):
        if current is None:
            current = yield from context.wait_scene(
                [397,
                399],
                wait=transition_timeout,
                label="日常_红包：等待开红包或已领完状态",
            )
        opened_count = 0
        while int(current.id or 0) == 397:
            if opened_count >= max(1, int(max_open_count)):
                raise RuntimeError(f"日常_红包：已打开 {opened_count} 个红包仍未进入 #399，停止避免无限循环")
            yield from context.wait_click(397, "开", timeout=transition_timeout)
            if all(
                hasattr(context, name)
                for name in ("wait_action_settle", "cur_frame", "ocr_text")
            ):
                # The quota toast is short-lived and the UI immediately falls
                # back to the chat page. Capture the frame before waiting for
                # result scenes, otherwise it is indistinguishable from an
                # unknown transition.
                yield from context.wait_action_settle(0.15)
                quota_frame = context.cur_frame(update=True)
                quota_text = re.sub(r"\s+", "", context.ocr_text(quota_frame))
                if "领取次数不足" in quota_text:
                    attrs = getattr(context, "attrs", None)
                    if isinstance(attrs, dict):
                        attrs["daily_redpacket_quota_exhausted"] = True
                    self._log("success", "日常_红包：今日领取次数不足，停止继续开启红包")
                    return opened_count
            result_view = yield from context.wait_scene(
                [398,
                399,
                672],
                wait=transition_timeout,
                label="日常_红包：等待红包结果",
            )
            if int(result_view.id or 0) == 672:
                yield from self._dismiss_daily_redpacket_sold_out(
                    context,
                    transition_timeout=transition_timeout,
                )
                self._log(
                    "success",
                    "日常_红包：打开队列期间遇到已抢光红包，已关闭结果并返回当前群聊",
                )
                return opened_count
            opened_count += 1
            if int(result_view.id or 0) == 399:
                current = result_view
                break
            yield from context.wait_click(398, "下一个", timeout=transition_timeout)
            current = yield from context.wait_scene(
                [397,
                399],
                wait=transition_timeout,
                label="日常_红包：等待下一个红包",
            )
        if int(current.id or 0) != 399:
            raise RuntimeError(f"日常_红包：领取循环意外停在 #{current.id if current is not None else 'unknown'}")
        yield from context.wait_click(399, "返回", timeout=transition_timeout)
        yield from context.wait_scene(
            [30],
            wait=transition_timeout,
            label="日常_红包：领取完成后返回群聊 #30",
        )
        return opened_count

    def _dismiss_daily_redpacket_sold_out(self, context: Any, *, transition_timeout: float):
        """Consume #672 only inside the card-click transaction."""

        return (yield from context.click_shape_center_then_scene(
            672,
            "弹窗外背景",
            30,
            timeout=transition_timeout,
            label="日常_红包：关闭已抢光结果并返回当前群聊",
        ))

    def _exit_daily_redpacket_group_to_world(self, context: Any, *, transition_timeout: float):
        """Exit the verified #30 -> #332 -> #34 chat stack explicitly."""

        yield from self._return_daily_redpacket_group_to_list(
            context,
            transition_timeout=transition_timeout,
        )
        yield from self._close_daily_redpacket_chat_to_world(
            context,
            transition_timeout=transition_timeout,
        )

    def _close_daily_redpacket_chat_to_world(self, context: Any, *, transition_timeout: float):
        """Close #332 using only its annotated semantic return action."""

        try:
            return (yield from context.click_shape_center_then_scene(
                332,
                "返回",
                34,
                timeout=transition_timeout,
            ))
        except TimeoutError:
            scene_id, _score, _frame = (yield from context.current_scene([332, 34], update=True))
            if scene_id == 34:
                return (yield from context.wait_scene(
                    [34],
                    wait=1.0,
                    label="日常_红包：确认聊天返回已生效",
                ))
            # No reliable background Shape exists here.  Preserve the live
            # page instead of guessing a coordinate inside the chat surface.
            raise

    def _return_daily_redpacket_group_to_list(self, context: Any, *, transition_timeout: float):
        try:
            return (yield from context.click_shape_center_then_scene(
                30,
                "返回",
                [332, 20, 34],
                timeout=transition_timeout,
            ))
        except TimeoutError:
            scene_id, _score, _frame = (yield from context.current_scene([30, 332, 20, 34], update=True))
            if scene_id in {332, 20, 34}:
                return (yield from context.wait_scene(
                    [332,
                    20,
                    34],
                    wait=1.0,
                    label="日常_红包：确认群聊返回已生效",
                ))
            # A failed annotated return does not authorize a second guessed
            # click.  In particular, clicking the dimmed/chat area can open a
            # message context menu and make cleanup less recoverable.
            raise

    def _process_current_daily_redpacket_group(
        self,
        context: Any,
        ctx: dict[str, Any],
        *,
        transition_timeout: float,
        poll_seconds: float,
        max_open_count: int,
        max_locator_clicks: int = 5,
    ):
        # 群列表行只负责落实 Runtime 已选中的 channel/subChannelId；
        # 它不参与判断该群是否存在红包，也不能授权群内红包卡片点击。
        # #30 右上角的 [红包] 是游戏提供的待领红包定位入口，不是红包
        # 卡片本身。群聊可能停在任意历史消息；先确认 #30，再优先读取
        # 当前可见卡片，未命中时有界点击定位入口，直到卡片 OCR 连续两帧
        # 稳定出现。禁止把 #30 参考帧中的卡片坐标当作探针硬点。
        yield from context.wait_scene(
            [30],
            wait=transition_timeout,
            label="日常_红包：等待群聊 #30",
        )
        before_runtime = self._daily_redpacket_require_fresh_uid_snapshot(
            phase="进入当前群事务前"
        )
        targets: list[dict[str, Any]] = []
        short_probe_timeout = max(1.0, min(3.0, float(transition_timeout)))
        for locator_attempt in range(max(0, int(max_locator_clicks)) + 1):
            try:
                _frame, targets = yield from self._wait_daily_redpacket_ocr_targets(
                    context,
                    ctx,
                    timeout_seconds=(
                        short_probe_timeout
                        if locator_attempt < max(0, int(max_locator_clicks))
                        else transition_timeout
                    ),
                    poll_seconds=poll_seconds,
                )
                break
            except TimeoutError:
                if locator_attempt >= max(0, int(max_locator_clicks)):
                    self._daily_redpacket_verify_uid_postcondition(
                        before_runtime, phase="红包定位耗尽", require_reduction=True,
                    )
                    return 0, False
                try:
                    # Locator visibility only authorizes locating a card. Its
                    # absence cannot negate the provider's pending UID set.
                    yield from context.wait_shape(
                        30,
                        "红包",
                        timeout=transition_timeout,
                        label="日常_红包：确认右上红包定位入口仍有数字角标",
                    )
                except TimeoutError:
                    self._daily_redpacket_verify_uid_postcondition(
                        before_runtime, phase="未识别到红包卡片或定位入口", require_reduction=True,
                    )
                    return 0, False
                context.click_shape_center(30, "红包")
                self._log(
                    "action",
                    (
                        "日常_红包：当前 #30 窗口未见红包卡片，"
                        f"点击右上红包定位入口 {locator_attempt + 1}/{max(1, int(max_locator_clicks))}"
                    ),
                )
                yield from context.wait_action_settle(poll_seconds)
        target = self._select_daily_redpacket_ocr_target(targets)
        if target is None:
            return 0, False
        image = (ctx.get("images") or {}).get(30)
        if not isinstance(image, dict):
            raise RuntimeError("日常_红包：缺少 #30 标注")
        click_points = self._daily_redpacket_card_click_points(image, target)
        attempt_timeout = max(3.0, float(transition_timeout) / len(click_points))
        current = None
        for attempt, (click_x, click_y) in enumerate(click_points, start=1):
            # Every retry is a new action. Re-observe the current #30 card and
            # refuse to reuse a stale OCR hotspot from the previous click.
            if attempt > 1:
                try:
                    _fresh_frame, fresh_targets = yield from self._wait_daily_redpacket_ocr_targets(
                        context,
                        ctx,
                        timeout_seconds=attempt_timeout,
                        poll_seconds=poll_seconds,
                    )
                except TimeoutError:
                    return 0, False
                target = self._select_daily_redpacket_ocr_target(fresh_targets)
                if target is None:
                    return 0, False
                click_points = self._daily_redpacket_card_click_points(image, target)
                click_x, click_y = click_points[min(attempt - 1, len(click_points) - 1)]
            context.click_frame_point(30, click_x, click_y)
            self._log(
                "action",
                (
                    f"日常_红包：#30[窗口] 找到 {len(targets)} 个未领取 OCR 候选，"
                    f"按最新的“{target['matched_text']}”点击同卡片热区 "
                    f"{attempt}/{len(click_points)} ({click_x:.1f},{click_y:.1f})"
                ),
            )
            try:
                current = yield from context.wait_scene(
                    [397,
                    399,
                    672],
                    wait=attempt_timeout,
                    label="日常_红包：等待开红包、已领完或已抢光状态",
                )
                break
            except TimeoutError:
                scene_id, _score, _frame = (yield from context.current_scene([30], update=True))
                if scene_id != 30:
                    return 0, False
        if current is None:
            return 0, False
        if int(current.id or 0) == 672:
            yield from self._dismiss_daily_redpacket_sold_out(
                context,
                transition_timeout=transition_timeout,
            )
            self._log(
                "success",
                "日常_红包：当前传音群红包已抢光（页面可领数为 0），继续检查其他群",
            )
            self._daily_redpacket_verify_uid_postcondition(
                before_runtime,
                phase="#672 已抢光并回到 #30",
                legal_unclaimable=True,
            )
            return 0, False
        opened_count = yield from self._claim_daily_redpackets(
            context,
            transition_timeout=transition_timeout,
            max_open_count=max_open_count,
            current=current,
        )
        self._daily_redpacket_verify_uid_postcondition(
            before_runtime,
            phase="红包领取并回到 #30",
            require_reduction=opened_count > 0,
        )
        return opened_count, True

    def _execute_daily_redpacket_task(
        self,
        ctx: dict[str, Any],
        stop_event: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("日常_红包：缺少资产树路径")
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        transition_timeout = max(3.0, float(payload.get("transition_timeout_seconds") or 15.0))
        redpacket_confirm_seconds = max(
            1.0,
            float(payload.get("redpacket_confirm_seconds") or 60.0),
        )
        poll_seconds = max(0.2, float(payload.get("poll_seconds") or 0.8))
        # Real group lists can place the alliance row after nine upward
        # swipes. Keep enough headroom while preserving the existing hard cap.
        max_scrolls = max(0, min(20, int(payload.get("max_group_scrolls") or 12)))
        # A single group can legitimately accumulate well over 20 packets.
        # Keep a hard bound for unattended execution, but do not abort a
        # healthy #397 -> #398 -> #397 progress loop at the old low default.
        max_open_count = max(1, min(100, int(payload.get("max_open_count") or 100)))
        max_group_count = max(1, min(100, int(payload.get("max_group_count") or 50)))
        max_locator_clicks = max(1, min(20, int(payload.get("max_locator_clicks") or 5)))
        # Runtime is the authority for packet identity and route selection.
        # The 9033 QMCH entry uses a dedicated ``福`` surface and must be
        # dispatched before #395/#332 or any ordinary group/card action.
        route_plan: dict[str, Any] | None
        try:
            route_plan = self._daily_redpacket_runtime_route_plan()
        except FanxiuRuntimeMemoryError as exc:
            if exc.code != "data_not_loaded":
                raise
            route_plan = None
            self._log(
                "diagnostic",
                "日常_红包：Chat.ChatGroup 尚未自然加载；先服从世界页视觉门卫，再决定是否打开聊天加载",
            )
        if route_plan is not None and route_plan.get("route") == "qmch_reward_terminal":
            terminal_uids = [str(uid) for uid in route_plan.get("uids") or []]
            return self._daily_redpacket_result(
                payload,
                f"鸿运福签已是 rewarded 终态，幂等零动作跳过：{terminal_uids}",
                opened_count=0,
                current_scene=34,
            )
        if route_plan is not None and route_plan.get("route") == QMCH_REWARD_EVENT_KEY:
            return (yield from self._dispatch_daily_redpacket_runtime_route(
                context,
                ctx,
                stop_event,
                payload,
                route_plan,
            ))
        # Re-read the provider's current fact; the world badge may be covered
        # by voice UI and has no authority over whether chat should be opened.
        if route_plan is None:
            pending = self._daily_redpacket_require_fresh_uid_snapshot(
                phase="进入聊天前"
            )["snapshot"]
            has_pending = bool(pending.get("trigger_ready"))
        else:
            has_pending = bool(route_plan.get("trigger_ready"))
        if not has_pending:
            return self._daily_redpacket_result(
                payload, "当前 Runtime 无待处理红包", current_scene=34,
            )
        landing = yield from context.click_shape_center_then_scene(
            34, "聊天", [332, 333], timeout=transition_timeout,
            label="日常_红包：Runtime 有待处理红包，进入聊天",
        )
        # The chat popup preserves its last selected tab.  Opening it can
        # therefore legitimately land on #333 (contacts) instead of #332
        # (chat).  Follow the annotated tab edge instead of treating that
        # stable page as a dead end or repeatedly closing/reopening it.
        if int(landing.id or 0) == 333:
            yield from context.click_shape_center_then_scene(
                333,
                "聊天",
                332,
                timeout=transition_timeout,
                label="日常_红包：从通讯录切回聊天页 #332",
            )

        if route_plan is None:
            route_plan = yield from self._wait_daily_redpacket_runtime_route_plan(
                context,
                timeout_seconds=transition_timeout,
                poll_seconds=poll_seconds,
            )
            if route_plan.get("route") in {"qmch_reward_terminal", QMCH_REWARD_EVENT_KEY}:
                yield from self._close_daily_redpacket_chat_to_world(
                    context,
                    transition_timeout=transition_timeout,
                )
                if route_plan.get("route") == "qmch_reward_terminal":
                    terminal_uids = [str(uid) for uid in route_plan.get("uids") or []]
                    return self._daily_redpacket_result(
                        payload,
                        f"鸿运福签已是 rewarded 终态，幂等零动作跳过：{terminal_uids}",
                        opened_count=0,
                        current_scene=34,
                    )
                return (yield from self._dispatch_daily_redpacket_runtime_route(
                    context,
                    ctx,
                    stop_event,
                    payload,
                    route_plan,
                ))

        # Ordinary red packets use Runtime's channel -> groupType -> tab
        # contract.  "全部" expands the list and is not a search strategy.
        gui_target = yield from self._select_daily_redpacket_group_tab(
            context,
            transition_timeout=transition_timeout,
            exclude_group_keys=set(),
        )
        if gui_target is None:
            yield from self._close_daily_redpacket_chat_to_world(
                context,
                transition_timeout=transition_timeout,
            )
            return self._daily_redpacket_result(
                payload,
                "fresh Runtime 已无普通红包候选，零动作退出",
                current_scene=34,
            )

        try:
            initial_group = yield from self._wait_and_click_daily_redpacket_group(
                context,
                ctx,
                timeout_seconds=redpacket_confirm_seconds,
                poll_seconds=poll_seconds,
                max_scrolls=max_scrolls,
                exclude_group_keys=set(),
            )
        except TimeoutError:
            unavailable = self._daily_redpacket_require_fresh_uid_snapshot(
                phase="#332 Runtime-GUI 群行持续未对齐"
            )
            unclaimable_uids = sorted(unavailable.get("uids") or ())
            yield from self._close_daily_redpacket_chat_to_world(
                context,
                transition_timeout=transition_timeout,
            )
            return self._daily_redpacket_result(
                payload,
                (
                    "#332 持续未对齐到 Runtime 指定红包群；"
                    f"将 fresh Runtime 剩余 {len(unclaimable_uids)} 个 UID "
                    "类型化为 chat_gui_unaligned"
                ),
                current_scene=34,
                unclaimable_uids=unclaimable_uids,
            )

        window_shape = context.shape(332, "窗口")
        opened_count = 0
        processed_groups = 0
        quota_exhausted = False
        scroll_count = 0
        force_scroll = False
        left_chat_stack = False
        visually_exhausted_group_keys: set[str] = set()
        pending_group = initial_group
        while True:
            group = pending_group
            pending_group = None
            if group is None and not force_scroll:
                gui_target = yield from self._select_daily_redpacket_group_tab(
                    context,
                    transition_timeout=transition_timeout,
                    exclude_group_keys=visually_exhausted_group_keys,
                )
                if gui_target is None:
                    break
                group = yield from self._find_and_click_daily_redpacket_group(
                    context,
                    ctx,
                    max_scrolls=0,
                    settle_seconds=poll_seconds,
                    exclude_group_keys=visually_exhausted_group_keys,
                )
            force_scroll = False
            if group is not None:
                if processed_groups >= max_group_count:
                    raise RuntimeError(f"日常_红包：已处理 {processed_groups} 个群仍未加载到底，停止避免无限循环")
                processed_groups += 1
                group_opened, opened_page = yield from self._process_current_daily_redpacket_group(
                    context,
                    ctx,
                    transition_timeout=transition_timeout,
                    poll_seconds=poll_seconds,
                    max_open_count=max_open_count,
                    max_locator_clicks=max_locator_clicks,
                )
                opened_count += int(group_opened)
                if not opened_page:
                    visually_exhausted_group_keys.add(
                        f"{int(group.get('channel') or 0)}_"
                        f"{int(group.get('sub_channel_id') or 0)}"
                    )
                quota_exhausted = bool(
                    isinstance(getattr(context, "attrs", None), dict)
                    and context.attrs.pop("daily_redpacket_quota_exhausted", False)
                )
                return_view = yield from self._return_daily_redpacket_group_to_list(
                    context,
                    transition_timeout=transition_timeout,
                )
                return_scene = int(return_view.id or 0)
                if return_scene != 332:
                    left_chat_stack = True
                    if return_scene != 34:
                        yield from context.go_scene(34)
                    break
                if quota_exhausted:
                    break
                # 未能打开领取页时，该 UID 可能仍在 Runtime；强制向下加载，
                # 避免反复对齐并点击同一行。成功领取后由 fresh UID 集合决定下一目标。
                force_scroll = not bool(opened_page)
                continue
            if scroll_count >= max_scrolls:
                break
            changed = yield from context.scroll_shape_content(
                window_shape,
                direction="down",
                # The chat list can produce one near-identical stabilized frame
                # around a page boundary even though a later row is still
                # reachable. Require two consecutive unchanged observations
                # before declaring the bounded search exhausted.
                unchanged_confirmations=2,
            )
            scroll_count += 1
            if not changed:
                break
            yield from context.wait_action_settle(poll_seconds)

        if not left_chat_stack:
            yield from self._close_daily_redpacket_chat_to_world(
                context,
                transition_timeout=transition_timeout,
            )
        message = (
            f"今日红包领取次数已用尽，处理 {processed_groups} 个群，共打开 {opened_count} 个红包"
            if quota_exhausted
            else f"红包群列表已加载到底，处理 {processed_groups} 个群，共打开 {opened_count} 个红包"
        )
        return self._daily_redpacket_result(
            payload,
            message,
            opened_count=opened_count,
        )
