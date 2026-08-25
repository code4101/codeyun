from __future__ import annotations

import difflib
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from pathlib import Path
from types import GeneratorType

from sqlalchemy.exc import OperationalError

from backend.core.fanxiu.mail.policy import (
    fanxiu_mail_action_policy_for_record,
    fanxiu_mail_action_policy_for_rewards,
    fanxiu_mail_reward_name_known,
    fanxiu_mail_rewards_from_payload,
    fanxiu_mail_rewards_unresolved,
    fanxiu_mail_title_force_claim_allowed,
)
from backend.core.fanxiu.mail.runtime_store import (
    align_runtime_mail_records_claimable_between_visible_neighbors,
    find_runtime_mail_record_by_raw_title,
    find_runtime_mail_record_exact,
    mark_runtime_mail_record_missing_from_list,
    runtime_mail_records_by_normalized_title,
    runtime_mail_records_for_visible_row_exact,
    runtime_mail_records_for_visible_row_same_time,
    runtime_mail_records_same_time,
    runtime_mail_records_same_title,
    pending_runtime_mail_action_candidates,
    pending_runtime_mail_records,
    recent_runtime_mail_records,
    trace_runtime_mail_gap,
    update_runtime_mail_action,
    current_runtime_mail_sequence_snapshot,
)
from backend.core.fanxiu.mail.visual_alignment import (
    build_mail_visual_observations,
    diagnose_mail_window,
    mail_window_geometry_from_asset,
)
from backend.core.fanxiu.runtime_gui import ocr_name_similarity
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation import behavior_tree_runtime as _behavior_tree_runtime
from backend.core.fanxiu.data_annotation.behavior_tree_runtime import (
    _RuntimeMailRow,
    _data_annotation_mail_scan_state_path,
    _db_engine,
    _now,
)
from backend.core.fanxiu.data_annotation.state import (
    parse_data_annotation_task_time,
    read_data_annotation_json as _read_data_annotation_json,
    write_data_annotation_json as _write_data_annotation_json,
)
from backend.core.fanxiu.data_annotation.effective_time import job_now
from pyxllib.autogui import Shape, View, image_number as _runtime_image_number
from pyxllib.prog import BehaviorTreeStatus


@dataclass(frozen=True)
class _RuntimeMailActionOutcome:
    policy: str
    wait_result: str
    visual_confirmed: bool


