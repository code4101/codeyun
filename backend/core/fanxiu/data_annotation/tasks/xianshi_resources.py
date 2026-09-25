"""仙市资源业务：秘藏阁免费仙币与每周资源领取。

本模块定义领取窗口、物品分组、完成观察和下一次调度时间；聚合作业中
仅报告完成事实，由父作业统一写入调度。仙市兑换策略见 xianshi_exchange。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.prayer_cycle import (
    PRAYER_CYCLE_NAMES, PRAYER_CYCLE_TIMEZONE, current_prayer_cycle, next_prayer_cycle,
)
from ..effective_time import job_now as _now
from ..job_times import next_business_time


class XianshiResourceTaskMixin:
    def xianshi_weekly_resources_admission(
        self,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload = dict(payload or {})
        if self._xianshi_weekly_resources_phase(payload) != "skip":
            return None
        now = datetime.now(PRAYER_CYCLE_TIMEZONE)
        days_until_monday = (7 - now.weekday()) % 7 or 7
        next_time = (now + timedelta(days=days_until_monday)).replace(
            hour=0,
            minute=4,
            second=0,
            microsecond=0,
        )
        return self._persist_admission_decision(payload, {
            "result": "success",
            "message": "仙市_每周资源：当前不是周一领取日，未执行游戏操作",
            "next_time": next_time.strftime("%Y-%m-%d %H:%M:%S"),
            "current_scene": None,
        })

    def _execute_daily_xianshi_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少仙市_秘藏阁资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image34 = images.get(34)
        image247 = images.get(247)
        image248 = images.get(248)
        image249 = images.get(249)
        image250 = images.get(250)

        task_label = "仙市_秘藏阁"
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        with self._lock:
            self._set_status_locked(
                "running",
                f"{task_label}：从事务锚点 #34 开始",
                phase="daily_xianshi_go_world",
                current_scene=None,
            )
            self._log_locked("action", f"{task_label}：先回到 #34，再执行完整事务")
        yield from context.go_scene(34)
        yield from context.wait_scene([34], label=f"{task_label}：等待世界 #34")
        yield from self._open_daily_xianshi_coin_list(ctx, stop_event, payload, image34, image247, image248, task_label=task_label)

        # This transaction always re-enters through the #249 list.  The list
        # itself contains both "宝匣" and "兑换所需", so treating those words
        # as proof of an already-open detail skips the required first-item
        # click.  Open the current first item unconditionally; its detail then
        # proves either free/领取 or paid/兑换 idempotence.
        completed = yield from self._click_daily_xianshi_free_coin_box(
            ctx,
            stop_event,
            payload,
            image249,
            image250,
            task_label=task_label,
        )

        if completed == "not_free":
            self._record_daily_xianshi_done(payload, message="首项详情已确认价格与兑换，今日免费项已无可领")
        elif completed is True:
            self._record_daily_xianshi_done(payload, message="免费宝匣已领取，并已复查首项变为付费")
        else:
            raise RuntimeError(f"{task_label}：未取得免费项完成证据")

        yield from self._safe_daily_done_cleanup(
            lambda: self._return_daily_xianshi_to_world(ctx, stop_event, payload, image249, task_label=task_label),
            label=task_label,
            repeat_risk="重复领取",
        )
        return "success"

    def _execute_xianshi_weekly_resources_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少仙市_每周资源资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image34 = images.get(34)
        image247 = images.get(247)
        if not isinstance(image34, dict) or not isinstance(image247, dict):
            raise RuntimeError("仙市_每周资源：缺少 #34 或 #247 标注，无法进入仙市")

        task_label = "仙市_每周资源"
        phase = self._xianshi_weekly_resources_phase(payload)

        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([316, 247, 34], wait=5.0, required=False)
        (scene_id, _score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        claimed: list[str] = []
        claim_attempts = 0
        uncertain_detail = False
        if scene_id == 316:
            if phase != "midnight":
                raise RuntimeError(f"{task_label}：当前停在商品详情 #316，非 00:00-05:00 补领窗口不自动领取")
            claim_attempts += 1
            claim_result = yield from self._claim_current_xianshi_weekly_resource_detail(context, current_prayer_cycle())
            if claim_result["status"] == "claimed":
                claimed.append(str(claim_result["item"]))
            elif claim_result["status"] == "unknown":
                uncertain_detail = True
            yield from context.wait_scene([247], label=f"{task_label}：等待返回秘藏阁 #247")
            scene_id = 247
        if scene_id != 247:
            if scene_id != 34:
                yield from context.go_scene(34)
                yield from context.wait_scene([34])
            yield from self._open_xianshi_weekly_resource_entry(
                context,
                payload,
            )

        already_terminal = yield from self._confirm_xianshi_weekly_resources_terminal(
            context,
            phase=phase,
            reserved_group=next_prayer_cycle(),
        )
        if already_terminal:
            yield from context.wait_click_then_scene(
                247,
                "返回",
                34,
                settle_seconds=float(payload.get("xianshi_return_settle_seconds") or 1.0),
            )
            next_time = self._record_xianshi_weekly_resources_done(payload)
            self._log(
                "success",
                f"{task_label}：当前免费资源集合已满足 {phase} 阶段终态，下次 {next_time}",
            )
            return "success"

        if phase == "midnight" and not uncertain_detail:
            target = current_prayer_cycle()
            max_attempts = 2
            while claim_attempts < max_attempts:
                claim_attempts += 1
                claim_result = yield from self._claim_xianshi_weekly_resource_slot(context, "第1个物品", target)
                if claim_result["status"] == "unknown":
                    uncertain_detail = True
                    break
                if claim_result["status"] != "claimed":
                    break
                claimed.append(str(claim_result["item"]))
        elif phase != "midnight" and not uncertain_detail:
            max_attempts = 8
            skipped_groups = 0
            skipped_group = next_prayer_cycle()
            for group in PRAYER_CYCLE_NAMES:
                if group == skipped_group:
                    skipped_groups += 1
                    continue
                slot = "第3个物品" if skipped_groups else "第1个物品"
                for _ in range(2):
                    if claim_attempts >= max_attempts:
                        break
                    claim_attempts += 1
                    claim_result = yield from self._claim_xianshi_weekly_resource_slot(context, slot, group)
                    if claim_result["status"] == "unknown":
                        uncertain_detail = True
                        break
                    if claim_result["status"] != "claimed":
                        break
                    claimed.append(str(claim_result["item"]))
                if uncertain_detail:
                    break
                if claim_attempts >= max_attempts:
                    break

        terminal_confirmed = yield from self._confirm_xianshi_weekly_resources_terminal(
            context,
            phase=phase,
            reserved_group=next_prayer_cycle(),
        )
        yield from context.wait_click_then_scene(247, "返回", 34, settle_seconds=float(payload.get("xianshi_return_settle_seconds") or 1.0))
        if not terminal_confirmed:
            next_time = self._record_xianshi_weekly_resources_retry(
                payload,
                seconds=int(payload.get("unknown_detail_retry_seconds") or 600),
            )
            self._log(
                "skip",
                f"{task_label}：未确认免费资源集合达到本阶段幂等终态，本轮不推进周周期，下次 {next_time}",
            )
            return "skipped"
        suffix = f"，跳过 {next_prayer_cycle()} 资源" if phase == "after_reset" else ""
        claimed_text = ", ".join(claimed) if claimed else "目标资源已领完"
        next_time = self._record_xianshi_weekly_resources_done(payload)
        self._log(
            "success",
            f"{task_label}：已领取 {claimed_text}{suffix}，下次 {next_time}",
        )
        return "success"

    def _record_xianshi_weekly_resources_done(
        self,
        payload: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> str:
        current = (now or datetime.now(PRAYER_CYCLE_TIMEZONE)).replace(tzinfo=None)
        next_time = next_business_time(
            ("00:00", "05:00"),
            now=current,
            weekdays=(0,),
        )
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "xianshi-weekly-resources"),
            next_time,
        )
        return next_time

    def _record_xianshi_weekly_resources_retry(
        self,
        payload: dict[str, Any],
        *,
        seconds: int,
        now: datetime | None = None,
    ) -> str:
        current = now or datetime.now(PRAYER_CYCLE_TIMEZONE).replace(tzinfo=None)
        next_time = (current + timedelta(seconds=max(60, int(seconds)))).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "xianshi-weekly-resources"),
            next_time,
        )
        return next_time

    def _open_xianshi_weekly_resource_entry(
        self,
        context: Any,
        payload: dict[str, Any],
    ):
        max_scrolls = max(0, int(payload.get("xianshi_entry_max_scrolls") or 4))
        entry_shape = context.shape(34, "仙市")
        internal_scene_left = False
        for scroll_index in range(max_scrolls + 1):
            if context.match_shape(entry_shape):
                yield from context.wait_click_then_shape(
                    34,
                    "仙市",
                    247,
                    "秘藏阁",
                    settle_seconds=2.0,
                    timeout=float(payload.get("xianshi_entry_wait_seconds") or 6.0),
                    retry_if_source_remains=True,
                    max_clicks=int(payload.get("xianshi_entry_max_clicks") or 3),
                )
                return True
            right_menu_text = context.ocr_text_in_shapes(
                34,
                ("右侧菜单",),
                padding=8,
            )
            compact_menu_text = _sanitize_ocr_text(right_menu_text).replace(" ", "")
            if (
                not internal_scene_left
                and "仙市" not in compact_menu_text
                and "天机阁" in compact_menu_text
                and "战斗" in compact_menu_text
            ):
                self._log(
                    "action",
                    "仙市_每周资源：当前是世界样式的内部场景，先通过正式离开确认返回正常世界",
                )
                context.click_shape_center(85, "离开")
                yield from context.wait_scene(
                    [34],
                    wait=20.0,
                    label="仙市_每周资源：等待 Layer 0 处理离开确认并返回世界",
                )
                internal_scene_left = True
                continue
            if scroll_index >= max_scrolls:
                break
            self._log(
                "action",
                f"仙市_每周资源：右侧菜单当前未显示「仙市」，向上恢复菜单 {scroll_index + 1}/{max_scrolls}",
            )
            can_continue = yield from context.scroll_shape_content(
                34,
                "右侧菜单",
                direction="up",
                ratio=0.55,
                duration=0.6,
                settle_seconds=0.8,
                unchanged_confirmations=2,
            )
            if not can_continue:
                break
        raise RuntimeError("仙市_每周资源：右侧菜单已恢复到顶端，仍未识别到「仙市」入口")

    def _xianshi_weekly_resources_phase(self, payload: dict[str, Any]) -> str:
        override = str(payload.get("phase") or "").strip()
        if override in {"midnight", "after_reset"}:
            return override
        now = datetime.now(PRAYER_CYCLE_TIMEZONE)
        if now.hour < 5:
            return "midnight" if now.weekday() == 0 else "after_reset"
        return "after_reset"

    def _xianshi_weekly_resources_terminal_observation(
        self,
        text: str,
        item_names: list[str],
        *,
        phase: str,
        reserved_group: str,
    ) -> dict[str, Any]:
        compact = _sanitize_ocr_text(text).replace(" ", "")
        free_count = compact.count("免费")
        groups = [
            group
            for name in item_names
            if (group := self._classify_xianshi_weekly_resource_name(name)) is not None
        ]
        obstructed = any(marker in compact for marker in ("幸运地获得了", "获得了"))
        # 免费周资源领完后会从列表消失，首屏只剩永久限购的付费商品。
        # 不能再用已消失资源的“每周限购”证明页面加载，否则幂等重跑
        # 会继续打开付费商品，并把正常的零免费库存误判为领取失败。
        page_loaded = (
            "每周限购" in compact and ("超值必买" in compact or "仙市" in compact)
        ) or (
            "超值必买" in compact
            and "永久限购" in compact
            and "购买所需" in compact
        )
        if phase == "midnight":
            terminal = not obstructed and page_loaded and free_count == 0
        else:
            terminal = not obstructed and (
                (free_count == 0 and page_loaded)
                or (
                    0 < free_count <= 2
                    and len(groups) == free_count
                    and all(group == reserved_group for group in groups)
                )
            )
        return {
            "terminal": terminal,
            "obstructed": obstructed,
            "page_loaded": page_loaded,
            "free_count": free_count,
            "groups": groups,
        }

    def _confirm_xianshi_weekly_resources_terminal(
        self,
        context: Any,
        *,
        phase: str,
        reserved_group: str,
    ):
        previous_key: tuple[int, tuple[str, ...]] | None = None
        for read_index in range(6):
            frame = context.cur_frame(update=True)
            text = context.ocr_text(frame)
            fragments = context.ocr_fragments(frame)
            item_names = [str(fragment.get("text") or "") for fragment in fragments]
            observation = self._xianshi_weekly_resources_terminal_observation(
                text,
                item_names,
                phase=phase,
                reserved_group=reserved_group,
            )
            if not observation["page_loaded"]:
                self._log("detail", f"仙市_每周资源：未确认列表加载，页面OCR={text!r}")
            key = (
                int(observation["free_count"]),
                tuple(str(group) for group in observation["groups"]),
            )
            self._log(
                "detail",
                (
                    "仙市_每周资源：终态复核 "
                    f"phase={phase} 免费={key[0]} groups={list(key[1])} "
                    f"obstructed={observation['obstructed']} terminal={observation['terminal']}"
                ),
            )
            if observation["obstructed"]:
                previous_key = None
                if read_index < 5:
                    yield from context.wait_action_settle(1.5)
                continue
            if key == previous_key:
                return bool(observation["terminal"])
            previous_key = key
            if read_index < 5:
                yield from context.wait_action_settle(1.0)
        return False

    def _classify_xianshi_weekly_resource_name(self, name: str) -> str | None:
        text = _sanitize_ocr_text(name)
        if "洗灵" in text:
            return "洗灵"
        if "花神" in text:
            return "仙花"
        if "灵草" in text:
            return "炼丹"
        if "御兽" in text or "兽" in text:
            return "灵兽"
        if "玄魄" in text or "玄" in text:
            return "淬体"
        return None

    def _claim_xianshi_weekly_resource_slot(self, context: Any, slot: str, expected_group: str):
        landed = yield from context.wait_click_then_scene(247, slot, 316)
        if int(getattr(landed, "id", landed)) != 316:
            raise RuntimeError(
                f"仙市_每周资源：{slot} 未进入资源详情 #316，实际为 #{landed}，停止领取"
            )
        return (yield from self._claim_current_xianshi_weekly_resource_detail(context, expected_group, slot=slot))

    def _claim_current_xianshi_weekly_resource_detail(self, context: Any, expected_group: str, *, slot: str = "当前物品"):
        observations: list[tuple[str, str | None]] = []
        name_text = ""
        group = None
        for read_index in range(3):
            name_text = str(context.ocr_text_in_shapes(316, ("物品名称",), padding=8) or "")
            group = self._classify_xianshi_weekly_resource_name(name_text)
            observations.append((name_text, group))
            if group == expected_group:
                break
            known_groups = [item_group for _text, item_group in observations if item_group is not None]
            if len(known_groups) >= 2 and known_groups[-2:] == [group, group]:
                break
            if read_index < 2:
                self._log(
                    "detail",
                    f"仙市_每周资源：{slot} 第 {read_index + 1} 次商品名识别为「{name_text}」={group or '未知'}，等待遮挡消退后复核",
                )
                yield from context.wait_action_settle(1.0)

        if group is None:
            summary = " / ".join(text or "空" for text, _group in observations)
            self._log("skip", f"仙市_每周资源：{slot} 三次商品名仍无法分类（{summary}），本轮结果未知")
            yield from self._return_xianshi_weekly_resource_detail(context)
            return {"status": "unknown", "item": None}
        if group != expected_group:
            agreeing = sum(1 for _text, item_group in observations if item_group == group)
            if agreeing < 2:
                self._log("skip", f"仙市_每周资源：{slot} 商品分类不稳定，无法确认 {expected_group} 已领完")
                yield from self._return_xianshi_weekly_resource_detail(context)
                return {"status": "unknown", "item": None}
            self._log("skip", f"仙市_每周资源：{slot} 连续识别到 {group}，预期 {expected_group}，目标资源已领完")
            yield from self._return_xianshi_weekly_resource_detail(context)
            return {"status": "exhausted", "item": None}
        for click_index in range(3):
            yield from context.wait_click(316, "领取")
            yield from context.wait_action_settle(1.2)
            _wait_scene_match = yield from context.wait_scene([247, 316], wait=5.0, required=False)
            (scene_id, _score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id == 247:
                self._log("success", f"仙市_每周资源：已领取 {name_text}，并确认返回列表")
                return {"status": "claimed", "item": str(name_text or expected_group)}
            if scene_id != 316:
                raise RuntimeError(
                    f"仙市_每周资源：点击领取后落点不是列表 #247 或详情 #316，而是 {scene_id}"
                )
            if click_index < 2:
                self._log("detail", f"仙市_每周资源：点击领取后仍在详情页，重试 {click_index + 2}/3")
        self._log("skip", f"仙市_每周资源：连续三次点击领取仍停在详情页，领取状态未确认")
        yield from self._return_xianshi_weekly_resource_detail(context)
        return {"status": "unknown", "item": None}

    def _return_xianshi_weekly_resource_detail(self, context: Any):
        last_error: Exception | None = None
        for shape_title in ("返回", "shape 3"):
            try:
                yield from context.wait_click_then_scene(
                    316,
                    shape_title,
                    247,
                    settle_seconds=1.0,
                    timeout=6.0,
                )
                return True
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        return False

    def _daily_xianshi_text_is_coin_list(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return (
            "秘藏阁" in normalized
            and "仙币" in normalized
            and any(fragment in normalized for fragment in ("天衍灵石", "兑换所需", "限购"))
        )

    def _record_daily_xianshi_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._next_daily_boss_reset_time_text()
        if bool(payload.get("schedule", True)):
            self._persist_scheduler_task_next_time(
                str(payload.get("__scheduler_task_id") or "legacy-daily-xianshi"),
                next_time,
            )
            self._log("success", f"仙市_秘藏阁：{message}，下次 {next_time}")
        else:
            # Aggregated run: record the business fact only.  The parent Job owns
            # the single schedule write, so no retired id may be rescheduled.
            self._log("success", f"仙市_秘藏阁：{message}")
        return next_time

    def _schedule_daily_xianshi_next_check(self, payload: dict[str, Any], *, message: str, seconds: int) -> str:
        if not bool(payload.get("schedule", True)):
            # A bounded recheck is not a completion.  Under aggregation it must
            # surface to the parent instead of pretending the stage finished.
            raise RuntimeError(
                f"仙市_秘藏阁：聚合调度下不能用短重试伪装完成（{message}）"
            )
        task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-xianshi").strip() or "legacy-daily-xianshi"
        next_time = (_now() + timedelta(seconds=max(60, int(seconds)))).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(
            task_id,
            next_time,
        )
        self._log("skip", f"仙市_秘藏阁：{message}，下次 {next_time}")
        return next_time

    def _open_daily_xianshi_coin_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image34: dict[str, Any],
        image247: dict[str, Any],
        image248: dict[str, Any],
        *,
        task_label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from self._ensure_world_main_for_right_menu(ctx, context, stop_event, image34, task_label=task_label)
        yield from context.wait_click_then_shape(
            34,
            "仙市",
            247,
            "秘藏阁",
            settle_seconds=2.0,
            timeout=float(payload.get("xianshi_entry_wait_seconds") or 6.0),
            retry_if_source_remains=True,
            max_clicks=int(payload.get("xianshi_entry_max_clicks") or 3),
            label=f"{task_label}：等待仙市入口页",
        )
        # #248 is a reference frame for the 秘藏阁 tab strip, not a globally
        # recognizable scene, so it can never satisfy wait_click's source-scene
        # guard.  Keep #247 as the established source and click the strip.
        yield from context.wait_click(247, "秘藏阁")
        yield from context.wait_action_settle(1.5)
        # The tab strip is regular text whose tabs are added or shifted by game
        # versions, so the 仙币 tab is located as OCR text inside the whole
        # 页签条 region.  A fixed box keeps matching the neighbouring tab at
        # ~79% and would click it after any threshold relaxation.
        yield from context.wait_click_ocr_text(
            248,
            "仙币",
            in_shapes=("页签条",),
            timeout_seconds=float(payload.get("coin_tab_visible_wait_seconds") or 30.0),
        )
        yield from context.wait_action_settle(float(payload.get("coin_tab_settle_seconds") or 2.5))
        yield from context.wait_scene(
            [249],
            wait=float(payload.get("coin_list_wait_seconds") or 30.0),
            label=f"{task_label}：等待仙币免费宝匣列表",
        )

    def _click_daily_xianshi_free_coin_box(
        self, ctx: dict[str, Any], stop_event: threading.Event,
        payload: dict[str, Any], image249: dict[str, Any], image250: dict[str, Any],
        *, task_label: str,
    ):
        """仙币免费项排首位；详情正向证据与领取后首项变化共同证明完成。"""
        context = self._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
        claimed = False
        for _inspection in range(2):
            state = None
            for attempt in range(1, 4):
                landed = yield from context.wait_scene([249], label=f"{task_label}：确认仙币列表")
                if int(landed) != 249:
                    raise RuntimeError(f"{task_label}：未进入仙币列表，当前 #{int(landed)}")
                context.click_shape_center(249, "首个宝匣")
                try:
                    state = yield from context.wait_any(
                        {
                            "claim": context.shape_visible(250, "领取"),
                            "exchange": context.shape_visible(250, "兑换"),
                        },
                        timeout=8.0,
                        label=f"{task_label}：等待首项详情打开",
                    )
                    # 详情已打开后，只以固定价格栏区分是否付费。
                    priced = context.shape_matches(250, "价格") is not None
                    if not priced and state == "exchange":
                        raise RuntimeError(f"{task_label}：详情显示兑换但价格栏未识别，未确认免费")
                    state = "paid" if priced else "free"
                    break
                except TimeoutError:
                    # 未进入详情时，列表的“兑换所需”不能证明已领取。
                    if attempt == 3:
                        raise
                    landed = yield from context.wait_scene([249], wait=3.0)
                    if int(landed) != 249:
                        raise RuntimeError(f"{task_label}：首项详情未确认，当前 #{int(landed)}")
                    self._log("warning", f"{task_label}：第 {attempt} 次点击仍在列表，重新打开首项")
            if state == "paid":
                yield from self._return_daily_xianshi_box_detail_to_coin_list(
                    ctx, stop_event, payload, image250, task_label=task_label,
                )
                self._log("success", f"{task_label}：首项详情同时确认价格与兑换，免费项已无可领")
                return True if claimed else "not_free"
            if state != "free" or claimed:
                raise RuntimeError(f"{task_label}：领取后免费项仍可领取，未确认完成")
            yield from context.wait_click_then_scene(
                250, "领取", [249], timeout=30.0, max_clicks=1,
                label=f"{task_label}：领取后等待仙币列表并复查首项",
            )
            claimed = True
        raise RuntimeError(f"{task_label}：未完成领取后验证")

    def _return_daily_xianshi_box_detail_to_coin_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image250: dict[str, Any],
        *,
        task_label: str,
    ):
        del image250
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        # 同一商品详情结构共用 #250 的正式关闭位置，不点击付费兑换。
        yield from context.click_shape_center_then_scene(250, "返回", [249], timeout=20.0)
        return "success"

    def _return_daily_xianshi_to_world(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image249: dict[str, Any],
        *,
        task_label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._log_locked("action", f"{task_label}：收尾前往 #34")
        yield from context.go_scene(
            34,
            wait=float(payload.get("return_world_layer0_wait_seconds") or 2.0),
        )
        yield from context.wait_scene([34], label=f"{task_label}：等待世界 #34")
