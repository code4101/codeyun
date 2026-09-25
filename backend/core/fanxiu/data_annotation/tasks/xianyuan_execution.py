"""日常挑战仙缘：入口进度、人物选择、对话挑战与离场复核。

将同一玩法原先散落在日常基础和挑战类中的流程集中到此；
重入边界由 xianyuan_reentry 定义，通用场景与执行能力由宿主提供。
仙缘斗法是独立业务，不由本模块承担。
"""
from __future__ import annotations

import re
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from pyxllib.prog import BehaviorTreeStatus
from pyxllib.autogui import View
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.data_annotation.ocr_values import FULLWIDTH_DIGIT_TRANSLATION
from backend.core.fanxiu.data_annotation.tasks.scene_candidates import DAILY_XIANYUAN_CHALLENGE_LAYER0_SCENE_IDS
from backend.core.fanxiu.data_annotation.tasks.scene_candidates import DAILY_XIANYUAN_LAYER0_SCENE_IDS
from backend.core.fanxiu.data_annotation.tasks.scene_candidates import DAILY_XIANYUAN_RETURN_LAYER0_SCENE_IDS
from backend.core.fanxiu.data_annotation.tasks.xianyuan_reentry import DAILY_XIANYUAN_REENTRY_REQUESTED
from backend.core.fanxiu.data_annotation.tasks.xianyuan_reentry import DAILY_XIANYUAN_SHARED_DEADLINE_KEY
from backend.core.fanxiu.data_annotation.tasks.xianyuan_reentry import daily_xianyuan_shared_deadline
from backend.core.fanxiu.data_annotation.tasks.xianyuan_reentry import daily_xianyuan_reentry_limit

def read_daily_task_runtime_snapshot(task_id: int) -> dict[str, Any]:
    from backend.core.fanxiu.instrumentation.daily_task import read_daily_task_snapshot

    return read_daily_task_snapshot(task_id)


