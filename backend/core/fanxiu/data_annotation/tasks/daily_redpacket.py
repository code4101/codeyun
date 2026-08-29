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
)
from backend.core.fanxiu.runtime.mumu_control import text_mumu_adb


REDPACKET_OCR_PATTERN = re.compile(r"首领[累猎]杀|奖赏|第一|获赠|红包")
REDPACKET_HISTORY_PATTERN = re.compile(r"你领取了|已领取")
REDPACKET_SELF_CHECK_INTERVAL_SECONDS = 12 * 60 * 60


def _now() -> datetime:
    return datetime.now()


class DailyRedpacketTaskMixin:
    """执行由巡检调度、由当前画面独立授权的“日常_红包”领取闭环。"""

    # 仅适用于“日常_红包”的业务安全约束（禁止删除或绕过，不得外推到其他作业）：
    # 1. 巡检与 Runtime 事实只能提前 next_time，绝不能直接授权 GUI 点击。
    # 2. 每一次进入聊天、选择群行、点击红包卡片，都必须先由对应的当前帧
    #    视觉检测命中；不得拿旧帧、群名推测、固定坐标或标注框中心代替检测。
    # 3. 任一视觉门卫未命中时零业务点击，按“当前无红包”成功退出并等待
    #    下一次巡检；禁止为了探测红包是否存在而先点一下再判断。

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

    def _daily_redpacket_quick_gate(self, ctx: dict[str, Any], frame: str) -> dict[str, Any]:
        """在任何 GUI 操作前匹配当前世界页的红包标记。

        巡检事实只负责把 Job 的 ``next_time`` 提前，不能授权点击。Job 每次
        执行都必须重新通过视觉门卫；巡检过期或误报时应正常成功退出。
        """

        # #395 is a business reference frame, not a globally identifiable
        # scene.  Match only its [红包] shape against the current live frame so
        # this gate cannot make #395 participate in scene identification.
        image = (ctx.get("images") or {}).get(395)
        shape = self._find_shape(image, "红包")
        if not isinstance(image, dict) or not isinstance(shape, dict):
            raise RuntimeError("日常_红包：缺少 #395[红包] 快速门卫标注")
        return self._match_shape(ctx, image, shape, frame, condition="image")

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
        plan = classify_redpacket_runtime_routes(snapshot)
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
        runtime: Any,
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
            yield from runtime.wait_action_settle(poll_seconds)
        raise TimeoutError(
            f"日常_红包：鸿运福签打开后 fresh Runtime 未出现同 UID rewarded 终态：{uid}"
        )

    def _dispatch_daily_redpacket_runtime_route(
        self,
        runtime: Any,
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
            runtime,
            ctx,
            stop_event,
            payload,
            route_plan,
        ))

    def _wait_daily_qmch_activity_row(
        self,
        runtime: Any,
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
            frame = runtime.cur_frame(update=True)
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
            changed = yield from runtime.scroll_shape_content(
                332,
                "窗口",
                direction="down",
                ratio=0.5,
                unchanged_confirmations=2,
            )
            scroll_count += 1
            if not changed:
                break
            yield from runtime.wait_action_settle(poll_seconds)
        raise TimeoutError(
            "日常_红包：#332[窗口] 未唯一对齐到 Runtime 指定聊天行，"
            f"anchors={list(anchors or ())}，OCR={last_text[:200]}"
        )

    @staticmethod
    def _normalize_daily_qmch_phrase(value: Any) -> str:
        return re.sub(
            r"[\s,，。！？!?；;：:“”‘’、（）()【】\[\]《》<>·…—-]+",
            "",
            str(value or ""),
        )

    def _execute_daily_qmch_reward_route(
        self,
        runtime: Any,
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

        channel_route = read_chat_channel_gui_target(channel, sub_id)
        if (
            int(channel_route.get("group_type") or 0) != 1
            or channel_route.get("tab_label") != "活动"
        ):
            raise RuntimeError(
                "日常_红包：qmch_reward Runtime 频道未对齐活动 Tab，"
                f"route={channel_route}"
            )
        phrase_fact = read_repeated_chat_phrase(channel, sub_id)
        phrase = str(phrase_fact.get("phrase") or "").strip()
        if not phrase_fact.get("ready") or not phrase:
            raise RuntimeError(
                "日常_红包：当前活动频道 Runtime 未形成唯一重复话术，拒绝发送："
                f"{phrase_fact.get('reason') or 'unknown'}"
            )
        landing = yield from runtime.click_shape_center_then_view(
            34,
            "聊天",
            332,
            333,
            timeout=transition_timeout,
            label="鸿运福签：等待聊天容器",
        )
        if int(landing.id or 0) == 333:
            yield from runtime.click_shape_center_then_view(
                333,
                "聊天",
                332,
                timeout=transition_timeout,
                label="鸿运福签：从通讯录切回聊天页",
            )
        yield from runtime.click_shape_center_then_view(
            332,
            str(channel_route["tab_label"]),
            332,
            timeout=transition_timeout,
            label="鸿运福签：幂等切到活动消息",
        )
        _frame, row = yield from self._wait_daily_qmch_activity_row(
            runtime,
            ctx,
            timeout_seconds=transition_timeout,
            poll_seconds=poll_seconds,
            anchors=list(channel_route.get("anchors") or ()),
        )
        runtime.click_frame_point(332, float(row.x + row.w / 2), float(row.y + row.h / 2))
        yield from runtime.wait_view(
            30,
            timeout=transition_timeout,
            label="鸿运福签：等待活动聊天页",
        )
        yield from runtime.wait_shape(
            673,
            "鸿运福签",
            timeout=transition_timeout,
            label="鸿运福签：确认专用活动聊天身份",
        )
        self._log(
            "success",
            f"鸿运福签：已按 fresh route channel={channel}, sub_id={sub_id} 进入活动聊天 #30",
        )
        yield from runtime.wait_shape(
            673,
            "输入框空态",
            timeout=transition_timeout,
            label="鸿运福签：输入话术前确认输入框为空",
        )
        # Runtime 已经给出当前活动频道的唯一话术；GUI 只负责进入对应聊天、
        # 输入、发送和领取。禁止再依赖会随卡片动画漂移的复制图标。
        runtime.click_shape_center(673, "输入框空态")
        yield from runtime.wait_action_settle(0.5)
        text_mumu_adb(phrase)
        yield from runtime.wait_action_settle(0.5)
        # 输入法打开时，第一次点击发送热区只收起输入层。收起后先 OCR
        # 回读输入框，确认 Runtime 话术确实落入 GUI，再授权真正发送。
        runtime.click_shape_center_fast(673, "发送")
        yield from runtime.wait_action_settle(0.8)
        typed_frame = runtime.cur_frame(update=True)
        typed_text = runtime.ocr_text_in_shapes(
            673,
            ("输入框空态",),
            padding=0,
            frame_data_url=typed_frame,
            crop=True,
        )
        normalized_phrase = self._normalize_daily_qmch_phrase(phrase)
        normalized_typed = self._normalize_daily_qmch_phrase(typed_text)
        if not normalized_phrase or normalized_phrase not in normalized_typed:
            raise RuntimeError(
                "日常_红包：Runtime 话术输入后 OCR 回读不一致，拒绝发送："
                f"expected={normalized_phrase[:80]}, actual={normalized_typed[:80]}"
            )
        yield from runtime.wait_click_then_shape(
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
            yield from runtime.wait_click(397, "开", timeout=transition_timeout)
            opened_count = 1
            yield from runtime.wait_action_settle(poll_seconds)
            yield from self._wait_daily_qmch_uid_terminal(
                runtime,
                uid,
                timeout_seconds=transition_timeout,
                poll_seconds=poll_seconds,
            )
        runtime.click_shape_center(672, "弹窗外背景", x_ratio=0.1)
        yield from runtime.wait_view(
            30,
            timeout=transition_timeout,
            label="鸿运福签：关闭奖励详情回到活动聊天",
        )
        yield from runtime.click_shape_center_then_view(
            30,
            "返回",
            332,
            timeout=transition_timeout,
            label="鸿运福签：从活动聊天返回聊天列表",
        )
        yield from runtime.click_shape_center_then_view(
            332,
            "返回",
            34,
            timeout=transition_timeout,
            label="鸿运福签：从聊天列表安全返回世界",
        )
        return self._daily_redpacket_result(
            payload,
            f"鸿运福签 UID={uid} 已确认 rewarded 终态",
            opened_count=opened_count,
            current_scene=34,
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
        runtime: Any,
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
            frame = runtime.cur_frame(update=True)
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
            yield from runtime.wait_action_settle(poll_seconds)
        raise TimeoutError(f"日常_红包：#30[窗口] 未找到 {REDPACKET_OCR_PATTERN.pattern}，OCR={last_text[:160]}")

    def _find_and_click_daily_redpacket_group(
        self,
        runtime: Any,
        ctx: dict[str, Any],
        *,
        max_scrolls: int,
        settle_seconds: float,
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
        items = list(route_plan.get("ordinary_chat_items") or ())
        if not items:
            return None
        target = items[0]
        channel = int(target.get("channel") or 0)
        sub_id = int(target.get("sub_channel_id") or 0)
        gui_target = read_chat_channel_gui_target(channel, sub_id)
        anchors = list(gui_target.get("anchors") or ())
        if not anchors:
            raise RuntimeError(
                f"日常_红包：Runtime 群 {channel}_{sub_id} 缺少 GUI 对齐锚点"
            )

        tab_label = str(gui_target.get("tab_label") or "")
        if not tab_label:
            raise RuntimeError(
                f"日常_红包：Runtime 群 {channel}_{sub_id} 缺少 Tab 路由"
            )
        yield from runtime.click_shape_center_then_view(
            332,
            tab_label,
            332,
            timeout=max(3.0, float(settle_seconds) * 10.0),
            label=f"日常_红包：Runtime 对齐到 {tab_label} Tab",
        )

        window_shape = runtime.shape(332, "窗口")
        window_box = window_shape.box()
        last_text = ""

        for scroll_index in range(max(0, int(max_scrolls)) + 1):
            frame = runtime.cur_frame(update=True)
            cached = self._shared_spatial_ocr_result(ctx, frame)
            spatial = query_spatial_ocr(cached.get("tokens") or [], window_box)
            tokens = spatial.get("tokens") if isinstance(spatial.get("tokens"), list) else []
            last_text = "".join(str(token.get("text") or "") for token in tokens)
            resolved = None
            matched_anchor = ""
            for anchor in anchors:
                candidate = select_text_match(find_text_matches(tokens, anchor), anchor)
                if candidate is not None:
                    resolved = candidate
                    matched_anchor = anchor
                    break
            if resolved is not None:
                click_x = float(window_box["x"]) + float(window_box["w"]) * 0.55
                click_y = float(resolved.y + resolved.h / 2)
                runtime.click_frame_point(332, click_x, click_y)
                self._log(
                    "action",
                    (
                        f"日常_红包：Runtime 群 {channel}_{sub_id} 通过列表锚点"
                        f"「{matched_anchor}」对齐，scroll={scroll_index}"
                    ),
                )
                yield from runtime.wait_action_settle(settle_seconds)
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
            runtime.drag_shape_content(window_shape, direction="down")
            yield from runtime.wait_action_settle(settle_seconds)
        self._log(
            "diagnostic",
            (
                f"日常_红包：Runtime 群 {channel}_{sub_id} 未对齐，"
                f"tab={tab_label}，anchors={anchors}，OCR={last_text[:240]}"
            ),
        )
        return None

    def _wait_and_click_daily_redpacket_group(
        self,
        runtime: Any,
        ctx: dict[str, Any],
        *,
        timeout_seconds: float,
        poll_seconds: float,
        max_scrolls: int = 0,
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
                runtime,
                ctx,
                max_scrolls=max_scrolls,
                settle_seconds=poll_seconds,
            )
            if group is not None:
                return group
            if max_scrolls > 0:
                break
            yield from runtime.wait_action_settle(poll_seconds)
        raise TimeoutError("日常_红包：#332[窗口] 未对齐到 Runtime 指定红包群")

    def _claim_daily_redpackets(
        self,
        runtime: Any,
        *,
        transition_timeout: float,
        max_open_count: int,
        current: Any | None = None,
    ):
        if current is None:
            current = yield from runtime.wait_view(
                397,
                399,
                timeout=transition_timeout,
                label="日常_红包：等待开红包或已领完状态",
            )
        opened_count = 0
        while int(current.id or 0) == 397:
            if opened_count >= max(1, int(max_open_count)):
                raise RuntimeError(f"日常_红包：已打开 {opened_count} 个红包仍未进入 #399，停止避免无限循环")
            yield from runtime.wait_click(397, "开", timeout=transition_timeout)
            if all(
                hasattr(runtime, name)
                for name in ("wait_action_settle", "cur_frame", "ocr_text")
            ):
                # The quota toast is short-lived and the UI immediately falls
                # back to the chat page. Capture the frame before waiting for
                # result scenes, otherwise it is indistinguishable from an
                # unknown transition.
                yield from runtime.wait_action_settle(0.15)
                quota_frame = runtime.cur_frame(update=True)
                quota_text = re.sub(r"\s+", "", runtime.ocr_text(quota_frame))
                if "领取次数不足" in quota_text:
                    attrs = getattr(runtime, "attrs", None)
                    if isinstance(attrs, dict):
                        attrs["daily_redpacket_quota_exhausted"] = True
                    self._log("success", "日常_红包：今日领取次数不足，停止继续开启红包")
                    return opened_count
            opened_count += 1
            result_view = yield from runtime.wait_view(
                398,
                399,
                timeout=transition_timeout,
                label="日常_红包：等待红包结果",
            )
            if int(result_view.id or 0) == 399:
                current = result_view
                break
            yield from runtime.wait_click(398, "下一个", timeout=transition_timeout)
            current = yield from runtime.wait_view(
                397,
                399,
                timeout=transition_timeout,
                label="日常_红包：等待下一个红包",
            )
        if int(current.id or 0) != 399:
            raise RuntimeError(f"日常_红包：领取循环意外停在 #{current.id if current is not None else 'unknown'}")
        yield from runtime.wait_click(399, "返回", timeout=transition_timeout)
        yield from runtime.wait_view(
            30,
            timeout=transition_timeout,
            label="日常_红包：领取完成后返回群聊 #30",
        )
        return opened_count

    def _dismiss_daily_redpacket_sold_out(self, runtime: Any, *, transition_timeout: float):
        """Consume #672 only inside the card-click transaction."""

        return (yield from runtime.click_shape_center_then_view(
            672,
            "弹窗外背景",
            30,
            timeout=transition_timeout,
            label="日常_红包：关闭已抢光结果并返回当前群聊",
        ))

    def _exit_daily_redpacket_group_to_world(self, runtime: Any, *, transition_timeout: float):
        """Exit the verified #30 -> #332 -> #34 chat stack explicitly."""

        yield from self._return_daily_redpacket_group_to_list(
            runtime,
            transition_timeout=transition_timeout,
        )
        yield from self._close_daily_redpacket_chat_to_world(
            runtime,
            transition_timeout=transition_timeout,
        )

    def _close_daily_redpacket_chat_to_world(self, runtime: Any, *, transition_timeout: float):
        """Close #332, whose dimmed modal can intercept the inherited back button."""

        try:
            return (yield from runtime.click_shape_center_then_view(
                332,
                "返回",
                34,
                timeout=transition_timeout,
            ))
        except TimeoutError:
            scene_id, _score, _frame = runtime.current_scene([332, 34], update=True)
            if scene_id != 332:
                raise

        # #332 is a modal sheet over the world.  If the inherited world back
        # button is intercepted, tap the dimmed area immediately left of its
        # annotated content window to dismiss the sheet without guessing inside
        # any chat row.
        window = runtime.shape(332, "窗口").box()
        outside_x = max(8.0, float(window.get("x") or 0) * 0.5)
        outside_y = float(window.get("y") or 0) + float(window.get("h") or 0) * 0.5
        runtime.click_frame_point(332, outside_x, outside_y)
        yield from runtime.wait_action_settle(1.0)
        return (yield from runtime.wait_view(
            34,
            timeout=transition_timeout,
            label="日常_红包：点击聊天弹层外部返回世界",
        ))

    def _return_daily_redpacket_group_to_list(self, runtime: Any, *, transition_timeout: float):
        try:
            return (yield from runtime.click_shape_center_then_view(
                30,
                "返回",
                [332, 20, 34],
                timeout=transition_timeout,
            ))
        except TimeoutError:
            scene_id, _score, _frame = runtime.current_scene([30, 332, 20, 34], update=True)
            if scene_id != 30:
                raise

        # The inherited bottom-left return action can be intercepted by the
        # group-chat modal and leave the screen unchanged.  Dismiss the same
        # modal through the dimmed area outside its annotated content window.
        # Landing directly on #34 is also a valid completed unwind.
        window = runtime.shape(30, "窗口").box()
        outside_x = max(8.0, float(window.get("x") or 0) * 0.5)
        outside_y = float(window.get("y") or 0) + float(window.get("h") or 0) * 0.5
        runtime.click_frame_point(30, outside_x, outside_y)
        yield from runtime.wait_action_settle(1.0)
        return (yield from runtime.wait_view(
            332,
            20,
            34,
            timeout=transition_timeout,
            label="日常_红包：点击群聊弹层外部返回",
        ))

    def _process_current_daily_redpacket_group(
        self,
        runtime: Any,
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
        yield from runtime.wait_view(
            30,
            timeout=transition_timeout,
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
                    runtime,
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
                    return 0, False
                try:
                    # The top-right locator remains as a plain ``福`` icon
                    # after its numeric pending-count badge disappears.  Its
                    # formal Shape intentionally requires ``\d+``; absence is
                    # therefore the normal "no more pending locator entries"
                    # state, not a failed click.  Detect first so ``wait_click``
                    # cannot turn that terminal state into RuntimeError.
                    yield from runtime.wait_shape(
                        30,
                        "红包",
                        timeout=transition_timeout,
                        label="日常_红包：确认右上红包定位入口仍有数字角标",
                    )
                except TimeoutError:
                    self._log(
                        "success",
                        "日常_红包：#30 右上定位入口无数字角标，当前群没有更多待领红包",
                    )
                    return 0, False
                runtime.click_shape_center(30, "红包")
                self._log(
                    "action",
                    (
                        "日常_红包：当前 #30 窗口未见红包卡片，"
                        f"点击右上红包定位入口 {locator_attempt + 1}/{max(1, int(max_locator_clicks))}"
                    ),
                )
                yield from runtime.wait_action_settle(poll_seconds)
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
                        runtime,
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
            runtime.click_frame_point(30, click_x, click_y)
            self._log(
                "action",
                (
                    f"日常_红包：#30[窗口] 找到 {len(targets)} 个未领取 OCR 候选，"
                    f"按最新的“{target['matched_text']}”点击同卡片热区 "
                    f"{attempt}/{len(click_points)} ({click_x:.1f},{click_y:.1f})"
                ),
            )
            try:
                current = yield from runtime.wait_view(
                    397,
                    399,
                    672,
                    timeout=attempt_timeout,
                    label="日常_红包：等待开红包、已领完或已抢光状态",
                )
                break
            except TimeoutError:
                scene_id, _score, _frame = runtime.current_scene([30], update=True)
                if scene_id != 30:
                    return 0, False
        if current is None:
            return 0, False
        if int(current.id or 0) == 672:
            yield from self._dismiss_daily_redpacket_sold_out(
                runtime,
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
            runtime,
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
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
        route_plan = self._daily_redpacket_runtime_route_plan()
        if route_plan.get("route") == "qmch_reward_terminal":
            terminal_uids = [str(uid) for uid in route_plan.get("uids") or []]
            return self._daily_redpacket_result(
                payload,
                f"鸿运福签已是 rewarded 终态，幂等零动作跳过：{terminal_uids}",
                opened_count=0,
                current_scene=34,
            )
        if route_plan.get("route") == QMCH_REWARD_EVENT_KEY:
            return (yield from self._dispatch_daily_redpacket_runtime_route(
                runtime,
                ctx,
                stop_event,
                payload,
                route_plan,
            ))
        # 巡检和 Job 执行各司其职：巡检只推进 next_time；本 Cell 从当前帧
        # 重新授权每一步。门卫阴性属于正常业务结果（run_status=success），
        # 之后巡检若再次看到红包，仍可把十二小时后的 next_time 提前。
        gate_frame = runtime.cur_frame(update=True)
        gate = self._daily_redpacket_quick_gate(ctx, gate_frame)
        if not bool(gate.get("matched")):
            runtime_candidates = self._daily_redpacket_runtime_candidates()
            if not bool(runtime_candidates.get("trigger_ready")):
                return self._daily_redpacket_result(
                    payload,
                    "#395[红包] 阴性且无新鲜完整 Runtime 候选，本轮无需深入检查",
                    current_scene=34,
                )
            self._log(
                "diagnostic",
                "日常_红包：#395 阴性但 Runtime 有新鲜结构化候选；仅进入聊天做逐层视觉检查",
            )

        yield from runtime.wait_shape(
            395,
            "聊天",
            timeout=transition_timeout,
            label="日常_红包：逐帧确认世界页聊天入口",
        )
        runtime.click_shape_center(395, "聊天")
        landing = yield from runtime.wait_view(
            332,
            333,
            timeout=transition_timeout,
            label="日常_红包：等待聊天或通讯录页",
        )
        # The chat popup preserves its last selected tab.  Opening it can
        # therefore legitimately land on #333 (contacts) instead of #332
        # (chat).  Follow the annotated tab edge instead of treating that
        # stable page as a dead end or repeatedly closing/reopening it.
        if int(landing.id or 0) == 333:
            yield from runtime.click_shape_center_then_view(
                333,
                "聊天",
                332,
                timeout=transition_timeout,
                label="日常_红包：从通讯录切回聊天页 #332",
            )

        # The popup preserves the last category tab.  Red-packet-like entries
        # can belong to 群聊 or 活动, so a fixed category silently filters legal
        # rows.  Idempotently select the formally annotated 全部 tab before the
        # list-local visual gate.
        yield from runtime.click_shape_center_then_view(
            332,
            "全部",
            332,
            timeout=transition_timeout,
            label="日常_红包：幂等切到全部消息",
        )

        try:
            initial_group = yield from self._wait_and_click_daily_redpacket_group(
                runtime,
                ctx,
                timeout_seconds=redpacket_confirm_seconds,
                poll_seconds=poll_seconds,
                max_scrolls=max_scrolls,
            )
        except TimeoutError:
            unavailable = self._daily_redpacket_require_fresh_uid_snapshot(
                phase="#332 Runtime-GUI 群行持续未对齐"
            )
            unclaimable_uids = sorted(unavailable.get("uids") or ())
            yield from self._close_daily_redpacket_chat_to_world(
                runtime,
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

        window_shape = runtime.shape(332, "窗口")
        opened_count = 0
        processed_groups = 0
        quota_exhausted = False
        scroll_count = 0
        force_scroll = False
        left_chat_stack = False
        pending_group = initial_group
        while True:
            group = pending_group
            pending_group = None
            if group is None and not force_scroll:
                group = yield from self._find_and_click_daily_redpacket_group(
                    runtime,
                    ctx,
                    max_scrolls=0,
                    settle_seconds=poll_seconds,
                )
            force_scroll = False
            if group is not None:
                if processed_groups >= max_group_count:
                    raise RuntimeError(f"日常_红包：已处理 {processed_groups} 个群仍未加载到底，停止避免无限循环")
                processed_groups += 1
                group_opened, opened_page = yield from self._process_current_daily_redpacket_group(
                    runtime,
                    ctx,
                    transition_timeout=transition_timeout,
                    poll_seconds=poll_seconds,
                    max_open_count=max_open_count,
                    max_locator_clicks=max_locator_clicks,
                )
                opened_count += int(group_opened)
                quota_exhausted = bool(
                    isinstance(getattr(runtime, "attrs", None), dict)
                    and runtime.attrs.pop("daily_redpacket_quota_exhausted", False)
                )
                return_view = yield from self._return_daily_redpacket_group_to_list(
                    runtime,
                    transition_timeout=transition_timeout,
                )
                return_scene = int(return_view.id or 0)
                if return_scene != 332:
                    left_chat_stack = True
                    if return_scene != 34:
                        yield from runtime.goto_view(34)
                    break
                if quota_exhausted:
                    break
                # 未能打开领取页时，该 UID 可能仍在 Runtime；强制向下加载，
                # 避免反复对齐并点击同一行。成功领取后由 fresh UID 集合决定下一目标。
                force_scroll = not bool(opened_page)
                continue
            if scroll_count >= max_scrolls:
                break
            changed = yield from runtime.scroll_shape_content(
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
            yield from runtime.wait_action_settle(poll_seconds)

        if not left_chat_stack:
            yield from self._close_daily_redpacket_chat_to_world(
                runtime,
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
