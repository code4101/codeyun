from __future__ import annotations

import difflib
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any
from pathlib import Path
from types import GeneratorType

from sqlalchemy.exc import OperationalError

from backend.core.fanxiu.runtime.capture_runtime import (
    FANXIU_CAPTURE_RUNTIME_MAIL_TASK_REASON,
    ensure_fanxiu_capture_runtime_backstop,
    fanxiu_capture_runtime_service,
)
from backend.core.fanxiu.mail.policy import (
    fanxiu_mail_action_policy_for_record,
    fanxiu_mail_action_policy_for_rewards,
    fanxiu_mail_rewards_from_payload,
    fanxiu_mail_visible_group_action_policy,
)
from backend.core.fanxiu.mail.runtime_store import (
    align_packet_mail_records_claimable_between_visible_neighbors,
    find_packet_mail_record_by_raw_title,
    find_packet_mail_record_exact,
    mark_packet_mail_record_missing_from_list,
    packet_mail_records_by_normalized_title,
    packet_mail_records_for_visible_row_exact,
    packet_mail_records_for_visible_row_same_time,
    packet_mail_records_same_time,
    packet_mail_records_same_title,
    pending_packet_mail_action_candidates,
    pending_packet_mail_records,
    recent_packet_mail_records,
    trace_packet_mail_gap,
    update_packet_mail_action,
)
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation import runtime_runner as _runtime_runner
from backend.core.fanxiu.data_annotation.runtime_runner import (
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
from pyxllib.autogui import Shape, View, image_number as _runtime_image_number
from pyxllib.prog import BehaviorTreeStatus


class MailTaskMixin:
    _FORCE_CLAIM_MAIL_TITLE_KEYWORDS = ("宗门灵泉", "宗门镇邪")

    def _execute_mail_legacy_scan_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        _runtime_runner.ensure_fanxiu_mail_table()
        payload = dict(payload or {})
        entry_mode = str(payload.get("entry_mode") or payload.get("mail_entry_mode") or "dynamic").strip().lower()
        observe_only = bool(payload.get("observe_only") or payload.get("scan_only"))
        scan_mode = str(payload.get("scan_mode") or ("full" if payload.get("full_scan") else "incremental")).strip().lower()
        use_current_page = bool(payload.get("use_current_page"))
        target_title = str(payload.get("target_title") or payload.get("mail_title") or "").strip()
        target_time_text = self._normalize_mail_time_text(str(payload.get("target_time_text") or payload.get("mail_time_text") or "").strip())
        capture_enabled = not bool(payload.get("skip_capture") or payload.get("no_capture"))
        game_first = bool(payload.get("game_first") or payload.get("ui_first"))
        fail_on_packet_gap = bool(payload.get("fail_on_packet_gap"))
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
        capture_reason = f"mail-full-scan:{'observe' if observe_only else 'action'}"
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少邮件_历史扫描资产树路径，无法执行邮件作业")
        try:
            if capture_enabled:
                fanxiu_capture_runtime_service.ensure_running(capture_reason)
                with self._lock:
                    self._log_locked("info", f"邮件_抓包：已请求抓包服务 {capture_reason}")
                self._refresh_recent_mail_packets_for_runtime_log("启动抓包后", flush_capture=False)
            else:
                with self._lock:
                    self._log_locked("info", "邮件_历史扫描：本轮跳过抓包协作，仅使用当前页与既有邮件事实")
        except Exception as exc:
            with self._lock:
                self._log_locked("error", f"邮件_抓包：启动抓包服务失败：{exc}")
            raise
        try:
            if observe_only:
                with self._lock:
                    self._log_locked("info", "邮件_全量遍历：只观察并滚动加载邮件，不领取、不删除")
            elif game_first:
                with self._lock:
                    self._log_locked("info", "邮件_历史扫描：游戏画面优先模式，缺 packet 的可见邮件按详情页按钮处理")
            runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
            scene_id, score, frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
            force_reopen_mail = observe_only or scan_mode in {"full", "full_scan", "observe", "observe_only", "refresh", "sync"}
            if scene_id == 121 and not use_current_page and (force_reopen_mail or (not observe_only and self._pending_packet_mail_action_count() > 0)):
                image121 = ctx.get("images", {}).get(121)
                back_shape = self._find_shape(image121, "空白-返回") if isinstance(image121, dict) else None
                if isinstance(image121, dict) and back_shape:
                    reason = "刷新邮件 packet 列表" if force_reopen_mail else "重置列表顶部"
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
                fail_on_packet_gap=fail_on_packet_gap,
            )
            return (yield from scan_result) if isinstance(scan_result, GeneratorType) else scan_result
        finally:
            if capture_enabled:
                self._refresh_recent_mail_packets_for_runtime_log("释放抓包前", flush_capture=True)
                try:
                    fanxiu_capture_runtime_service.release(capture_reason)
                    with self._lock:
                        self._log_locked("info", f"邮件_抓包：已释放抓包服务 {capture_reason}")
                except Exception as exc:
                    with self._lock:
                        self._log_locked("error", f"邮件_抓包：释放抓包服务失败：{exc}")

    def _execute_mail_selective_claim_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        _runtime_runner.ensure_fanxiu_mail_table()
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少邮件_选择性领取资产树路径，无法执行邮件作业")
        try:
            capture_status = ensure_fanxiu_capture_runtime_backstop(FANXIU_CAPTURE_RUNTIME_MAIL_TASK_REASON)
            with self._lock:
                self._log_locked(
                    "info",
                    "邮件_抓包：清理入口兜底 "
                    f"ensured={bool(capture_status.get('ensured'))} "
                    f"state={((capture_status.get('status') or {}).get('state') if isinstance(capture_status, dict) else '')}",
                )
        except Exception as exc:
            with self._lock:
                self._log_locked("error", f"邮件_抓包：清理入口兜底失败：{exc}")
            raise
        self._wait_mail_capture_runtime_ready("清理入口", stop_event=stop_event)
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
            text = self._ocr_text(self._ocr_lines(frame))
        if scene_id not in {121, 122, 123} and (
            yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label="邮件_选择性领取")
        ):
            scene_id, score, frame, text = self._fanxiu_runtime_scene_text(ctx, runtime, [121, 122, 123, 227, 34, 35, 69], update=True)
        if scene_id == 121:
            with self._lock:
                self._status.update({"current_scene": 121, "updated_at": time.time()})
                self._log_locked("info", f"邮件_选择性领取：当前已在邮件 #121 {score:.0f}%，先退出重进刷新邮件抓包")
            image121_for_reset = ctx.get("images", {}).get(121) if isinstance(ctx.get("images"), dict) else None
            if isinstance(image121_for_reset, dict) and self._find_shape(image121_for_reset, "空白-返回") is not None:
                yield from self._leave_mail_scene_to_world(ctx, stop_event, runtime, 121, label="邮件_选择性领取")
                yield from self._open_mail_selective_claim_entry(runtime)
            else:
                self._log("error", "邮件_选择性领取：缺少 #121「空白-返回」标注，无法重进刷新邮件抓包，保留当前页扫描")
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
            if back_shape is None:
                raise RuntimeError(f"邮件_选择性领取：本轮入口位于 #{scene_id}，缺少「空白-返回」标注，拒绝盲目处理")
            back_shape.click(runtime)
            yield from runtime.wait_view(121, timeout=12.0, label="邮件_选择性领取：详情页安全返回邮件 #121")
        else:
            raise RuntimeError(f"邮件_选择性领取：从稳定起点进入邮件后落点异常 #{scene_id or 'unknown'}")
        image121 = ctx.get("images", {}).get(121)
        if not isinstance(image121, dict):
            raise RuntimeError("缺少 #121 邮件帧标注，无法清理邮件")
        view121 = View(image121)
        list_shape = view121.get_shape("邮件清单2")
        if list_shape is None:
            raise RuntimeError("缺少 #121「邮件清单2」标注，无法遍历邮件清单")

        self._refresh_recent_mail_packets_for_runtime_log("进入邮件列表后", flush_capture=True)

        processed_count = 0
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
            page_packet_counts: dict[str, int] = {}
            page_rows_summary: list[str] = []
            for mail in rows:
                seen_count += 1
                self._prepare_mail_row_policy(mail.raw, action_enabled=True, action_policies={"claim", "delete"})
                packet_match = str(mail.raw.get("packet_match") or "-")
                page_packet_counts[packet_match] = page_packet_counts.get(packet_match, 0) + 1
                if len(page_rows_summary) < 5:
                    page_rows_summary.append(
                        f"{mail.title[:16]}|{mail.raw.get('time_text') or '-'}|"
                        f"{mail.raw.get('policy') or '-'}|{packet_match}"
                    )
                if mail.raw.get("policy") in {"claim", "delete"}:
                    action_row = mail
                    break
                if delete_probe_row is None and self._mail_selective_claim_delete_probe_candidate(mail.raw):
                    delete_probe_row = mail
                    continue
                if (
                    detail_probe_row is None
                    and str(mail.raw.get("packet_match") or "") in {"missing", "title_only"}
                    and str(mail.raw.get("title") or "").strip()
                    and str(mail.raw.get("time_text") or "").strip()
                ):
                    detail_probe_row = mail
            if action_row is not None:
                action_started_at = time.monotonic()
                try:
                    actual_policy = yield from self._claim_runtime_mail_row(runtime, action_row)
                except TimeoutError as exc:
                    action_elapsed = time.monotonic() - action_started_at
                    self._log(
                        "warning",
                        f"邮件_选择性领取：打开/处理「{action_row.title}」超时 {action_elapsed:.1f}s，跳过该行继续翻页；{exc}",
                    )
                else:
                    action_elapsed = time.monotonic() - action_started_at
                    self._log("detail", f"邮件_选择性领取：处理「{action_row.title}」耗时 {action_elapsed:.1f}s，动作 {actual_policy}")
                    self._update_packet_mail_action_for_row(
                        action_row.raw,
                        status=f"{actual_policy}_requested",
                        evidence={
                            "runtime_requested_action": actual_policy,
                            "runtime_action_requested_at": _runtime_runner._now().strftime("%Y-%m-%d %H:%M:%S"),
                            "runtime_action_source": "mail_selective_claim",
                        },
                    )
                    processed_count += 1
                    self._refresh_recent_mail_packets_for_runtime_log("领取后同步", flush_capture=True)
                    continue

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
                        self._refresh_recent_mail_packets_for_runtime_log("详情页处理后同步", flush_capture=True)
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
                        self._refresh_recent_mail_packets_for_runtime_log("详情页删除后同步", flush_capture=True)
                        continue

            if rows:
                counts_text = ",".join(f"{key}={value}" for key, value in sorted(page_packet_counts.items()))
                self._log(
                    "detail",
                    f"邮件_选择性领取：当前页无可领取，packet {counts_text}；"
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
            yield from self._close_mail_world_reward_tip_stack_if_present(ctx, runtime, stop_event, label="邮件_选择性领取")
            final_scene = yield from self._ensure_clean_world_after_task(ctx, stop_event, label="邮件_选择性领取")

        if scanned_to_end and not reached_scroll_limit:
            marked_count = self._mark_pending_packet_mail_actions_not_visible(
                reason=f"mail_selective_claim_full_scan_seen={seen_count}; processed={processed_count}; scrolls={scroll_count}",
                allowed_policies={"claim"},
            )
            if marked_count:
                self._log(
                    "success",
                    f"邮件_选择性领取：完整扫到底未见 packet 待领取项，标记 {marked_count} 封为 missing_from_list",
                )

        result = "success"
        if reached_scroll_limit:
            result = "skipped"
            retry_after = (datetime.now() + timedelta(seconds=max(60, int(payload.get("retry_seconds") or 600)))).strftime("%Y-%m-%d %H:%M:%S")
            self._record_scheduler_task_discovered_retry_after(
                str(payload.get("__scheduler_task_id") or "mail-selective-claim"),
                retry_after,
                task_type="mail_selective_claim",
                label="邮件_选择性领取",
                last_result="skipped",
            )
            message = (
                f"邮件_选择性领取：未确认到底，见到 {seen_count} 封，领取 {processed_count} 封，"
                f"滚动 {scroll_count} 次，{retry_after} 重试"
            )
        else:
            message = f"邮件_选择性领取：完成，见到 {seen_count} 封，领取 {processed_count} 封，滚动 {scroll_count} 次"

        with self._lock:
            self._set_status_locked(
                "running",
                message,
                phase="mail_selective_claim_done",
                current_scene=final_scene,
            )
            self._log_locked("success" if result == "success" else "skip", self._status["message"])
        if result != "success":
            return {"result": result, "message": message}
        return "success"

    def _click_confirmed_mail_delete_prompt(
        self,
        runtime,
        scene_id: int,
        *,
        frame_data_url: str | None = None,
    ) -> None:
        """点击已经由当前帧或 wait_view 确认过的删除确认弹窗。"""

        runtime.click_shape(int(scene_id), "确认", frame_data_url=frame_data_url)

    def _delete_read_mail_once(self, runtime: FanxiuRuntime, mail_view: View, *, reason: str):
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
        result_view = yield from runtime.wait_view(
            348,
            210,
            278,
            121,
            timeout=12.0,
            label="邮件_选择性领取：一键删除后等待确认弹窗或邮件页",
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
            self._refresh_recent_mail_packets_for_runtime_log("一键删除已阅后同步", flush_capture=True)
        elif result_scene == 121:
            self._log("info", "邮件_选择性领取：没有可删除邮件，继续当前流程")
        else:
            raise RuntimeError("邮件_选择性领取：一键删除后未进入确认弹窗，也未回到邮件页")
        return result_scene


















































































    def _open_mail_selective_claim_entry(self, runtime: FanxiuRuntime):
        """按清理邮件伪代码进入邮件页：#34 -> #68 或 #34 -> #35。"""

        asset_tree_path = runtime.asset_tree_path
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少邮件_选择性领取资产树路径，无法进入邮件")
        ctx = runtime.ctx
        stop_event = runtime.stop_event or threading.Event()
        recovered_green = yield from self._leave_green_bottle_to_world_if_present(ctx, stop_event, runtime, label="邮件_选择性领取")
        if recovered_green:
            result = self._open_mail_stable_entry(ctx, stop_event, asset_tree_path, probe_before_open=True)
            opened = (yield from result) if isinstance(result, GeneratorType) else result
            if opened == "blocked_reward_tip":
                return (yield from self._reopen_mail_from_current_world_like(runtime))
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
        if opened == "blocked_reward_tip":
            return (yield from self._reopen_mail_from_current_world_like(runtime))
        return opened

    def _leave_green_bottle_to_world_if_present(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        runtime: FanxiuRuntime,
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
                result = align_packet_mail_records_claimable_between_visible_neighbors(
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

    def _runtime_mail_rows_from_frame(self, runtime: FanxiuRuntime, view121: View, frame: str) -> list[_RuntimeMailRow]:
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

    def _claim_runtime_mail_row(self, runtime: FanxiuRuntime, mail: _RuntimeMailRow):
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_选择性领取：打开「{mail.title}」",
                phase="mail_selective_claim_open_row",
                current_scene=121,
            )
            self._log_locked("action", f"邮件_选择性领取：点击标题「{mail.title}」")
        mail.title_shape.click(runtime)
        detail_view = yield from runtime.wait_view(122, 123, timeout=12.0, label=f"邮件_选择性领取：等待「{mail.title}」详情")
        if not isinstance(detail_view, View) or detail_view.id not in {122, 123}:
            return "claim"
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
        action_shape.click(runtime)
        wait_result = yield from self._wait_mail_list_or_reopen_from_world_after_action(
            runtime,
            detail_view,
            timeout=18.0,
            label="邮件_选择性领取：返回邮件 #121",
        )
        if wait_result in {"list_after_reward", "reopened_after_reward"}:
            image121 = (runtime.ctx.get("images") or {}).get(121)
            if not isinstance(image121, dict):
                raise RuntimeError("缺少 #121 邮件帧标注，无法在 #347 奖励后执行一键删除")
            yield from self._delete_read_mail_once(runtime, View(image121), reason="#347 奖励后")
        if wait_result in {"timeout", "detail_still_open"}:
            back_shape = detail_view.get_shape("空白-返回")
            if back_shape is None:
                raise RuntimeError("邮件_选择性领取：领取后未回邮件列表，且缺少详情页「空白-返回」标注")
            self._log("info", f"邮件_选择性领取：{action_title}后未自动回列表，点击详情页返回")
            back_shape.click(runtime)
            yield from runtime.wait_view(121, timeout=12.0, label="邮件_选择性领取：详情页返回邮件 #121")
        return actual_policy

    def _wait_mail_list_after_detail_action(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        runtime: FanxiuRuntime,
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
        runtime: FanxiuRuntime,
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
        while True:
            self._raise_if_stopped(stop_event)
            runtime.clear_frame() if hasattr(runtime, "clear_frame") else self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            elapsed = time.monotonic() - start
            detail_scene_id = detail_view.id if isinstance(detail_view.id, int) else None
            candidates = [scene for scene in [121, 347, 34, detail_scene_id] if isinstance(scene, int)]
            scene_id, score, frame, text = self._fanxiu_runtime_scene_text(ctx, runtime, candidates, update=True)
            last_scene_id, last_score = scene_id, score
            marker_score = 0.0
            marker_matched = False
            if scene_id == 347 or self._mail_reward_transition_text_matches(text):
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
            if scene_id == 34 or (text and self._daily_assistant_text_is_world_like(text)):
                if self._close_mail_world_reward_tip_if_present(ctx, runtime, frame, text):
                    yield from runtime.wait_action_settle(0.3)
                self._log("info", f"{label}：领取后落到世界页，重新打开邮件列表")
                yield from runtime.wait_action_settle(0.8)
                reopened = self._reopen_mail_from_current_world_like(runtime)
                result = (yield from reopened) if isinstance(reopened, GeneratorType) else reopened
                if result == "success":
                    return "reopened_after_reward" if saw_reward_transition else "reopened"
                self._log("info", f"{label}：从世界页重新打开邮件失败 result={result}，继续等待")
            if self._close_mail_wait_popup_once(ctx, frame):
                runtime.clear_frame()
                yield BehaviorTreeStatus.RUNNING
                continue
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

    def _reopen_mail_from_current_world_like(self, runtime: FanxiuRuntime):
        ctx = runtime.ctx
        stop_event = runtime.stop_event or threading.Event()
        asset_tree_path = runtime.asset_tree_path
        frame = runtime.cur_frame(update=True)
        if self._close_mail_world_reward_tip_if_present(ctx, runtime, frame, ""):
            yield from runtime.wait_action_settle(0.3)
        if isinstance(asset_tree_path, Path):
            try:
                for attempt in range(2):
                    stable_result = self._open_mail_stable_entry(ctx, stop_event, asset_tree_path, probe_before_open=(attempt > 0))
                    stable_opened = (yield from stable_result) if isinstance(stable_result, GeneratorType) else stable_result
                    if stable_opened == "success":
                        return "success"
                    if stable_opened != "blocked_reward_tip":
                        break
                    frame = runtime.cur_frame(update=True)
                    if not self._close_mail_world_reward_tip_if_present(ctx, runtime, frame, ""):
                        break
                    yield from runtime.wait_action_settle(0.3)
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

    def _mail_world_reward_tip_text_matches(self, text: str) -> bool:
        return self._world_reward_tip_text_matches(text)

    def _mail_world_reward_tip_detected(
        self,
        ctx: dict[str, Any],
        frame: str,
        text: str = "",
        *,
        menu_ocr_lines: list[dict[str, Any]] | None = None,
    ) -> bool:
        return self._world_reward_tip_detected(ctx, frame, text, menu_ocr_lines=menu_ocr_lines)

    def _close_mail_world_reward_tip_if_present(self, ctx: dict[str, Any], runtime: FanxiuRuntime, frame: str, text: str) -> bool:
        return self._close_world_reward_tip_if_present(ctx, runtime, frame, text, label="邮件_选择性领取")

    def _close_mail_world_reward_tip_stack_if_present(
        self,
        ctx: dict[str, Any],
        runtime: FanxiuRuntime,
        stop_event: threading.Event,
        *,
        label: str,
        max_attempts: int = 15,
    ):
        yield from self._close_world_reward_tip_stack_if_present(ctx, runtime, stop_event, label=label, max_attempts=max_attempts)

    def _wait_mail_capture_runtime_ready(
        self,
        label: str,
        *,
        stop_event: threading.Event | None = None,
        timeout: float = 20.0,
        settle_seconds: float = 1.5,
    ) -> bool:
        deadline = time.monotonic() + max(1.0, float(timeout))
        last_state = ""
        while time.monotonic() < deadline:
            if stop_event is not None:
                self._raise_if_stopped(stop_event)
            status = fanxiu_capture_runtime_service.status()
            if bool(status.get("running")):
                time.sleep(max(0.0, float(settle_seconds)))
                with self._lock:
                    self._log_locked(
                        "info",
                        f"邮件_抓包协作：{label} 已运行 "
                        f"pcap_size={int(status.get('current_pcap_size') or 0)}",
                    )
                return True
            state = str(status.get("state") or "")
            if state != last_state:
                last_state = state
                with self._lock:
                    self._log_locked("detail", f"邮件_抓包协作：{label} 等待运行 state={state or '-'}")
            time.sleep(0.5)
        status = fanxiu_capture_runtime_service.status()
        with self._lock:
            self._log_locked(
                "error",
                f"邮件_抓包协作：{label} 未等到运行 "
                f"state={status.get('state') or '-'} error={status.get('last_error') or ''}",
            )
        return False

    def _refresh_recent_mail_packets_for_runtime_log(self, label: str, *, flush_capture: bool) -> None:
        try:
            pcap_paths: list[str] = []
            if flush_capture:
                flush = fanxiu_capture_runtime_service.flush_recent_capture(f"mail-selective-claim:{label}", restart=True)
                pcap_path = str(flush.get("pcap_path") or "").strip() if isinstance(flush, dict) else ""
                if pcap_path and bool(flush.get("flushed")):
                    pcap_paths.append(pcap_path)
                with self._lock:
                    self._log_locked(
                        "info",
                        "邮件_抓包协作："
                        f"{label} flush={bool(flush.get('flushed')) if isinstance(flush, dict) else False} "
                        f"pcap_size={flush.get('pcap_size', 0) if isinstance(flush, dict) else 0}",
                    )
            if pcap_paths:
                result = _runtime_runner.sync_fanxiu_capture_paths(pcap_paths, max_streams=4)
            else:
                result = {"decoded_count": 0, "mail_packet_sync": {}}
            mail_sync = result.get("mail_packet_sync") or {}
            if not mail_sync:
                for decoded_item in result.get("decoded") or []:
                    if isinstance(decoded_item, dict) and isinstance(decoded_item.get("batch_mail_packet_sync"), dict):
                        mail_sync = decoded_item["batch_mail_packet_sync"]
                        break
            with self._lock:
                self._log_locked(
                    "info",
                    "邮件_抓包协作："
                    f"{label} decoded={result.get('decoded_count', 0)} "
                    f"updated={mail_sync.get('updated', 0)} inserted={mail_sync.get('inserted', 0)} "
                    f"source_packets={mail_sync.get('source_packets', 0)} action_packets={mail_sync.get('action_packets', 0)}"
                    + (f" skipped={mail_sync.get('reason')}" if mail_sync.get("skipped") else ""),
                )
        except Exception as exc:
            with self._lock:
                self._log_locked("error", f"邮件_抓包协作：{label}失败：{exc}")

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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        yield from self._close_world_reward_tip_stack_if_present(ctx, runtime, stop_event, label="邮件_历史扫描")
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
                if visible_opened == "blocked_reward_tip":
                    return "blocked_reward_tip"
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
            if visible_opened == "blocked_reward_tip":
                return "blocked_reward_tip"
            yield from runtime.wait_action_settle(0.8)
            visible_result = self._click_mail_from_visible_world_menu_once(ctx, stop_event, require_world_scene=False)
            visible_opened = (yield from visible_result) if isinstance(visible_result, GeneratorType) else visible_result
            if visible_opened == "success":
                return "success"
            if visible_opened == "blocked_reward_tip":
                return "blocked_reward_tip"
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
            ocr_lines = self._ocr_lines_in_shapes(frame, image35, ("菜单",), padding=8)
            if self._mail_world_reward_tip_detected(ctx, frame, menu_ocr_lines=ocr_lines):
                self._log("info", "邮件_历史扫描：#35 菜单区域检测到世界奖励提示，先关闭提示")
                return "blocked_reward_tip"
            menu_matches = self._ocr_centers_in_shape(ocr_lines, image35, "菜单", include=("邮件",))
            menu_matches = [match for match in menu_matches if self._looks_like_world_menu_mail_entry_ocr(match[2])]
            if not menu_matches:
                if mail_shape and self._looks_like_world_menu_open_ocr(ocr_lines):
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
        menu_matches = self._ocr_centers_in_shape(runtime.ocr_lines(frame), image35, "菜单", include=("邮件",))
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
                ocr_lines = runtime.ocr_lines(frame)
                last_ocr = " / ".join(str(item.get("text") or "") for item in ocr_lines[-3:]) or last_ocr
                menu_matches = self._ocr_centers_in_shape(ocr_lines, image35, "菜单", include=("邮件",))
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
            image34 = ctx.get("images", {}).get(34)
            if not isinstance(image34, dict):
                raise original_error from restore_error
            with self._lock:
                self._log_locked("warning", f"{label}：场景图恢复失败，点击左下返回兜底：{restore_error}")
            runtime.click_frame_point(image34, 70, 1490)
            try:
                yield from runtime.wait_view(34, timeout=18.0, label="邮件_历史扫描：恢复世界 #34")
                raise original_error
            except RuntimeError as fallback_error:
                if fallback_error is original_error:
                    raise
                raise original_error from fallback_error

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
                ocr_lines = self._ocr_lines_in_shapes(frame, image35, ("菜单",), padding=8)
                menu_matches = self._ocr_centers_in_shape(ocr_lines, image35, "菜单", include=("邮件",))
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
                if mail_shape and self._looks_like_world_menu_open_ocr(ocr_lines):
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
            menu_matches = self._ocr_centers_in_shape(runtime.ocr_lines(frame), image35, "菜单", include=("邮件",))
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
        fail_on_packet_gap: bool = False,
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
        pending_actions = self._pending_packet_mail_action_count(allowed_policies=allowed_policies) if action_enabled else 0
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
        packet_missing_rows: list[dict[str, str]] = []
        packet_missing_traces: list[dict[str, Any]] = []
        if action_enabled:
            with self._lock:
                self._log_locked("info", f"邮件_历史扫描：packet 待处理邮件 {pending_actions} 封")
                if target_requested:
                    target_parts = []
                    if target_title:
                        target_parts.append(f"标题={target_title}")
                    if target_time_text:
                        target_parts.append(f"时间={target_time_text}")
                    self._log_locked("info", f"邮件_历史扫描：本轮只处理目标邮件：{'，'.join(target_parts)}")
            if pending_actions <= 0 and not full_scan and not target_requested:
                with self._lock:
                    self._log_locked("success", "邮件_历史扫描：packet 无待处理邮件，跳过动作扫描")
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
                if row.get("packet_match") == "missing" and len(packet_missing_rows) < 20:
                    missing_item = {
                        "title": str(row.get("title") or ""),
                        "time_text": str(row.get("time_text") or ""),
                        "reason": str(row.get("packet_missing_reason") or ""),
                    }
                    packet_missing_rows.append(
                        missing_item
                    )
                    if len(packet_missing_traces) < 8:
                        packet_missing_traces.append(self._trace_mail_packet_gap(missing_item))
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
                    and row.get("packet_match") == "missing"
                    and not row.get("policy")
                ):
                    game_first_candidate = row
            if rows:
                row_summary = "；".join(
                    f"{str(row.get('title') or '')[:18]}|{row.get('time_text') or '-'}|{row.get('policy') or '-'}|{row.get('packet_match') or '-'}|lock={row.get('list_lock_score', '-')}"
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
                    if not full_scan and self._pending_packet_mail_action_count(allowed_policies=allowed_policies) <= 0:
                        with self._lock:
                            self._log_locked("success", "邮件_历史扫描：packet 待处理邮件已清零，停止扫描")
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
            if action_enabled and not full_scan and not target_requested and self._pending_packet_mail_action_count(allowed_policies=allowed_policies) <= 0:
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
        pending_after_scan = self._pending_packet_mail_action_count(allowed_policies=allowed_policies) if action_enabled else 0
        if action_enabled and full_scan and pending_after_scan > 0 and not scan_truncated:
            marked_count = self._mark_pending_packet_mail_actions_not_visible(
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
        if action_enabled and full_scan and packet_missing_rows:
            sample_text = "；".join(
                f"{item['title']}|{item['time_text']}|{item['reason']}"
                for item in packet_missing_rows[:8]
            )
            self._write_mail_scan_state(
                {
                    **scan_state,
                    "status": "packet_gap",
                    "last_scan_at": _runtime_runner._now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_seen_top_time": top_time,
                    "last_seen_count": seen_count,
                    "last_processed_count": processed_count,
                    "packet_missing_count": len(packet_missing_rows),
                    "packet_missing_rows": packet_missing_rows,
                    "packet_missing_traces": packet_missing_traces,
                    "packet_gap_history": self._mail_packet_gap_history(scan_state, packet_missing_rows, packet_missing_traces),
                    "message": "可见邮件缺可用 packet 事实，标题+时间和标题降级均未匹配",
                }
            )
            with self._lock:
                self._log_locked(
                    "error" if fail_on_packet_gap else "info",
                    (
                        f"邮件_历史扫描：发现 {len(packet_missing_rows)} 个可见邮件缺 packet 事实，"
                        f"{'本轮不能证明已清干净' if fail_on_packet_gap else '已按游戏画面优先策略记录并继续'}：{sample_text}"
                    ),
                )
            if fail_on_packet_gap:
                raise RuntimeError(f"邮件_历史扫描：发现 {len(packet_missing_rows)} 个可见邮件缺 packet 事实，请先修复抓包/解析缺口：{sample_text}")
        if action_enabled and watermark_time and not crossed_watermark:
            self._write_mail_scan_state(
                {
                    **scan_state,
                    "status": "gap_risk",
                    "last_scan_at": _runtime_runner._now().strftime("%Y-%m-%d %H:%M:%S"),
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
                    "packet_gap_history": scan_state.get("packet_gap_history") or [],
                    "status": "confirmed",
                    "confirmed_time_bucket": top_time,
                    "confirmed_at": _runtime_runner._now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_scan_mode": "full" if full_scan else "incremental",
                    "last_seen_count": seen_count,
                    "last_processed_count": processed_count,
                    "previous_confirmed_time_bucket": scan_state.get("confirmed_time_bucket") or "",
                }
            )

        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_历史扫描：完成，见到 {seen_count} 封，处理 {processed_count} 封，packet缺失 {len(packet_missing_rows)} 封",
                phase="mail_claim_done",
                current_scene=121,
            )
            self._log_locked("success", f"邮件_历史扫描：完成，见到 {seen_count} 封，处理 {processed_count} 封，packet缺失 {len(packet_missing_rows)} 封")
        return "success"

    def _trace_mail_packet_gap(self, row: dict[str, str]) -> dict[str, Any]:
        try:
            return trace_packet_mail_gap(
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
        lines = runtime.ocr_lines_in_shapes(image121, ("第1封", "邮件清单2"), frame_data_url=frame)
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

    def _read_mail_scan_state(self) -> dict[str, Any]:
        payload = _read_data_annotation_json(_data_annotation_mail_scan_state_path(), {})
        return payload if isinstance(payload, dict) else {}

    def _write_mail_scan_state(self, payload: dict[str, Any]) -> None:
        _write_data_annotation_json(_data_annotation_mail_scan_state_path(), payload)

    def _mail_packet_gap_history(
        self,
        scan_state: dict[str, Any],
        rows: list[dict[str, str]],
        traces: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        history = [item for item in scan_state.get("packet_gap_history") or [] if isinstance(item, dict)]
        history.append(
            {
                "recorded_at": _runtime_runner._now().strftime("%Y-%m-%d %H:%M:%S"),
                "rows": rows,
                "traces": traces,
            }
        )
        return history[-20:]

    def _pending_packet_mail_action_count(self, *, allowed_policies: set[str] | None = None) -> int:
        policies = (set(allowed_policies or {"claim"}) & {"claim"}) or {"claim"}
        records = pending_packet_mail_records(_db_engine)
        groups: dict[tuple[str, str], list[Any]] = {}
        for record in records:
            key = (str(record.normalized_title or record.title or "").strip(), str(record.create_time_text or "").strip())
            if key[0] and key[1]:
                groups.setdefault(key, []).append(record)
        return sum(
            1
            for group in groups.values()
            if any(fanxiu_mail_action_policy_for_record(record) in policies for record in group)
            and fanxiu_mail_visible_group_action_policy(group) in policies
        )

    def _mark_pending_packet_mail_actions_not_visible(self, *, reason: str, allowed_policies: set[str] | None = None) -> int:
        now_text = _runtime_runner._now().strftime("%Y-%m-%d %H:%M:%S")
        marked = 0
        policies = (set(allowed_policies or {"claim"}) & {"claim"}) or {"claim"}
        records = pending_packet_mail_action_candidates(_db_engine, policies)
        for record in records:
            if fanxiu_mail_action_policy_for_record(record) not in policies:
                continue
            try:
                mark_packet_mail_record_missing_from_list(_db_engine, record, reason=reason, marked_at=now_text)
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
        current = parse_data_annotation_task_time(_runtime_runner.normalize_fanxiu_mail_time_text(current_time_text))
        watermark = parse_data_annotation_task_time(_runtime_runner.normalize_fanxiu_mail_time_text(watermark_time_text))
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
            row["packet_match"] = "ui_skipped"
            row["packet_missing_reason"] = ""
            return
        if not self._is_valid_mail_time_text(time_text):
            row["time_text"] = ""
            row["mail_key"] = ""
            return
        row["time_text"] = time_text
        records = self._find_packet_mail_records_for_visible_row(title, time_text)
        record = records[0] if records else None
        row["mail_key"] = str(record.mail_key or "") if record else ""
        if records:
            row["packet_match"] = "matched" if any(self._mail_record_matches_visible_time(record, time_text) for record in records) else "title_only"
            row["packet_missing_reason"] = ""
        else:
            row["packet_match"] = "missing"
            row["packet_missing_reason"] = self._mail_row_packet_missing_reason(title, time_text)
        if action_enabled:
            # These recurring sect activity mails are known reward mails.  Their
            # packet state can lag behind the visible list, so title recognition
            # is the authoritative claim rule for them.
            policy = (
                "claim"
                if self._mail_title_forces_claim(title)
                else self._mail_row_packet_action_policy(title, time_text, records=records)
            )
            allowed_policies = (set(action_policies or {"claim"}) & {"claim"}) or {"claim"}
            row["policy"] = policy if policy in allowed_policies else ""

    def _mail_title_forces_claim(self, title: str) -> bool:
        normalized_title = _runtime_runner.normalize_fanxiu_mail_title(title)
        compact_title = re.sub(r"\s+", "", normalized_title)
        return any(keyword in compact_title for keyword in self._FORCE_CLAIM_MAIL_TITLE_KEYWORDS)

    def _mail_row_packet_action_policy(self, title: str, time_text: str, *, records: list[Any] | None = None) -> str:
        if records is None:
            records = self._find_packet_mail_records_for_visible_row(title, time_text)
        return self._visible_packet_mail_group_action_policy(records, time_text=time_text)

    def _mail_row_packet_missing_reason(self, title: str, time_text: str) -> str:
        normalized_title = _runtime_runner.normalize_fanxiu_mail_title(title)
        normalized_time = _runtime_runner.normalize_fanxiu_mail_time_text(time_text)
        if not normalized_title or not normalized_time:
            return "invalid_title_or_time"
        same_title = packet_mail_records_same_title(_db_engine, normalized_title, limit=5)
        same_time = packet_mail_records_same_time(_db_engine, normalized_time, limit=5)
        if same_title:
            times = ",".join(str(record.create_time_text or "") for record in same_title[:3])
            return f"same_title_without_time:{times}"
        if same_time:
            titles = ",".join(str(record.title or record.normalized_title or "") for record in same_time[:3])
            return f"same_time_without_title:{titles}"
        return "no_packet_fact"

    def _visible_packet_mail_group_action_policy(self, records: list[Any], *, time_text: str = "") -> str:
        if not records:
            return ""
        policies = {self._visible_packet_mail_action_policy(record) for record in records}
        policies.discard("")
        if len(policies) != 1:
            return ""
        policy = next(iter(policies))
        for record in records:
            if self._visible_packet_mail_action_policy(record) != policy:
                return ""
        return policy

    def _packet_mail_record_initially_claimable(self, record: Any | None) -> bool:
        if record is None:
            return False
        return str(getattr(record, "status", "") or "").strip() == "可领"

    def _find_packet_mail_records_for_visible_row(
        self,
        title: str,
        time_text: str,
        *,
        allow_title_only: bool = True,
    ) -> list[Any]:
        normalized_title = _runtime_runner.normalize_fanxiu_mail_title(title)
        normalized_time = _runtime_runner.normalize_fanxiu_mail_time_text(time_text)
        if not normalized_title or not normalized_time:
            return []
        try:
            exact = packet_mail_records_for_visible_row_exact(_db_engine, normalized_title=normalized_title, normalized_time=normalized_time)
            if exact:
                return list(exact)
            same_time = packet_mail_records_for_visible_row_same_time(_db_engine, normalized_time)
        except OperationalError as exc:
            if not self._mail_packet_store_operational_error_is_transient(exc):
                raise
            self._log("warning", f"邮件_历史扫描：packet 查找遇到瞬态数据库异常，跳过本行匹配：{exc}")
            return []
        observed_key = self._mail_title_similarity_key(title)
        if len(observed_key) < 3:
            return self._find_packet_mail_records_by_title_only(title) if allow_title_only else []
        scored: list[tuple[float, Any]] = []
        for record in same_time:
            score = self._mail_title_similarity(title, str(record.title or record.normalized_title or ""))
            if score > 0:
                scored.append((score, record))
        if not scored:
            return self._find_packet_mail_records_by_title_only(title) if allow_title_only else []
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score = scored[0][0]
        threshold = 0.58 if len(observed_key) >= 5 else 0.72
        if best_score < threshold:
            return self._find_packet_mail_records_by_title_only(title) if allow_title_only else []
        fuzzy_matches = [record for score, record in scored if score >= best_score - 0.06]
        if fuzzy_matches:
            return fuzzy_matches
        return self._find_packet_mail_records_by_title_only(title) if allow_title_only else []

    def _find_packet_mail_records_by_title_only(self, title: str) -> list[Any]:
        normalized_title = _runtime_runner.normalize_fanxiu_mail_title(title)
        if not normalized_title:
            return []
        try:
            exact = packet_mail_records_by_normalized_title(_db_engine, normalized_title, limit=20)
            if exact:
                return list(exact)
        except OperationalError as exc:
            if not self._mail_packet_store_operational_error_is_transient(exc):
                raise
            self._log("warning", f"邮件_历史扫描：packet 标题查找遇到瞬态数据库异常，跳过标题匹配：{exc}")
            return []
        observed_key = self._mail_title_similarity_key(title)
        if len(observed_key) < 5:
            return []
        try:
            recent = recent_packet_mail_records(_db_engine, limit=200)
        except OperationalError as exc:
            if not self._mail_packet_store_operational_error_is_transient(exc):
                raise
            self._log("warning", f"邮件_历史扫描：packet 近期记录查找遇到瞬态数据库异常，跳过标题匹配：{exc}")
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

    def _find_packet_mail_record(
        self,
        title: str,
        time_text: str,
        *,
        action_policies: set[str] | None = None,
    ) -> Any | None:
        normalized_title = _runtime_runner.normalize_fanxiu_mail_title(title)
        normalized_time = _runtime_runner.normalize_fanxiu_mail_time_text(time_text)
        if not normalized_title or not normalized_time:
            return None
        try:
            record = find_packet_mail_record_exact(_db_engine, normalized_title=normalized_title, normalized_time=normalized_time)
            if record:
                return record
            record = find_packet_mail_record_by_raw_title(_db_engine, title=title, normalized_time=normalized_time)
            if record:
                return record
            time_candidates = packet_mail_records_for_visible_row_same_time(_db_engine, normalized_time)
        except OperationalError as exc:
            if not self._mail_packet_store_operational_error_is_transient(exc):
                raise
            self._log("warning", f"邮件_历史扫描：packet 记录查找遇到瞬态数据库异常，跳过状态回写匹配：{exc}")
            return None
        fuzzy = self._select_packet_mail_record_by_fuzzy_title(
            title,
            time_candidates,
            action_policies=action_policies,
        )
        if fuzzy:
            return fuzzy
        return None

    @staticmethod
    def _mail_packet_store_operational_error_is_transient(exc: OperationalError) -> bool:
        message = str(exc).lower()
        return "database is locked" in message or "database schema has changed" in message

    def _visible_packet_mail_action_policy(self, record: Any | None) -> str:
        if record is None:
            return ""
        status = str(record.status or "").strip().lower()
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

    def _select_packet_mail_record_by_fuzzy_title(
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
            scored.append((score, candidate, self._visible_packet_mail_action_policy(candidate)))
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
        text = _runtime_runner.normalize_fanxiu_mail_title(value)
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
        return _runtime_runner.normalize_fanxiu_mail_time_text(str(record.create_time_text or "")) == _runtime_runner.normalize_fanxiu_mail_time_text(time_text)

    def _find_packet_mail_key(self, title: str, time_text: str) -> str:
        record = self._find_packet_mail_record(title, time_text)
        return str(record.mail_key or "") if record else ""

    def _update_packet_mail_action_for_row(self, row: dict[str, Any], *, status: str, evidence: dict[str, Any]) -> None:
        mail_key = str(row.get("mail_key") or "").strip()
        if not mail_key:
            mail_key = self._find_packet_mail_key(
                str(row.get("title") or ""),
                str(row.get("time_text") or ""),
            )
        if not mail_key:
            return
        try:
            update_packet_mail_action(_db_engine, mail_key=mail_key, status=status, evidence=evidence)
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
            self._log_locked("action", f"邮件_历史扫描：缺 packet，打开「{title}」按详情页判断")
        self._open_mail_row(ctx, stop_event, row)
        scene_result = self._wait_mail_detail_or_list_scene(ctx, stop_event, timeout=12.0, label=f"邮件_历史扫描：等待「{title}」详情")
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
        self._update_packet_mail_action_for_row(
            row,
            status=f"{actual_policy}_requested",
            evidence={
                "runtime_requested_action": actual_policy,
                "runtime_action_requested_at": _runtime_runner._now().strftime("%Y-%m-%d %H:%M:%S"),
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
        scene_result = self._wait_mail_detail_or_list_scene(ctx, stop_event, timeout=12.0, label=f"邮件_历史扫描：等待「{title}」详情")
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
        self._update_packet_mail_action_for_row(
            row,
            status=f"{actual_policy}_requested",
            evidence={"runtime_requested_action": actual_policy, "runtime_action_requested_at": _runtime_runner._now().strftime("%Y-%m-%d %H:%M:%S")},
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
        scene_id, score = yield from self._wait_mail_detail_or_list_scene(ctx, stop_event, timeout=12.0, label=f"邮件_历史扫描：探测「{title}」详情")
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
        self._update_packet_mail_action_for_row(
            row,
            status="delete_requested",
            evidence={
                "runtime_requested_action": "delete",
                "runtime_action_requested_at": _runtime_runner._now().strftime("%Y-%m-%d %H:%M:%S"),
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
        packet_match = str(row.get("packet_match") or "")
        if status == "已阅":
            return True
        return packet_match in {"missing", "title_only", "ui_skipped"}

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
            if self._close_mail_wait_popup_once(ctx, frame):
                yield BehaviorTreeStatus.RUNNING
                continue
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
            title = re.sub(r"[0-9A-Za-z]+$", "", _runtime_runner.normalize_fanxiu_mail_title(text)).strip()
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
        template_box = self._box(template_shape, image)
        row_pitch = float(template_box.get("h") or 0)
        if row_pitch < 24:
            return
        origin_y = float(template_box.get("y") or 0)
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
                title = _runtime_runner.normalize_fanxiu_mail_title(text).strip()
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
        return _runtime_runner.normalize_fanxiu_mail_time_text(_sanitize_ocr_text(text))

    def _is_valid_mail_time_text(self, text: str) -> bool:
        return bool(_runtime_runner.normalize_fanxiu_mail_time_text(text))

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
        normalized = _runtime_runner.normalize_fanxiu_mail_title(text)
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
            title = _runtime_runner.normalize_fanxiu_mail_title(str(row.get("title") or ""))
            time_text = self._normalize_mail_time_text(str(row.get("time_text") or ""))
            y_bucket = int(round(float(row.get("y") or 0) / 16.0))
            key = (title, time_text, y_bucket)
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
        return merged