class DailyXianyuanTaskMixin:
    def _execute_daily_xianyuan_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        max_reentries = daily_xianyuan_reentry_limit(payload)
        for reentry_count in range(max_reentries + 1):
            result = yield from self._execute_daily_xianyuan_task_once(
                ctx,
                stop_event,
                payload,
            )
            if result is not DAILY_XIANYUAN_REENTRY_REQUESTED:
                return result

            deadline = payload.get(DAILY_XIANYUAN_SHARED_DEADLINE_KEY)
            now = time.monotonic()
            if isinstance(deadline, (int, float)) and now >= float(deadline):
                raise TimeoutError(
                    "日常_挑战仙缘：离开场景后整单重入已超过共享挑战 deadline，停止"
                )
            if reentry_count >= max_reentries:
                raise RuntimeError(
                    "日常_挑战仙缘：离开场景后整单重入次数超过上限 "
                    f"{max_reentries}，停止"
                )
            self._log(
                "warning",
                "日常_挑战仙缘：离开场景后从稳定入口整单重入 "
                f"{reentry_count + 1}/{max_reentries}",
            )

        raise RuntimeError("日常_挑战仙缘：整单重入状态异常")

    def _execute_daily_xianyuan_task_once(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ) -> str:
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_挑战仙缘资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image34 = images.get(34)
        image69 = images.get(69)

        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        task_snapshot = self._daily_xianyuan_runtime_snapshot(payload)
        self._log(
            "detail",
            "日常_挑战仙缘：Runtime 日常任务 "
            f"complete={bool(task_snapshot.get('complete'))} "
            f"status={task_snapshot.get('status')} "
            f"progress={task_snapshot.get('turn')}/{task_snapshot.get('target_turn')} "
            f"done={bool(task_snapshot.get('done'))} "
            f"elapsed={float(task_snapshot.get('elapsed_seconds') or 0):.2f}s",
        )
        if task_snapshot.get("complete") is True and task_snapshot.get("task_id") == 1008 and task_snapshot.get("done") is True:
            self._record_daily_xianyuan_done(payload, message="Runtime 已证明挑战仙缘任务完成")
            yield from context.go_scene(34)
            return "success"
        _wait_scene_match = yield from context.wait_scene(DAILY_XIANYUAN_LAYER0_SCENE_IDS, wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id == 197 and not self._daily_xianyuan_text_is_people_list(text):
            if context.recognize_scene_in_frame([69], frame_data_url=frame)[0] == 69:
                scene_id = 69
            else:
                scene_id = None
        if scene_id in DAILY_XIANYUAN_CHALLENGE_LAYER0_SCENE_IDS:
            return (yield from self._run_daily_xianyuan_from_challenge_state(ctx, stop_event, payload, int(scene_id)))
        if scene_id == 199:
            return (yield from self._run_daily_xianyuan_from_dialogue(ctx, stop_event, payload))
        if scene_id == 198:
            return (yield from self._run_daily_xianyuan_from_detail(ctx, stop_event, payload))
        if scene_id == 197:
            return (yield from self._run_daily_xianyuan_from_list(ctx, stop_event, payload))
        if scene_id != 69:
            if self._daily_xianyuan_text_is_dialogue(text):
                return (yield from self._run_daily_xianyuan_from_dialogue(ctx, stop_event, payload))
            if self._daily_xianyuan_text_is_detail(text) or self._daily_xianyuan_detail_crop_matches(context, frame):
                return (yield from self._run_daily_xianyuan_from_detail(ctx, stop_event, payload))
            if scene_id != 34:
                if self._daily_xianyuan_text_is_people_list(text):
                    return (yield from self._run_daily_xianyuan_from_list(ctx, stop_event, payload))
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label="日常_挑战仙缘")):
                _wait_scene_match = yield from context.wait_scene([69, 34], wait=5.0, required=False)
                (scene_id, _score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text = context.ocr_text(frame)
            if scene_id != 69:
                scene_id = yield from self._enter_daily_from_world_like(
                    ctx,
                    context,
                    stop_event,
                    frame,
                    scene_id,
                    text,
                    label="日常_挑战仙缘",
                )

        daily_status = yield from self._open_daily_xianyuan_from_daily(ctx, stop_event, payload)
        if daily_status == "done":
            self._record_daily_xianyuan_done(payload, message="日常列表显示已完成")
            yield from self._safe_daily_done_cleanup(
                lambda: self._return_daily_xianyuan_to_world(ctx, stop_event),
                label="日常_挑战仙缘",
                repeat_risk="重复挑战",
            )
            return "success"
        if daily_status == "not_found":
            raise RuntimeError("日常_挑战仙缘：未找到未完成入口，不能按完成处理")

        scene_id, _score = yield from self._wait_daily_xianyuan_after_entry(ctx, stop_event, payload)
        if scene_id in DAILY_XIANYUAN_CHALLENGE_LAYER0_SCENE_IDS:
            return (yield from self._run_daily_xianyuan_from_challenge_state(ctx, stop_event, payload, int(scene_id)))
        if scene_id == 199:
            return (yield from self._run_daily_xianyuan_from_dialogue(ctx, stop_event, payload))
        if scene_id == 198:
            return (yield from self._run_daily_xianyuan_from_detail(ctx, stop_event, payload))
        if scene_id == 197:
            return (yield from self._run_daily_xianyuan_from_list(ctx, stop_event, payload))
        raise RuntimeError(f"日常_挑战仙缘：入口点击后回到 #{scene_id or 'unknown'}，尚未完成挑战流程，不能按完成处理")

    def _open_daily_xianyuan_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        image69 = ctx.get("images", {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError("缺少 #69「日常」标注，无法查找挑战仙缘")
        if self._find_shape(image69, "滚动窗口") is None:
            raise RuntimeError("缺少 #69「滚动窗口」标注，无法滚动查找挑战仙缘")
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        max_scrolls = int(payload.get("max_scrolls") or payload.get("xianyuan_max_scrolls") or 14)
        reset_max_scrolls = int(payload.get("xianyuan_reset_max_scrolls") or max_scrolls) + 1
        fallback_seen = 0

        # 上一个日常任务可能把共享的 #69 列表停在任意位置。先明确回到顶部，
        # 再从上到下做一次有界扫描，避免在列表底部把“未找到”误判为任务不存在。
        top_confirmed = False
        for reset_index in range(reset_max_scrolls):
            self._raise_if_stopped(stop_event)
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_挑战仙缘：复位日常列表到顶部 {reset_index + 1}/{reset_max_scrolls}",
                    phase="daily_xianyuan_reset_daily_list",
                    current_scene=69,
                )
            changed = yield from self._scroll_daily_xianyuan_list(ctx, stop_event, image69, direction="up")
            if not changed:
                top_confirmed = True
                break
            context.clear_frame()
        if not top_confirmed:
            raise RuntimeError(f"日常_挑战仙缘：{reset_max_scrolls} 次向上滚动后仍未确认列表顶部")

        for direction, scroll_count in (("down", max_scrolls),):
            for scroll_index in range(scroll_count + 1):
                self._raise_if_stopped(stop_event)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_挑战仙缘：查找日常任务「挑战仙缘」 {direction} {scroll_index}/{scroll_count}",
                        phase="daily_xianyuan_find_daily_entry",
                        current_scene=69,
                    )
                frame = context.cur_frame(update=True)
                lines = context.ocr_fragments(frame)
                # ``ocr_fragments`` already resolved the frame's scene and
                # populated the shared OCR cache.  Build text from that exact
                # observation instead of recognizing the same frame again.
                text = self._ocr_text(lines)
                matches = self._daily_xianyuan_entry_matches(lines, image69)
                if matches:
                    x, y, matched_text = matches[0]
                    progress = self._daily_xianyuan_row_progress(lines, y)
                    if progress is not None and progress[0] >= progress[1]:
                        return "done"
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"日常_挑战仙缘：点击日常任务 {matched_text}",
                            phase="daily_xianyuan_click_daily_entry",
                            current_scene=69,
                        )
                        self._log_locked("action", f"日常_挑战仙缘：点击 #69「{matched_text}」")
                    context.click_frame_point(View(image69), x, y)
                    yield from context.wait_action_settle(float(payload.get("xianyuan_entry_click_settle_seconds") or 2.0))
                    return "open"
                if self._daily_xianyuan_progress_done(text):
                    return "done"
                if re.search(r"(?:挑战\s*仙缘|仙缘人物)", text) and not re.search(r"仙缘斗法|斗法", text):
                    fallback_seen += 1
                if scroll_index >= scroll_count:
                    break
                with self._lock:
                    self._log_locked("action", f"日常_挑战仙缘：未找到「挑战仙缘」，{direction} 滚动日常列表 {scroll_index + 1}")
                changed = yield from self._scroll_daily_xianyuan_list(ctx, stop_event, image69, direction=direction)
                if not changed:
                    break
                context.clear_frame()
        if fallback_seen >= int(payload.get("completed_fallback_min_total") or 3):
            raise RuntimeError("日常_挑战仙缘：看到标题但未解析到未完成进度，不能按完成处理")
        return "not_found"

    def _daily_xianyuan_text_is_people_list(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return bool("仙缘" in compact and ("可送礼" in compact or "隐藏已无物品的仙缘" in compact))

    def _wait_daily_xianyuan_after_entry(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        timeout = float(payload.get("post_click_timeout") or 30.0)
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from context.wait_scene([199, 198, 197, 69, 34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            last_scene_id, last_score = scene_id, score
            if scene_id == 197 and not self._daily_xianyuan_text_is_people_list(text):
                scene_id = None
            if scene_id in {199, 198, 197, 69, 34}:
                return int(scene_id), float(score)
            last_text = text or last_text
            if self._daily_xianyuan_text_is_people_list(text):
                return 197, 100.0
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_挑战仙缘：等待入口点击结果，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_xianyuan_wait_after_entry",
                    current_scene=scene_id,
                )
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(
                    f"日常_挑战仙缘：等待入口点击结果超时，未检测到 #69/#34 或仙缘列表，"
                    f"最后 {scene_text} {last_score:.0f}%，OCR={last_text[:120]}"
                )

    def _scroll_daily_xianyuan_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image69: dict[str, Any],
        *,
        direction: str,
    ):
        list_shape = self._find_shape(image69, "滚动窗口")
        if list_shape is None:
            raise RuntimeError("缺少 #69「滚动窗口」标注，无法滚动查找挑战仙缘")
        context = self._behavior_tree_context(
            ctx,
            ctx.get("asset_tree_path") if isinstance(ctx.get("asset_tree_path"), Path) else None,
            stop_event=stop_event,
        )
        before_text = re.sub(r"\s+", "", context.ocr_text(context.cur_frame(update=True)))
        changed = yield from self._scroll_shape_content_changed(ctx, image69, list_shape, stop_event, reverse=(direction == "up"))
        if not changed:
            # scroll_shape_content 的像素签名可能在列表惯性动画期间误判未变化；
            # 用动作后的真实 OCR 再确认一次，不能把实际已滚动当成边界。
            context.clear_frame()
            after_text = re.sub(r"\s+", "", context.ocr_text(context.cur_frame(update=True)))
            same_page_threshold = 0.9
            ocr_similarity = SequenceMatcher(None, before_text, after_text).ratio() if before_text and after_text else 1.0
            if ocr_similarity < same_page_threshold:
                changed = True
                with self._lock:
                    self._log_locked(
                        "action",
                        f"#69「滚动窗口」{direction} 像素签名未变化，但 OCR 内容已换页"
                        f"（相似度 {ocr_similarity:.1%} < {same_page_threshold:.0%}），继续扫描",
                    )
        if not changed:
            boundary = "顶部" if direction == "up" else "底部"
            with self._lock:
                self._log_locked("action", f"#69「滚动窗口」{direction} 拖拽后签名未变化，判定已到{boundary}")
            return False
        return True

    def _daily_xianyuan_people_list_box(self, image197: dict[str, Any]) -> dict[str, Any]:
        list_shape = self._find_shape(image197, "人物列表")
        if list_shape is not None:
            return self._box(list_shape, image197)
        width, height = self._frame_size(image197)
        return {"name": "人物列表", "x": width * 0.07, "y": height * 0.19, "w": width * 0.88, "h": height * 0.66}

    def _daily_xianyuan_target_pattern(self, payload: dict[str, Any]) -> str:
        raw = (
            payload.get("target_pattern")
            or payload.get("xianyuan_target_pattern")
            or payload.get("target")
            or payload.get("指定目标")
            or payload.get("xianyuan_target")
            or ""
        )
        target = _sanitize_ocr_text(str(raw or "")).strip()
        if target:
            return target
        return r"两立"

    def _daily_xianyuan_list_target_candidates(
        self,
        lines: list[dict[str, Any]],
        image197: dict[str, Any],
        payload: dict[str, Any],
    ) -> list[tuple[float, float, str]]:
        box = self._daily_xianyuan_people_list_box(image197)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        pattern = self._daily_xianyuan_target_pattern(payload)
        candidates: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            line_x = float(line.get("x") or 0)
            line_y = float(line.get("y") or 0)
            line_w = float(line.get("w") or 0)
            line_h = float(line.get("h") or 0)
            cx = line_x + line_w / 2
            cy = line_y + line_h / 2
            if cx < left or cx > right or cy < top or cy > bottom:
                continue
            try:
                matches = list(re.finditer(pattern, text))
            except re.error:
                matches = []
                index = text.find(pattern)
                if index >= 0:
                    matches = [re.match(re.escape(pattern), text[index:]) or re.match(r".*", text[index:index + len(pattern)])]
            for match in matches:
                if match is None:
                    continue
                span_start, span_end = match.span()
                if span_end <= span_start:
                    continue
                text_len = max(1, len(text))
                click_x = line_x + line_w * ((span_start + span_end) / 2) / text_len
                click_y = max(top, line_y + line_h / 2 - 120)
                candidates.append((click_x, click_y, text))
        return sorted(candidates, key=lambda item: (item[1], item[0]))

    def _scroll_daily_xianyuan_people_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image197: dict[str, Any],
        *,
        direction: str = "down",
    ):
        list_shape = self._find_shape(image197, "人物列表")
        if list_shape is None:
            raise RuntimeError("日常_挑战仙缘：#197 缺少「人物列表」标注，无法滚动查找仙缘人物")
        return (yield from self._scroll_shape_content_changed(ctx, image197, list_shape, stop_event, reverse=(direction == "up")))

    def _run_daily_xianyuan_from_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        image197 = ctx.get("images", {}).get(197)
        if not isinstance(image197, dict):
            raise RuntimeError("日常_挑战仙缘：缺少 #197「仙缘列表」标注，无法选择仙缘人物")
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("日常_挑战仙缘：缺少资产树路径，无法执行作业")
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        frame = context.cur_frame(update=True)
        text = context.ocr_text(frame)
        if not self._daily_xianyuan_text_is_people_list(text):
            if context.recognize_scene_in_frame([69], frame_data_url=frame)[0] == 69:
                raise RuntimeError("日常_挑战仙缘：当前仍在日常列表，#197 场景身份误判，不能查找仙缘人物")
            raise RuntimeError(f"日常_挑战仙缘：当前不是仙缘人物列表，OCR={text[:120]}")
        max_scrolls = int(payload.get("people_max_scrolls") or payload.get("xianyuan_people_max_scrolls") or 8)
        hide_empty_toggled = False
        for search_round in range(2):
            if search_round == 1:
                hide_shape = self._find_shape(image197, "隐藏已无物品的仙缘")
                if hide_shape is None:
                    break
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_挑战仙缘：关闭隐藏已无物品后继续查找",
                        phase="daily_xianyuan_toggle_hide_empty",
                        current_scene=197,
                    )
                    self._log_locked("action", "日常_挑战仙缘：点击 #197「隐藏已无物品的仙缘」后重试目标搜索")
                context.click_shape_center(View(image197), "隐藏已无物品的仙缘")
                hide_empty_toggled = True
                yield from context.wait_action_settle(float(payload.get("xianyuan_toggle_settle_seconds") or 2.0))

            for direction, scroll_count in (("down", max_scrolls),):
                for scroll_index in range(scroll_count + 1):
                    self._raise_if_stopped(stop_event)
                    frame = context.cur_frame(update=True)
                    lines = context.ocr_fragments(frame)
                    candidates = self._daily_xianyuan_list_target_candidates(lines, image197, payload)
                    if candidates:
                        x, y, matched_text = candidates[0]
                        with self._lock:
                            self._set_status_locked(
                                "running",
                                f"日常_挑战仙缘：选择仙缘人物 {matched_text[:24]}",
                                phase="daily_xianyuan_click_person",
                                current_scene=197,
                            )
                            self._log_locked("action", f"日常_挑战仙缘：点击 #197 仙缘人物候选「{matched_text[:40]}」")
                        context.click_frame_point(View(image197), x, y)
                        yield from context.wait_action_settle(float(payload.get("xianyuan_person_click_settle_seconds") or 2.0))
                        scene_id, score = yield from self._wait_daily_xianyuan_after_person_click(ctx, stop_event, payload)
                        if scene_id == 198:
                            return (yield from self._run_daily_xianyuan_from_detail(ctx, stop_event, payload))
                        raise RuntimeError(f"日常_挑战仙缘：已进入后续页面 #{scene_id or 'unknown'} {score:.0f}%，需要继续补详情/对话/挑战标注")
                    if scroll_index >= scroll_count:
                        break
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"日常_挑战仙缘：查找仙缘人物 {direction} {scroll_index + 1}/{scroll_count}",
                            phase="daily_xianyuan_find_person",
                            current_scene=197,
                        )
                        suffix = "（已关闭隐藏无物品）" if hide_empty_toggled else ""
                        self._log_locked("action", f"日常_挑战仙缘：未找到目标{suffix}，{direction} 滚动仙缘人物列表 {scroll_index + 1}")
                    yield from self._scroll_daily_xianyuan_people_list(ctx, stop_event, image197, direction=direction)
        raise RuntimeError(f"日常_挑战仙缘：仙缘列表未找到目标「{self._daily_xianyuan_target_pattern(payload)}」")

    def _wait_daily_xianyuan_after_person_click(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("日常_挑战仙缘：缺少资产树路径，无法执行作业")
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        timeout = float(payload.get("person_click_timeout") or 18.0)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        while True:
            self._raise_if_stopped(stop_event)
            context.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from context.wait_scene([199, 198, 197, 69, 34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            last_scene_id, last_score = scene_id, score
            text = context.ocr_text(frame)
            if self._daily_xianyuan_text_is_dialogue(text):
                return 199, 100.0
            if self._daily_xianyuan_text_is_detail(text) or self._daily_xianyuan_detail_crop_matches(context, frame):
                return 198, 100.0
            if scene_id in {199, 198}:
                return scene_id, score
            if time.monotonic() - start >= timeout:
                return last_scene_id, last_score

    def _daily_xianyuan_text_is_detail(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return bool(
            "前往" in compact
            and ("身份" in compact or "功法主修" in compact or "出没地点" in compact)
            and not re.search(r"可送礼|隐藏已无物品的仙缘|教他做人|看招吧", compact)
        )

    def _daily_xianyuan_detail_crop_matches(self, context: Any, frame: str) -> bool:
        """Use two small stable ROIs when the dynamic character art defeats full-frame OCR."""

        try:
            go_text = _sanitize_ocr_text(
                context.ocr_text_in_shapes(198, ["前往"], frame_data_url=frame, crop=True)
            )
            identity_text = _sanitize_ocr_text(
                context.ocr_text_in_shapes(198, ["仙缘人物详情标识"], frame_data_url=frame, crop=True)
            )
        except Exception:
            return False
        return bool(
            re.search(r"前\s*.?\s*往", go_text)
            and re.search(r"身份|功法|出没|喜好", identity_text)
        )

    def _run_daily_xianyuan_from_detail(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        image198 = ctx.get("images", {}).get(198)
        if not isinstance(image198, dict):
            raise RuntimeError("日常_挑战仙缘：缺少 #198「仙缘人物详情」标注，无法前往人物")
        go_shape = self._find_shape(image198, "前往")
        if go_shape is None:
            raise RuntimeError("日常_挑战仙缘：缺少 #198「前往」标注，无法前往人物")
        with self._lock:
            self._set_status_locked("running", "日常_挑战仙缘：点击人物详情「前往」", phase="daily_xianyuan_go_person", current_scene=198)
            self._log_locked("action", "日常_挑战仙缘：点击 #198「前往」")
        context = self._behavior_tree_context(ctx, stop_event=stop_event)
        scene_id: int | None = None
        score = 0.0
        max_attempts = max(1, int(payload.get("detail_go_max_attempts") or 2))
        for attempt_index in range(max_attempts):
            box = self._box(go_shape, image198)
            x = float(box.get("x") or 0) + float(box.get("w") or 0) / 2
            y = float(box.get("y") or 0) + float(box.get("h") or 0) / 2
            context.click_frame_point(image198, x, y)
            yield from context.wait_action_settle(float(payload.get("xianyuan_detail_go_settle_seconds") or 2.0))
            scene_id, score = yield from self._wait_daily_xianyuan_after_detail_go(ctx, stop_event, payload)
            if scene_id == 199:
                return (yield from self._run_daily_xianyuan_from_dialogue(ctx, stop_event, payload))
            if scene_id != 198:
                break
            if attempt_index + 1 < max_attempts:
                with self._lock:
                    self._log_locked("action", "日常_挑战仙缘：人物详情仍停留 #198，按旧版逻辑再次点击「前往」")
        raise RuntimeError(f"日常_挑战仙缘：已前往后续页面 #{scene_id or 'unknown'} {score:.0f}%，需要继续补人物对话/挑战标注")

    def _wait_daily_xianyuan_after_detail_go(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("日常_挑战仙缘：缺少资产树路径，无法执行作业")
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        # “前往”会先短暂回到世界 #34，再经过较长的自动寻路才抵达人物
        # 对话页。实机高峰期可超过 35 秒；#34 只是中转态，不能据此提前
        # 判定流程未实现。
        timeout = float(payload.get("detail_go_timeout") or 75.0)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        while True:
            self._raise_if_stopped(stop_event)
            context.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from context.wait_scene([199, 198, 197, 69, 34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            last_scene_id, last_score = scene_id, score
            text = context.ocr_text(frame)
            if self._daily_xianyuan_text_is_dialogue(text):
                return 199, 100.0
            if self._daily_xianyuan_text_is_detail(text) or self._daily_xianyuan_detail_crop_matches(context, frame):
                return 198, 100.0
            if scene_id in {199, 198}:
                return scene_id, score
            if time.monotonic() - start >= timeout:
                return last_scene_id, last_score

    def _daily_xianyuan_text_is_dialogue(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return bool(
            ("教他做人" in compact or ("查探" in compact and "送礼" in compact))
            and not re.search(r"可送礼|隐藏已无物品的仙缘|出没地点", compact)
        )

    def _run_daily_xianyuan_from_dialogue(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        image199 = ctx.get("images", {}).get(199)
        if not isinstance(image199, dict):
            raise RuntimeError("日常_挑战仙缘：缺少 #199「仙缘人物对话」标注，无法发起挑战")
        observer = self._fanxiu_observer(ctx, stop_event)
        frame = observer.cur_frame(update=True)
        lines = observer.ocr_fragments(frame)
        text = observer.ocr_text(frame)
        teach_matches = self._daily_xianyuan_dialogue_button_matches(lines, image199, r"教他做人")
        if not teach_matches:
            raise RuntimeError(f"日常_挑战仙缘：当前仙缘人物没有「教他做人」按钮，不能挑战；OCR={text[:120]}")
        match_x, match_y, matched_text = teach_matches[0]
        x, y = match_x, match_y
        with self._lock:
            self._set_status_locked("running", "日常_挑战仙缘：点击「教他做人」", phase="daily_xianyuan_teach", current_scene=199)
            self._log_locked("action", f"日常_挑战仙缘：点击 #199「{matched_text}」")
        observer.click_frame_point(View(image199), x, y)
        yield from observer.wait_action_settle(float(payload.get("xianyuan_click_settle_seconds") or 2.0))
        return (yield from self._run_daily_xianyuan_after_teach(ctx, stop_event, payload))

    def _run_daily_xianyuan_from_challenge_state(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        scene_id: int,
    ):
        ref_image = self._daily_xianyuan_reference_image(ctx)
        if scene_id in {200, 201}:
            return (yield from self._run_daily_xianyuan_after_teach(ctx, stop_event, payload))
        if scene_id == 202:
            yield from self._wait_daily_xianyuan_challenge_result(ctx, stop_event, payload, ref_image)
            self._record_daily_xianyuan_done(payload, message="挑战流程已完成")
            yield from self._safe_daily_done_cleanup(
                lambda: self._leave_daily_xianyuan_battle(ctx, stop_event, payload, ref_image),
                label="日常_挑战仙缘",
                action="离开挑战结果",
                repeat_risk="重复挑战",
            )
            return "success"
        raise RuntimeError(f"日常_挑战仙缘：无法从 #{scene_id} 恢复挑战流程")

    def _daily_xianyuan_reference_image(self, ctx: dict[str, Any]) -> dict[str, Any]:
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        for scene_id in DAILY_XIANYUAN_LAYER0_SCENE_IDS:
            image = images.get(scene_id)
            if isinstance(image, dict):
                return image
        return {"filename": "daily_xianyuan_runtime.png", "width": 900, "height": 1600}

    def _daily_xianyuan_challenge_count_empty(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = normalized.replace("O", "0").replace("o", "0")
        match = re.search(r"(?:今日)?可挑战次数[:：]?(.*)", normalized)
        if match is None:
            return False
        fraction = parse_ocr_values(match.group(1), expected_count=2, allow_extra_numbers=True)
        return bool(fraction is not None and fraction[0] == 0 and fraction[1] > 0)

    def _daily_xianyuan_text_button_matches(
        self,
        lines: list[dict[str, Any]],
        pattern: str,
        *,
        left_ratio: float = 0.0,
        right_ratio: float = 1.0,
        top_ratio: float = 0.0,
        bottom_ratio: float = 1.0,
        width: float = 900.0,
        height: float = 1600.0,
    ) -> list[tuple[float, float, str]]:
        left = width * left_ratio
        right = width * right_ratio
        top = height * top_ratio
        bottom = height * bottom_ratio
        matches: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text or not re.search(pattern, text):
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if left <= cx <= right and top <= cy <= bottom:
                matches.append((cx, cy, text))
        return sorted(matches, key=lambda item: (item[1], item[0]))

    def _run_daily_xianyuan_after_teach(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        observer = self._fanxiu_observer(ctx, stop_event)
        ref_image = self._daily_xianyuan_reference_image(ctx)
        width, height = self._frame_size(ref_image)
        challenge_scene_ids = DAILY_XIANYUAN_LAYER0_SCENE_IDS

        existing_deadline = payload.get(DAILY_XIANYUAN_SHARED_DEADLINE_KEY)
        teach_deadline = time.monotonic() + float(payload.get("teach_disappear_timeout") or 15.0)
        if isinstance(existing_deadline, (int, float)):
            teach_deadline = min(teach_deadline, float(existing_deadline))
        while time.monotonic() < teach_deadline:
            self._raise_if_stopped(stop_event)
            observer.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from observer.wait_scene(challenge_scene_ids, wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, observer.frame_data_url or "")
            )
            if scene_id in DAILY_XIANYUAN_CHALLENGE_LAYER0_SCENE_IDS:
                break
            lines = observer.ocr_fragments(frame)
            teach_matches = self._daily_xianyuan_text_button_matches(
                lines,
                r"教他做人",
                left_ratio=0.45,
                right_ratio=0.98,
                top_ratio=0.45,
                bottom_ratio=0.82,
                width=width,
                height=height,
            )
            if not teach_matches:
                break
            x, y, _text = teach_matches[0]
            observer.click_frame_point(View(ref_image), x, y)
            yield from observer.wait_action_settle(float(payload.get("xianyuan_click_settle_seconds") or 2.0))

        attack_deadline = daily_xianyuan_shared_deadline(
            payload,
            now=time.monotonic(),
        )
        last_advance = 0.0
        while True:
            self._raise_if_stopped(stop_event)
            if time.monotonic() >= attack_deadline:
                raise TimeoutError("日常_挑战仙缘：等待「看招吧」超过共享挑战 deadline")
            observer.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from observer.wait_scene(challenge_scene_ids, wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, observer.frame_data_url or "")
            )
            lines = observer.ocr_fragments(frame)
            text = observer.ocr_text(frame)
            if scene_id == 201:
                break
            if self._daily_xianyuan_challenge_count_empty(text):
                self._record_daily_xianyuan_done(payload, message="仙缘对话显示今日可挑战次数已空")
                yield from self._safe_daily_done_cleanup(
                    lambda: self._return_daily_xianyuan_current_to_world(ctx, stop_event),
                    label="日常_挑战仙缘",
                    repeat_risk="重复挑战",
                )
                return "success"
            attack_matches = self._daily_xianyuan_text_button_matches(
                lines,
                r"看招吧",
                left_ratio=0.35,
                right_ratio=0.98,
                top_ratio=0.35,
                bottom_ratio=0.86,
                width=width,
                height=height,
            )
            if attack_matches:
                x, y, matched_text = attack_matches[0]
                with self._lock:
                    self._set_status_locked("running", "日常_挑战仙缘：点击「看招吧」", phase="daily_xianyuan_attack", current_scene=199)
                    self._log_locked("action", f"日常_挑战仙缘：点击「{matched_text}」")
                observer.click_frame_point(View(ref_image), x, y)
                yield from observer.wait_action_settle(float(payload.get("xianyuan_click_settle_seconds") or 2.0))
                break
            if scene_id == 34 and (yield from self._leave_world_side_scene_if_present(
                ctx,
                stop_event,
                frame,
                text,
                label="日常_挑战仙缘",
            )):
                with self._lock:
                    self._log_locked("action", "日常_挑战仙缘：离开场景后重新复核日常进度")
                return DAILY_XIANYUAN_REENTRY_REQUESTED
            now = time.monotonic()
            if now >= attack_deadline:
                raise TimeoutError(f"日常_挑战仙缘：等待「看招吧」超时，OCR={text[:120]}")
            if now - last_advance >= 3.0:
                observer.click_frame_point(View(ref_image), width * 0.48, height * 0.76)
                yield from observer.wait_action_settle(float(payload.get("xianyuan_dialogue_advance_settle_seconds") or 2.0))
                last_advance = now

        continue_deadline = time.monotonic() + float(payload.get("challenge_continue_timeout") or 5.0)
        while time.monotonic() <= continue_deadline:
            self._raise_if_stopped(stop_event)
            observer.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from observer.wait_scene(challenge_scene_ids, wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, observer.frame_data_url or "")
            )
            if scene_id == 202:
                break
            lines = observer.ocr_fragments(frame)
            matches = self._daily_xianyuan_text_button_matches(
                lines,
                r"继续",
                left_ratio=0.25,
                right_ratio=0.85,
                top_ratio=0.45,
                bottom_ratio=0.88,
                width=width,
                height=height,
            )
            if matches:
                x, y, matched_text = matches[0]
                with self._lock:
                    self._log_locked("action", f"日常_挑战仙缘：点击挑战提示「{matched_text}」")
                observer.click_frame_point(View(ref_image), x, y)
                yield from observer.wait_action_settle(float(payload.get("xianyuan_click_settle_seconds") or 2.0))
                break

        yield from self._wait_daily_xianyuan_challenge_result(ctx, stop_event, payload, ref_image)
        self._record_daily_xianyuan_done(payload, message="挑战流程已完成")
        yield from self._safe_daily_done_cleanup(
            lambda: self._leave_daily_xianyuan_battle(ctx, stop_event, payload, ref_image),
            label="日常_挑战仙缘",
            action="离开挑战结果",
            repeat_risk="重复挑战",
        )
        return "success"

    def _wait_daily_xianyuan_challenge_result(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        ref_image: dict[str, Any],
    ):
        observer = self._fanxiu_observer(ctx, stop_event)
        width, height = self._frame_size(ref_image)
        deadline = time.monotonic() + float(payload.get("challenge_result_timeout") or 300.0)
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            observer.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            frame = observer.cur_frame(update=True)
            text = observer.ocr_text(frame)
            last_text = text or last_text
            if re.search(r"友好度|减少", _sanitize_ocr_text(text)):
                with self._lock:
                    self._log_locked("success", "日常_挑战仙缘：识别到友好度减少结果")
                observer.click_frame_point(View(ref_image), width * 0.50, height * 0.62)
                yield from observer.wait_action_settle(float(payload.get("xianyuan_click_settle_seconds") or 2.0))
                return "success"
            if re.search(r"离\s*开|离开", _sanitize_ocr_text(text)):
                return "success"
            if time.monotonic() >= deadline:
                raise TimeoutError(f"日常_挑战仙缘：等待挑战结果超时，OCR={last_text[:120]}")

    def _leave_daily_xianyuan_battle(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        ref_image: dict[str, Any],
    ):
        observer = self._fanxiu_observer(ctx, stop_event)
        width, height = self._frame_size(ref_image)
        deadline = time.monotonic() + float(payload.get("battle_leave_timeout") or 60.0)
        last_leave_click = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            observer.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from observer.wait_scene(DAILY_XIANYUAN_RETURN_LAYER0_SCENE_IDS, wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, observer.frame_data_url or "")
            )
            if scene_id == 34:
                with self._lock:
                    self._log_locked("success", f"日常_挑战仙缘：已回到世界 #34 {score:.0f}%")
                return "success"
            if scene_id == 85:
                # The result can leave us in the shared battle scene. Its
                # declared exit/confirmation graph owns the action; guessing
                # the result-page button here repeatedly misses that exit.
                yield from observer.go_scene(34, wait=float(payload.get("battle_leave_timeout") or 90.0))
                return "success"
            lines = observer.ocr_fragments(frame)
            text = observer.ocr_text(frame)
            last_text = text or last_text
            # 离开确认由 current_scene 的弹窗守护统一处理，业务不猜确认坐标。
            now = time.monotonic()
            if now - last_leave_click >= 3.0:
                leave_matches = self._daily_xianyuan_text_button_matches(
                    lines,
                    r"离\s*开|离开",
                    left_ratio=0.72,
                    right_ratio=1.0,
                    top_ratio=0.35,
                    bottom_ratio=0.72,
                    width=width,
                    height=height,
                )
                if leave_matches:
                    x, y, matched_text = leave_matches[0]
                    with self._lock:
                        self._log_locked("action", f"日常_挑战仙缘：点击「{matched_text}」")
                    observer.click_frame_point(View(ref_image), x, y)
                else:
                    observer.click_frame_point(View(ref_image), width * 0.92, height * 0.08)
                yield from observer.wait_action_settle(float(payload.get("xianyuan_leave_settle_seconds") or 2.0))
                last_leave_click = now
            if now >= deadline:
                raise TimeoutError(f"日常_挑战仙缘：点击离开后等待返回世界超时，OCR={last_text[:120]}")

    def _daily_xianyuan_dialogue_button_matches(
        self,
        lines: list[dict[str, Any]],
        image199: dict[str, Any],
        pattern: str,
    ) -> list[tuple[float, float, str]]:
        width, height = self._frame_size(image199)
        left = width * 0.45
        right = width * 0.97
        top = height * 0.45
        bottom = height * 0.78
        matches: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text or not re.search(pattern, text):
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if left <= cx <= right and top <= cy <= bottom:
                matches.append((cx, cy, text))
        return sorted(matches, key=lambda item: (item[1], item[0]))

    def _return_daily_xianyuan_current_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        observer = self._fanxiu_observer(ctx, stop_event)
        _wait_scene_match = yield from observer.wait_scene(DAILY_XIANYUAN_RETURN_LAYER0_SCENE_IDS, wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, observer.frame_data_url or "")
        )
        if scene_id == 34:
            with self._lock:
                self._status.update({"current_scene": 34, "updated_at": time.time()})
            return "success"
        if scene_id == 69:
            return (yield from self._return_daily_xianyuan_to_world(ctx, stop_event))
        if scene_id in {197, 198}:
            image = ctx.get("images", {}).get(scene_id)
            if not isinstance(image, dict):
                raise RuntimeError(f"日常_挑战仙缘：缺少 #{scene_id} 标注，无法安全返回世界")
            back_shape = self._find_shape(image, "返回")
            if back_shape is None:
                raise RuntimeError(f"日常_挑战仙缘：缺少 #{scene_id}「返回」标注，无法安全返回世界")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_挑战仙缘：从 #{scene_id} 返回日常/世界",
                    phase="daily_xianyuan_return_current",
                    current_scene=scene_id,
                )
            self._log_locked("action", f"日常_挑战仙缘：点击 #{scene_id}「返回」")
            observer.click_shape_center(View(image), "返回")
            yield from observer.wait_action_settle(2.0)
            _wait_scene_match = yield from observer.wait_scene([34, 69], wait=5.0, required=False)
            (next_scene_id, next_score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, observer.frame_data_url or "")
            )
            if next_scene_id == 34:
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                    self._log_locked("success", f"日常_挑战仙缘：已回到世界 #34 {next_score:.0f}%")
                return "success"
            if next_scene_id == 69:
                return (yield from self._return_daily_xianyuan_to_world(ctx, stop_event))
            raise RuntimeError(f"日常_挑战仙缘：点击 #{scene_id}「返回」后未回到 #69/#34，当前 #{next_scene_id or 'unknown'} {next_score:.0f}%")
        raise RuntimeError(
            f"日常_挑战仙缘：当前 #{scene_id or 'unknown'} 显示次数已空，但缺少该页返回世界标注，不能按完成处理"
        )

    def _return_daily_xianyuan_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        image69 = ctx.get("images", {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError("日常_挑战仙缘：缺少 #69「日常」标注，无法回世界")
        observer = self._fanxiu_observer(ctx, stop_event)
        _wait_scene_match = yield from observer.wait_scene([69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, observer.frame_data_url or "")
        )
        if scene_id == 34:
            return "success"
        if scene_id != 69:
            raise RuntimeError("日常_挑战仙缘：当前不在 #69 或 #34，缺少后续页面标注，无法安全返回")
        exit_shape = self._find_shape(image69, "退出")
        if exit_shape is None:
            raise RuntimeError("日常_挑战仙缘：缺少 #69「退出」标注，无法回世界")
        with self._lock:
            self._set_status_locked("running", "日常_挑战仙缘：从日常列表返回世界", phase="daily_xianyuan_return_daily", current_scene=69)
            self._log_locked("action", "日常_挑战仙缘：点击 #69「退出」")
        observer.click_shape_center(View(image69), "退出")
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            observer.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from observer.wait_scene([34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, observer.frame_data_url or "")
            )
            last_scene_id, last_score = scene_id, score
            if scene_id == 34:
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                    self._log_locked("success", f"日常_挑战仙缘：已回到世界 #34 {score:.0f}%")
                return "success"
            text = observer.ocr_text(frame)
            last_text = text or last_text
            if time.monotonic() - start >= 18.0:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(f"日常_挑战仙缘：等待世界超时，最后 {scene_text} {last_score:.0f}% OCR={last_text[:120]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_挑战仙缘：等待世界，当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    phase="daily_xianyuan_wait_world",
                    current_scene=scene_id,
                )
        return "success"

    def _daily_xianyuan_progress_done(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        if re.search(r"仙缘斗法|斗法", normalized):
            return False
        match = re.search(r"(?:挑战\s*仙缘|仙缘人物)(.*)", normalized)
        if match:
            fraction = parse_ocr_values(match.group(1), expected_count=2, allow_extra_numbers=True)
            if fraction is None:
                return bool(re.search(r"(?:已完成|完成)", match.group(1)))
            current, total = fraction
            return total > 0 and current >= total
        return bool(re.search(r"(?:挑战\s*仙缘|仙缘人物).*?(?:已完成|完成)", normalized))

    def _daily_xianyuan_row_progress(
        self,
        lines: list[dict[str, Any]],
        title_y: float,
        *,
        y_tolerance: float = 130.0,
    ) -> tuple[int, int] | None:
        fragments: list[str] = []
        for line in lines:
            cy = float(line.get("y") or 0) + float(line.get("h") or 0) / 2
            if abs(cy - title_y) > y_tolerance:
                continue
            text = _sanitize_ocr_text(line.get("text")).translate(FULLWIDTH_DIGIT_TRANSLATION)
            if text:
                fragments.append(text)
        row_text = "".join(fragments)
        if re.search(r"仙缘斗法|斗法", row_text):
            return None
        fraction = parse_ocr_values(row_text, expected_count=2, allow_extra_numbers=True)
        if fraction is None:
            return None
        current_int, total_int = fraction
        current_text = str(current_int)
        if total_int > 0 and current_int > total_int and len(current_text) >= 2:
            suffix_int = int(current_text[-1])
            if suffix_int <= total_int:
                current_int = suffix_int
        return (current_int, total_int) if total_int > 0 else None

    def _daily_xianyuan_entry_matches(self, lines: list[dict[str, Any]], image69: dict[str, Any]) -> list[tuple[float, float, str]]:
        matches: list[tuple[float, float, str]] = []
        list_shape = self._find_shape(image69, "滚动窗口")
        if list_shape is None:
            return matches
        box = self._box(list_shape, image69)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text or re.search(r"仙缘斗法|斗法", text):
                continue
            if not re.search(r"(?:挑战\s*仙缘|仙缘人物)", text):
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if left <= cx <= right and top <= cy <= bottom:
                progress = self._daily_xianyuan_row_progress(lines, cy)
                if progress is not None and progress[0] >= progress[1]:
                    continue
                matches.append((cx, cy, text))
        return sorted(matches, key=lambda item: (item[1], item[0]))

    def _record_daily_xianyuan_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._next_daily_boss_reset_time_text()
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-xianyuan")
        self._persist_scheduler_task_next_time(
            scheduler_task_id,
            next_time,
        )
        self._log("success", f"日常_挑战仙缘：{message}，下次 {next_time}")
        return next_time

    def _daily_xianyuan_runtime_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        override = payload.get("execution_task_snapshot")
        if isinstance(override, dict):
            return dict(override)
        return read_daily_task_runtime_snapshot(1008)