class MailTaskMixin:
    # 邮件详情偶尔会在服务器结算或连续翻页后延迟二十余秒才稳定为
    # #122/#123。12 秒会把仍在加载的真实详情误判成 unknown。
    _MAIL_DETAIL_READY_TIMEOUT_SECONDS = 30.0

    def _execute_mail_legacy_scan_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        _behavior_tree_runtime.ensure_fanxiu_mail_table()
        payload = dict(payload or {})
        self._mail_selective_claim_terminal_message = ""
        entry_mode = str(payload.get("entry_mode") or payload.get("mail_entry_mode") or "dynamic").strip().lower()
        observe_only = bool(payload.get("observe_only") or payload.get("scan_only"))
        scan_mode = str(payload.get("scan_mode") or ("full" if payload.get("full_scan") else "incremental")).strip().lower()
        use_current_page = bool(payload.get("use_current_page"))
        target_title = str(payload.get("target_title") or payload.get("mail_title") or "").strip()
        target_time_text = self._normalize_mail_time_text(str(payload.get("target_time_text") or payload.get("mail_time_text") or "").strip())
        game_first = bool(payload.get("game_first") or payload.get("ui_first"))
        fail_on_runtime_gap = bool(payload.get("fail_on_runtime_gap"))
        try:
            max_actions = int(payload.get("max_actions") or 0)
        except (TypeError, ValueError):
            max_actions = 0
        raw_action_policies = payload.get("action_policies")
        if isinstance(raw_action_policies, list):
            action_policies = {str(item or "").strip().lower() for item in raw_action_policies}
            action_policies &= {"claim", "delete"}
        else:
            action_policies = {"claim", "delete"}
        if not action_policies:
            action_policies = {"claim", "delete"}
        if observe_only and not payload.get("scan_mode") and not payload.get("full_scan"):
            scan_mode = "full"
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少邮件_历史扫描资产树路径，无法执行邮件作业")
        with self._lock:
            self._log_locked("info", "邮件_历史扫描：仅使用 Runtime 与当前画面")
        try:
            if observe_only:
                with self._lock:
                    self._log_locked("info", "邮件_全量遍历：只观察并滚动加载邮件，不领取、不删除")
            elif game_first:
                with self._lock:
                    self._log_locked("info", "邮件_历史扫描：游戏画面优先模式，缺 Runtime 记录的可见邮件按详情页按钮处理")
            runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
            scene_id, score, frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
            force_reopen_mail = observe_only or scan_mode in {"full", "full_scan", "observe", "observe_only", "refresh", "sync"}
            if scene_id == 121 and not use_current_page and (force_reopen_mail or (not observe_only and self._pending_runtime_mail_action_count() > 0)):
                image121 = ctx.get("images", {}).get(121)
                back_shape = self._find_shape(image121, "空白-返回") if isinstance(image121, dict) else None
                if isinstance(image121, dict) and back_shape:
                    reason = "刷新邮件 Runtime 列表" if force_reopen_mail else "重置列表顶部"
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"邮件_历史扫描：退出邮件页以{reason}",
                            phase="mail_claim_reset_mail_list",
                            current_scene=121,
                        )
                        self._log_locked("action", f"邮件_历史扫描：点击 #121「空白-返回」，重新从顶部进入邮件，{reason}")
                    yield from runtime.wait_click(121, "空白-返回")
                    yield from runtime.wait_view(34, label="邮件_历史扫描：返回世界 #34")
                    scene_id = 34
                else:
                    with self._lock:
                        self._log_locked("error", "邮件_历史扫描：缺少 #121「空白-返回」标注，保留当前位置扫描")
            if scene_id != 121:
                open_result = self._open_mail_scene(ctx, stop_event, asset_tree_path, entry_mode=entry_mode)
                result = (yield from open_result) if isinstance(open_result, GeneratorType) else open_result
                if result == "no_mail":
                    return "success"
                if result != "success":
                    return result
            else:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"邮件_历史扫描：当前已在邮件 #121 {score:.0f}%",
                        phase="mail_claim_scan_current_mail_scene",
                        current_scene=121,
                    )
                    self._log_locked("info", f"邮件_历史扫描：当前已在邮件 #121 {score:.0f}%，直接扫描")
            scan_result = self._scan_mail_scene(
                ctx,
                stop_event,
                action_enabled=not observe_only,
                scan_mode=scan_mode,
                action_policies=action_policies,
                max_actions=max_actions if max_actions > 0 else None,
                target_title=target_title,
                target_time_text=target_time_text,
                game_first=game_first,
                fail_on_runtime_gap=fail_on_runtime_gap,
            )
            return (yield from scan_result) if isinstance(scan_result, GeneratorType) else scan_result
        finally:
            pass

    def _execute_mail_selective_claim_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        _behavior_tree_runtime.ensure_fanxiu_mail_table()
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少邮件_选择性领取资产树路径，无法执行邮件作业")
        if not self._refresh_runtime_mail_snapshot("任务开始", force_refresh=True):
            raise RuntimeError("邮件_选择性领取：动态邮件模型不可用，拒绝基于过期记录处理")
        initial_snapshot = current_runtime_mail_sequence_snapshot(_db_engine)
        if not initial_snapshot.get("complete"):
            raise RuntimeError("邮件_选择性领取：任务开始未得到完整 Runtime 邮件序列")
        self._validate_precise_mail_policy_snapshot(initial_snapshot, reason="任务开始")
        raw_max_actions = int(payload.get("max_actions") or 0)
        max_actions = raw_max_actions if raw_max_actions > 0 else None
        # 上限只负责防失控，不能承担“到底”判断。200 封邮件叠加半页滚动时仍可能
        # 超过 80 次，因此保留更宽的工程保险；正常流程应由重复邮件行主动收尾。
        max_scrolls = max(1, int(payload.get("max_scrolls") or 150))
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)

        with self._lock:
            self._set_status_locked("running", "邮件_选择性领取：进入邮件 #121", phase="mail_selective_claim_go_mail")
        yield from self._open_mail_selective_claim_entry(runtime)
        scene_id, score, frame, text = self._fanxiu_runtime_scene_text(
            ctx,
            runtime,
            [121, 122, 123, 227, 34, 35, 69],
            update=True,
        )
        if scene_id == 227:
            with self._lock:
                self._set_status_locked("running", "邮件_选择性领取：关闭本轮奖励页", phase="mail_selective_claim_close_reward_page", current_scene=227)
                self._log_locked("action", "邮件_选择性领取：点击 #227「继续」关闭本轮奖励页")
            yield from runtime.wait_click(227, "继续", timeout=8.0)
            reward_result_view = yield from runtime.wait_view(121, 34, timeout=12.0, label="邮件_选择性领取：奖励页关闭后等待邮件或世界")
            scene_id = reward_result_view.id if isinstance(reward_result_view, View) else None
            score = 100.0 if scene_id in {121, 34} else 0.0
            frame = runtime.cur_frame(update=True)
            text = runtime.ocr_text(frame)
        if scene_id not in {121, 122, 123} and (
            yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label="邮件_选择性领取")
        ):
            scene_id, score, frame, text = self._fanxiu_runtime_scene_text(ctx, runtime, [121, 122, 123, 227, 34, 35, 69], update=True)
        if scene_id == 121:
            overlay_scene = self._mail_detail_overlay_scene(ctx, frame)
            if overlay_scene is not None:
                self._log(
                    "info",
                    f"邮件_选择性领取：底层命中 #121，但独立详情动作确认浮层 #{overlay_scene}",
                )
                scene_id = overlay_scene
        if scene_id == 121:
            with self._lock:
                self._status.update({"current_scene": 121, "updated_at": time.time()})
                self._log_locked("info", f"邮件_选择性领取：当前已在邮件 #121 {score:.0f}%，先退出重进刷新列表")
            image121_for_reset = ctx.get("images", {}).get(121) if isinstance(ctx.get("images"), dict) else None
            if isinstance(image121_for_reset, dict) and self._find_shape(image121_for_reset, "空白-返回") is not None:
                yield from self._leave_mail_scene_to_world(
                    ctx,
                    stop_event,
                    runtime,
                    121,
                    label="邮件_选择性领取",
                )
                yield from self._open_mail_selective_claim_entry(runtime)
            else:
                self._log("error", "邮件_选择性领取：缺少 #121「空白-返回」标注，无法重进刷新列表，保留当前页扫描")
        elif scene_id in {122, 123}:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"邮件_选择性领取：本轮入口异常落入详情页 #{scene_id}",
                    phase="mail_selective_claim_return_detail",
                    current_scene=scene_id,
                )
                self._log_locked(
                    "action",
                    f"邮件_选择性领取：本轮入口落入 #{scene_id}，缺少列表策略证据，只返回列表不领取/删除",
                )
            detail_image = ctx.get("images", {}).get(scene_id) if isinstance(ctx.get("images"), dict) else None
            back_shape = View(detail_image).get_shape("空白-返回") if isinstance(detail_image, dict) else None
            if back_shape is not None:
                back_shape.click(runtime)
            else:
                self._log("warning", f"邮件_选择性领取：#{scene_id} 缺少返回标注，使用已验证的左上空白返回")
                runtime.click_frame_point(scene_id, 1, 1)
            yield from runtime.wait_view(121, timeout=12.0, label="邮件_选择性领取：详情页安全返回邮件 #121")
            yield from self._leave_mail_scene_to_world(
                ctx,
                stop_event,
                runtime,
                121,
                label="邮件_选择性领取",
            )
            yield from self._open_mail_selective_claim_entry(runtime)
        else:
            raise RuntimeError(f"邮件_选择性领取：从稳定起点进入邮件后落点异常 #{scene_id or 'unknown'}")
        image121 = ctx.get("images", {}).get(121)
        if not isinstance(image121, dict):
            raise RuntimeError("缺少 #121 邮件帧标注，无法清理邮件")
        view121 = View(image121)
        list_shape = view121.get_shape("邮件清单2")
        if list_shape is None:
            raise RuntimeError("缺少 #121「邮件清单2」标注，无法遍历邮件清单")

        claimed_visible_count = sum(
            1
            for item in initial_snapshot.get("items") or []
            if bool(item.get("present_in_runtime"))
            and not bool(item.get("locked"))
            and str(item.get("runtime_status") or "") == "claimed"
        )
        if claimed_visible_count >= 20:
            self._log(
                "info",
                "邮件_选择性领取：跨 Cell 新批次检测到 "
                f"{claimed_visible_count} 封已领取邮件；先一键删除缩短列表，再重读 Runtime",
            )
            checkpoint_cleanup = yield from self._delete_read_mail_until_clean(
                runtime,
                view121,
                stop_event,
                reason=f"新批次已有 {claimed_visible_count} 封已领取邮件",
                initial_snapshot=initial_snapshot,
            )
            initial_snapshot = checkpoint_cleanup["snapshot"]

        geometry = mail_window_geometry_from_asset(image121)
        ordered_result = yield from self._execute_ordered_runtime_claim_batch(
            ctx,
            stop_event,
            runtime=runtime,
            image121=image121,
            view121=view121,
            list_shape=list_shape,
            geometry=geometry,
            snapshot=initial_snapshot,
            target_mail_ids={
                str(value)
                for value in payload.get("target_mail_ids") or []
                if str(value)
            },
        )
        target_count = len(
            self._select_precise_mail_claim_targets(
                initial_snapshot,
                {
                    str(value)
                    for value in payload.get("target_mail_ids") or []
                    if str(value)
                },
            )
        )
        self._validate_precise_mail_terminal_result(
            ordered_result,
            target_count=target_count,
        )
        message = (
            "邮件_选择性领取：完整闭环，"
            f"领取 {int(ordered_result.get('claimed_count') or 0)} 封，"
            f"删除前 {int(ordered_result.get('garbage_before') or 0)} 封，"
            f"删除 {int(ordered_result.get('deleted_count') or 0)} 封，"
            f"剩余垃圾 {int(ordered_result.get('garbage_after') or 0)} 封，"
            f"保留 {int(ordered_result.get('protected_count') or 0)} 封，"
            f"批次目标 {target_count} 封"
        )
        self._mail_selective_claim_terminal_message = message
        with self._lock:
            self._set_status_locked(
                "running",
                message,
                phase="mail_selective_claim_done",
                current_scene=34,
            )
            self._log_locked("success", message)
        return "success"

        self._refresh_runtime_mail_snapshot("进入邮件列表后", force_refresh=True)

        processed_count = 0
        unconfirmed_count = 0
        seen_count = 0
        scroll_count = 0
        scanned_to_end = False
        last_semantic_observation_scroll = -1
        first_scan_frame = frame if scene_id == 121 else None
        while (max_actions is None or processed_count < max_actions) and scroll_count < max_scrolls:
            self._raise_if_stopped(stop_event)
            if first_scan_frame is None and (yield from self._leave_green_bottle_to_world_if_present(ctx, stop_event, runtime, label="邮件_选择性领取")):
                scanned_to_end = True
                break
            if first_scan_frame is not None:
                frame = first_scan_frame
                first_scan_frame = None
            else:
                frame = runtime.cur_frame(update=True)
            rows = self._runtime_mail_rows_from_frame(runtime, view121, frame)
            visible_row_keys = self._mail_visible_row_keys(rows)
            if (
                visible_row_keys
                and scroll_count != last_semantic_observation_scroll
                and not runtime.observe_scroll_content(
                list_shape,
                visible_row_keys,
                unchanged_confirmations=2,
                )
            ):
                scanned_to_end = True
                self._log("success", "邮件_选择性领取：连续两次滚动未出现新邮件行，确认已到底")
                break
            if visible_row_keys:
                last_semantic_observation_scroll = scroll_count
            aligned_result = self._align_mail_records_from_visible_adjacency(rows, source="mail_selective_claim", dry_run=True)
            if aligned_result.get("updated"):
                self._log(
                    "warning",
                    "邮件_选择性领取：可见相邻断层仅记录为诊断，不自动改为可领；"
                    f"候选 {aligned_result.get('updated')} 封，区间 {aligned_result.get('interval_count')}",
                )
            action_row: _RuntimeMailRow | None = None
            detail_probe_row: _RuntimeMailRow | None = None
            delete_probe_row: _RuntimeMailRow | None = None
            page_runtime_counts: dict[str, int] = {}
            page_rows_summary: list[str] = []
            for mail in rows:
                seen_count += 1
                self._prepare_mail_row_policy(mail.raw, action_enabled=True, action_policies={"claim", "delete"})
                runtime_match = str(mail.raw.get("runtime_match") or "-")
                page_runtime_counts[runtime_match] = page_runtime_counts.get(runtime_match, 0) + 1
                if len(page_rows_summary) < 5:
                    page_rows_summary.append(
                        f"{mail.title[:16]}|{mail.raw.get('time_text') or '-'}|"
                        f"{mail.raw.get('policy') or '-'}|{runtime_match}"
                    )
                if mail.raw.get("policy") in {"claim", "delete"}:
                    action_row = mail
                    break
                if delete_probe_row is None and self._mail_selective_claim_delete_probe_candidate(mail.raw):
                    delete_probe_row = mail
                    continue
                if (
                    detail_probe_row is None
                    and str(mail.raw.get("runtime_match") or "") in {"missing", "title_only"}
                    and str(mail.raw.get("title") or "").strip()
                    and str(mail.raw.get("time_text") or "").strip()
                ):
                    detail_probe_row = mail
            if action_row is not None:
                action_started_at = time.monotonic()
                try:
                    outcome = yield from self._claim_runtime_mail_row(runtime, action_row)
                except TimeoutError as exc:
                    action_elapsed = time.monotonic() - action_started_at
                    self._log(
                        "warning",
                        f"邮件_选择性领取：打开/处理「{action_row.title}」超时 {action_elapsed:.1f}s，跳过该行继续翻页；{exc}",
                    )
                else:
                    action_elapsed = time.monotonic() - action_started_at
                    actual_policy = outcome.policy
                    self._update_runtime_mail_action_for_row(
                        action_row.raw,
                        status=f"{actual_policy}_requested",
                        evidence={
                            "runtime_requested_action": actual_policy,
                            "runtime_action_requested_at": _behavior_tree_runtime._now().strftime("%Y-%m-%d %H:%M:%S"),
                            "runtime_action_source": "mail_selective_claim",
                            "runtime_action_wait_result": outcome.wait_result,
                            "runtime_action_visual_confirmed": outcome.visual_confirmed,
                        },
                    )
                    self._refresh_runtime_mail_snapshot("领取后同步", force_refresh=True)
                    runtime_confirmed = self._runtime_mail_action_confirmed_for_row(action_row.raw, actual_policy)
                    confirmed = outcome.visual_confirmed or runtime_confirmed
                    self._log(
                        "detail",
                        f"邮件_选择性领取：处理「{action_row.title}」耗时 {action_elapsed:.1f}s，"
                        f"动作 {actual_policy}，画面确认={outcome.visual_confirmed}，回包确认={runtime_confirmed}",
                    )
                    if confirmed:
                        processed_count += 1
                        continue
                    unconfirmed_count += 1
                    self._log(
                        "warning",
                        f"邮件_选择性领取：「{action_row.title}」未可靠返回邮件列表且未见服务器成功回包，"
                        "本轮不计为已领取，稍后重试",
                    )

            if detail_probe_row is not None:
                probe_started_at = time.monotonic()
                try:
                    status = yield from self._process_mail_row_by_detail(
                        ctx,
                        stop_event,
                        image121,
                        detail_probe_row.raw,
                        allowed_policies={"delete"},
                    )
                except TimeoutError as exc:
                    probe_elapsed = time.monotonic() - probe_started_at
                    self._log(
                        "warning",
                        f"邮件_选择性领取：详情页探测「{detail_probe_row.title}」超时 {probe_elapsed:.1f}s，跳过该行继续翻页；{exc}",
                    )
                else:
                    probe_elapsed = time.monotonic() - probe_started_at
                    self._log(
                        "detail",
                        f"邮件_选择性领取：详情页探测「{detail_probe_row.title}」耗时 {probe_elapsed:.1f}s，结果 {status}",
                    )
                    if status == "processed":
                        processed_count += 1
                        self._refresh_runtime_mail_snapshot("详情页处理后同步", force_refresh=True)
                        continue

            if delete_probe_row is not None:
                probe_started_at = time.monotonic()
                try:
                    status = yield from self._probe_and_maybe_delete_mail_row(ctx, stop_event, image121, delete_probe_row.raw)
                except TimeoutError as exc:
                    probe_elapsed = time.monotonic() - probe_started_at
                    self._log(
                        "warning",
                        f"邮件_选择性领取：删除探测「{delete_probe_row.title}」超时 {probe_elapsed:.1f}s，跳过该行继续翻页；{exc}",
                    )
                else:
                    probe_elapsed = time.monotonic() - probe_started_at
                    self._log(
                        "detail",
                        f"邮件_选择性领取：删除探测「{delete_probe_row.title}」耗时 {probe_elapsed:.1f}s，结果 {status}",
                    )
                    if status == "processed":
                        processed_count += 1
                        self._refresh_runtime_mail_snapshot("详情页删除后同步", force_refresh=True)
                        continue

            if rows:
                counts_text = ",".join(f"{key}={value}" for key, value in sorted(page_runtime_counts.items()))
                self._log(
                    "detail",
                    f"邮件_选择性领取：当前页无可领取，模型 {counts_text}；"
                    + "；".join(page_rows_summary),
                )

            scroll_started_at = time.monotonic()
            runtime.attrs["load_new"] = yield from runtime.scroll_shape_content(
                list_shape,
                # 邮件页会出现横幅、飘字等动态遮挡；多等一拍可避免滚动动画尚未
                # 稳定就计算图像签名。行级重复检测仍是最终的到底依据。
                settle_seconds=2.0,
            )
            scroll_elapsed = time.monotonic() - scroll_started_at
            self._log("detail", f"邮件_选择性领取：翻页 {scroll_count + 1} 耗时 {scroll_elapsed:.1f}s，load_new={bool(runtime.attrs.get('load_new'))}")
            if not runtime.attrs.get("load_new"):
                scanned_to_end = True
                break
            scroll_count += 1

        action_limit_reached = max_actions is not None and processed_count >= max_actions
        reached_scroll_limit = not scanned_to_end and not action_limit_reached
        if reached_scroll_limit:
            self._log("info", f"邮件_选择性领取：达到 max_scrolls={max_scrolls} 仍未确认到底，继续一键删除已阅")

        delete_result_scene: int | None = None
        if (yield from self._leave_green_bottle_to_world_if_present(ctx, stop_event, runtime, label="邮件_选择性领取")):
            delete_result_scene = 34
            self._log("info", "邮件_选择性领取：当前已离开邮件页，跳过一键删除已阅")
        scene_before_delete, _score_before_delete, _frame_before_delete, _text_before_delete = self._fanxiu_runtime_scene_text(
            ctx,
            runtime,
            [121, 34, 20],
            update=True,
        )
        if delete_result_scene is None and scene_before_delete == 121:
            delete_result_scene = yield from self._delete_read_mail_once(runtime, view121, reason="任务收尾")
        elif delete_result_scene is None:
            delete_result_scene = 34 if scene_before_delete == 34 else None
            self._log("info", f"邮件_选择性领取：当前不在 #121（#{scene_before_delete or 'unknown'}），跳过一键删除已阅")

        final_scene = 34 if delete_result_scene == 34 else 121
        image121 = ctx.get("images", {}).get(121) if isinstance(ctx.get("images"), dict) else None
        back_shape = self._find_shape(image121, "空白-返回") if isinstance(image121, dict) else None
        if final_scene == 34:
            self._log("info", "邮件_选择性领取：一键删除后已回到世界页")
        elif back_shape is not None:
            yield from self._leave_mail_scene_to_world(ctx, stop_event, runtime, 121, label="邮件_选择性领取")
            final_scene = 34
        else:
            self._log("info", "邮件_选择性领取：缺少 #121「空白-返回」标注，结束后保留在邮件页")
        if final_scene == 34:
            final_scene = yield from self._ensure_clean_world_after_task(ctx, stop_event, label="邮件_选择性领取")

        if scanned_to_end and not reached_scroll_limit and not unconfirmed_count:
            marked_count = self._mark_pending_runtime_mail_actions_not_visible(
                reason=f"mail_selective_claim_full_scan_seen={seen_count}; processed={processed_count}; scrolls={scroll_count}",
                allowed_policies={"claim"},
            )
            if marked_count:
                self._log(
                    "success",
                    f"邮件_选择性领取：完整扫到底未见模型待领取项；视觉缺失不再改写动态事实",
                )

        if reached_scroll_limit or unconfirmed_count:
            next_time = (datetime.now() + timedelta(seconds=max(60, int(payload.get("retry_seconds") or 600)))).strftime("%Y-%m-%d %H:%M:%S")
            self._persist_scheduler_task_next_time(
                str(payload.get("__scheduler_task_id") or "mail-selective-claim"),
                next_time,
            )
            message = (
                f"邮件_选择性领取：见到 {seen_count} 封，确认领取 {processed_count} 封，"
                f"未确认 {unconfirmed_count} 封，滚动 {scroll_count} 次，{next_time} 重试"
            )
        else:
            message = f"邮件_选择性领取：完成，见到 {seen_count} 封，领取 {processed_count} 封，滚动 {scroll_count} 次"
            message = self._finish_mail_selective_claim_schedule(payload, message)

        with self._lock:
            self._set_status_locked(
                "running",
                message,
                phase="mail_selective_claim_done",
                current_scene=final_scene,
            )
            self._log_locked("success", self._status["message"])
        return "success"

    @staticmethod
    def _precise_mail_claim_targets(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        items = snapshot.get("items")
        if not isinstance(items, list):
            return []
        return [
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("runtime_status") or "") == "unclaimed"
            and bool(item.get("present_in_runtime"))
            and not bool(item.get("locked"))
            and str(item.get("action_policy") or "") == "claim"
        ]

    @staticmethod
    def _runtime_mail_identity(item: dict[str, Any]) -> str:
        return str(item.get("id") or item.get("mail_id") or "").strip()

    @classmethod
    def _deletable_runtime_mail_garbage(
        cls,
        snapshot: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Return the exact unlocked Runtime mails covered by one-key delete."""

        items = snapshot.get("items")
        if not isinstance(items, list):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            mail_id = cls._runtime_mail_identity(item)
            if (
                mail_id
                and bool(item.get("present_in_runtime"))
                and not bool(item.get("locked"))
                and (
                    str(item.get("runtime_status") or "") == "claimed"
                    or (
                        str(item.get("runtime_status") or "") == "no_attachment"
                        and cls._runtime_mail_read_state(item) is True
                    )
                )
            ):
                result[mail_id] = item
        return result

    @staticmethod
    def _runtime_mail_read_state(item: dict[str, Any]) -> bool | None:
        """Return Runtime's authoritative read flag when projection preserved it."""

        direct = item.get("read")
        if isinstance(direct, bool):
            return direct
        payload = item.get("payload")
        runtime_payload = payload.get("runtime") if isinstance(payload, dict) else None
        nested = runtime_payload.get("read") if isinstance(runtime_payload, dict) else None
        return nested if isinstance(nested, bool) else None

    @classmethod
    def _protected_runtime_mail_ids(cls, snapshot: dict[str, Any]) -> set[str]:
        """Return locked or policy-retained mails that deletion must preserve."""

        return {
            cls._runtime_mail_identity(item)
            for item in snapshot.get("items") or []
            if isinstance(item, dict)
            and bool(item.get("present_in_runtime"))
            and cls._runtime_mail_identity(item)
            and (
                bool(item.get("locked"))
                or (
                    bool(item.get("has_attachment"))
                    and str(item.get("runtime_status") or "") == "unclaimed"
                )
            )
        }

    @staticmethod
    def _validate_precise_mail_policy_snapshot(
        snapshot: dict[str, Any],
        *,
        reason: str,
    ) -> None:
        """Fail closed unless every live attachment has an explicit safe policy."""

        failures: list[str] = []
        for item in snapshot.get("items") or []:
            if not isinstance(item, dict) or not bool(item.get("present_in_runtime")):
                continue
            if str(item.get("runtime_status") or "") != "unclaimed" or not bool(
                item.get("has_attachment")
            ):
                continue
            mail_id = str(item.get("id") or item.get("mail_id") or "?")
            desired = str(item.get("desired_status") or "").strip()
            policy = str(item.get("action_policy") or "").strip()
            locked = bool(item.get("locked"))
            # Reward-name completeness is an authority requirement for a
            # positive claim action, not for an explicit no-op.  A retained
            # or locked mail is already fail-closed: this task will not click
            # it, so a newly introduced item id must not block unrelated,
            # fully classified claim targets forever.
            if locked:
                if desired != "锁定" or policy:
                    failures.append(f"{mail_id}:锁定邮件策略不一致")
                continue
            if desired == "留存" and not policy:
                continue
            payload = item.get("payload")
            rewards = fanxiu_mail_rewards_from_payload(payload)
            if fanxiu_mail_rewards_unresolved(payload) or not rewards:
                failures.append(f"{mail_id}:奖励未解析")
                continue
            if any(not fanxiu_mail_reward_name_known(reward) for reward in rewards):
                failures.append(f"{mail_id}:存在未知道具")
                continue
            if desired == "可领" and policy == "claim":
                continue
            failures.append(f"{mail_id}:desired={desired or '-'} policy={policy or '-'}")
        if failures:
            raise RuntimeError(
                f"邮件_选择性领取：{reason}存在 {len(failures)} 封未完成安全分类的附件邮件，"
                f"拒绝领取并拒绝顺延到次日；details={failures[:8]}"
            )

    @staticmethod
    def _validate_precise_mail_terminal_result(
        result: dict[str, Any],
        *,
        target_count: int,
    ) -> None:
        """Validate the count contract before exposing a successful summary."""

        if str(result.get("result") or "") != "success":
            raise RuntimeError("邮件_选择性领取：批次没有形成 success 业务终态")
        claimed_count = int(result.get("claimed_count") or 0)
        garbage_before = int(result.get("garbage_before") or 0)
        garbage_after = int(result.get("garbage_after") or 0)
        deleted_count = int(result.get("deleted_count") or 0)
        protected_count = int(result.get("protected_count") or 0)
        if claimed_count != int(target_count):
            raise RuntimeError(
                "邮件_选择性领取：批次仍有待领取目标或领取计数不一致，"
                f"target={target_count} claimed={claimed_count}"
            )
        if garbage_after != 0 or deleted_count != garbage_before:
            raise RuntimeError(
                "邮件_选择性领取：可删除垃圾未形成归零闭环，"
                f"before={garbage_before} deleted={deleted_count} after={garbage_after}"
            )
        if min(claimed_count, garbage_before, garbage_after, deleted_count, protected_count) < 0:
            raise RuntimeError("邮件_选择性领取：业务终态计数非法，拒绝报告成功")

    @classmethod
    def _select_precise_mail_claim_targets(
        cls,
        snapshot: dict[str, Any],
        target_mail_ids: set[str] | None,
    ) -> list[dict[str, Any]]:
        targets = cls._precise_mail_claim_targets(snapshot)
        wanted = {str(value) for value in target_mail_ids or set() if str(value)}
        if not wanted:
            return targets
        return [
            item
            for item in targets
            if str(item.get("id") or item.get("mail_id") or "") in wanted
        ]

    @classmethod
    def _runtime_mail_target_still_requires_claim(
        cls,
        snapshot: dict[str, Any],
        mail_id: str,
    ) -> bool:
        """Return whether one exact Runtime identity still needs a claim.

        A claim request is irreversible and the detail sheet may already have
        switched from #122 (claim) to #123 (delete) before the batch's stable
        snapshot is refreshed.  Only a new complete MailMgr read may classify
        that case as already completed; title similarity is insufficient when
        several adjacent mails look identical.
        """

        target_id = str(mail_id or "")
        return any(
            str(item.get("id") or item.get("mail_id") or "") == target_id
            for item in cls._precise_mail_claim_targets(snapshot)
        )

    @staticmethod
    def _plan_precise_mail_window_action(
        mappings: list[dict[str, Any]],
        targets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Choose a visible claim before permitting any forward scroll."""

        if not mappings:
            raise RuntimeError("邮件_选择性领取：当前窗口没有 Runtime 行映射，禁止滚动绕过")
        target_by_id = {
            str(item.get("id") or item.get("mail_id") or ""): item
            for item in targets
        }
        visible = [
            mapping
            for mapping in mappings
            if str(mapping.get("mail_id") or "") in target_by_id
        ]
        if visible:
            mapping = min(visible, key=lambda item: int(item.get("slot_index") or 0))
            return {
                "action": "claim",
                "mapping": mapping,
                "target": target_by_id[str(mapping.get("mail_id") or "")],
            }
        target_indices = [int(item.get("runtime_index") or 0) for item in targets]
        last_visible_index = max(int(item.get("runtime_index") or 0) for item in mappings)
        if min(target_indices) <= last_visible_index:
            raise RuntimeError(
                "邮件_选择性领取：当前窗口包含应领 Runtime 目标却没有生成点击映射，"
                f"window={[(item.get('slot_index'), item.get('runtime_index'), item.get('mail_id')) for item in mappings]} "
                f"targets={target_indices}；禁止滚动绕过"
            )
        return {"action": "scroll"}

    @staticmethod
    def _first_screen_runtime_mapping(
        snapshot: dict[str, Any],
        image121: dict[str, Any],
        fragments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Infer Runtime #0 exclusively from the ordered OCR rows 2/3/4."""

        items = snapshot.get("items")
        if (
            not snapshot.get("complete")
            or not isinstance(items, list)
            or int(snapshot.get("decoded_count") or -1) != len(items)
            or len(items) < 4
        ):
            raise RuntimeError("邮件_选择性领取：首屏领取缺少完整 Runtime #0..#3 序列")
        geometry = mail_window_geometry_from_asset(image121)
        observations = build_mail_visual_observations(
            fragments,
            geometry,
            visible_slots=(1, 2, 3),
        )
        by_slot = {item.slot_index: item for item in observations}
        evidence: list[dict[str, Any]] = []
        for slot in (1, 2, 3):
            observation = by_slot.get(slot)
            runtime_item = items[slot]
            if observation is None:
                raise RuntimeError(f"邮件_选择性领取：首屏第 {slot + 1} 行 OCR 缺失，不能反推第1行")
            expected_title = str(runtime_item.get("title") or "")
            title_score = max(
                (
                    ocr_name_similarity(expected_title, candidate)
                    for candidate in observation.title_candidates
                ),
                default=0.0,
            )
            expected_time = re.sub(
                r"\D+",
                "",
                str(
                    runtime_item.get("create_time_text")
                    or runtime_item.get("create_time")
                    or runtime_item.get("create_time_ms")
                    or ""
                ),
            )
            time_matched = any(
                len(candidate_digits) >= 4
                and len(expected_time) >= 4
                and candidate_digits[-4:] == expected_time[-4:]
                for candidate in observation.time_candidates
                if (candidate_digits := re.sub(r"\D+", "", candidate))
            )
            if title_score < 0.68 or not time_matched:
                raise RuntimeError(
                    f"邮件_选择性领取：首屏第 {slot + 1} 行未按序匹配 Runtime #{slot}；"
                    f"title_score={title_score:.2f} time_matched={time_matched} "
                    f"ocr_titles={list(observation.title_candidates)} "
                    f"ocr_times={list(observation.time_candidates)} "
                    f"runtime_title={expected_title} runtime_time={expected_time}"
                )
            evidence.append(
                {
                    "slot_index": slot,
                    "runtime_index": slot,
                    "title_score": round(title_score, 4),
                    "time_matched": True,
                }
            )
        first = items[0]
        return {
            "slot_index": 0,
            "runtime_index": int(first.get("runtime_index") or 0),
            "mail_id": str(first.get("id") or first.get("mail_id") or ""),
            "title": str(first.get("title") or ""),
            "create_time_text": str(first.get("create_time_text") or ""),
            "evidence": evidence,
        }

    def _execute_first_screen_runtime_claim(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        runtime: BehaviorTreeRuntime,
        image121: dict[str, Any],
        view121: View,
        list_shape: Shape,
        geometry: Any,
        snapshot: dict[str, Any],
        target_mail_ids: set[str] | None = None,
    ):
        """Claim inferred Runtime #0 before any list scroll or drag."""

        self._raise_if_stopped(stop_event)
        frame = runtime.cur_frame(update=True)
        fragments = runtime.ocr_fragments_in_shapes(
            image121,
            ("第1封", "邮件清单2"),
            padding=0,
            frame_data_url=frame,
        )
        mapping = self._first_screen_runtime_mapping(snapshot, image121, fragments)
        click_x, click_y = self._precise_mail_click_point(
            image121,
            list_shape,
            geometry,
            slot_index=0,
        )
        title = str(mapping.get("title") or "")
        row_shape = self._mail_row_title_shape(
            view121,
            {
                "title": title,
                "time_text": str(mapping.get("create_time_text") or ""),
                "x": click_x,
                "y": click_y,
            },
        )
        if row_shape is None:
            raise RuntimeError("邮件_选择性领取：无法构造首屏第1行正式点击区域")
        self._log(
            "action",
            "邮件_选择性领取：第2/3/4行已按序匹配 Runtime #1/#2/#3；"
            f"反推第1行为 Runtime #0「{title}」，在任何滚动前立即打开",
        )
        outcome = yield from self._claim_runtime_mail_row(
            runtime,
            _RuntimeMailRow(
                {
                    "title": title,
                    "time_text": str(mapping.get("create_time_text") or ""),
                    "mail_key": str(mapping.get("mail_id") or ""),
                },
                row_shape,
            ),
            delete_after_reward=False,
            require_claim=True,
        )
        if not outcome.visual_confirmed:
            raise RuntimeError(
                f"邮件_选择性领取：首屏 Runtime #0「{title}」未完成打开→领取→返回 #121 闭环；"
                f"wait_result={outcome.wait_result}"
            )
        self._log(
            "success",
            f"邮件_选择性领取：首屏 Runtime #0「{title}」已打开详情、点击领取并返回 #121；"
            "本轮最小验收到此结束，未调用 scroll/drag",
        )
        return "success"

    @staticmethod
    def _ordered_runtime_window_mapping(
        snapshot: dict[str, Any],
        image121: dict[str, Any],
        fragments: list[dict[str, Any]],
        *,
        previous_offset: int,
        known_top: bool,
    ) -> dict[str, Any]:
        """Map one freshly OCRed GUI window to the ordered Runtime sequence."""

        items = list(snapshot.get("items") or [])
        geometry = mail_window_geometry_from_asset(image121)
        # The footer overlays the fifth lattice row on the real #121 screen.
        # Only rows 1..4 (slots 0..3) are fully actionable; a partially visible
        # fifth title must trigger a downward scroll, never a click.
        visible_slots = [slot for slot in geometry.visible_slot_indices() if int(slot) <= 3]
        observations = []
        if known_top:
            MailTaskMixin._first_screen_runtime_mapping(snapshot, image121, fragments)
            offset = 0
            anchor_count = 3
        else:
            observations = build_mail_visual_observations(
                fragments,
                geometry,
                visible_slots=visible_slots,
            )
            candidates: list[tuple[int, int, list[dict[str, Any]]]] = []
            # Returning from a claimed detail can restore the list at the
            # previous pre-scroll offset, or one row above it after the list
            # settles.  A strictly increasing lower bound then rejects a fully
            # aligned window and aborts the batch.  Accept that one-row rebound;
            # the caller only scrolls again when every remaining target is
            # beyond this verified visible window.
            for offset_candidate in range(max(0, int(previous_offset) - 1), len(items)):
                evidence: list[dict[str, Any]] = []
                for observation in observations:
                    runtime_index = offset_candidate + int(observation.slot_index)
                    if not 0 <= runtime_index < len(items):
                        continue
                    runtime_item = items[runtime_index]
                    expected_title = str(runtime_item.get("title") or "")
                    title_score = max(
                        (
                            ocr_name_similarity(expected_title, candidate)
                            for candidate in observation.title_candidates
                        ),
                        default=0.0,
                    )
                    expected_time = re.sub(
                        r"\D+",
                        "",
                        str(
                            runtime_item.get("create_time_text")
                            or runtime_item.get("create_time")
                            or runtime_item.get("create_time_ms")
                            or ""
                        ),
                    )
                    time_matched = any(
                        len(candidate_digits) >= 4
                        and len(expected_time) >= 4
                        and candidate_digits[-4:] == expected_time[-4:]
                        for candidate in observation.time_candidates
                        if (candidate_digits := re.sub(r"\D+", "", candidate))
                    )
                    runtime_title_unknown = expected_title.startswith("未知邮件类型")
                    if time_matched and (title_score >= 0.68 or runtime_title_unknown):
                        evidence.append(
                            {
                                "slot_index": int(observation.slot_index),
                                "runtime_index": runtime_index,
                                "title_score": round(title_score, 4),
                                "runtime_title_unknown": runtime_title_unknown,
                            }
                        )
                if (
                    len(evidence) >= 2
                    and any(float(item["title_score"]) >= 0.68 for item in evidence)
                ):
                    candidates.append((len(evidence), offset_candidate, evidence))
            if not candidates:
                raise RuntimeError(
                    "邮件_选择性领取：滚动后当前窗口没有至少两行按序匹配 Runtime，拒绝继续滚动"
                )
            strongest = max(item[0] for item in candidates)
            strongest_candidates = [item for item in candidates if item[0] == strongest]
            _count, offset, _evidence = min(strongest_candidates, key=lambda item: item[1])
            anchor_count = strongest
        mappings = []
        for slot in visible_slots:
            runtime_index = offset + int(slot)
            if not 0 <= runtime_index < len(items):
                continue
            item = items[runtime_index]
            observation = next(
                (
                    candidate
                    for candidate in observations
                    if int(candidate.slot_index) == int(slot)
                ),
                None,
            )
            observed_title = ""
            if observation is not None and observation.title_candidates:
                runtime_title = str(item.get("title") or "")
                if runtime_title.startswith("未知邮件类型"):
                    observed_title = max(
                        observation.title_candidates,
                        key=lambda candidate: len(re.sub(r"\s+", "", candidate)),
                    )
                else:
                    observed_title = max(
                        observation.title_candidates,
                        key=lambda candidate: ocr_name_similarity(runtime_title, candidate),
                    )
            mappings.append(
                {
                    "slot_index": int(slot),
                    "runtime_index": int(item.get("runtime_index") or runtime_index),
                    "mail_id": str(item.get("id") or item.get("mail_id") or ""),
                    "title": str(item.get("title") or ""),
                    "observed_title": observed_title,
                    "create_time_text": str(item.get("create_time_text") or ""),
                }
            )
        return {
            "runtime_offset": offset,
            "anchor_count": anchor_count,
            "mappings": mappings,
        }

    def _execute_ordered_runtime_claim_batch(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        runtime: BehaviorTreeRuntime,
        image121: dict[str, Any],
        view121: View,
        list_shape: Shape,
        geometry: Any,
        snapshot: dict[str, Any],
        target_mail_ids: set[str] | None = None,
    ):
        """Claim the one Runtime target batch, then delete once and verify once."""

        targets = self._select_precise_mail_claim_targets(
            snapshot,
            target_mail_ids,
        )
        target_by_id = {
            str(item.get("id") or item.get("mail_id") or ""): item
            for item in targets
        }
        claimed_ids: set[str] = set()
        open_attempts: dict[str, int] = {}
        previous_offset = -1
        known_top = True
        scroll_calls = 0
        wrong_detail_recoveries: dict[str, int] = {}
        while len(claimed_ids) < len(target_by_id):
            self._raise_if_stopped(stop_event)
            window: dict[str, Any] | None = None
            last_mapping_error: RuntimeError | None = None
            for ocr_attempt in range(4):
                frame = runtime.cur_frame(update=True)
                fragments = runtime.ocr_fragments_in_shapes(
                    image121,
                    ("第1封", "邮件清单2"),
                    padding=0,
                    frame_data_url=frame,
                )
                try:
                    window = self._ordered_runtime_window_mapping(
                        snapshot,
                        image121,
                        fragments,
                        previous_offset=previous_offset,
                        known_top=known_top,
                    )
                    break
                except RuntimeError as exc:
                    last_mapping_error = exc
                    if ocr_attempt >= 3:
                        raise
                    self._log(
                        "info",
                        f"邮件_选择性领取：{'首屏第2/3/4行' if known_top else '滚动稳定窗口'}"
                        f"第 {ocr_attempt + 1}/4 帧暂未齐全；原地等待重取，禁止 scroll/drag",
                    )
                    yield from runtime.wait_action_settle(0.6 if known_top else 1.0)
                    runtime.clear_frame()
            if window is None:
                raise last_mapping_error or RuntimeError("邮件_选择性领取：首屏映射失败")
            mappings = list(window["mappings"])
            visible_targets = [
                mapping
                for mapping in mappings
                if str(mapping.get("mail_id") or "") in target_by_id
                and str(mapping.get("mail_id") or "") not in claimed_ids
            ]
            if visible_targets:
                mapping = min(visible_targets, key=lambda item: int(item.get("slot_index") or 0))
                slot_index = int(mapping.get("slot_index") or 0)
                click_x, click_y = self._precise_mail_click_point(
                    image121,
                    list_shape,
                    geometry,
                    slot_index=slot_index,
                )
                title = str(mapping.get("title") or "")
                observed_title = str(mapping.get("observed_title") or "")
                repeated_visible_signature = sum(
                    1
                    for visible_mapping in mappings
                    if str(visible_mapping.get("title") or "") == title
                    and str(visible_mapping.get("create_time_text") or "")
                    == str(mapping.get("create_time_text") or "")
                ) > 1
                if not known_top:
                    geometry_title = (
                        observed_title
                        if title.startswith("未知邮件类型") and observed_title
                        else title
                    )
                    observed_point = self._precise_mail_observed_title_point(
                        fragments,
                        title=geometry_title,
                        fallback_y=click_y,
                        geometry=geometry,
                        # A drag may stop well between two asset lattice rows.
                        # Runtime already selected the row; OCR only refines
                        # its position on this stable new frame.
                        # A wide correction is useful for a uniquely named row
                        # after an inertial drag.  It is unsafe for adjacent
                        # identical mails: the neighbouring title can be closer
                        # than the intended row and opens an already-claimed
                        # #123 detail.  Keep repeated groups inside half a row.
                        max_row_distance_ratio=(
                            0.48 if repeated_visible_signature else 0.8
                        ),
                    )
                    if observed_point is not None:
                        click_x, click_y = observed_point
                        self._log(
                            "detail",
                            f"邮件_选择性领取：滚动稳定帧重新定位 Runtime "
                            f"#{mapping.get('runtime_index')}「{title}」"
                            f"（画面标题「{geometry_title}」）到 "
                            f"({click_x:.0f},{click_y:.0f})",
                        )
                row_shape = self._mail_row_title_shape(
                    view121,
                    {
                        "title": title,
                        "time_text": str(mapping.get("create_time_text") or ""),
                        "x": click_x,
                        "y": click_y,
                    },
                )
                if row_shape is None:
                    raise RuntimeError(f"邮件_选择性领取：无法构造 Runtime #{mapping.get('runtime_index')} 点击区域")
                self._log(
                    "action",
                    f"邮件_选择性领取：当前窗口 Runtime #{mapping.get('runtime_index')}「{title}」可见；"
                    "立即打开领取，禁止先滚动",
                )
                outcome = yield from self._claim_runtime_mail_row(
                    runtime,
                    _RuntimeMailRow(
                        {
                            "title": title,
                            "time_text": str(mapping.get("create_time_text") or ""),
                            "mail_key": str(mapping.get("mail_id") or ""),
                        },
                        row_shape,
                    ),
                    delete_after_reward=False,
                    require_claim=True,
                )
                if outcome.wait_result == "claim_action_absent":
                    mail_id = str(mapping.get("mail_id") or "")
                    refreshed = self._read_complete_precise_mail_snapshot(
                        stop_event,
                        reason=f"#{mapping.get('runtime_index')} 详情无领取动作后只读复验",
                    )
                    if not self._runtime_mail_target_still_requires_claim(
                        refreshed,
                        mail_id,
                    ):
                        snapshot = refreshed
                        claimed_ids.add(mail_id)
                        self._log(
                            "success",
                            f"邮件_选择性领取：Runtime #{mapping.get('runtime_index')}「{title}」"
                            "详情已无领取动作，刷新 MailMgr 确认该精确邮件已结算；"
                            "按幂等完成继续，不重复点击",
                        )
                        continue
                    wrong_detail_recoveries[mail_id] = (
                        wrong_detail_recoveries.get(mail_id, 0) + 1
                    )
                    if wrong_detail_recoveries[mail_id] >= 2:
                        raise RuntimeError(
                            f"邮件_选择性领取：Runtime #{mapping.get('runtime_index')}「{title}」"
                            "连续打开无领取动作的相邻详情，但只读 MailMgr 仍判该精确邮件可领"
                        )
                    self._log(
                        "warning",
                        f"邮件_选择性领取：Runtime #{mapping.get('runtime_index')}「{title}」"
                        "详情无领取动作，且只读 MailMgr 仍判目标可领；判定本次落到相邻已领行，"
                        "返回世界并从邮件顶部重建一次窗口，不执行删除或领取",
                    )
                    yield from self._leave_mail_scene_to_world(
                        ctx,
                        stop_event,
                        runtime,
                        121,
                        label="邮件_选择性领取",
                    )
                    yield from self._open_mail_selective_claim_entry(runtime)
                    snapshot = refreshed
                    previous_offset = -1
                    known_top = True
                    runtime.clear_frame()
                    continue
                if not outcome.visual_confirmed:
                    mail_id = str(mapping.get("mail_id") or "")
                    open_attempts[mail_id] = open_attempts.get(mail_id, 0) + 1
                    if (
                        outcome.wait_result == "detail_not_found"
                        and open_attempts[mail_id] < 2
                    ):
                        self._log(
                            "warning",
                            f"邮件_选择性领取：Runtime #{mapping.get('runtime_index')}「{title}」"
                            "首次点击未打开详情；丢弃旧坐标，原地重新 OCR/定位后重试一次",
                        )
                        yield from runtime.wait_action_settle(1.5)
                        runtime.clear_frame()
                        continue
                    raise RuntimeError(
                        f"邮件_选择性领取：Runtime #{mapping.get('runtime_index')}「{title}」"
                        f"未完成领取返回闭环；wait_result={outcome.wait_result}"
                    )
                claimed_ids.add(str(mapping.get("mail_id") or ""))
                self._log(
                    "success",
                    f"邮件_选择性领取：Runtime #{mapping.get('runtime_index')}「{title}」"
                    f"领取完成并返回 #121；批次进度 {len(claimed_ids)}/{len(target_by_id)}",
                )
                continue

            remaining_indices = [
                int(item.get("runtime_index") or 0)
                for mail_id, item in target_by_id.items()
                if mail_id not in claimed_ids
            ]
            last_visible_index = max(int(item.get("runtime_index") or 0) for item in mappings)
            if min(remaining_indices) <= last_visible_index:
                raise RuntimeError(
                    "邮件_选择性领取：当前窗口存在应领目标却没有生成点击映射，禁止滚动；"
                    f"remaining={remaining_indices} window={[(m.get('slot_index'), m.get('runtime_index')) for m in mappings]}"
                )
            loaded = yield from runtime.scroll_shape_content(list_shape, settle_seconds=2.0)
            scroll_calls += 1
            if not loaded:
                raise RuntimeError(
                    f"邮件_选择性领取：单向到底仍有 Runtime 目标 {remaining_indices}，拒绝回顶"
                )
            # Content-change detection can fire while the inertial drag is
            # still settling.  Never OCR/click that transition frame.
            yield from runtime.wait_action_settle(3.0)
            runtime.clear_frame()
            previous_offset = int(window["runtime_offset"])
            known_top = False

        cleanup = yield from self._delete_read_mail_until_clean(
            runtime,
            view121,
            stop_event,
            reason="批量领取完成后统一删除",
        )
        yield from self._leave_mail_scene_to_world(
            ctx,
            stop_event,
            runtime,
            121,
            label="邮件_选择性领取",
        )
        final_snapshot = cleanup["snapshot"]
        self._validate_precise_mail_policy_snapshot(final_snapshot, reason="任务完成复查")
        remaining = self._select_precise_mail_claim_targets(
            final_snapshot,
            target_mail_ids,
        )
        if remaining:
            raise RuntimeError(
                "邮件_选择性领取：一键删除后 Runtime 终检仍有必领目标："
                f"{[int(item.get('runtime_index') or 0) for item in remaining]}"
            )
        self._log(
            "success",
            f"邮件_选择性领取完整闭环：领取 {len(claimed_ids)} 封，"
            f"删除 {cleanup['deleted_count']}/{cleanup['before_count']} 封，"
            "Runtime 终检无必领目标且可删除垃圾为 0",
        )
        return {
            "result": "success",
            "claimed_count": len(claimed_ids),
            "garbage_before": int(cleanup["before_count"]),
            "garbage_after": int(cleanup["after_count"]),
            "deleted_count": int(cleanup["deleted_count"]),
            "delete_batches": int(cleanup["batch_count"]),
            "protected_count": int(cleanup["protected_count"]),
        }

    @staticmethod
    def _safe_ambiguous_claim_mapping(
        snapshot: dict[str, Any],
        targets: list[dict[str, Any]],
        diagnosis: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return one safe row when every competitive offset means the same claim.

        MailMgr can contain several adjacent mails with identical title and time.
        Their internal IDs are intentionally invisible in the UI, so OCR cannot
        choose one unique offset.  Clicking is nevertheless safe when *all*
        competitive offsets map a visible slot to an unlocked ``claim`` target
        with the same visible title/time.  This does not weaken ordinary
        alignment: mixed claim/retain/locked candidates still fail closed.
        """

        if str(diagnosis.get("status") or "") != "ambiguous":
            return None
        alignment = diagnosis.get("alignment") or {}
        scored_offsets = sorted({int(value) for value in alignment.get("competitive_offsets") or []})
        if len(scored_offsets) < 2:
            return None
        hypotheses = list(alignment.get("hypotheses") or [])
        competitive = [
            item
            for item in hypotheses
            if int(item.get("runtime_offset") or 0) in scored_offsets
        ]
        if len(competitive) != len(scored_offsets):
            return None
        # The sequence scorer intentionally keeps every near-score hypothesis
        # for diagnostics.  A hypothesis with no exact OCR anchors is not an
        # equal business competitor to one supported by several exact anchors,
        # even when repeated mail titles make their aggregate scores close.
        # First retain the strongest evidence cohort; only then ask whether the
        # remaining GUI ambiguity crosses a Runtime action boundary.
        strongest_exact_count = max(
            int(item.get("exact_anchor_count") or 0) for item in competitive
        )
        if strongest_exact_count > 0:
            competitive = [
                item
                for item in competitive
                if int(item.get("exact_anchor_count") or 0) == strongest_exact_count
            ]
        else:
            strongest_anchor_count = max(
                int(item.get("anchor_count") or 0) for item in competitive
            )
            competitive = [
                item
                for item in competitive
                if int(item.get("anchor_count") or 0) == strongest_anchor_count
            ]
        offsets = sorted({int(item.get("runtime_offset") or 0) for item in competitive})
        if len(offsets) < 2:
            return None
        items = list(snapshot.get("items") or [])
        observations_by_slot = {
            int(item.get("slot_index") or 0): item
            for item in diagnosis.get("observations") or []
            if isinstance(item, dict)
        }
        target_by_id = {
            str(item.get("id") or item.get("mail_id") or ""): item
            for item in targets
        }
        for slot in (int(value) for value in diagnosis.get("visible_slots") or []):
            # Action equivalence removes the need to distinguish internal IDs,
            # but it does not remove the need to identify the visible repeated
            # group.  A repeated title without the current row's OCR timestamp
            # can still be a neighbouring locked group, so it is navigation
            # evidence only and may never authorize a click.
            observation = observations_by_slot.get(slot) or {}
            if not observation.get("time_candidates"):
                continue
            if any(
                slot not in {int(value) for value in item.get("matched_slots") or []}
                for item in competitive
            ):
                continue
            candidates: list[dict[str, Any]] = []
            for offset in offsets:
                runtime_index = offset + slot
                if not 0 <= runtime_index < len(items):
                    candidates = []
                    break
                item = items[runtime_index]
                mail_id = str(item.get("id") or item.get("mail_id") or "")
                target = target_by_id.get(mail_id)
                if target is None:
                    candidates = []
                    break
                candidates.append(target)
            if not candidates:
                continue
            visible_keys = {
                (
                    str(item.get("title") or "").strip(),
                    str(item.get("create_time_text") or "").strip(),
                    str(item.get("action_policy") or "").strip(),
                )
                for item in candidates
            }
            if len(visible_keys) != 1 or next(iter(visible_keys))[2] != "claim":
                continue
            representative = candidates[0]
            return {
                "slot_index": slot,
                "runtime_index": int(representative.get("runtime_index") or 0),
                "mail_id": str(representative.get("id") or representative.get("mail_id") or ""),
                "title": str(representative.get("title") or ""),
                "create_time": representative.get("create_time_ms"),
                "observed": True,
                "inferred": False,
                "equivalent_candidate_ids": [
                    str(item.get("id") or item.get("mail_id") or "") for item in candidates
                ],
            }
        return None

    def _read_complete_precise_mail_snapshot(
        self,
        stop_event: threading.Event,
        *,
        reason: str,
    ) -> dict[str, Any]:
        self._raise_if_stopped(stop_event)
        if not self._refresh_runtime_mail_snapshot(reason, force_refresh=True):
            raise RuntimeError(f"邮件_选择性领取：{reason} Runtime 读取失败")
        current = current_runtime_mail_sequence_snapshot(_db_engine)
        items = current.get("items")
        if (
            not current.get("complete")
            or not isinstance(items, list)
            or int(current.get("decoded_count") or -1) != len(items)
        ):
            raise RuntimeError(f"邮件_选择性领取：{reason}未得到完整 Runtime 邮件序列")
        return current

    @staticmethod
    def _precise_mail_click_point(
        image121: dict[str, Any],
        list_shape: Shape,
        geometry: Any,
        *,
        slot_index: int,
    ) -> tuple[float, float]:
        width = float(image121.get("width") or geometry.frame_width)
        raw = list_shape.raw
        click_x = (float(raw.get("x") or 0) + float(raw.get("w") or 0) * 0.43) * width
        click_y = float(geometry.row_center_y(slot_index))
        if int(slot_index) > 0 and geometry.list_bottom > geometry.list_top:
            row_top = click_y - float(geometry.row_half_height)
            row_bottom = click_y + float(geometry.row_half_height)
            edge_tolerance = min(12.0, float(geometry.row_half_height) * 0.2)
            if (
                row_top < geometry.list_top - edge_tolerance
                or row_bottom > geometry.list_bottom + edge_tolerance
            ):
                raise RuntimeError(
                    "邮件_选择性领取：目标行只有中心落入清单，但整行未进入可点击窗口，必须继续滚动"
                )
        return click_x, click_y

    @staticmethod
    def _precise_mail_observed_title_point(
        fragments: list[dict[str, Any]],
        *,
        title: str,
        fallback_y: float,
        geometry: Any,
        max_row_distance_ratio: float = 0.48,
    ) -> tuple[float, float] | None:
        """Use the stable frame's title box when OCR observed the target row.

        MailMgr decides *which* mail is actionable and the global alignment
        decides *which visible row* represents it.  A list may nevertheless
        stop between the asset's integer row lattice after a nudge.  In that
        case the observed OCR title is a more accurate click coordinate than
        the nominal slot centre.  OCR never chooses the business target here;
        it only refines the pixel position of an already aligned MailMgr item.
        """

        expected = re.sub(r"\s+", "", _sanitize_ocr_text(title))
        if not expected:
            return None
        expected_title_y = float(fallback_y) + float(geometry.title_center_offset)
        # OCR is only allowed to refine the pixel position of the row already
        # selected by the global sequence alignment.  Adjacent mails can have
        # exactly the same title (and even the same minute); when OCR misses the
        # intended row, accepting a same-title fragment from a neighbouring row
        # would silently turn slot N into slot N-1/N+1.
        row_pitch = float(getattr(geometry, "row_pitch", 0.0) or 0.0)
        max_row_distance = (
            row_pitch * max(0.0, float(max_row_distance_ratio))
            if row_pitch > 0
            else None
        )
        candidates: list[tuple[float, float, float]] = []
        for fragment in fragments:
            text = re.sub(r"\s+", "", _sanitize_ocr_text(fragment.get("text")))
            if not text:
                continue
            similarity = difflib.SequenceMatcher(None, text, expected).ratio()
            if text != expected and similarity < 0.92:
                continue
            width = float(fragment.get("w") or 0)
            height = float(fragment.get("h") or 0)
            if width <= 0 or height <= 0:
                continue
            cx = float(fragment.get("x") or 0) + width / 2
            cy = float(fragment.get("y") or 0) + height / 2
            if (
                max_row_distance is not None
                and abs(cy - expected_title_y) > max_row_distance
            ):
                continue
            candidates.append((similarity, cx, cy))
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (item[0], -abs(item[2] - expected_title_y)),
            reverse=True,
        )
        _similarity, click_x, click_y = candidates[0]
        return click_x, click_y

    def _execute_precise_mail_claim_loop(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        runtime: BehaviorTreeRuntime,
        image121: dict[str, Any],
        view121: View,
        list_shape: Shape,
        geometry: Any,
        initial_snapshot: dict[str, Any] | None = None,
    ):
        """Use the complete MailMgr sequence as truth and OCR only for window alignment."""

        # A precise run can legitimately traverse the same part of the list more
        # than once: newly arrived mails force us back to the earliest target and
        # an ambiguous OCR window forces a clean reset.  The old budget of 150
        # counted those safety moves as if they were forward progress and could
        # abort a healthy run halfway through a large mailbox.  Keep a generous
        # runaway guard here; the task-wide runtime limit remains the primary
        # bound for normal standard-job execution.
        max_scrolls = max(1, int(payload.get("max_scrolls") or 600))
        raw_max_actions = int(payload.get("max_actions") or 0)
        max_actions = raw_max_actions if raw_max_actions > 0 else None
        processed_count = 0
        scroll_count = 0
        target_open_attempts: dict[str, int] = {}
        visually_confirmed_claim_ids: set[str] = set()
        workflow_error: Exception | None = None
        at_known_top = True
        alignment_reference_offset = 0
        expected_alignment_offset: int | None = 0
        stable_snapshot = initial_snapshot or current_runtime_mail_sequence_snapshot(_db_engine)

        try:
            while True:
                self._raise_if_stopped(stop_event)
                snapshot = stable_snapshot
                target_mail_ids = {str(value) for value in payload.get("target_mail_ids") or [] if str(value)}
                targets = [
                    item
                    for item in self._precise_mail_claim_targets(snapshot)
                    if str(item.get("id") or item.get("mail_id") or "")
                    not in visually_confirmed_claim_ids
                    and (not target_mail_ids or str(item.get("id") or item.get("mail_id") or "") in target_mail_ids)
                ]
                if not targets:
                    self._log("success", "邮件_选择性领取：动态模型已无可领取目标，提前结束遍历")
                    break
                if max_actions is not None and processed_count >= max_actions:
                    raise RuntimeError(
                        f"邮件_选择性领取：达到 max_actions={max_actions}，但仍有 {len(targets)} 封必须领取"
                    )

                diagnosis: dict[str, Any] | None = None
                frame = ""
                # OCR is refreshed for the current frame.  If that frame cannot
                # map its visible Runtime fragment, scrolling would risk
                # skipping an actionable row, so fail in place instead of
                # gathering a shifted frame.
                local_nudges: tuple[str, ...] = ()
                for alignment_attempt in range(len(local_nudges) + 1):
                    frame = runtime.cur_frame(update=True)
                    fragments = runtime.ocr_fragments_in_shapes(
                        image121,
                        ("第1封", "邮件清单2"),
                        padding=0,
                        frame_data_url=frame,
                    )
                    diagnosis = diagnose_mail_window(
                        snapshot,
                        image121,
                        fragments,
                        expected_runtime_offset=expected_alignment_offset,
                    )
                    safe_ambiguous_mapping = self._safe_ambiguous_claim_mapping(
                        snapshot,
                        targets,
                        diagnosis,
                    )
                    if (
                        str(diagnosis.get("status") or "") == "ambiguous"
                        and safe_ambiguous_mapping is None
                    ):
                        alignment_debug = diagnosis.get("alignment") or {}
                        self._log(
                            "detail",
                            "邮件_选择性领取：歧义动作边界诊断 "
                            f"targets={[int(item.get('runtime_index') or 0) for item in targets]} "
                            f"competitive_offsets={alignment_debug.get('competitive_offsets') or []} "
                            "hypotheses="
                            f"{[(item.get('runtime_offset'), item.get('anchor_count'), item.get('exact_anchor_count'), item.get('matched_slots')) for item in (alignment_debug.get('hypotheses') or [])[:12]]}",
                        )
                    if safe_ambiguous_mapping is not None:
                        diagnosis = {
                            **diagnosis,
                            "ok": True,
                            "status": "equivalent_claim_ambiguity",
                            "reason": "竞争偏移均映射到同标题、同时间、同领取策略邮件",
                            "alignment": {
                                **(diagnosis.get("alignment") or {}),
                                "runtime_offset": int(safe_ambiguous_mapping["runtime_index"])
                                - int(safe_ambiguous_mapping["slot_index"]),
                                "mappings": [safe_ambiguous_mapping],
                            },
                        }
                        self._log(
                            "info",
                            "邮件_选择性领取：视觉偏移不唯一，但当前行全部竞争候选均为"
                            f"同一领取等价类，共 {len(safe_ambiguous_mapping['equivalent_candidate_ids'])} 封；"
                            "允许点击并继续用详情页与 MailMgr 复核",
                        )
                    if (
                        diagnosis.get("ok")
                        and at_known_top
                        and int((diagnosis.get("alignment") or {}).get("runtime_offset") or 0) != 0
                    ):
                        diagnosis = {
                            **diagnosis,
                            "ok": False,
                            "status": "top_offset_conflict",
                            "reason": "刚从 #34 重进，首屏只能对应动态序列 offset=0",
                        }
                    if diagnosis.get("ok"):
                        candidate_offset = int(
                            (diagnosis.get("alignment") or {}).get("runtime_offset") or 0
                        )
                        if abs(candidate_offset - alignment_reference_offset) > 6:
                            diagnosis = {
                                **diagnosis,
                                "ok": False,
                                "status": "offset_discontinuity",
                                "reason": (
                                    f"窗口偏移从 {alignment_reference_offset} 跳到 {candidate_offset}，"
                                    "超过单次滚动连续性边界"
                                ),
                            }
                    if not diagnosis.get("ok") and expected_alignment_offset is not None:
                        visible_slots = [int(slot) for slot in geometry.visible_slot_indices()]
                        earliest_target_index = min(
                            int(item.get("runtime_index") or 0) for item in targets
                        )
                        predicted_offset = int(expected_alignment_offset)
                        # Opening #121 from #34 is a structural top anchor.  The
                        # first title is frequently hidden by banners, so OCR can
                        # have too little evidence even though the list origin is
                        # known exactly.  Keep offset=0 and let the detail page
                        # verify the dynamic MailMgr title/action; nudging down here
                        # loses the top anchor and can strand target #0 forever.
                        if at_known_top and predicted_offset == 0 and visible_slots:
                            snapshot_items = list(snapshot.get("items") or [])
                            predicted_mappings = []
                            for slot in visible_slots:
                                runtime_index = slot
                                if not 0 <= runtime_index < len(snapshot_items):
                                    continue
                                item = snapshot_items[runtime_index]
                                predicted_mappings.append(
                                    {
                                        "slot_index": slot,
                                        "runtime_index": int(item.get("runtime_index") or runtime_index),
                                        "mail_id": str(item.get("id") or item.get("mail_id") or ""),
                                        "title": str(item.get("title") or ""),
                                        "create_time": item.get("create_time_ms"),
                                        "observed": False,
                                        "inferred": True,
                                    }
                                )
                            diagnosis = {
                                **diagnosis,
                                "ok": True,
                                "status": "known_top_runtime_mapping",
                                "reason": (
                                    "#121 从 #34 重进后列表顶部已知；"
                                    "使用 MailMgr 顺序映射，并在详情页复核标题与动作"
                                ),
                                "alignment": {
                                    "runtime_offset": 0,
                                    "anchor_count": 0,
                                    "exact_anchor_count": 0,
                                    "score_margin": 0.0,
                                    "mappings": predicted_mappings,
                                },
                            }
                            self._log(
                                "info",
                                "邮件_选择性领取：首屏 OCR 证据不足，但 #121 顶部已知；"
                                "保持 offset=0，交由详情页联合验证",
                            )
                        if (
                            not diagnosis.get("ok")
                            and
                            visible_slots
                            and earliest_target_index > predicted_offset + max(visible_slots)
                        ):
                            snapshot_items = list(snapshot.get("items") or [])
                            predicted_mappings = []
                            for slot in visible_slots:
                                runtime_index = predicted_offset + slot
                                if not 0 <= runtime_index < len(snapshot_items):
                                    continue
                                item = snapshot_items[runtime_index]
                                predicted_mappings.append(
                                    {
                                        "slot_index": slot,
                                        "runtime_index": int(item.get("runtime_index") or runtime_index),
                                        "mail_id": str(item.get("id") or item.get("mail_id") or ""),
                                        "title": str(item.get("title") or ""),
                                        "create_time": item.get("create_time_ms"),
                                        "observed": False,
                                        "inferred": True,
                                    }
                                )
                            diagnosis = {
                                **diagnosis,
                                "ok": True,
                                "status": "predicted_offset_navigation",
                                "reason": "固定滚动给出连续预测偏移；目标仍在窗口之后，仅用于安全向下导航",
                                "alignment": {
                                    "runtime_offset": predicted_offset,
                                    "anchor_count": 0,
                                    "exact_anchor_count": 0,
                                    "score_margin": 0.0,
                                    "mappings": predicted_mappings,
                                },
                            }
                            self._log(
                                "info",
                                f"邮件_选择性领取：预测 offset={predicted_offset}；"
                                f"最早目标 #{earliest_target_index} 尚未进入窗口，跳过点击配准，仅向下导航",
                            )
                    if diagnosis.get("ok"):
                        break
                    self._log(
                        "warning",
                        "邮件_选择性领取：窗口配准第 "
                        f"{alignment_attempt + 1}/{len(local_nudges) + 1} 次未通过：{diagnosis.get('status')} "
                        f"{diagnosis.get('reason')}",
                    )
                    if alignment_attempt < len(local_nudges):
                        direction = local_nudges[alignment_attempt]
                        self._log(
                            "action",
                            f"邮件_选择性领取：小幅向{direction}移动清单，重新布置第2/3/4封可信锚点",
                        )
                        yield from runtime.scroll_shape_content(
                            list_shape,
                            direction=direction,
                            ratio=0.12,
                            settle_seconds=1.5,
                            # Mail banners and floating notices may keep changing
                            # forever.  The fixed post-drag delay is the hard gate;
                            # do not make whole-frame visual stability a prerequisite.
                            stable_sample_count=1,
                        )
                        at_known_top = False
                        expected_alignment_offset = None
                    else:
                        stop_event.wait(0.35)

                if not diagnosis or not diagnosis.get("ok"):
                    raise RuntimeError(
                        "邮件_选择性领取：当前可见 Runtime 连续片段跨越不同业务动作边界；"
                        "已追加一帧向下证据，仍不能安全选择领取行"
                    )

                alignment = diagnosis["alignment"]
                mappings = list(alignment.get("mappings") or [])
                if not mappings:
                    raise RuntimeError("邮件_选择性领取：配准成功但没有生成可见槽位映射")
                runtime_offset = int(alignment.get("runtime_offset") or 0)
                alignment_reference_offset = runtime_offset
                expected_alignment_offset = runtime_offset
                target_indices = [int(item.get("runtime_index") or 0) for item in targets]
                earliest_target_index = min(target_indices)
                if earliest_target_index < runtime_offset:
                    raise RuntimeError(
                        f"邮件_选择性领取：最早目标 #{earliest_target_index} 已落在当前窗口 "
                        f"#{runtime_offset} 上方；稳定批次只允许单向向下，拒绝回滚或回顶"
                    )
                window_action = self._plan_precise_mail_window_action(mappings, targets)

                if window_action["action"] == "claim":
                    mapping = window_action["mapping"]
                    slot_index = int(mapping.get("slot_index") or 0)
                    if slot_index == 0 and (
                        int(alignment.get("anchor_count") or 0) < 3
                        and float(alignment.get("score_margin") or 0) < 2.0
                    ):
                        self._log(
                            "info",
                            "邮件_选择性领取：首行由当前 Runtime 连续片段推导；"
                            "不回顶，进入详情页动作复核",
                        )

                    mail_id = str(mapping.get("mail_id") or "")
                    target = window_action["target"]
                    click_x, click_y = self._precise_mail_click_point(
                        image121,
                        list_shape,
                        geometry,
                        slot_index=slot_index,
                    )
                    title = str(target.get("title") or "")
                    time_text = str(target.get("create_time_text") or "")
                    if bool(mapping.get("observed")):
                        observed_point = self._precise_mail_observed_title_point(
                            fragments,
                            title=title,
                            fallback_y=click_y,
                            geometry=geometry,
                        )
                        if observed_point is not None:
                            click_x, click_y = observed_point
                            self._log(
                                "detail",
                                f"邮件_选择性领取：稳定帧已观察到「{title}」，"
                                f"使用 OCR 标题中心精确落点 ({click_x:.0f},{click_y:.0f})",
                            )
                    row_shape = self._mail_row_title_shape(
                        view121,
                        {"title": title, "time_text": time_text, "x": click_x, "y": click_y},
                    )
                    if row_shape is None:
                        raise RuntimeError(f"邮件_选择性领取：无法构造 {mail_id} 的精确点击区域")
                    self._log(
                        "action",
                        f"邮件_选择性领取：模型 #{mapping.get('runtime_index')} → 槽位 {slot_index}，"
                        f"点击 {time_text}「{title}」[{mail_id}]",
                    )
                    outcome = yield from self._claim_runtime_mail_row(
                        runtime,
                        _RuntimeMailRow(
                            {
                                "title": title,
                                "time_text": time_text,
                                "mail_key": str(target.get("mail_key") or ""),
                            },
                            row_shape,
                        ),
                        delete_after_reward=False,
                        require_claim=True,
                    )
                    if outcome.wait_result in {"detail_not_found", "timeout", "detail_still_open"}:
                        target_open_attempts[mail_id] = target_open_attempts.get(mail_id, 0) + 1
                        if target_open_attempts[mail_id] < 2:
                            self._log(
                                "warning",
                                f"邮件_选择性领取：「{title}」首次点击后仍在列表；"
                                "等待稳定并重新读取当前窗口，不终止整轮遍历",
                            )
                            yield from runtime.wait_action_settle(1.5)
                            expected_alignment_offset = runtime_offset
                            continue
                        raise RuntimeError(
                            f"邮件_选择性领取：{time_text}「{title}」点击后未完成可靠的领取闭环"
                        )
                    if outcome.visual_confirmed:
                        visually_confirmed_claim_ids.add(mail_id)
                        processed_count += 1
                        # Claiming a single mail changes only its claim state;
                        # list membership and order remain stable until the
                        # final one-key delete.  Keep this batch's Runtime
                        # sequence/offset and exclude the confirmed ID in memory.
                        expected_alignment_offset = runtime_offset
                        self._log(
                            "success",
                            f"邮件_选择性领取：「{title}」点击领取后已可靠返回邮件 #121；"
                            "本轮记录已领取，保持 Runtime 稳定序列继续处理后续目标",
                        )
                        continue
                    raise RuntimeError(
                        f"邮件_选择性领取：{time_text}「{title}」未得到可靠的详情领取返回证据"
                    )

                if scroll_count >= max_scrolls:
                    raise RuntimeError(
                        f"邮件_选择性领取：达到 max_scrolls={max_scrolls}，仍有 {len(targets)} 封必须领取"
                    )
                loaded = yield from runtime.scroll_shape_content(
                    list_shape,
                    settle_seconds=2.0,
                )
                scroll_count += 1
                at_known_top = False
                list_height = float(list_shape.raw.get("h") or 0) * float(geometry.frame_height)
                expected_alignment_offset = alignment_reference_offset + max(
                    1,
                    int(round((list_height * 0.5) / geometry.row_pitch)),
                )
                if not loaded:
                    raise RuntimeError(
                        "邮件_选择性领取：单向遍历已到底但稳定 Runtime 批次仍有必领目标，拒绝回顶重试"
                    )
        except Exception as exc:
            workflow_error = exc

        # 一键删除是统一收尾动作；即使严格配准失败，也只会删除已领取或无附件邮件。
        try:
            scene_id, _score, _frame = runtime.current_scene([121, 122, 123, 34], update=True)
            if scene_id in {122, 123}:
                detail_view = runtime.view(scene_id)
                back_shape = detail_view.get_shape("空白-返回")
                if back_shape is not None:
                    back_shape.click(runtime)
                else:
                    runtime.click_frame_point(scene_id, 1, 1)
                yield from runtime.wait_view(121, timeout=12.0, label="邮件_选择性领取：收尾返回邮件 #121")
                scene_id = 121
            if scene_id == 34:
                yield from self._open_mail_selective_claim_entry(runtime)
                yield from runtime.wait_view(121, timeout=12.0, label="邮件_选择性领取：收尾重新进入邮件 #121")
                scene_id = 121
            if scene_id != 121:
                raise RuntimeError(f"邮件_选择性领取：收尾前无法确认邮件 #121，当前 #{scene_id or 'unknown'}")
            delete_scene = yield from self._delete_read_mail_once(runtime, view121, reason="任务统一收尾")
            if delete_scene != 34:
                yield from self._leave_mail_scene_to_world(
                    ctx,
                    stop_event,
                    runtime,
                    121,
                    label="邮件_选择性领取",
                )
            final_scene = yield from self._ensure_clean_world_after_task(
                ctx,
                stop_event,
                label="邮件_选择性领取",
            )
        except Exception as cleanup_exc:
            if workflow_error is None:
                workflow_error = cleanup_exc
            else:
                self._log("error", f"邮件_选择性领取：主流程失败后收尾也失败：{cleanup_exc}")
            final_scene = None

        final_snapshot = self._read_complete_precise_mail_snapshot(
            stop_event,
            reason="任务完成复查",
        )
        remaining = self._precise_mail_claim_targets(final_snapshot)
        if remaining and workflow_error is None:
            workflow_error = RuntimeError(
                "邮件_选择性领取：最终一键删除并刷新 Runtime 后仍有 "
                f"{len(remaining)} 封应领邮件，拒绝回顶重试或报告成功；"
                f"remaining={[int(item.get('runtime_index') or 0) for item in remaining]}"
            )
        if workflow_error is not None:
            raise workflow_error

        message = (
            f"邮件_选择性领取：完成，精确领取 {processed_count} 封，滚动 {scroll_count} 次，"
            f"最终清单 {int(final_snapshot.get('decoded_count') or 0)} 封"
        )
        message = self._finish_mail_selective_claim_schedule(payload, message)
        with self._lock:
            self._set_status_locked(
                "running",
                message,
                phase="mail_selective_claim_done",
                current_scene=final_scene,
            )
            self._log_locked("success", message)
        return "success"

    def _finish_mail_selective_claim_schedule(
        self,
        payload: dict[str, Any],
        message: str,
    ) -> str:
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "").strip()
        if not scheduler_task_id:
            return message
        # Respect Scheduler's planned business clock during an early run.
        # datetime.now() would advance a midnight job back to the very same
        # upcoming midnight, leaving a successfully completed job due again.
        now = job_now()
        next_time = (now + timedelta(days=1)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        ).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
        return f"{message}，下次 {next_time}"

    def _click_confirmed_mail_delete_prompt(
        self,
        runtime,
        scene_id: int,
        *,
        frame_data_url: str | None = None,
    ) -> None:
        """点击已经由当前帧或 wait_view 确认过的删除确认弹窗。"""

        runtime.click_shape(int(scene_id), "确认", frame_data_url=frame_data_url)

    def _delete_read_mail_until_clean(
        self,
        runtime: BehaviorTreeRuntime,
        mail_view: View,
        stop_event: threading.Event,
        *,
        reason: str,
        initial_snapshot: dict[str, Any] | None = None,
        max_batches: int = 8,
    ):
        """Delete eligible mail in bounded batches with fresh Runtime proof."""

        snapshot = initial_snapshot or self._read_complete_precise_mail_snapshot(
            stop_event,
            reason=f"{reason}删除前读取",
        )
        eligible = self._deletable_runtime_mail_garbage(snapshot)
        protected_ids = self._protected_runtime_mail_ids(snapshot)
        before_count = len(eligible)
        deleted_count = 0
        batches = 0
        while eligible:
            self._raise_if_stopped(stop_event)
            if batches >= max(1, int(max_batches)):
                raise RuntimeError(
                    f"邮件_选择性领取：{reason}达到 {max_batches} 批后仍有 "
                    f"{len(eligible)} 封可删除垃圾"
                )
            previous_ids = set(eligible)
            result_scene = yield from self._delete_read_mail_once(
                runtime,
                mail_view,
                reason=f"{reason}（第 {batches + 1} 批，删除前 {len(previous_ids)} 封）",
            )
            if result_scene == 34:
                yield from self._open_mail_selective_claim_entry(runtime)
                yield from runtime.wait_view(
                    121,
                    timeout=12.0,
                    label="邮件_选择性领取：批量删除后重新进入邮件 #121",
                )
            elif result_scene != 121:
                raise RuntimeError(
                    f"邮件_选择性领取：确认删除后落点异常 #{result_scene or 'unknown'}"
                )
            snapshot = self._read_complete_precise_mail_snapshot(
                stop_event,
                reason=f"{reason}第 {batches + 1} 批删除后强制刷新",
            )
            current_ids = {
                self._runtime_mail_identity(item)
                for item in snapshot.get("items") or []
                if isinstance(item, dict)
                and bool(item.get("present_in_runtime"))
                and self._runtime_mail_identity(item)
            }
            missing_protected = protected_ids - current_ids
            if missing_protected:
                raise RuntimeError(
                    "邮件_选择性领取：一键删除后锁定或策略保留邮件消失，拒绝继续；"
                    f"missing={sorted(missing_protected)[:8]}"
                )
            eligible = self._deletable_runtime_mail_garbage(snapshot)
            current_eligible_ids = set(eligible)
            if not current_eligible_ids < previous_ids:
                raise RuntimeError(
                    "邮件_选择性领取：一键删除后可删除垃圾集合没有严格减少，拒绝误报成功；"
                    f"before={len(previous_ids)} after={len(current_eligible_ids)}"
                )
            removed = len(previous_ids - current_eligible_ids)
            deleted_count += removed
            batches += 1
            self._log(
                "success",
                f"邮件_选择性领取：{reason}第 {batches} 批严格减少 {removed} 封，"
                f"剩余 {len(current_eligible_ids)} 封",
            )
        return {
            "before_count": before_count,
            "after_count": 0,
            "deleted_count": deleted_count,
            "batch_count": batches,
            "protected_count": len(protected_ids),
            "snapshot": snapshot,
        }

    def _delete_read_mail_once(self, runtime: BehaviorTreeRuntime, mail_view: View, *, reason: str):
        """在已确认位于邮件列表时执行一次安全的一键删除闭环。"""

        delete_read_shape = mail_view.get_shape("一键删除")
        if delete_read_shape is None:
            raise RuntimeError("缺少 #121「一键删除」标注，无法完成邮件删除闭环")
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_选择性领取：{reason}，一键删除已阅",
                phase="mail_selective_claim_delete_read",
                current_scene=121,
            )
            self._log_locked("action", f"邮件_选择性领取：{reason}，点击 #121「一键删除」")
        delete_read_shape.click(runtime)
        # A modal is rendered above #121 while the underlying mail scene stays
        # fully recognizable.  Waiting for modal and base scene in one call
        # lets #121 win immediately and leaves the real confirmation untouched.
        # Give formal modal scenes an exclusive bounded gate first; only when
        # all of them are absent may the base mail page prove an idempotent
        # no-op.
        try:
            result_view = yield from runtime.wait_view(
                348,
                210,
                278,
                timeout=6.0,
                label="邮件_选择性领取：一键删除后优先等待确认弹窗",
            )
        except TimeoutError:
            result_view = yield from runtime.wait_view(
                121,
                timeout=6.0,
                label="邮件_选择性领取：未见确认弹窗后复核邮件页",
            )
        result_scene = result_view.id if isinstance(result_view, View) else None
        if result_scene in {348, 210, 278}:
            with self._lock:
                self._set_status_locked(
                    "running",
                    "邮件_选择性领取：确认一键删除",
                    phase="mail_selective_claim_confirm_delete_read",
                    current_scene=result_scene,
                )
                self._log_locked("action", f"邮件_选择性领取：#{result_scene} 点击「确认」")
            self._click_confirmed_mail_delete_prompt(runtime, result_scene)
            targets = (121,) if result_scene == 348 else (121, 34)
            result_view = yield from runtime.wait_view(
                *targets,
                timeout=12.0,
                label="邮件_选择性领取：确认一键删除后等待邮件页",
            )
            result_scene = result_view.id if isinstance(result_view, View) else None
        elif result_scene == 121:
            self._log("info", "邮件_选择性领取：没有可删除邮件，继续当前流程")
        else:
            raise RuntimeError("邮件_选择性领取：一键删除后未进入确认弹窗，也未回到邮件页")
        return result_scene


















































































    def _open_mail_selective_claim_entry(self, runtime: BehaviorTreeRuntime):
        """从当前已识别场景恢复到世界，再走稳定的 #34 -> #35 邮件入口。"""

        asset_tree_path = runtime.asset_tree_path
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少邮件_选择性领取资产树路径，无法进入邮件")
        ctx = runtime.ctx
        stop_event = runtime.stop_event or threading.Event()
        recovered_green = yield from self._leave_green_bottle_to_world_if_present(ctx, stop_event, runtime, label="邮件_选择性领取")
        if recovered_green:
            result = self._open_mail_stable_entry(ctx, stop_event, asset_tree_path, probe_before_open=True)
            opened = (yield from result) if isinstance(result, GeneratorType) else result
            return opened
        scene_id, _score, _frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, [121, 34, 35, 20, 58, 227], update=True)
        if scene_id == 121:
            return "success"
        if scene_id == 227:
            yield from runtime.wait_click(227, "继续", timeout=8.0)
            view = yield from runtime.wait_view(121, 34, timeout=12.0, label="邮件_选择性领取：奖励页关闭后等待邮件或世界")
            scene_id = view.id if isinstance(view, View) else None
            if scene_id == 121:
                return "success"
        if scene_id not in {34, 35}:
            yield from self._ensure_clean_world_after_task(ctx, stop_event, label="邮件_选择性领取")
        result = self._open_mail_stable_entry(ctx, stop_event, asset_tree_path, probe_before_open=True)
        opened = (yield from result) if isinstance(result, GeneratorType) else result
        return opened

    def _leave_green_bottle_to_world_if_present(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        runtime: BehaviorTreeRuntime,
        *,
        label: str,
    ):
        scene_id, _score, _frame, text = self._fanxiu_runtime_scene_text(ctx, runtime, [20, 34, 58, 121, 122, 123], update=True)
        if scene_id in {34, 121, 122, 123}:
            return False
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        green_tokens = sum(1 for token in ("炼丹炉", "神物园", "衣装阁", "丹药") if token in compact)
        looks_green_bottle = scene_id == 20 or (scene_id is None and green_tokens >= 2)
        if not looks_green_bottle:
            return False
        with self._lock:
            self._set_status_locked("running", f"{label}：从绿瓶页返回世界", phase="mail_selective_claim_leave_green_bottle", current_scene=20)
            self._log_locked("action", f"{label}：点击 #20「世界」返回 #34")
        runtime.click_frame_point(20, 80, 1435)
        yield from runtime.wait_view(34, timeout=12.0, label=f"{label}：绿瓶返回世界 #34")
        return True

    def _visible_mail_adjacency_intervals(self, rows: list[dict[str, Any]]) -> list[dict[str, str]]:
        ordered = sorted(rows, key=lambda item: float(item.get("y") or 0))
        intervals: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for newer, older in zip(ordered, ordered[1:]):
            if str(newer.get("visual_scope") or "") != str(older.get("visual_scope") or ""):
                continue
            newer_slot = newer.get("visual_slot_index")
            older_slot = older.get("visual_slot_index")
            if not isinstance(newer_slot, int) or not isinstance(older_slot, int):
                continue
            if older_slot != newer_slot + 1:
                continue
            newer_time = self._normalize_mail_time_text(str(newer.get("time_text") or ""))
            older_time = self._normalize_mail_time_text(str(older.get("time_text") or ""))
            if not newer_time or not older_time or newer_time == older_time:
                continue
            if newer_time <= older_time:
                continue
            key = (newer_time, older_time)
            if key in seen:
                continue
            seen.add(key)
            intervals.append({"newer_time_text": newer_time, "older_time_text": older_time})
        return intervals

    def _align_mail_records_from_visible_adjacency(
        self,
        rows: list[_RuntimeMailRow] | list[dict[str, Any]],
        *,
        source: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        raw_rows = [row.raw if isinstance(row, _RuntimeMailRow) else row for row in rows]
        intervals = self._visible_mail_adjacency_intervals([row for row in raw_rows if isinstance(row, dict)])
        results: list[dict[str, Any]] = []
        updated = 0
        matched = 0
        for interval in intervals:
            try:
                result = align_runtime_mail_records_claimable_between_visible_neighbors(
                    _db_engine,
                    newer_time_text=str(interval.get("newer_time_text") or ""),
                    older_time_text=str(interval.get("older_time_text") or ""),
                    source=source,
                    dry_run=dry_run,
                )
            except OperationalError as exc:
                if "database is locked" not in str(exc).lower():
                    raise
                self._log(
                    "warning",
                    "邮件_选择性领取：可见相邻断层校准遇到数据库锁，跳过本次校准写入",
                )
                continue
            results.append(result)
            updated += int(result.get("updated") or 0)
            matched += int(result.get("matched") or 0)
        return {
            "ok": True,
            "interval_count": len(intervals),
            "matched": matched,
            "updated": updated,
            "intervals": intervals,
            "results": results,
        }

    def _runtime_mail_rows_from_frame(self, runtime: BehaviorTreeRuntime, view121: View, frame: str) -> list[_RuntimeMailRow]:
        if not isinstance(view121.raw, dict):
            return []
        rows = self._recognize_visible_mail_rows(runtime.ctx, view121.raw, frame)
        result: list[_RuntimeMailRow] = []
        for row in rows:
            shape = self._mail_row_title_shape(view121, row)
            if shape is not None:
                result.append(_RuntimeMailRow(row, shape))
        return result

    @staticmethod
    def _mail_visible_row_keys(rows: list[_RuntimeMailRow]) -> set[str]:
        """生成不受横幅、飘字和列表高度动画影响的可见邮件行签名。"""
        occurrences: dict[str, int] = {}
        keys: set[str] = set()
        for row in rows:
            raw = row.raw if isinstance(row, _RuntimeMailRow) else {}
            time_text = re.sub(r"\s+", "", str(raw.get("time_text") or ""))
            title = re.sub(r"\s+", "", _sanitize_ocr_text(str(raw.get("title") or "")))
            # 时间通常位于横幅遮挡区之外，比整块截图哈希稳定；缺时间时再退回标题。
            base = f"time:{time_text}" if time_text else f"title:{title}"
            if base in {"time:", "title:"}:
                continue
            occurrence = occurrences.get(base, 0) + 1
            occurrences[base] = occurrence
            keys.add(f"{base}#{occurrence}")
        return keys

    def _mail_row_title_shape(self, view: View, row: dict[str, Any]) -> Shape | None:
        if not isinstance(view.raw, dict):
            return None
        width, height = self._frame_size(view.raw)
        try:
            x = float(row.get("x") or 0)
            y = float(row.get("y") or 0)
        except (TypeError, ValueError):
            return None
        raw = {
            "id": f"mail-row-title:{row.get('time_text') or ''}:{row.get('title') or ''}",
            "kind": "rect",
            "title": str(row.get("title") or "邮件标题"),
            "x": max(0.0, min(0.999, (x - 16.0) / max(1, width))),
            "y": max(0.0, min(0.999, (y - 12.0) / max(1, height))),
            "w": max(1.0 / max(1, width), 32.0 / max(1, width)),
            "h": max(1.0 / max(1, height), 24.0 / max(1, height)),
            "imageMatchRole": "off",
            "ocrMatchRole": "off",
        }
        return Shape(raw, parent_view=view)

    def _claim_runtime_mail_row(
        self,
        runtime: BehaviorTreeRuntime,
        mail: _RuntimeMailRow,
        *,
        delete_after_reward: bool = True,
        require_claim: bool = False,
    ):
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_选择性领取：打开「{mail.title}」",
                phase="mail_selective_claim_open_row",
                current_scene=121,
            )
            self._log_locked("action", f"邮件_选择性领取：点击标题「{mail.title}」")
        mail.title_shape.click(runtime)
        action_point: tuple[float, float] | None = None
        if require_claim:
            detail_view, action_point = yield from self._wait_precise_mail_detail(
                runtime,
                mail.title,
                timeout=self._MAIL_DETAIL_READY_TIMEOUT_SECONDS,
            )
        else:
            detail_view = yield from runtime.wait_view(122, 123, timeout=12.0, label=f"邮件_选择性领取：等待「{mail.title}」详情")
        if not isinstance(detail_view, View) or detail_view.id not in {122, 123}:
            return _RuntimeMailActionOutcome("claim", "detail_not_found", False)
        if require_claim and detail_view.id != 122:
            back_shape = detail_view.get_shape("空白-返回")
            if back_shape is not None:
                back_shape.click(runtime)
            else:
                runtime.click_frame_point(detail_view.id, 1, 1)
            yield from runtime.wait_view(
                121,
                timeout=12.0,
                label="邮件_选择性领取：模型与详情不一致，安全返回邮件 #121",
            )
            self._log(
                "warning",
                f"邮件_选择性领取：模型判定「{mail.title}」可领，但详情页 "
                f"#{detail_view.id} 已无领取动作；先返回列表，交由新鲜 MailMgr 精确身份复验",
            )
            return _RuntimeMailActionOutcome("claim", "claim_action_absent", False)
        actual_policy = "claim" if detail_view.id == 122 else "delete"
        action_title = "领取" if detail_view.id == 122 else "删除"
        action_shape = detail_view.get_shape(action_title)
        if action_shape is None and detail_view.id == 123:
            action_shape = detail_view.get_shape("领取")
        if action_shape is None:
            raise RuntimeError(f"缺少 #{detail_view.id}「{action_title}」标注，无法处理邮件")
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_选择性领取：{action_title}「{mail.title}」",
                phase="mail_selective_claim_claim",
                current_scene=detail_view.id,
            )
            self._log_locked("action", f"邮件_选择性领取：点击 #{detail_view.id}「{action_shape.title}」")
        if action_point is not None:
            self._log(
                "detail",
                f"邮件_选择性领取：标题与动作词联合确认后，点击详情页「{action_title}」动作区域中心 {action_point}",
            )
            runtime.click_frame_point(detail_view.id, *action_point)
        else:
            action_shape.click(runtime)
        wait_result = yield from self._wait_mail_list_or_reopen_from_world_after_action(
            runtime,
            detail_view,
            timeout=18.0,
            label="邮件_选择性领取：返回邮件 #121",
        )
        confirmed_list_results = {"list", "reopened", "list_after_reward", "reopened_after_reward"}
        if delete_after_reward and wait_result in confirmed_list_results:
            image121 = (runtime.ctx.get("images") or {}).get(121)
            if not isinstance(image121, dict):
                raise RuntimeError("缺少 #121 邮件帧标注，无法在领取返回后执行一键删除")
            yield from self._delete_read_mail_once(runtime, View(image121), reason="领取返回 #121 后")
        if wait_result in {"timeout", "detail_still_open"}:
            back_shape = detail_view.get_shape("空白-返回")
            if back_shape is None:
                raise RuntimeError("邮件_选择性领取：领取后未回邮件列表，且缺少详情页「空白-返回」标注")
            self._log("info", f"邮件_选择性领取：{action_title}后未自动回列表，点击详情页返回")
            back_shape.click(runtime)
            yield from runtime.wait_view(121, timeout=12.0, label="邮件_选择性领取：详情页返回邮件 #121")
        return _RuntimeMailActionOutcome(
            actual_policy,
            wait_result,
            actual_policy == "claim"
            and wait_result in confirmed_list_results,
        )

    def _wait_precise_mail_detail(
        self,
        runtime: BehaviorTreeRuntime,
        expected_title: str,
        *,
        timeout: float,
    ):
        """Confirm a mail detail by its title and action button, not a rigid body template."""

        expected = re.sub(r"\s+", "", _sanitize_ocr_text(expected_title))
        started_at = time.monotonic()
        last_frame = ""
        last_texts: list[str] = []
        last_scene_hint: int | None = None
        stable_scene_reads = 0
        while time.monotonic() - started_at < max(1.0, float(timeout)):
            frame = runtime.cur_frame(update=True)
            last_frame = frame
            scene_id, _score, _current = runtime.current_scene(
                [122, 123],
                frame_data_url=frame,
            )
            if scene_id not in {122, 123}:
                # ``current_scene`` also evaluates active business/popup nodes.
                # A full-screen mail sheet can therefore lose that combined
                # graph even though the formal #122/#123 subgraph identifies it
                # unambiguously (the SDK floating ball is a common overlay).
                # Reuse the strict detail-only graph as an observation fallback;
                # the existing two-consecutive-frame gate below still prevents
                # one noisy template match from authorizing a claim click.
                scene_id = self._mail_detail_overlay_scene(runtime.ctx, frame)
            action_scene = self._mail_detail_action_shape_scene(runtime, frame)
            if action_scene in {122, 123}:
                scene_id = action_scene
            if scene_id in {122, 123} and scene_id == last_scene_hint:
                stable_scene_reads += 1
            elif scene_id in {122, 123}:
                last_scene_hint = scene_id
                stable_scene_reads = 1
            else:
                last_scene_hint = None
                stable_scene_reads = 0
            fragments = runtime.ocr_fragments(frame)
            texts = [
                re.sub(r"\s+", "", _sanitize_ocr_text(item.get("text")))
                for item in fragments
                if isinstance(item, dict) and str(item.get("text") or "").strip()
            ]
            last_texts = texts
            claim_fragments = [
                item
                for item in fragments
                if re.sub(r"\s+", "", _sanitize_ocr_text(item.get("text"))) == "领取"
                and float(item.get("w") or 0) > 0
                and float(item.get("h") or 0) > 0
            ]
            delete_fragments = [
                item
                for item in fragments
                if re.sub(r"\s+", "", _sanitize_ocr_text(item.get("text"))) == "删除"
                and float(item.get("w") or 0) > 0
                and float(item.get("h") or 0) > 0
            ]
            # The list window has already been aligned against the ordered MailMgr
            # sequence.  A row identity may therefore be inferred from adjacent
            # OCR anchors (for example, seeing c/d also fixes the preceding b);
            # do not require the detail title to OCR perfectly a second time.
            if len(claim_fragments) == 1 and not delete_fragments:
                detail_view = runtime.view(122)
                action_shape = detail_view.get_shape("领取")
                if action_shape is None:
                    return None, None
                raw_action = action_shape.raw
                frame_width = float(detail_view.raw.get("width") or 900)
                frame_height = float(detail_view.raw.get("height") or 1600)
                action_point = (
                    (float(raw_action.get("x") or 0) + float(raw_action.get("w") or 0) / 2) * frame_width,
                    (float(raw_action.get("y") or 0) + float(raw_action.get("h") or 0) / 2) * frame_height,
                )
                self._log(
                    "info",
                    f"邮件_选择性领取：清单序列已定位「{expected_title}」，详情唯一动作「领取」确认 #122",
                )
                return detail_view, action_point
            if len(delete_fragments) == 1 and not claim_fragments:
                detail_view = runtime.view(123)
                action_shape = detail_view.get_shape("删除") or detail_view.get_shape("领取")
                if action_shape is None:
                    return None, None
                raw_action = action_shape.raw
                frame_width = float(detail_view.raw.get("width") or 900)
                frame_height = float(detail_view.raw.get("height") or 1600)
                return detail_view, (
                    (float(raw_action.get("x") or 0) + float(raw_action.get("w") or 0) / 2) * frame_width,
                    (float(raw_action.get("y") or 0) + float(raw_action.get("h") or 0) / 2) * frame_height,
                )
            if stable_scene_reads >= 2 and scene_id in {122, 123}:
                # OCR can miss a stylised button.  Two consecutive scene reads are
                # an independent fallback; a single image match is not enough to
                # override the MailMgr/list-window plan.
                return runtime.view(scene_id), None
            yield from runtime.wait_action_settle(0.6)
        if last_frame:
            try:
                evidence = _behavior_tree_runtime.build_unknown_evidence(
                    self,
                    runtime.ctx,
                    last_frame,
                    label=f"mail_detail_{expected_title}",
                    expected_scene_ids=[122, 123],
                    last_scene_id=None,
                    last_score=0.0,
                )
                self._log(
                    "warning",
                    f"邮件_选择性领取：详情联合确认超时，目标「{expected_title}」，"
                    f"OCR={last_texts}，截图={evidence.frame_path}，证据={evidence.report_path}",
                )
            except Exception as exc:
                self._log(
                    "warning",
                    f"邮件_选择性领取：详情联合确认超时，目标「{expected_title}」，OCR={last_texts}；"
                    f"保存证据失败：{exc}",
                )
        return None, None

    @staticmethod
    def _mail_detail_action_shape_scene(
        runtime: BehaviorTreeRuntime,
        frame_data_url: str,
    ) -> int | None:
        """Resolve a detail overlay from its formal action Shapes."""

        try:
            claim_score = float(
                runtime.shape_score(122, "领取", frame_data_url=frame_data_url)
            )
        except Exception:
            claim_score = 0.0
        try:
            delete_score = float(
                runtime.shape_score(123, "删除", frame_data_url=frame_data_url)
            )
        except Exception:
            delete_score = 0.0
        if claim_score >= 90.0 and claim_score > delete_score + 10.0:
            return 122
        if delete_score >= 90.0 and delete_score > claim_score + 10.0:
            return 123
        return None

    def _mail_detail_overlay_scene(self, ctx: dict[str, Any], frame_data_url: str) -> int | None:
        """Resolve the overlay through the formal #122/#123 graph nodes."""

        scene_id, _score = self._identify_scene_number(ctx, frame_data_url, [122, 123])
        return scene_id if scene_id in {122, 123} else None

    def _wait_mail_list_after_detail_action(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        runtime: BehaviorTreeRuntime,
        scene_id: int,
        *,
        timeout: float,
        label: str,
    ):
        detail_image = (ctx.get("images") or {}).get(scene_id)
        if isinstance(detail_image, dict):
            detail_view = View(detail_image)
            wait_result = yield from self._wait_mail_list_or_reopen_from_world_after_action(
                runtime,
                detail_view,
                timeout=timeout,
                label=label,
            )
            if wait_result in {"list", "reopened", "list_after_reward", "reopened_after_reward"}:
                return wait_result
            if wait_result in {"timeout", "detail_still_open"}:
                back_shape = detail_view.get_shape("空白-返回")
                if back_shape is not None:
                    self._log("info", f"{label}：详情页未自动回列表，点击详情页返回")
                    back_shape.click(runtime)
                    yield from self._wait_mail_list_ready_or_restore_world(
                        ctx,
                        stop_event,
                        timeout=12.0,
                        label=label,
                    )
                    return "list"
        yield from self._wait_mail_list_ready_or_restore_world(ctx, stop_event, timeout=timeout, label=label)
        return "list"

    def _wait_mail_list_or_reopen_from_world_after_action(
        self,
        runtime: BehaviorTreeRuntime,
        detail_view: View,
        *,
        timeout: float,
        label: str,
    ):
        ctx = runtime.ctx
        stop_event = runtime.stop_event or threading.Event()
        image121 = (ctx.get("images") or {}).get(121)
        marker_shape = self._find_shape(image121, "邮件标识") if isinstance(image121, dict) else None
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_marker_score = 0.0
        last_ocr_at = 0.0
        last_text = ""
        saw_reward_transition = False
        reward_continue_clicked = False
        reward_item_detail_close_count = 0
        while True:
            self._raise_if_stopped(stop_event)
            runtime.clear_frame() if hasattr(runtime, "clear_frame") else self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            elapsed = time.monotonic() - start
            detail_scene_id = detail_view.id if isinstance(detail_view.id, int) else None
            candidates = [scene for scene in [121, 347, 250, 34, detail_scene_id] if isinstance(scene, int)]
            scene_id, score, frame, text = self._fanxiu_runtime_scene_text(ctx, runtime, candidates, update=True)
            last_scene_id, last_score = scene_id, score
            marker_score = 0.0
            marker_matched = False
            if scene_id == 250:
                if reward_item_detail_close_count >= 2:
                    raise RuntimeError(f"{label}：奖励后连续打开 #250 道具详情，已停止避免循环")
                self._log("info", f"{label}：奖励点击后打开 #250 道具详情，使用正式「返回」标注关闭")
                yield from runtime.wait_click(250, "返回", timeout=8.0, label=f"{label}：关闭奖励道具详情")
                reward_item_detail_close_count += 1
                yield from runtime.wait_action_settle(0.8)
                continue
            if (
                scene_id != 347
                and self._mail_continue_hint_text_matches(text)
            ):
                if not saw_reward_transition:
                    compact_transition_text = _sanitize_ocr_text(text).replace("\n", " | ")
                    self._log(
                        "info",
                        f"{label}：奖励继续帧 OCR={compact_transition_text[:600] or '<empty>'}",
                    )
                saw_reward_transition = True
                with self._lock:
                    self._status.update(
                        {
                            "phase": "mail_selective_claim_wait_reward_transition",
                            "current_scene": scene_id,
                            "message": f"{label}：检测到领取奖励继续提示，等待回到邮件 #121",
                            "updated_at": time.time(),
                        }
                    )
                if not reward_continue_clicked:
                    continue_point = self._mail_continue_hint_click_point(runtime, frame)
                    if continue_point is not None:
                        image347 = (ctx.get("images") or {}).get(347)
                        click_view = image347 if isinstance(image347, dict) else detail_view
                        self._log(
                            "info",
                            f"{label}：领取结果明确提示「点击屏幕继续」，点击提示文字关闭过场",
                        )
                        runtime.click_frame_point(click_view, *continue_point)
                        reward_continue_clicked = True
                        yield from runtime.wait_action_settle(0.8)
                continue
            if scene_id == 347 or self._mail_reward_transition_text_matches(text):
                if not saw_reward_transition:
                    compact_transition_text = _sanitize_ocr_text(text).replace("\n", " | ")
                    self._log(
                        "info",
                        f"{label}：奖励过场 OCR={compact_transition_text[:600] or '<empty>'}",
                    )
                saw_reward_transition = True
                with self._lock:
                    self._status.update(
                        {
                            "phase": "mail_selective_claim_wait_reward_transition",
                            "current_scene": 347 if scene_id == 347 else scene_id,
                            "message": f"{label}：检测到 #347 领取奖励过场，等待自动回到邮件 #121",
                            "updated_at": time.time(),
                        }
                    )
                continue
            if scene_id == 121:
                if isinstance(image121, dict) and marker_shape:
                    try:
                        marker_result = self._match_shape(ctx, image121, marker_shape, frame)
                        marker_score = float(marker_result.get("similarity") or 0)
                        marker_matched = bool(marker_result.get("matched"))
                    except Exception as exc:
                        self._log("detail", f"{label}：邮件标识匹配失败：{exc}")
                else:
                    marker_matched = True
                last_marker_score = marker_score
                if marker_matched:
                    with self._lock:
                        self._status.update({"current_scene": 121, "updated_at": time.time()})
                    self._log("success", f"{label}：已到达 #121 {score:.0f}%，邮件标识 {marker_score:.0f}%")
                    return "list_after_reward" if saw_reward_transition else "list"
            if detail_scene_id is not None and scene_id == detail_scene_id and elapsed >= 5.0:
                self._log("info", f"{label}：领取后仍停留 #{detail_scene_id} {score:.0f}%，提前走详情页返回")
                return "detail_still_open"
            now = time.monotonic()
            if scene_id == 34 or now - last_ocr_at >= 1.2:
                last_ocr_at = now
                last_text = text or last_text
            if scene_id == 34:
                self._log("info", f"{label}：领取后落到世界页，重新打开邮件列表")
                yield from runtime.wait_action_settle(0.8)
                reopened = self._reopen_mail_from_current_world_like(runtime)
                result = (yield from reopened) if isinstance(reopened, GeneratorType) else reopened
                if result == "success":
                    return "reopened_after_reward" if saw_reward_transition else "reopened"
                self._log("info", f"{label}：从世界页重新打开邮件失败 result={result}，继续等待")
            with self._lock:
                self._status.update(
                    {
                        "phase": "mail_selective_claim_wait_list_or_world",
                        "current_scene": scene_id,
                        "message": (
                            f"{label}：当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} "
                            f"{score:.0f}%，邮件标识 {marker_score:.0f}%"
                        ),
                        "updated_at": time.time(),
                    }
                )
            if elapsed >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                self._log("info", f"{label}：等待列表或世界页超时，最后 {scene_text} {last_score:.0f}%，邮件标识 {last_marker_score:.0f}% OCR={last_text}")
                return "timeout"

    def _reopen_mail_from_current_world_like(self, runtime: BehaviorTreeRuntime):
        ctx = runtime.ctx
        stop_event = runtime.stop_event or threading.Event()
        asset_tree_path = runtime.asset_tree_path
        if isinstance(asset_tree_path, Path):
            try:
                stable_result = self._open_mail_stable_entry(ctx, stop_event, asset_tree_path, probe_before_open=False)
                stable_opened = (yield from stable_result) if isinstance(stable_result, GeneratorType) else stable_result
                if stable_opened == "success":
                    return "success"
            except RuntimeError as exc:
                self._log("info", f"邮件_历史扫描：#35 稳定入口失败，尝试 #68 动态入口：{exc}")
        visible_result = self._try_open_mail_from_visible_world_menu(ctx, stop_event, timeout=0.2)
        visible_opened = (yield from visible_result) if isinstance(visible_result, GeneratorType) else visible_result
        if visible_opened == "success":
            return "success"
        dynamic_result = self._try_open_mail_dynamic_entry(ctx, stop_event)
        dynamic_opened = (yield from dynamic_result) if isinstance(dynamic_result, GeneratorType) else dynamic_result
        return dynamic_opened if dynamic_opened != "missing" else visible_opened

    def _mail_reward_transition_text_matches(self, text: str) -> bool:
        compact = _sanitize_ocr_text(text).replace(" ", "")
        if not compact:
            return False
        has_reward_title = "恭喜获得" in compact or bool(re.search(r"[恭共]喜.{0,3}[获莎]?得", compact))
        has_continue_hint = "点击屏幕继续" in compact or "点击继续" in compact
        has_auto_close = "自动关闭" in compact or bool(re.search(r"\d+秒后.{0,4}关闭", compact))
        return bool(has_reward_title and (has_continue_hint or has_auto_close or "获得" in compact))

    def _mail_continue_hint_text_matches(self, text: str) -> bool:
        compact = _sanitize_ocr_text(text).replace(" ", "")
        return "点击屏幕继续" in compact or "点击继续" in compact

    def _mail_continue_hint_click_point(
        self,
        runtime: BehaviorTreeRuntime,
        frame: str,
    ) -> tuple[float, float] | None:
        for fragment in runtime.ocr_fragments(frame):
            text = _sanitize_ocr_text(fragment.get("text")).replace(" ", "")
            if "点击屏幕继续" not in text and "点击继续" not in text:
                continue
            x = float(fragment.get("x") or 0)
            y = float(fragment.get("y") or 0)
            width = float(fragment.get("w") or 0)
            height = float(fragment.get("h") or 0)
            if width > 0 and height > 0:
                return x + width / 2, y + height / 2
        return None

    def _refresh_runtime_mail_snapshot(self, label: str, *, force_refresh: bool) -> bool:
        del force_refresh
        try:
            from sqlmodel import Session

            from backend.core.fanxiu.mail.runtime_sync import sync_fanxiu_mail_from_runtime

            with Session(_db_engine()) as session:
                result = sync_fanxiu_mail_from_runtime(session)
            runtime_timings = result.get("runtime_timings") or {}
            stage_text = "/".join(
                f"{key}={float(runtime_timings.get(key) or 0.0):.2f}s"
                for key in (
                    "process_discovery",
                    "lua_state",
                    "manager_resolve",
                    "snapshot_decode",
                )
            )
            with self._lock:
                self._log_locked(
                    "info",
                    "邮件_动态模型："
                    f"{label} current={result.get('record_count', 0)} "
                    f"updated={result.get('updated', 0)} inserted={result.get('inserted', 0)} "
                    f"absent={result.get('absent', 0)} "
                    f"runtime={float(result.get('runtime_elapsed_seconds') or 0.0):.2f}s "
                    f"projection={float(result.get('projection_elapsed_seconds') or 0.0):.2f}s "
                    f"root_cache_hit={result.get('root_cache_hit')} stages={stage_text}",
                )
            return bool(result.get("ok"))
        except Exception as exc:
            with self._lock:
                self._log_locked("error", f"邮件_动态模型：{label}失败：{exc}")
            return False

    def _open_mail_scene(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        asset_tree_path: Path,
        *,
        entry_mode: str = "dynamic",
    ) -> str:
        visible_menu_result = self._try_open_mail_from_visible_world_menu(ctx, stop_event, timeout=1.0)
        opened_from_menu = (yield from visible_menu_result) if isinstance(visible_menu_result, GeneratorType) else visible_menu_result
        if opened_from_menu == "success":
            return "success"
        with self._lock:
            self._set_status_locked("running", "邮件_历史扫描：确认世界 #34", phase="mail_claim_go_world")
            self._log_locked("action", "邮件_历史扫描：先确认进入世界 #34")
        go_scene_result = self._go_scene_task(ctx, asset_tree_path, 34, stop_event)
        result = (yield from go_scene_result) if isinstance(go_scene_result, GeneratorType) else go_scene_result
        if result != "success":
            return result
        if entry_mode in {"stable", "menu", "full", "full_scan", "debug"}:
            stable_result = self._open_mail_stable_entry(ctx, stop_event, asset_tree_path, probe_before_open=False)
            return (yield from stable_result) if isinstance(stable_result, GeneratorType) else stable_result
        stable_opened = "missing"
        try:
            stable_result = self._open_mail_stable_entry(ctx, stop_event, asset_tree_path, probe_before_open=False)
            stable_opened = (yield from stable_result) if isinstance(stable_result, GeneratorType) else stable_result
            if stable_opened == "success":
                return "success"
        except RuntimeError as exc:
            self._log("info", f"邮件_历史扫描：#35 稳定入口失败，尝试 #68 动态入口：{exc}")
        dynamic_result = self._try_open_mail_dynamic_entry(ctx, stop_event)
        opened = (yield from dynamic_result) if isinstance(dynamic_result, GeneratorType) else dynamic_result
        if opened == "no_mail":
            return "no_mail"
        if opened == "success":
            return "success"
        return stable_opened

    def _try_open_mail_dynamic_entry(self, ctx: dict[str, Any], stop_event: threading.Event) -> str:
        image34 = ctx.get("images", {}).get(34)
        image68 = ctx.get("images", {}).get(68)
        if not isinstance(image68, dict) and isinstance(image34, dict):
            image68 = self._find_child_image_by_number(image34, 68)
        mail_shape = self._find_shape(image68, "邮件") if isinstance(image68, dict) else None
        if not isinstance(image68, dict) or not mail_shape:
            self._log("detail", "邮件_历史扫描：#68 动态邮件入口标注缺失，尝试稳定入口")
            return "missing"
        with self._lock:
            self._set_status_locked("running", "邮件_历史扫描：检测 #68 邮件入口", phase="mail_claim_check_mail", current_scene=34)
            self._log_locked("action", "邮件_历史扫描：检测 #68「邮件」")
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        frame = runtime.cur_frame(update=True)
        result = self._match_shape(ctx, image68, mail_shape, frame)
        similarity = float(result.get("similarity") or 0)
        matched = bool(result.get("matched"))
        if not matched:
            with self._lock:
                self._set_status_locked("running", "邮件_历史扫描：#68 未命中，改走 #35 稳定入口", phase="mail_claim_dynamic_missing", current_scene=34)
                self._log_locked("info", f"邮件_历史扫描：未发现 #68「邮件」{similarity:.0f}%，改走稳定入口")
            return "missing"
        with self._lock:
            self._set_status_locked("running", "邮件_历史扫描：打开 #68 邮件入口", phase="mail_claim_open_mail", current_scene=34)
            self._log_locked("action", f"邮件_历史扫描：识别到 #68「邮件」{similarity:.0f}%，点击打开")
        box = self._box(mail_shape, image68)
        click_x = float(box.get("x") or 0) + float(box.get("w") or 0) / 2
        click_y = float(box.get("y") or 0) + float(box.get("h") or 0) / 2
        runtime.click_frame_point(image68, click_x, click_y)
        try:
            yield from self._wait_mail_list_ready_or_restore_world(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
        except RuntimeError as exc:
            self._log("info", f"邮件_历史扫描：#68 邮件入口点击后未进入 #121，改走稳定入口：{exc}")
            return "missing"
        return "success"

    def _open_mail_stable_entry(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        asset_tree_path: Path,
        *,
        probe_before_open: bool = True,
    ) -> str:
        image34 = ctx.get("images", {}).get(34)
        open_shape = self._find_shape(image34, "打开下方菜单") if isinstance(image34, dict) else None
        if not isinstance(image34, dict) or not open_shape:
            raise RuntimeError("缺少 #34「打开下方菜单」标注，无法走稳定邮件入口")
        with self._lock:
            self._set_status_locked("running", "邮件_历史扫描：打开下方菜单 #35", phase="mail_claim_open_world_menu", current_scene=34)
            self._log_locked("action", "邮件_历史扫描：#68 不可用，尝试 #34 -> #35 稳定入口")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        last_error: Exception | None = None
        for attempt in range(2):
            if probe_before_open:
                visible_result = self._click_mail_from_visible_world_menu_once(ctx, stop_event, require_world_scene=False)
                visible_opened = (yield from visible_result) if isinstance(visible_result, GeneratorType) else visible_result
                if visible_opened == "success":
                    return "success"
            box = self._box(open_shape, image34)
            x = float(box.get("x") or 0) + float(box.get("w") or 0) / 2
            y = float(box.get("y") or 0) + float(box.get("h") or 0) / 2
            runtime.click_frame_point(image34, x, y)
            with self._lock:
                self._set_status_locked("running", "邮件_历史扫描：等待下方菜单展开", phase="mail_claim_wait_world_menu", current_scene=34)
            yield from runtime.wait_action_settle(1.0)
            visible_result = self._click_mail_from_visible_world_menu_once(ctx, stop_event, require_world_scene=False)
            visible_opened = (yield from visible_result) if isinstance(visible_result, GeneratorType) else visible_result
            if visible_opened == "success":
                return "success"
            yield from runtime.wait_action_settle(0.8)
            visible_result = self._click_mail_from_visible_world_menu_once(ctx, stop_event, require_world_scene=False)
            visible_opened = (yield from visible_result) if isinstance(visible_result, GeneratorType) else visible_result
            if visible_opened == "success":
                return "success"
            last_error = RuntimeError("邮件_历史扫描：等待 #35 邮件入口超时，最后 0%")
            if attempt >= 1:
                break
            self._log("info", "邮件_历史扫描：下方菜单未展开或未识别，重试打开 #34 下方菜单")
        if last_error is not None:
            raise last_error
        return "missing"

    def _click_mail_from_visible_world_menu_once(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        require_world_scene: bool = True,
    ) -> str:
        image35 = ctx.get("images", {}).get(35)
        if not isinstance(image35, dict):
            return "missing"
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        self._raise_if_stopped(stop_event)
        frame = runtime.cur_frame(update=True)
        if require_world_scene:
            scene_id, score, _frame = runtime.current_scene(frame_data_url=frame)
            if scene_id not in {34, 35} or score < float(self.scene_threshold):
                return "missing"
        mail_shape = self._find_shape(image35, "邮件")
        menu_shape = self._find_shape(image35, "菜单")
        if not require_world_scene and menu_shape:
            ocr_fragments = self._ocr_fragments_in_shapes(frame, image35, ("菜单",), padding=8)
            menu_matches = runtime.ocr_centers_in_shape(35, "菜单", include=("邮件",), frame_data_url=frame)
            menu_matches = [match for match in menu_matches if self._looks_like_world_menu_mail_entry_ocr(match[2])]
            if not menu_matches:
                if mail_shape and self._looks_like_world_menu_open_ocr(ocr_fragments):
                    x, y = self._mail_world_menu_icon_click_point(image35, 0, 0)
                    with self._lock:
                        self._set_status_locked("running", "邮件_历史扫描：点击 #35 邮件标注", phase="mail_claim_click_world_menu_mail", current_scene=35)
                        self._log_locked("action", f"邮件_历史扫描：#35 菜单已展开，点击邮件入口 ({x:.0f},{y:.0f})")
                    runtime.click_frame_point(image35, x, y)
                    yield from self._wait_mail_list_ready_or_restore_world(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
                    return "success"
                return "missing"
            x, y = self._mail_world_menu_icon_click_point(image35, menu_matches[0][0], menu_matches[0][1])
            text = menu_matches[0][2]
            with self._lock:
                self._set_status_locked("running", "邮件_历史扫描：点击 #35 邮件 OCR", phase="mail_claim_click_world_menu_mail", current_scene=35)
                self._log_locked("action", f"邮件_历史扫描：#35 菜单 OCR 命中「{text}」，点击邮件入口 ({x:.0f},{y:.0f})")
            runtime.click_frame_point(image35, x, y)
            yield from self._wait_mail_list_ready_or_restore_world(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
            return "success"
        if mail_shape:
            match_score = self._shape_score(ctx, image35, mail_shape, frame, match_strategy="auto", ocr_fallback=False)
            if match_score < float(self.scene_threshold):
                return "missing"
            x, y = self._mail_world_menu_icon_click_point(image35, 0, 0)
            with self._lock:
                self._set_status_locked("running", "邮件_历史扫描：点击已展开菜单邮件入口", phase="mail_claim_click_world_menu_mail", current_scene=35)
                self._log_locked("action", f"邮件_历史扫描：#35「邮件」可见 {match_score:.0f}%，点击标注中心 ({x:.0f},{y:.0f})")
            runtime.click_frame_point(image35, x, y)
            yield from self._wait_mail_list_ready_or_restore_world(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
            return "success"
        menu_matches = runtime.ocr_centers_in_shape(35, "菜单", include=("邮件",), frame_data_url=frame)
        menu_matches = [match for match in menu_matches if self._looks_like_world_menu_mail_entry_ocr(match[2])]
        if not menu_matches:
            return "missing"
        x, y, text = menu_matches[0]
        x, y = self._mail_world_menu_icon_click_point(image35, x, y)
        with self._lock:
            self._set_status_locked("running", "邮件_历史扫描：点击 #35 邮件 OCR", phase="mail_claim_click_world_menu_mail", current_scene=35)
            self._log_locked("action", f"邮件_历史扫描：#35 菜单 OCR 命中「{text}」，点击邮件入口 ({x:.0f},{y:.0f})")
        runtime.click_frame_point(image35, x, y)
        yield from self._wait_mail_list_ready_or_restore_world(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
        return "success"

    def _open_mail_from_world_menu_shape(self, ctx: dict[str, Any], stop_event: threading.Event) -> str:
        # Runtime actions must be driven by the asset-tree annotations. Do not
        # infer alternate menu coordinates from screenshots here; if the current
        # UI changed, update the #34/#35/#121 shapes or sceneJumpTarget data.
        image35 = ctx.get("images", {}).get(35)
        mail_shape = self._find_shape(image35, "邮件") if isinstance(image35, dict) else None
        menu_shape = self._find_shape(image35, "菜单") if isinstance(image35, dict) else None
        if not isinstance(image35, dict) or (not mail_shape and not menu_shape):
            raise RuntimeError("缺少 #35「邮件」或「菜单」标注，无法走稳定邮件入口")
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        self._raise_if_stopped(stop_event)
        with self._lock:
            self._set_status_locked("running", "邮件_历史扫描：等待 #35 邮件命中", phase="mail_claim_wait_world_menu_mail", current_scene=35)
        if mail_shape and not menu_shape:
            wait_result = self._wait_shape_match(
                ctx,
                stop_event,
                image35,
                mail_shape,
                timeout=8.0,
                label="邮件_历史扫描：等待 #35「邮件」",
            )
            try:
                frame, match_result = (yield from wait_result) if isinstance(wait_result, GeneratorType) else wait_result
            except RuntimeError as exc:
                if "超时" not in str(exc):
                    raise
                x, y = self._mail_world_menu_icon_click_point(image35, 0, 0)
                with self._lock:
                    self._set_status_locked("running", "邮件_历史扫描：按 #35 邮件固定标注点击", phase="mail_claim_click_world_menu_mail", current_scene=35)
                    self._log_locked("action", f"邮件_历史扫描：#35「邮件」未命中，按资产树标注点击 ({x:.0f},{y:.0f})")
                runtime.click_frame_point(image35, x, y)
                yield from self._wait_mail_list_ready_or_restore_world(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
                return "success"
            with self._lock:
                self._set_status_locked("running", "邮件_历史扫描：点击 #35 邮件", phase="mail_claim_click_world_menu_mail", current_scene=35)
                self._log_locked("action", "邮件_历史扫描：按 #35「邮件」标注点击")
            runtime.click_shape(image35, mail_shape, frame_data_url=frame, match_result=match_result)
            yield from self._wait_mail_list_ready_or_restore_world(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
            return "success"
        deadline = time.time() + 8.0
        last_score = 0.0
        last_ocr = ""
        while time.time() < deadline:
            self._raise_if_stopped(stop_event)
            frame = runtime.cur_frame(update=True)
            if mail_shape:
                match_score = self._shape_score(ctx, image35, mail_shape, frame, match_strategy="auto")
                last_score = max(last_score, match_score)
                if match_score >= float(self.scene_threshold):
                    mail_box = self._box(mail_shape, image35)
                    x = float(mail_box.get("x") or 0) + float(mail_box.get("w") or 0) / 2
                    y = float(mail_box.get("y") or 0) + float(mail_box.get("h") or 0) / 2
                    with self._lock:
                        self._set_status_locked("running", "邮件_历史扫描：点击 #35 邮件", phase="mail_claim_click_world_menu_mail", current_scene=35)
                        self._log_locked("action", f"邮件_历史扫描：#35「邮件」标注命中 {match_score:.0f}%，点击标注中心 ({x:.0f},{y:.0f})")
                    runtime.click_frame_point(image35, x, y)
                    yield from self._wait_mail_list_ready_or_restore_world(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
                    return "success"

            if not mail_shape:
                ocr_fragments = runtime.ocr_fragments(frame)
                last_ocr = " / ".join(str(item.get("text") or "") for item in ocr_fragments[-3:]) or last_ocr
                menu_matches = runtime.ocr_centers_in_shape(35, "菜单", include=("邮件",), frame_data_url=frame)
                menu_matches = [match for match in menu_matches if self._looks_like_world_menu_mail_entry_ocr(match[2])]
                if menu_matches:
                    x, y, text = menu_matches[0]
                    x, y = self._mail_world_menu_icon_click_point(image35, x, y)
                    with self._lock:
                        self._set_status_locked("running", "邮件_历史扫描：点击 #35 邮件 OCR", phase="mail_claim_click_world_menu_mail", current_scene=35)
                        self._log_locked("action", f"邮件_历史扫描：#35 菜单 OCR 命中「{text}」，点击邮件入口 ({x:.0f},{y:.0f})")
                    runtime.click_frame_point(image35, x, y)
                    yield from self._wait_mail_list_ready_or_restore_world(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
                    return "success"

            with self._lock:
                self._set_status_locked(
                    "running",
                    f"邮件_历史扫描：等待 #35 邮件入口 {last_score:.0f}%",
                    phase="mail_claim_wait_world_menu_mail",
                    current_scene=35,
                )
            yield BehaviorTreeStatus.RUNNING
        if mail_shape:
            self._log("info", f"邮件_历史扫描：#35 邮件入口未命中，最后 {last_score:.0f}%，不盲点未命中 shape")
        raise RuntimeError(f"邮件_历史扫描：等待 #35 邮件入口超时，最后 {last_score:.0f}% OCR={last_ocr}")

    def _mail_world_menu_icon_click_point(self, image35: dict[str, Any], x: float, y: float) -> tuple[float, float]:
        mail_shape = self._find_shape(image35, "邮件")
        if mail_shape:
            box = self._box(mail_shape, image35)
            return float(box.get("x") or 0) + float(box.get("w") or 0) / 2, float(box.get("y") or 0) + float(box.get("h") or 0) / 2
        return x, y

    def _looks_like_world_menu_mail_entry_ocr(self, text: str) -> bool:
        compact = _sanitize_ocr_text(text).replace(" ", "")
        if not compact or "邮件" not in compact:
            return False
        if any(token in compact for token in ("已锁定", "封邮件", "一键", "领取", "删除", "已阅", "未阅", "已读", "附件", "年月日")):
            return False
        chinese = re.sub(r"[^\u4e00-\u9fff]", "", compact)
        if chinese == "邮件":
            return True
        return any(token in chinese for token in ("仙缘", "修为", "设置"))

    def _looks_like_world_menu_open_ocr(self, lines: list[dict[str, Any]]) -> bool:
        compact = _sanitize_ocr_text("".join(str(line.get("text") or "") for line in lines)).replace(" ", "")
        if not compact:
            return False
        if any(token in compact for token in ("已锁定", "封邮件", "一键领取", "一键删除", "已阅", "未阅", "附件")):
            return False
        if "邮件" in compact:
            return True
        return "仙缘" in compact and "设置" in compact

    def _wait_mail_list_ready_or_restore_world(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        timeout: float,
        label: str,
    ):
        try:
            yield from self._wait_mail_list_ready(ctx, stop_event, timeout=timeout, label=label)
            return
        except RuntimeError as exc:
            original_error = exc
        with self._lock:
            self._log_locked("warning", f"{label} 超时，尝试恢复到 #34，避免污染后续作业起点")
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        try:
            yield from runtime.goto_view(34)
            raise original_error
        except RuntimeError as restore_error:
            if restore_error is original_error:
                raise
            with self._lock:
                self._log_locked(
                    "warning",
                    f"{label}：场景图恢复失败，保留当前现场并停止，不执行猜测坐标：{restore_error}",
                )
            raise original_error from restore_error

    def _try_open_mail_from_visible_world_menu(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        timeout: float,
    ) -> str:
        image35 = ctx.get("images", {}).get(35)
        if not isinstance(image35, dict):
            return "missing"
        mail_shape = self._find_shape(image35, "邮件")
        menu_shape = self._find_shape(image35, "菜单")
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        deadline = time.time() + max(0.1, float(timeout or 0.1))
        while time.time() < deadline:
            self._raise_if_stopped(stop_event)
            frame = runtime.cur_frame(update=True)
            scene_id, score, _frame = runtime.current_scene()
            in_world_menu_context = scene_id in {34, 35} and score >= float(self.scene_threshold)
            if not in_world_menu_context:
                with self._lock:
                    self._set_status_locked("running", "邮件_历史扫描：探测可见下方菜单邮件入口", phase="mail_claim_probe_world_menu_mail")
                yield BehaviorTreeStatus.RUNNING
                continue
            if menu_shape:
                ocr_fragments = self._ocr_fragments_in_shapes(frame, image35, ("菜单",), padding=8)
                menu_matches = runtime.ocr_centers_in_shape(35, "菜单", include=("邮件",), frame_data_url=frame)
                menu_matches = [match for match in menu_matches if self._looks_like_world_menu_mail_entry_ocr(match[2])]
                if menu_matches:
                    x, y = self._mail_world_menu_icon_click_point(image35, menu_matches[0][0], menu_matches[0][1])
                    text = menu_matches[0][2]
                    with self._lock:
                        self._set_status_locked("running", "邮件_历史扫描：点击 #35 邮件 OCR", phase="mail_claim_click_world_menu_mail", current_scene=35)
                        self._log_locked("action", f"邮件_历史扫描：#35 菜单 OCR 命中「{text}」，点击邮件入口 ({x:.0f},{y:.0f})")
                    runtime.click_frame_point(image35, x, y)
                    yield from self._wait_mail_list_ready_or_restore_world(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
                    return "success"
                if mail_shape and self._looks_like_world_menu_open_ocr(ocr_fragments):
                    x, y = self._mail_world_menu_icon_click_point(image35, 0, 0)
                    with self._lock:
                        self._set_status_locked("running", "邮件_历史扫描：点击 #35 邮件标注", phase="mail_claim_click_world_menu_mail", current_scene=35)
                        self._log_locked("action", f"邮件_历史扫描：#35 菜单已展开，点击邮件入口 ({x:.0f},{y:.0f})")
                    runtime.click_frame_point(image35, x, y)
                    yield from self._wait_mail_list_ready_or_restore_world(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
                    return "success"
                with self._lock:
                    self._set_status_locked("running", "邮件_历史扫描：探测可见下方菜单邮件入口", phase="mail_claim_probe_world_menu_mail")
                yield BehaviorTreeStatus.RUNNING
                continue
            if mail_shape:
                match_score = self._shape_score(ctx, image35, mail_shape, frame, match_strategy="auto", ocr_fallback=False)
                if match_score >= float(self.scene_threshold):
                    x, y = self._mail_world_menu_icon_click_point(image35, 0, 0)
                    with self._lock:
                        self._set_status_locked("running", "邮件_历史扫描：点击已展开菜单邮件入口", phase="mail_claim_click_world_menu_mail", current_scene=35)
                        self._log_locked("action", f"邮件_历史扫描：#35「邮件」可见 {match_score:.0f}%，点击标注中心 ({x:.0f},{y:.0f})")
                    runtime.click_frame_point(image35, x, y)
                    yield from self._wait_mail_list_ready_or_restore_world(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
                    return "success"
                with self._lock:
                    self._set_status_locked("running", "邮件_历史扫描：探测可见下方菜单邮件入口", phase="mail_claim_probe_world_menu_mail")
                yield BehaviorTreeStatus.RUNNING
                continue
            menu_matches = runtime.ocr_centers_in_shape(35, "菜单", include=("邮件",), frame_data_url=frame)
            if menu_matches:
                x, y, text = menu_matches[0]
                x, y = self._mail_world_menu_icon_click_point(image35, x, y)
                with self._lock:
                    self._set_status_locked("running", "邮件_历史扫描：点击 #35 邮件 OCR", phase="mail_claim_click_world_menu_mail", current_scene=35)
                    self._log_locked("action", f"邮件_历史扫描：#35 无「邮件」shape，点击菜单 OCR「{text}」({x:.0f},{y:.0f})")
                runtime.click_frame_point(image35, x, y)
                yield from self._wait_mail_list_ready_or_restore_world(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
                return "success"
            with self._lock:
                self._set_status_locked("running", "邮件_历史扫描：探测可见下方菜单邮件入口", phase="mail_claim_probe_world_menu_mail")
            yield BehaviorTreeStatus.RUNNING
        return "missing"

    def _scan_mail_scene(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        action_enabled: bool = True,
        scan_mode: str = "incremental",
        action_policies: set[str] | None = None,
        max_actions: int | None = None,
        target_title: str = "",
        target_time_text: str = "",
        game_first: bool = False,
        fail_on_runtime_gap: bool = False,
    ) -> str:
        image121 = ctx.get("images", {}).get(121)
        if not isinstance(image121, dict):
            raise RuntimeError("缺少 #121 邮件帧标注，无法扫描邮件")
        first_shape = self._find_shape(image121, "第1封")
        list_shape = self._find_shape(image121, "邮件清单2") or self._find_shape(image121, "邮件清单")
        if not first_shape:
            raise RuntimeError("缺少 #121「第1封」标注，无法处理首封邮件")
        if not list_shape:
            raise RuntimeError("缺少 #121「邮件清单2」标注，无法遍历邮件清单")

        processed_count = 0
        seen_count = 0
        scroll_count = 0
        scan_started_at = time.monotonic()
        empty_pages = 0
        max_scrolls = 80
        configured_max_actions = max_actions is not None
        max_actions = max(1, min(int(max_actions or 200), 200))
        max_empty_pages = 5
        mode = str(scan_mode or "incremental").strip().lower()
        full_scan = mode in {"full", "full_scan", "observe", "observe_only"}
        target_title = str(target_title or "").strip()
        target_time_text = self._normalize_mail_time_text(str(target_time_text or "").strip())
        target_requested = bool(target_title or target_time_text)
        allowed_policies = (set(action_policies or {"claim", "delete"}) & {"claim", "delete"}) or {"claim", "delete"}
        pending_actions = self._pending_runtime_mail_action_count(allowed_policies=allowed_policies) if action_enabled else 0
        if action_enabled and (pending_actions > 0 or full_scan):
            max_scrolls = min(max_scrolls, 24)
            max_scan_seconds = 420.0 if full_scan else 300.0
        elif not action_enabled and full_scan:
            max_scrolls = min(max_scrolls, 16)
            max_scan_seconds = 360.0
        else:
            max_scan_seconds = 0.0
        scan_state = self._read_mail_scan_state()
        watermark_time = "" if full_scan else str(scan_state.get("confirmed_time_bucket") or "")
        top_time = ""
        crossed_watermark = not bool(watermark_time)
        scan_truncated = False
        runtime_missing_rows: list[dict[str, str]] = []
        runtime_missing_traces: list[dict[str, Any]] = []
        if action_enabled:
            with self._lock:
                self._log_locked("info", f"邮件_历史扫描：runtime 待处理邮件 {pending_actions} 封")
                if target_requested:
                    target_parts = []
                    if target_title:
                        target_parts.append(f"标题={target_title}")
                    if target_time_text:
                        target_parts.append(f"时间={target_time_text}")
                    self._log_locked("info", f"邮件_历史扫描：本轮只处理目标邮件：{'，'.join(target_parts)}")
            if pending_actions <= 0 and not full_scan and not target_requested:
                with self._lock:
                    self._log_locked("success", "邮件_历史扫描：runtime 无待处理邮件，跳过动作扫描")
                return "success"
        if watermark_time:
            with self._lock:
                self._log_locked("info", f"邮件_历史扫描：增量扫描水位 {watermark_time}，需扫到更早时间才可停止")
        else:
            with self._lock:
                self._log_locked("info", "邮件_历史扫描：未建立增量水位，本轮按深扫建立水位")
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        while scroll_count <= max_scrolls and processed_count < max_actions:
            self._raise_if_stopped(stop_event)
            if max_scan_seconds > 0 and time.monotonic() - scan_started_at >= max_scan_seconds:
                scan_truncated = True
                with self._lock:
                    self._log_locked(
                        "info",
                        f"邮件_历史扫描：动作扫描达到内部时间预算 {max_scan_seconds:.0f}s，提前收尾",
                    )
                break
            frame = runtime.cur_frame(update=True)
            rows = self._recognize_visible_mail_rows(ctx, image121, frame)
            action_candidate: dict[str, Any] | None = None
            game_first_candidate: dict[str, Any] | None = None
            for row in rows:
                self._prepare_mail_row_policy(row, action_enabled=action_enabled, action_policies=allowed_policies)
                if row.get("runtime_match") == "missing" and len(runtime_missing_rows) < 20:
                    missing_item = {
                        "title": str(row.get("title") or ""),
                        "time_text": str(row.get("time_text") or ""),
                        "reason": str(row.get("runtime_missing_reason") or ""),
                    }
                    runtime_missing_rows.append(
                        missing_item
                    )
                    if len(runtime_missing_traces) < 8:
                        runtime_missing_traces.append(self._trace_mail_runtime_gap(missing_item))
                if target_requested and not self._mail_row_matches_target(row, target_title=target_title, target_time_text=target_time_text):
                    row["policy"] = ""
                seen_count += 1
                top_time = top_time or str(row.get("time_text") or "")
                if watermark_time and self._mail_time_is_older_than(str(row.get("time_text") or ""), watermark_time):
                    crossed_watermark = True
                if action_candidate is None and row.get("policy"):
                    action_candidate = row
                if (
                    game_first
                    and action_enabled
                    and game_first_candidate is None
                    and row.get("runtime_match") == "missing"
                    and not row.get("policy")
                ):
                    game_first_candidate = row
            if rows:
                row_summary = "；".join(
                    f"{str(row.get('title') or '')[:18]}|{row.get('time_text') or '-'}|{row.get('policy') or '-'}|{row.get('runtime_match') or '-'}|lock={row.get('list_lock_score', '-')}"
                    for row in rows[:6]
                )
                with self._lock:
                    self._log_locked("detail", f"邮件_历史扫描：当前页重新 OCR 识别 {len(rows)} 行：{row_summary}")
            if action_candidate is not None:
                action_status = self._process_mail_row(ctx, stop_event, image121, action_candidate)
                status = (yield from action_status) if isinstance(action_status, GeneratorType) else action_status
                if status == "processed":
                    processed_count += 1
                    if target_requested:
                        break
                    if not full_scan and self._pending_runtime_mail_action_count(allowed_policies=allowed_policies) <= 0:
                        with self._lock:
                            self._log_locked("success", "邮件_历史扫描：runtime 待处理邮件已清零，停止扫描")
                        break
                    continue
            if game_first_candidate is not None:
                action_status = self._process_mail_row_by_detail(
                    ctx,
                    stop_event,
                    image121,
                    game_first_candidate,
                    allowed_policies=allowed_policies,
                )
                status = (yield from action_status) if isinstance(action_status, GeneratorType) else action_status
                if status == "processed":
                    processed_count += 1
                    if target_requested:
                        break
                    continue
            if action_enabled and not full_scan and not target_requested and self._pending_runtime_mail_action_count(allowed_policies=allowed_policies) <= 0:
                break

            if watermark_time and crossed_watermark:
                with self._lock:
                    self._log_locked("success", f"邮件_历史扫描：已扫到早于水位 {watermark_time} 的邮件，增量段完整接回")
                break

            if rows:
                empty_pages = 0
            else:
                empty_pages += 1
                if empty_pages >= max_empty_pages:
                    break
            scroll_count += 1
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"邮件_历史扫描：邮件清单向下滚动 {scroll_count}",
                    phase="mail_claim_scroll_list",
                    current_scene=121,
                )
                self._log_locked("action", f"邮件_历史扫描：当前页无可处理邮件，滚动邮件清单2 {scroll_count}")
            changed = yield from self._scroll_shape_content_changed(ctx, image121, list_shape, stop_event)
            if not changed:
                break

        if processed_count >= max_actions and not configured_max_actions:
            raise RuntimeError(f"邮件_历史扫描：达到单轮处理上限 {max_actions}，为避免异常循环已停止")
        if configured_max_actions and processed_count >= max_actions:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"邮件_历史扫描：达到本轮指定处理数 {max_actions}，见到 {seen_count} 封，处理 {processed_count} 封",
                    phase="mail_claim_done",
                    current_scene=121,
                )
                self._log_locked("success", f"邮件_历史扫描：达到本轮指定处理数 {max_actions}，见到 {seen_count} 封，处理 {processed_count} 封")
            return "success"
        if target_requested and processed_count > 0:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"邮件_历史扫描：目标邮件处理完成，见到 {seen_count} 封，处理 {processed_count} 封",
                    phase="mail_claim_done",
                    current_scene=121,
                )
                self._log_locked("success", f"邮件_历史扫描：目标邮件处理完成，见到 {seen_count} 封，处理 {processed_count} 封")
            return "success"
        pending_after_scan = self._pending_runtime_mail_action_count(allowed_policies=allowed_policies) if action_enabled else 0
        if action_enabled and full_scan and pending_after_scan > 0 and not scan_truncated:
            marked_count = self._mark_pending_runtime_mail_actions_not_visible(
                reason=f"full_scan_seen={seen_count}; processed={processed_count}; scrolls={scroll_count}",
                allowed_policies=allowed_policies,
            )
            with self._lock:
                self._log_locked(
                    "info",
                    f"邮件_历史扫描：完整扫描未见 {marked_count} 封待处理邮件，标记为 missing_from_list",
                )
        elif action_enabled and full_scan and pending_after_scan > 0 and scan_truncated:
            with self._lock:
                self._log_locked(
                    "info",
                    f"邮件_历史扫描：本轮扫描被时间预算截断，仍有 {pending_after_scan} 封待处理邮件，不标记 missing_from_list",
                )
        if action_enabled and full_scan and runtime_missing_rows:
            sample_text = "；".join(
                f"{item['title']}|{item['time_text']}|{item['reason']}"
                for item in runtime_missing_rows[:8]
            )
            self._write_mail_scan_state(
                {
                    **scan_state,
                    "status": "runtime_gap",
                    "last_scan_at": _behavior_tree_runtime._now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_seen_top_time": top_time,
                    "last_seen_count": seen_count,
                    "last_processed_count": processed_count,
                    "runtime_missing_count": len(runtime_missing_rows),
                    "runtime_missing_rows": runtime_missing_rows,
                    "runtime_missing_traces": runtime_missing_traces,
                    "runtime_gap_history": self._mail_runtime_gap_history(scan_state, runtime_missing_rows, runtime_missing_traces),
                    "message": "可见邮件缺可用 Runtime 事实，标题+时间和标题降级均未匹配",
                }
            )
            with self._lock:
                self._log_locked(
                    "error" if fail_on_runtime_gap else "info",
                    (
                        f"邮件_历史扫描：发现 {len(runtime_missing_rows)} 个可见邮件缺 Runtime 事实，"
                        f"{'本轮不能证明已清干净' if fail_on_runtime_gap else '已按游戏画面优先策略记录并继续'}：{sample_text}"
                    ),
                )
            if fail_on_runtime_gap:
                raise RuntimeError(f"邮件_历史扫描：发现 {len(runtime_missing_rows)} 个可见邮件缺 Runtime 事实，请先修复 Runtime 解析缺口：{sample_text}")
        if action_enabled and watermark_time and not crossed_watermark:
            self._write_mail_scan_state(
                {
                    **scan_state,
                    "status": "gap_risk",
                    "last_scan_at": _behavior_tree_runtime._now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_seen_top_time": top_time,
                    "last_seen_count": seen_count,
                    "last_processed_count": processed_count,
                    "message": f"未接回增量水位 {watermark_time}",
                }
            )
            raise RuntimeError(f"邮件_历史扫描：未接回增量水位 {watermark_time}，可能存在中间遗漏，请继续遍历或用 full_scan/observe_only 补洞")
        if top_time:
            self._write_mail_scan_state(
                {
                    "runtime_gap_history": scan_state.get("runtime_gap_history") or [],
                    "status": "confirmed",
                    "confirmed_time_bucket": top_time,
                    "confirmed_at": _behavior_tree_runtime._now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_scan_mode": "full" if full_scan else "incremental",
                    "last_seen_count": seen_count,
                    "last_processed_count": processed_count,
                    "previous_confirmed_time_bucket": scan_state.get("confirmed_time_bucket") or "",
                }
            )

        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_历史扫描：完成，见到 {seen_count} 封，处理 {processed_count} 封，Runtime 缺失 {len(runtime_missing_rows)} 封",
                phase="mail_claim_done",
                current_scene=121,
            )
            self._log_locked("success", f"邮件_历史扫描：完成，见到 {seen_count} 封，处理 {processed_count} 封，Runtime 缺失 {len(runtime_missing_rows)} 封")
        return "success"

    def _trace_mail_runtime_gap(self, row: dict[str, str]) -> dict[str, Any]:
        try:
            return trace_runtime_mail_gap(
                _db_engine,
                title=str(row.get("title") or ""),
                time_text=str(row.get("time_text") or ""),
                window_minutes=8,
                max_sources=24,
            )
        except Exception as exc:
            return {
                "title": str(row.get("title") or ""),
                "time_text": str(row.get("time_text") or ""),
                "diagnosis": "trace_error",
                "error": str(exc),
            }

    def _recognize_visible_mail_rows(
        self,
        ctx: dict[str, Any],
        image121: dict[str, Any],
        frame: str,
    ) -> list[dict[str, Any]]:
        started_at = time.monotonic()
        runtime = self._fanxiu_runtime(ctx, frame_data_url=frame)
        lines = runtime.ocr_fragments_in_shapes(image121, ("第1封", "邮件清单2"), frame_data_url=frame)
        first_rows = self._mail_rows_in_shape(lines, image121, "第1封")
        list_rows = self._mail_rows_in_shape(lines, image121, "邮件清单2")
        template_shape = self._find_shape(image121, "邮件模板")
        if isinstance(template_shape, dict):
            self._annotate_mail_rows_visual_slots(first_rows, image121, template_shape, "第1封")
            self._annotate_mail_rows_visual_slots(list_rows, image121, template_shape, "邮件清单2")
        rows = self._merge_visible_mail_rows_by_position(first_rows, list_rows)
        self._annotate_mail_rows_list_state(ctx, image121, frame, rows)
        elapsed = time.monotonic() - started_at
        self._log("detail", f"邮件_历史扫描：当前页 OCR+行解析耗时 {elapsed:.1f}s，识别 {len(rows)} 行")
        return rows

    def _compare_visible_mail_row_with_runtime_store(self, row: dict[str, Any]) -> dict[str, Any]:
        """只读判断一封游戏可见邮件是否存在对应 runtime 事实。

        完整性口径是 A - B：游戏当前可见邮件为 A，runtime 数据库为 B。
        同标题的历史邮件不能证明当前这封已入库，因此这里明确禁用
        title-only 降级；必须在相同分钟内找到标题相符的 runtime 记录。
        """

        title = str(row.get("title") or "").strip()
        time_text = self._normalize_mail_time_text(str(row.get("time_text") or ""))
        compared = dict(row)
        compared["time_text"] = time_text
        if not title or not self._is_valid_mail_time_text(time_text):
            compared.update(
                {
                    "runtime_match": "unresolved_visible_key",
                    "runtime_missing_reason": "invalid_title_or_time",
                    "mail_key": "",
                }
            )
            return compared
        records = self._find_runtime_mail_records_for_visible_row(
            title,
            time_text,
            allow_title_only=False,
        )
        records = [record for record in records if self._mail_record_matches_visible_time(record, time_text)]
        if records:
            compared.update(
                {
                    "runtime_match": "matched",
                    "runtime_missing_reason": "",
                    "mail_key": str(records[0].mail_key or ""),
                }
            )
            return compared
        compared.update(
            {
                "runtime_match": "missing",
                "runtime_missing_reason": self._mail_row_runtime_missing_reason(title, time_text),
                "mail_key": "",
            }
        )
        return compared

    def scan_mail_inventory_readonly(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        max_scrolls: int = 40,
        settle_seconds: float = 2.0,
        reset_to_top: bool = True,
    ):
        """滚动读取 #121 邮件清单并返回 A、A-B 和时间降序证据。

        该方法不会打开邮件详情，不会领取、删除或写邮件扫描水位；唯一
        的界面动作是滚动“邮件清单2”。调用前必须已经位于 #121。
        """

        image121 = ctx.get("images", {}).get(121)
        if not isinstance(image121, dict):
            raise RuntimeError("缺少 #121 邮件帧标注，无法只读扫描邮件")
        list_shape = self._find_shape(image121, "邮件清单2") or self._find_shape(image121, "邮件清单")
        if not list_shape:
            raise RuntimeError("缺少 #121「邮件清单2」标注，无法只读扫描邮件")
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        observations: list[dict[str, Any]] = []
        seen_observation_keys: set[tuple[str, str]] = set()
        windows: list[dict[str, Any]] = []
        descending_violations: list[dict[str, Any]] = []
        adjacency_intervals: list[dict[str, str]] = []
        reached_end = False
        scroll_limit = max(0, min(int(max_scrolls), 80))
        reset_scroll_count = 0

        if reset_to_top:
            for _ in range(scroll_limit + 1):
                self._raise_if_stopped(stop_event)
                changed = yield from self._scroll_shape_content_changed(
                    ctx,
                    image121,
                    list_shape,
                    stop_event,
                    reverse=True,
                    settle_seconds=max(0.5, float(settle_seconds)),
                )
                if not changed:
                    break
                reset_scroll_count += 1

        for window_index in range(scroll_limit + 1):
            self._raise_if_stopped(stop_event)
            frame = runtime.cur_frame(update=True)
            visible_rows = self._recognize_visible_mail_rows(ctx, image121, frame)
            compared_rows = [self._compare_visible_mail_row_with_runtime_store(row) for row in visible_rows]
            valid_times: list[tuple[str, Any]] = []
            for row in compared_rows:
                title = _behavior_tree_runtime.normalize_fanxiu_mail_title(str(row.get("title") or ""))
                time_text = self._normalize_mail_time_text(str(row.get("time_text") or ""))
                key = (title, time_text)
                if title and key not in seen_observation_keys:
                    seen_observation_keys.add(key)
                    observations.append(row)
                parsed = parse_data_annotation_task_time(time_text)
                if parsed is not None:
                    valid_times.append((time_text, parsed))
            for (newer_text, newer), (older_text, older) in zip(valid_times, valid_times[1:]):
                if newer < older:
                    descending_violations.append(
                        {
                            "window_index": window_index,
                            "newer_time_text": newer_text,
                            "older_time_text": older_text,
                        }
                    )
            adjacency_intervals.extend(self._visible_mail_adjacency_intervals(compared_rows))
            windows.append(
                {
                    "window_index": window_index,
                    "row_count": len(compared_rows),
                    "rows": compared_rows,
                }
            )
            if window_index >= scroll_limit:
                break
            changed = yield from self._scroll_shape_content_changed(
                ctx,
                image121,
                list_shape,
                stop_event,
                settle_seconds=max(0.5, float(settle_seconds)),
            )
            if not changed:
                reached_end = True
                break

        # A 只包含具备“标题+时间”身份的游戏邮件。滚动重叠区可能用不同
        # OCR 标题再次读到同一封邮件；匹配成功时优先用 runtime mail_key
        # 去重，缺包项才退回标题+时间。无时间文本只保留为 OCR 诊断观察，
        # 不能擅自放进 A-B。
        inventory: list[dict[str, Any]] = []
        inventory_keys: set[tuple[str, str]] = set()
        for row in observations:
            if row.get("runtime_match") == "unresolved_visible_key":
                continue
            mail_key = str(row.get("mail_key") or "").strip()
            key = (
                "mail_key" if mail_key else _behavior_tree_runtime.normalize_fanxiu_mail_title(str(row.get("title") or "")),
                mail_key or self._normalize_mail_time_text(str(row.get("time_text") or "")),
            )
            if key in inventory_keys:
                continue
            inventory_keys.add(key)
            inventory.append(row)
        missing = [row for row in inventory if row.get("runtime_match") == "missing"]
        unresolved = [row for row in observations if row.get("runtime_match") == "unresolved_visible_key"]
        unresolved = [
            row
            for row in unresolved
            if not any(
                self._mail_title_similarity(str(row.get("title") or ""), str(valid.get("title") or "")) >= 0.86
                for valid in inventory
            )
        ]
        unique_intervals = list(
            {
                (item["newer_time_text"], item["older_time_text"]): item
                for item in adjacency_intervals
            }.values()
        )
        return {
            "ok": True,
            "read_only": True,
            "reset_scroll_count": reset_scroll_count,
            "reached_end": reached_end,
            "window_count": len(windows),
            "inventory_count": len(inventory),
            "matched_count": sum(1 for row in inventory if row.get("runtime_match") == "matched"),
            "a_minus_b_count": len(missing),
            "a_minus_b": missing,
            "unresolved_visible_key_count": len(unresolved),
            "unresolved_visible_keys": unresolved,
            "descending_violation_count": len(descending_violations),
            "descending_violations": descending_violations,
            "adjacency_intervals": unique_intervals,
            "inventory": inventory,
            "observations": observations,
            "windows": windows,
        }

    def _read_mail_scan_state(self) -> dict[str, Any]:
        payload = _read_data_annotation_json(_data_annotation_mail_scan_state_path(), {})
        return payload if isinstance(payload, dict) else {}

    def _write_mail_scan_state(self, payload: dict[str, Any]) -> None:
        _write_data_annotation_json(_data_annotation_mail_scan_state_path(), payload)

    def _mail_runtime_gap_history(
        self,
        scan_state: dict[str, Any],
        rows: list[dict[str, str]],
        traces: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        history = [item for item in scan_state.get("runtime_gap_history") or [] if isinstance(item, dict)]
        history.append(
            {
                "recorded_at": _behavior_tree_runtime._now().strftime("%Y-%m-%d %H:%M:%S"),
                "rows": rows,
                "traces": traces,
            }
        )
        return history[-20:]

    def _pending_runtime_mail_action_count(self, *, allowed_policies: set[str] | None = None) -> int:
        policies = (set(allowed_policies or {"claim"}) & {"claim"}) or {"claim"}
        records = pending_runtime_mail_records(_db_engine)
        groups: dict[tuple[str, str], list[Any]] = {}
        for record in records:
            key = (str(record.normalized_title or record.title or "").strip(), str(record.create_time_text or "").strip())
            if key[0] and key[1]:
                groups.setdefault(key, []).append(record)
        return sum(
            1
            for group in groups.values()
            if self._visible_runtime_mail_group_action_policy(group) in policies
        )

    def _runtime_mail_action_confirmed_for_row(self, row: dict[str, Any], policy: str) -> bool:
        """只用服务端动作事实确认一次邮件操作，不把 UI 点击当作成功。"""
        if policy not in {"claim", "delete"}:
            return False
        record = self._find_runtime_mail_record(
            str(row.get("title") or ""),
            str(row.get("time_text") or ""),
        )
        if record is None:
            return False
        expected_status = "claimed" if policy == "claim" else "deleted"
        if str(getattr(record, "status", "") or "").strip().lower() == expected_status:
            return True
        expected_protocol = "SM_GetMailReward" if policy == "claim" else "SM_DeleteMail"
        evidence = record.evidence if isinstance(record.evidence, dict) else {}
        return any(
            isinstance(event, dict) and str(event.get("protocol") or "") == expected_protocol
            for event in evidence.get("mail_actions") or []
        )

    def _mark_pending_runtime_mail_actions_not_visible(self, *, reason: str, allowed_policies: set[str] | None = None) -> int:
        now_text = _behavior_tree_runtime._now().strftime("%Y-%m-%d %H:%M:%S")
        marked = 0
        policies = (set(allowed_policies or {"claim"}) & {"claim"}) or {"claim"}
        records = pending_runtime_mail_action_candidates(_db_engine, policies)
        for record in records:
            if fanxiu_mail_action_policy_for_record(record) not in policies:
                continue
            try:
                mark_runtime_mail_record_missing_from_list(_db_engine, record, reason=reason, marked_at=now_text)
            except OperationalError as exc:
                if "database is locked" not in str(exc).lower():
                    raise
                self._log(
                    "warning",
                    "邮件_历史扫描：标记未出现在列表中的邮件时遇到数据库锁，跳过本条状态标记",
                )
                continue
            marked += 1
        return marked

    def _mail_time_is_older_than(self, current_time_text: str, watermark_time_text: str) -> bool:
        current = parse_data_annotation_task_time(_behavior_tree_runtime.normalize_fanxiu_mail_time_text(current_time_text))
        watermark = parse_data_annotation_task_time(_behavior_tree_runtime.normalize_fanxiu_mail_time_text(watermark_time_text))
        return current is not None and watermark is not None and current < watermark

    def _prepare_and_maybe_process_mail_row(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image121: dict[str, Any],
        row: dict[str, Any],
        *,
        action_enabled: bool = True,
        action_policies: set[str] | None = None,
        target_title: str = "",
        target_time_text: str = "",
    ) -> str:
        self._prepare_mail_row_policy(row, action_enabled=action_enabled, action_policies=action_policies)
        if (target_title or target_time_text) and not self._mail_row_matches_target(row, target_title=target_title, target_time_text=target_time_text):
            row["policy"] = ""
        if not row.get("policy"):
            return "seen"
        return (yield from self._process_mail_row(ctx, stop_event, image121, row))

    def _mail_row_matches_target(self, row: dict[str, Any], *, target_title: str, target_time_text: str) -> bool:
        if target_title:
            observed_title = str(row.get("title") or "").strip()
            if self._mail_title_similarity(observed_title, target_title) < 0.86:
                return False
        if target_time_text:
            observed_time = self._normalize_mail_time_text(str(row.get("time_text") or ""))
            if observed_time != self._normalize_mail_time_text(target_time_text):
                return False
        return True

    def _prepare_mail_row_policy(
        self,
        row: dict[str, Any],
        *,
        action_enabled: bool = True,
        action_policies: set[str] | None = None,
    ) -> None:
        title = str(row.get("title") or "").strip()
        time_text = self._normalize_mail_time_text(str(row.get("time_text") or ""))
        row["policy"] = ""
        ui_status = self._normalize_mail_row_status(str(row.get("status") or ""))
        if not ui_status and bool(row.get("is_read")):
            ui_status = "已阅"
        if not ui_status and bool(row.get("list_has_lock")):
            ui_status = "锁定"
        row["status"] = ui_status or "无"
        if ui_status == "已阅":
            row["mail_key"] = ""
            row["policy"] = ""
            row["runtime_match"] = "ui_skipped"
            row["runtime_missing_reason"] = ""
            return
        if not self._is_valid_mail_time_text(time_text):
            row["time_text"] = ""
            row["mail_key"] = ""
            return
        row["time_text"] = time_text
        records = self._find_runtime_mail_records_for_visible_row(title, time_text)
        active_records = [
            record
            for record in records
            if not self._mail_runtime_record_is_terminal(record)
        ]
        # Same-title/same-minute mails are common. A terminal runtime may refer
        # to a duplicate that already disappeared from the UI; bind the
        # visible row to the next active runtime instead of repeatedly rewriting
        # the newest terminal record.
        record = active_records[0] if active_records else (records[0] if records else None)
        row["mail_key"] = str(record.mail_key or "") if record else ""
        if records:
            row["runtime_match"] = "matched" if any(self._mail_record_matches_visible_time(record, time_text) for record in records) else "title_only"
            row["runtime_missing_reason"] = ""
        else:
            row["runtime_match"] = "missing"
            row["runtime_missing_reason"] = self._mail_row_runtime_missing_reason(title, time_text)
        if action_enabled:
            # These recurring sect activity mails are known reward mails.  Their
            # runtime state can lag behind the visible list, so title recognition
            # is the authoritative claim rule for them.
            policy = (
                "claim"
                if self._mail_title_forces_claim(title, records)
                else self._mail_row_runtime_action_policy(title, time_text, records=records)
            )
            allowed_policies = (set(action_policies or {"claim"}) & {"claim"}) or {"claim"}
            row["policy"] = policy if policy in allowed_policies else ""

    def _mail_title_forces_claim(self, title: str, records: list[Any]) -> bool:
        active_records = [record for record in records if not self._mail_runtime_record_is_terminal(record)]
        if not active_records:
            return False
        return all(
            fanxiu_mail_title_force_claim_allowed(
                title,
                fanxiu_mail_rewards_from_payload(getattr(record, "payload", None)),
            )
            for record in active_records
        )

    def _mail_row_runtime_action_policy(self, title: str, time_text: str, *, records: list[Any] | None = None) -> str:
        if records is None:
            records = self._find_runtime_mail_records_for_visible_row(title, time_text)
        return self._visible_runtime_mail_group_action_policy(records, time_text=time_text)

    def _mail_row_runtime_missing_reason(self, title: str, time_text: str) -> str:
        normalized_title = _behavior_tree_runtime.normalize_fanxiu_mail_title(title)
        normalized_time = _behavior_tree_runtime.normalize_fanxiu_mail_time_text(time_text)
        if not normalized_title or not normalized_time:
            return "invalid_title_or_time"
        same_title = runtime_mail_records_same_title(_db_engine, normalized_title, limit=5)
        same_time = runtime_mail_records_same_time(_db_engine, normalized_time, limit=5)
        if same_title:
            times = ",".join(str(record.create_time_text or "") for record in same_title[:3])
            return f"same_title_without_time:{times}"
        if same_time:
            titles = ",".join(str(record.title or record.normalized_title or "") for record in same_time[:3])
            return f"same_time_without_title:{titles}"
        return "no_runtime_fact"

    def _visible_runtime_mail_group_action_policy(self, records: list[Any], *, time_text: str = "") -> str:
        active_records = [
            record
            for record in records
            if not self._mail_runtime_record_is_terminal(record)
        ]
        if not active_records:
            return ""
        policies = {self._visible_runtime_mail_action_policy(record) for record in active_records}
        policies.discard("")
        if len(policies) != 1:
            return ""
        policy = next(iter(policies))
        for record in active_records:
            if self._visible_runtime_mail_action_policy(record) != policy:
                return ""
        return policy

    @staticmethod
    def _mail_runtime_record_is_terminal(record: Any | None) -> bool:
        status = str(getattr(record, "status", "") or "").strip().lower()
        return status in {"claimed", "deleted", "missing_from_list"}

    def _runtime_mail_record_initially_claimable(self, record: Any | None) -> bool:
        if record is None:
            return False
        return str(getattr(record, "status", "") or "").strip() == "可领"

    def _find_runtime_mail_records_for_visible_row(
        self,
        title: str,
        time_text: str,
        *,
        allow_title_only: bool = True,
    ) -> list[Any]:
        normalized_title = _behavior_tree_runtime.normalize_fanxiu_mail_title(title)
        normalized_time = _behavior_tree_runtime.normalize_fanxiu_mail_time_text(time_text)
        if not normalized_title or not normalized_time:
            return []
        try:
            exact = runtime_mail_records_for_visible_row_exact(_db_engine, normalized_title=normalized_title, normalized_time=normalized_time)
            if exact:
                return list(exact)
            same_time = runtime_mail_records_for_visible_row_same_time(_db_engine, normalized_time)
        except OperationalError as exc:
            if not self._mail_runtime_store_operational_error_is_transient(exc):
                raise
            self._log("warning", f"邮件_历史扫描：runtime 查找遇到瞬态数据库异常，跳过本行匹配：{exc}")
            return []
        observed_key = self._mail_title_similarity_key(title)
        if len(observed_key) < 3:
            return self._find_runtime_mail_records_by_title_only(title) if allow_title_only else []
        scored: list[tuple[float, Any]] = []
        for record in same_time:
            score = self._mail_title_similarity(title, str(record.title or record.normalized_title or ""))
            if score > 0:
                scored.append((score, record))
        if not scored:
            return self._find_runtime_mail_records_by_title_only(title) if allow_title_only else []
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score = scored[0][0]
        threshold = 0.58 if len(observed_key) >= 5 else 0.72
        if best_score < threshold:
            return self._find_runtime_mail_records_by_title_only(title) if allow_title_only else []
        fuzzy_matches = [record for score, record in scored if score >= best_score - 0.06]
        if fuzzy_matches:
            return fuzzy_matches
        return self._find_runtime_mail_records_by_title_only(title) if allow_title_only else []

    def _find_runtime_mail_records_by_title_only(self, title: str) -> list[Any]:
        normalized_title = _behavior_tree_runtime.normalize_fanxiu_mail_title(title)
        if not normalized_title:
            return []
        try:
            exact = runtime_mail_records_by_normalized_title(_db_engine, normalized_title, limit=20)
            if exact:
                return list(exact)
        except OperationalError as exc:
            if not self._mail_runtime_store_operational_error_is_transient(exc):
                raise
            self._log("warning", f"邮件_历史扫描：runtime 标题查找遇到瞬态数据库异常，跳过标题匹配：{exc}")
            return []
        observed_key = self._mail_title_similarity_key(title)
        if len(observed_key) < 5:
            return []
        try:
            recent = recent_runtime_mail_records(_db_engine, limit=200)
        except OperationalError as exc:
            if not self._mail_runtime_store_operational_error_is_transient(exc):
                raise
            self._log("warning", f"邮件_历史扫描：runtime 近期记录查找遇到瞬态数据库异常，跳过标题匹配：{exc}")
            return []
        scored: list[tuple[float, Any]] = []
        for record in recent:
            score = self._mail_title_similarity(title, str(record.title or record.normalized_title or ""))
            if score >= 0.86:
                scored.append((score, record))
        if not scored:
            return []
        scored.sort(key=lambda item: (item[0], float(item[1].last_seen_at or item[1].updated_at or 0)), reverse=True)
        best_score = scored[0][0]
        return [record for score, record in scored if score >= best_score - 0.03]

    def _find_runtime_mail_record(
        self,
        title: str,
        time_text: str,
        *,
        action_policies: set[str] | None = None,
    ) -> Any | None:
        normalized_title = _behavior_tree_runtime.normalize_fanxiu_mail_title(title)
        normalized_time = _behavior_tree_runtime.normalize_fanxiu_mail_time_text(time_text)
        if not normalized_title or not normalized_time:
            return None
        try:
            record = find_runtime_mail_record_exact(_db_engine, normalized_title=normalized_title, normalized_time=normalized_time)
            if record:
                return record
            record = find_runtime_mail_record_by_raw_title(_db_engine, title=title, normalized_time=normalized_time)
            if record:
                return record
            time_candidates = runtime_mail_records_for_visible_row_same_time(_db_engine, normalized_time)
        except OperationalError as exc:
            if not self._mail_runtime_store_operational_error_is_transient(exc):
                raise
            self._log("warning", f"邮件_历史扫描：runtime 记录查找遇到瞬态数据库异常，跳过状态回写匹配：{exc}")
            return None
        fuzzy = self._select_runtime_mail_record_by_fuzzy_title(
            title,
            time_candidates,
            action_policies=action_policies,
        )
        if fuzzy:
            return fuzzy
        return None

    @staticmethod
    def _mail_runtime_store_operational_error_is_transient(exc: OperationalError) -> bool:
        message = str(exc).lower()
        return "database is locked" in message or "database schema has changed" in message

    def _visible_runtime_mail_action_policy(self, record: Any | None) -> str:
        if record is None:
            return ""
        status = str(record.status or "").strip().lower()
        if self._mail_runtime_record_is_terminal(record):
            return ""
        if status in {"claim_requested", "delete_requested"}:
            retry_policy = status.removesuffix("_requested")
            if not self._mail_requested_action_retryable(record, retry_policy):
                return ""
            return retry_policy if retry_policy == self._mail_initial_reward_action_policy(record) else ""
        return self._mail_initial_reward_action_policy(record)

    def _mail_initial_reward_action_policy(self, record: Any | None) -> str:
        if record is None:
            return ""
        explicit = str(getattr(record, "action_policy", "") or "").strip().lower()
        if explicit in {"claim", "delete"}:
            return explicit
        return fanxiu_mail_action_policy_for_rewards(fanxiu_mail_rewards_from_payload(getattr(record, "payload", None)))

    def _mail_requested_action_retryable(self, record: Any, policy: str) -> bool:
        if policy not in {"claim", "delete"}:
            return False
        evidence = record.evidence if isinstance(record.evidence, dict) else {}
        requested_action = str(evidence.get("runtime_requested_action") or "").strip().lower()
        if requested_action and requested_action != policy:
            return False
        requested_at = str(evidence.get("runtime_action_requested_at") or "").strip()
        try:
            requested_ts = datetime.strptime(requested_at, "%Y-%m-%d %H:%M:%S").timestamp() if requested_at else 0.0
        except ValueError:
            requested_ts = 0.0
        if requested_ts and time.time() - requested_ts < 60.0:
            return False
        server_protocol = "SM_GetMailReward" if policy == "claim" else "SM_DeleteMail"
        for event in evidence.get("mail_actions") or []:
            if isinstance(event, dict) and str(event.get("protocol") or "") == server_protocol:
                return False
        return True

    def _mail_row_has_attachment_hint(self, row: dict[str, Any]) -> bool:
        return bool(row.get("list_has_lock") or row.get("has_attachment_hint"))

    def _select_runtime_mail_record_by_fuzzy_title(
        self,
        observed_title: str,
        candidates: list[Any],
        *,
        action_policies: set[str] | None = None,
    ) -> Any | None:
        observed_key = self._mail_title_similarity_key(observed_title)
        if len(observed_key) < 3:
            return None
        scored: list[tuple[float, Any, str]] = []
        for candidate in candidates:
            candidate_title = str(candidate.title or candidate.normalized_title or "")
            score = self._mail_title_similarity(observed_title, candidate_title)
            if score <= 0:
                continue
            scored.append((score, candidate, self._visible_runtime_mail_action_policy(candidate)))
        if not scored:
            return None
        scored.sort(key=lambda item: (item[0], float(item[1].last_seen_at or item[1].updated_at or 0)), reverse=True)
        best_score, best_record, best_policy = scored[0]
        threshold = 0.58 if len(observed_key) >= 5 else 0.72
        if best_score < threshold:
            return None
        if action_policies is not None:
            allowed_policies = (set(action_policies or set()) & {"claim", "delete"}) or {"claim", "delete"}
            if best_policy not in allowed_policies:
                return None
            close_candidates = [item for item in scored if item[0] >= best_score - 0.06]
            close_policies = {policy for _, _, policy in close_candidates}
            if close_policies != {best_policy}:
                return None
            return best_record
        close_candidates = [item for item in scored if item[0] >= best_score - 0.06]
        close_titles = {self._mail_title_similarity_key(str(record.title or record.normalized_title or "")) for _, record, _ in close_candidates}
        if len(close_titles) > 1:
            return None
        return best_record

    def _mail_title_similarity_key(self, value: str) -> str:
        text = _behavior_tree_runtime.normalize_fanxiu_mail_title(value)
        return re.sub(r"[^\u4e00-\u9fff0-9A-Za-z]", "", text)

    def _mail_title_similarity(self, left: str, right: str) -> float:
        left_key = self._mail_title_similarity_key(left)
        right_key = self._mail_title_similarity_key(right)
        if not left_key or not right_key:
            return 0.0
        if left_key == right_key:
            return 1.0
        base = difflib.SequenceMatcher(None, left_key, right_key).ratio()
        shorter, longer = sorted((left_key, right_key), key=len)
        if len(shorter) >= 3 and shorter in longer:
            base = max(base, len(shorter) / max(len(longer), 1))
        return float(base)

    def _mail_record_matches_visible_time(self, record: Any, time_text: str) -> bool:
        return _behavior_tree_runtime.normalize_fanxiu_mail_time_text(str(record.create_time_text or "")) == _behavior_tree_runtime.normalize_fanxiu_mail_time_text(time_text)

    def _find_runtime_mail_key(self, title: str, time_text: str) -> str:
        record = self._find_runtime_mail_record(title, time_text)
        return str(record.mail_key or "") if record else ""

    def _update_runtime_mail_action_for_row(self, row: dict[str, Any], *, status: str, evidence: dict[str, Any]) -> None:
        mail_key = str(row.get("mail_key") or "").strip()
        if not mail_key:
            mail_key = self._find_runtime_mail_key(
                str(row.get("title") or ""),
                str(row.get("time_text") or ""),
            )
        if not mail_key:
            return
        try:
            update_runtime_mail_action(_db_engine, mail_key=mail_key, status=status, evidence=evidence)
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            self._log("warning", f"邮件_历史扫描：邮件状态写入遇到数据库锁，跳过本次状态标记：{mail_key}")

    def _process_mail_row_by_detail(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image121: dict[str, Any],
        row: dict[str, Any],
        *,
        allowed_policies: set[str],
    ) -> str:
        title = str(row.get("title") or "")
        if not title:
            return "seen"
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_历史扫描：打开「{title}」按详情页判断",
                phase="mail_claim_open_game_first",
                current_scene=121,
            )
            self._log_locked("action", f"邮件_历史扫描：缺 runtime，打开「{title}」按详情页判断")
        self._open_mail_row(ctx, stop_event, row)
        scene_result = self._wait_mail_detail_or_list_scene(
            ctx,
            stop_event,
            timeout=self._MAIL_DETAIL_READY_TIMEOUT_SECONDS,
            label=f"邮件_历史扫描：等待「{title}」详情",
        )
        scene_id, _score = yield from scene_result
        if scene_id == 121:
            return "seen"
        if scene_id not in {122, 123}:
            raise RuntimeError(f"邮件_历史扫描：打开「{title}」进入未知详情 #{scene_id}，为避免误操作已停止")
        actual_policy = "claim" if scene_id == 122 else "delete"
        if actual_policy not in allowed_policies:
            with self._lock:
                self._log_locked("detail", f"邮件_历史扫描：「{title}」详情为 {actual_policy}，不在本轮允许动作内，返回列表")
            yield from self._return_mail_detail_to_list(ctx, stop_event, scene_id)
            return "seen"
        action_title = "领取" if actual_policy == "claim" else "删除"
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_历史扫描：按详情页{action_title}「{title}」",
                phase=f"mail_claim_do_{actual_policy}",
                current_scene=scene_id,
            )
            self._log_locked("action", f"邮件_历史扫描：详情页确认 #{scene_id}，点击「{action_title}」：{title}")
        yield from runtime.wait_click(scene_id, action_title, timeout=8.0)
        yield from self._wait_mail_list_after_detail_action(
            ctx,
            stop_event,
            runtime,
            scene_id,
            timeout=18.0,
            label="邮件_历史扫描：返回邮件 #121",
        )
        self._update_runtime_mail_action_for_row(
            row,
            status=f"{actual_policy}_requested",
            evidence={
                "runtime_requested_action": actual_policy,
                "runtime_action_requested_at": _behavior_tree_runtime._now().strftime("%Y-%m-%d %H:%M:%S"),
                "runtime_action_source": "game_first_detail",
            },
        )
        return "processed"

    def _process_mail_row(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image121: dict[str, Any],
        row: dict[str, Any],
    ) -> str:
        title = str(row.get("title") or "")
        policy = str(row.get("policy") or "")
        if policy not in {"claim", "delete"}:
            return "seen"
        with self._lock:
            action_label = "处理"
            self._set_status_locked(
                "running",
                f"邮件_历史扫描：打开「{title}」准备{action_label}",
                phase=f"mail_claim_open_{policy}",
                current_scene=121,
            )
            self._log_locked("action", f"邮件_历史扫描：打开「{title}」准备{action_label}")
        self._open_mail_row(ctx, stop_event, row)
        scene_result = self._wait_mail_detail_or_list_scene(
            ctx,
            stop_event,
            timeout=self._MAIL_DETAIL_READY_TIMEOUT_SECONDS,
            label=f"邮件_历史扫描：等待「{title}」详情",
        )
        scene_id, _score = yield from scene_result
        if scene_id == 121:
            return "seen"
        if scene_id not in {122, 123}:
            raise RuntimeError(f"邮件_历史扫描：打开「{title}」进入未知详情 #{scene_id}，为避免误操作已停止")
        target_scene_id = scene_id
        actual_policy = "claim" if target_scene_id == 122 else "delete"
        if actual_policy != policy:
            with self._lock:
                self._log_locked(
                    "detail",
                    f"邮件_历史扫描：「{title}」列表策略={policy}，详情实际为 #{target_scene_id} {actual_policy}，按详情按钮处理",
                )
        action_title = "领取" if actual_policy == "claim" else "删除"
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_历史扫描：{action_title}「{title}」",
                phase=f"mail_claim_do_{actual_policy}",
                current_scene=target_scene_id,
            )
            self._log_locked("action", f"邮件_历史扫描：等待并点击 #{target_scene_id}「{action_title}」")
        current_scene_id, current_score, _frame, _text = self._fanxiu_runtime_scene_text(
            ctx,
            runtime,
            [target_scene_id, 121],
            update=True,
        )
        if current_scene_id == 121:
            self._log(
                "info",
                f"邮件_历史扫描：「{title}」详情页已回到列表 #121 {current_score:.0f}%，本轮跳过该行",
            )
            return "seen"
        yield from runtime.wait_click(target_scene_id, action_title, timeout=8.0)
        yield from self._wait_mail_list_after_detail_action(
            ctx,
            stop_event,
            runtime,
            target_scene_id,
            timeout=18.0,
            label="邮件_历史扫描：返回邮件 #121",
        )
        self._update_runtime_mail_action_for_row(
            row,
            status=f"{actual_policy}_requested",
            evidence={"runtime_requested_action": actual_policy, "runtime_action_requested_at": _behavior_tree_runtime._now().strftime("%Y-%m-%d %H:%M:%S")},
        )
        return "processed"

    def _probe_and_maybe_delete_mail_row(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image121: dict[str, Any],
        row: dict[str, Any],
    ) -> str:
        if self._mail_row_has_attachment_hint(row):
            return "seen"
        title = str(row.get("title") or "")
        if not title:
            return "seen"
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_历史扫描：探测「{title}」是否可删除",
                phase="mail_claim_probe_delete",
                current_scene=121,
            )
            self._log_locked("action", f"邮件_历史扫描：打开「{title}」探测无附件删除")
        self._open_mail_row(ctx, stop_event, row)
        scene_id, score = yield from self._wait_mail_detail_or_list_scene(
            ctx,
            stop_event,
            timeout=self._MAIL_DETAIL_READY_TIMEOUT_SECONDS,
            label=f"邮件_历史扫描：探测「{title}」详情",
        )
        if scene_id == 121:
            return "seen"
        if scene_id == 122:
            with self._lock:
                self._log_locked("detail", f"邮件_历史扫描：探测「{title}」进入 #122，视为有附件/可领取，返回不处理")
            yield from self._return_mail_detail_to_list(ctx, stop_event, 122)
            return "seen"
        if scene_id != 123:
            raise RuntimeError(f"邮件_历史扫描：探测「{title}」进入未知详情 #{scene_id}，为避免误操作已停止")
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_历史扫描：UI确认无附件，删除「{title}」",
                phase="mail_claim_do_delete",
                current_scene=123,
            )
            self._log_locked("action", f"邮件_历史扫描：UI确认 #123，点击「删除」：{title}")
        yield from runtime.wait_click(123, "删除")
        yield from self._wait_mail_list_after_detail_action(
            ctx,
            stop_event,
            runtime,
            123,
            timeout=18.0,
            label="邮件_历史扫描：返回邮件 #121",
        )
        self._update_runtime_mail_action_for_row(
            row,
            status="delete_requested",
            evidence={
                "runtime_requested_action": "delete",
                "runtime_action_requested_at": _behavior_tree_runtime._now().strftime("%Y-%m-%d %H:%M:%S"),
                "runtime_action_source": "ui_delete_probe",
            },
        )
        return "processed"

    def _mail_selective_claim_delete_probe_candidate(self, row: dict[str, Any]) -> bool:
        if self._mail_row_has_attachment_hint(row):
            return False
        title = str(row.get("title") or "").strip()
        if not title:
            return False
        status = self._normalize_mail_row_status(str(row.get("status") or ""))
        if status == "锁定" or bool(row.get("list_has_lock")):
            return False
        runtime_match = str(row.get("runtime_match") or "")
        if status == "已阅":
            return True
        return runtime_match in {"missing", "title_only", "ui_skipped"}

    def _open_mail_row(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        row: dict[str, Any],
    ) -> None:
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        runtime.click_frame_point(121, float(row.get("x") or 0), float(row.get("y") or 0))

    def _return_mail_detail_to_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        scene_id: int,
    ):
        detail_image = ctx.get("images", {}).get(scene_id)
        back_shape = self._find_shape(detail_image, "空白-返回") if isinstance(detail_image, dict) else None
        if not isinstance(detail_image, dict) or not back_shape:
            raise RuntimeError(f"缺少 #{scene_id}「空白-返回」标注，无法从邮件详情返回")
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.wait_click(scene_id, "空白-返回")
        yield from self._wait_mail_list_ready(ctx, stop_event, timeout=18.0, label="邮件_历史扫描：返回邮件 #121")

    def _wait_mail_detail_or_list_scene(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        timeout: float,
        label: str,
    ):
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        candidates = [121, 122, 123]
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        while True:
            self._raise_if_stopped(stop_event)
            runtime.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            scene_id, score, frame = runtime.current_scene(candidates, update=True)
            last_scene_id, last_score = scene_id, score
            if scene_id in candidates:
                with self._lock:
                    self._status.update({"current_scene": scene_id, "updated_at": time.time()})
                self._log("success", f"{label}：识别到 #{scene_id} {score:.0f}%")
                return scene_id, score
            with self._lock:
                self._status.update({
                    "phase": "wait_mail_detail",
                    "current_scene": scene_id,
                    "message": f"{label}：当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    "updated_at": time.time(),
                })
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise RuntimeError(f"{label} 超时，未检测到邮件详情，最后 {scene_text} {last_score:.0f}%")

    def _mail_rows_in_shape(self, lines: list[dict[str, Any]], image: dict[str, Any], shape_title: str) -> list[dict[str, Any]]:
        shape = self._find_shape(image, shape_title)
        if not shape:
            return []
        template_shape = self._find_shape(image, "邮件模板")
        template_children = self._flatten_shapes(template_shape.get("children")) if isinstance(template_shape, dict) else []
        title_shape = next((item for item in template_children if str(item.get("title") or "").strip() == "标题"), None)
        time_shape = next((item for item in template_children if str(item.get("title") or "").strip() == "时间"), None)
        status_shape = next((item for item in template_children if str(item.get("title") or "").strip() == "状态"), None)
        if template_shape and title_shape and time_shape:
            rows = self._mail_rows_in_shape_by_template(lines, image, shape, title_shape, time_shape, status_shape)
            if rows:
                return rows
        box = self._box(shape, image)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        candidates: list[dict[str, Any]] = []
        date_lines: list[dict[str, Any]] = []
        for line in sorted(lines, key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0))):
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if not (left <= cx <= right and top <= cy <= bottom):
                continue
            if self._looks_like_mail_time(text):
                date_lines.append({"text": text, "x": cx, "y": cy, "is_read": "已阅" in text or "已读" in text})
                continue
            if not self._looks_like_mail_title(text):
                continue
            title = re.sub(r"[0-9A-Za-z]+$", "", _behavior_tree_runtime.normalize_fanxiu_mail_title(text)).strip()
            if not title or not self._looks_like_mail_title(title):
                continue
            candidates.append({"title": title, "x": cx, "y": cy, "raw_text": text})
        rows: list[dict[str, Any]] = []
        for item in candidates:
            title = str(item.get("title") or "")
            if not title:
                continue
            below_dates = [line for line in date_lines if float(line["y"]) >= float(item["y"]) and float(line["y"]) - float(item["y"]) < 80]
            if below_dates:
                raw_time_text = str(below_dates[0].get("text") or "")
                item["time_text"] = self._normalize_mail_time_text(raw_time_text)
                item["raw_time_text"] = raw_time_text
                item["is_read"] = bool(below_dates[0].get("is_read"))
                item["status"] = "已阅" if item["is_read"] else "无"
            else:
                item["status"] = "无"
            rows.append(item)
        return rows

    def _annotate_mail_rows_visual_slots(
        self,
        rows: list[dict[str, Any]],
        image: dict[str, Any],
        template_shape: dict[str, Any],
        shape_title: str,
    ) -> None:
        del template_shape
        try:
            geometry = mail_window_geometry_from_asset(image)
        except (TypeError, ValueError):
            return
        row_pitch = geometry.row_pitch
        origin_y = geometry.first_center_y + geometry.title_center_offset
        for row in rows:
            try:
                row_y = float(row.get("y") or 0)
            except (TypeError, ValueError):
                continue
            row["visual_scope"] = shape_title
            row["visual_slot_index"] = int(round((row_y - origin_y) / row_pitch))

    def _mail_template_child_shape(self, image: dict[str, Any], title: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        template_shape = self._find_shape(image, "邮件模板")
        template_children = self._flatten_shapes(template_shape.get("children")) if isinstance(template_shape, dict) else []
        title_shape = next((item for item in template_children if str(item.get("title") or "").strip() == "标题"), None)
        child_shape = next((item for item in template_children if str(item.get("title") or "").strip() == title), None)
        if not (isinstance(template_shape, dict) and isinstance(title_shape, dict) and isinstance(child_shape, dict)):
            return None
        return template_shape, title_shape, child_shape

    def _mail_row_template_child_shape(self, image: dict[str, Any], row: dict[str, Any], title: str) -> dict[str, Any] | None:
        found = self._mail_template_child_shape(image, title)
        if found is None:
            return None
        _template_shape, title_shape, child_shape = found
        _width, height = self._frame_size(image)
        try:
            row_title_center_y = float(row.get("y") or 0) / max(1, height)
        except (TypeError, ValueError):
            return None
        title_center_y = float(title_shape.get("y") or 0) + float(title_shape.get("h") or 0) / 2
        child_center_y = float(child_shape.get("y") or 0) + float(child_shape.get("h") or 0) / 2
        adjusted = dict(child_shape)
        adjusted["y"] = max(0.0, min(1.0, row_title_center_y + (child_center_y - title_center_y) - float(child_shape.get("h") or 0) / 2))
        adjusted["title"] = str(child_shape.get("title") or title)
        adjusted["imageMatchRole"] = "required"
        adjusted["ocrMatchRole"] = "off"
        return adjusted

    def _annotate_mail_rows_list_state(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        frame_data_url: str,
        rows: list[dict[str, Any]],
    ) -> None:
        del ctx, image, frame_data_url
        for row in rows:
            row.setdefault("list_has_lock", False)
            row.setdefault("has_attachment_hint", False)

    def _mail_rows_in_shape_by_template(
        self,
        lines: list[dict[str, Any]],
        image: dict[str, Any],
        list_shape: dict[str, Any],
        title_shape: dict[str, Any],
        time_shape: dict[str, Any],
        status_shape: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        list_box = self._box(list_shape, image)
        title_box = self._box(title_shape, image)
        time_box = self._box(time_shape, image)
        status_box = self._box(status_shape, image) if isinstance(status_shape, dict) else None
        list_left = float(list_box.get("x") or 0)
        list_top = float(list_box.get("y") or 0)
        list_right = list_left + float(list_box.get("w") or 0)
        list_bottom = list_top + float(list_box.get("h") or 0)
        title_left = float(title_box.get("x") or 0)
        title_right = title_left + float(title_box.get("w") or 0)
        title_height = float(title_box.get("h") or 0)
        title_center_y = float(title_box.get("y") or 0) + title_height / 2
        time_center_y = float(time_box.get("y") or 0) + float(time_box.get("h") or 0) / 2
        title_offset_y = title_center_y - time_center_y
        status_offset_y = 0.0
        status_left = status_right = 0.0
        if status_box:
            status_left = float(status_box.get("x") or 0)
            status_right = status_left + float(status_box.get("w") or 0)
            status_center_y = float(status_box.get("y") or 0) + float(status_box.get("h") or 0) / 2
            status_offset_y = status_center_y - title_center_y
        title_y_tolerance = max(18.0, title_height * 1.25)
        normalized_lines: list[dict[str, Any]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if not (list_left <= cx <= list_right and list_top <= cy <= list_bottom):
                continue
            normalized_lines.append({"text": text, "x": cx, "y": cy, "w": w, "h": h})
        time_lines = [
            line for line in normalized_lines
            if self._looks_like_mail_time(str(line.get("text") or ""))
        ]
        rows: list[dict[str, Any]] = []
        for time_line in sorted(time_lines, key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0))):
            raw_time_text = str(time_line.get("text") or "")
            time_text = self._normalize_mail_time_text(raw_time_text)
            if not self._is_valid_mail_time_text(time_text):
                continue
            expected_title_y = float(time_line.get("y") or 0) + title_offset_y
            candidates: list[dict[str, Any]] = []
            for line in normalized_lines:
                text = str(line.get("text") or "")
                if text == raw_time_text:
                    continue
                if self._looks_like_mail_time(text):
                    continue
                cx = float(line.get("x") or 0)
                cy = float(line.get("y") or 0)
                if not (title_left <= cx <= title_right):
                    continue
                if abs(cy - expected_title_y) > title_y_tolerance:
                    continue
                if not self._looks_like_mail_title(text):
                    continue
                title = _behavior_tree_runtime.normalize_fanxiu_mail_title(text).strip()
                if not title or not self._looks_like_mail_title(title):
                    continue
                candidates.append({"title": title, "x": cx, "y": cy, "raw_text": text})
            if not candidates:
                continue
            best = max(candidates, key=lambda item: len(str(item.get("title") or "")))
            status = "已阅" if self._normalize_mail_row_status(raw_time_text) == "已阅" else "无"
            raw_status_text = ""
            if status_box:
                expected_status_y = float(best.get("y") or 0) + status_offset_y
                status_candidates: list[dict[str, Any]] = []
                for line in normalized_lines:
                    text = str(line.get("text") or "")
                    if text == raw_time_text or text == str(best.get("raw_text") or ""):
                        continue
                    cx = float(line.get("x") or 0)
                    cy = float(line.get("y") or 0)
                    if not (status_left <= cx <= status_right):
                        continue
                    if abs(cy - expected_status_y) > title_y_tolerance:
                        continue
                    normalized_status = self._normalize_mail_row_status(text)
                    if normalized_status:
                        status_candidates.append({"status": normalized_status, "raw_text": text})
                if status_candidates:
                    chosen = status_candidates[0]
                    status = str(chosen["status"])
                    raw_status_text = str(chosen["raw_text"])
            row = {
                **best,
                "time_text": time_text,
                "raw_time_text": raw_time_text,
                "is_read": status == "已阅",
                "status": status,
                "raw_status_text": raw_status_text,
            }
            if status == "锁定":
                row["list_has_lock"] = True
            rows.append(row)
        return rows

    def _normalize_mail_time_text(self, text: str) -> str:
        return _behavior_tree_runtime.normalize_fanxiu_mail_time_text(_sanitize_ocr_text(text))

    def _is_valid_mail_time_text(self, text: str) -> bool:
        return bool(_behavior_tree_runtime.normalize_fanxiu_mail_time_text(text))

    def _looks_like_mail_time(self, text: str) -> bool:
        return bool(re.search(r"\d{4}年|\d{1,2}月\d{1,2}(?:日)?|\d{1,2}:\d{2}", text))

    def _normalize_mail_row_status(self, text: str) -> str:
        normalized = _sanitize_ocr_text(text).replace(" ", "")
        if not normalized:
            return ""
        if "锁定" in normalized:
            return "锁定"
        if "已阅" in normalized or "已读" in normalized:
            return "已阅"
        compact = re.sub(r"[^\u4e00-\u9fff]", "", normalized)
        if not compact:
            return ""
        if len(compact) < 2:
            return ""
        if len(compact) == 2 and compact.startswith("锁"):
            return "锁定"
        if len(compact) <= 3 and compact.startswith("已"):
            return "已阅"
        for target in ("锁定", "已阅", "已读"):
            if difflib.SequenceMatcher(None, compact, target).ratio() >= 0.5:
                return "已阅" if target == "已读" else target
        return ""

    def _looks_like_mail_title(self, text: str) -> bool:
        normalized = _behavior_tree_runtime.normalize_fanxiu_mail_title(text)
        if len(normalized) < 2:
            return False
        if any(token in normalized for token in ("邮件", "已锁定", "一键删除", "一键领取", "年月日", "已阅", "未阅", "已读")):
            return False
        if self._looks_like_mail_time(normalized):
            return False
        return bool(re.search(r"[\u4e00-\u9fff]", normalized))

    def _merge_visible_mail_rows_by_position(
        self,
        first_rows: list[dict[str, Any]],
        list_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str, int]] = set()
        for row in sorted([*first_rows, *list_rows], key=lambda item: float(item.get("y") or 0)):
            title = _behavior_tree_runtime.normalize_fanxiu_mail_title(str(row.get("title") or ""))
            time_text = self._normalize_mail_time_text(str(row.get("time_text") or ""))
            y_bucket = int(round(float(row.get("y") or 0) / 16.0))
            key = (title, time_text, y_bucket)
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
        return merged

