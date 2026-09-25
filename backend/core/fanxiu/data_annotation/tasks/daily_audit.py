"""遍历日常列表并记录审计结果；识别规则由 observations 模块提供。"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.kernel_scheduler_control import record_world_discovery
from .daily_audit_observations import (
    daily_audit_task_identity,
    normalize_daily_audit_title,
    daily_audit_row_done,
    parse_daily_audit_rows,
    merge_daily_audit_rows,
)


class DailyAuditTaskMixin:
    _daily_audit_task_identity = staticmethod(daily_audit_task_identity)
    _daily_audit_normalize_title = staticmethod(normalize_daily_audit_title)
    _daily_audit_row_done = staticmethod(daily_audit_row_done)
    _merge_daily_audit_rows = staticmethod(merge_daily_audit_rows)

    def _daily_audit_visible_rows(
        self, lines: list[dict[str, Any]], image69: dict[str, Any], *, y_tolerance: float = 150.0,
    ) -> list[dict[str, Any]]:
        list_shape = self._find_shape(image69, "滚动窗口")
        if list_shape is None:
            raise RuntimeError("缺少 #69「滚动窗口」标注，无法遍历日常列表")
        frame_width, _ = self._frame_size(image69)
        return parse_daily_audit_rows(
            lines, self._box(list_shape, image69), frame_width=frame_width, y_tolerance=y_tolerance,
        )

    def _record_daily_audit_result(self, audit: dict[str, Any]) -> None:
        record_world_discovery("daily_audit", audit)

    def _execute_daily_audit_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        image69 = (ctx.get("images") or {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError("缺少 #69「日常」标注，无法遍历日常列表")

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
                label="日常_复核",
            )
        if scene_id != 69:
            raise RuntimeError("日常_复核：未能进入 #69 日常页，无法读取次数")

        max_scrolls = self._payload_int(payload, "max_scrolls", default=30)

        view69 = context.view(69)
        list_shape = context.shape(69, "滚动窗口")
        rows: list[dict[str, Any]] = []
        reached_boundaries: dict[str, bool] = {}
        # Entry may retain any prior scroll offset. Collect toward both ends;
        # pixel similarity is not proof that sparse text rows stopped moving.
        for direction in ("up", "down"):
            previous_keys: set[str] = set()
            unchanged_count = 0
            reached_boundaries[direction] = False
            for index in range(max_scrolls + 1):
                self._raise_if_stopped(stop_event)
                with self._lock:
                    self._set_status_locked("running", f"日常_复核：读取日常列表 {direction} {index + 1}/{max_scrolls + 1}", phase="daily_audit_scan", current_scene=69)
                frame = context.cur_frame(update=True)
                lines = self._ocr_fragments_in_scene_shapes(ctx, frame, image69)
                self._ensure_daily_list_frame(ctx, frame, lines, task_label="日常_复核")
                visible_rows = self._daily_audit_visible_rows(lines, image69)
                rows = self._merge_daily_audit_rows(rows, visible_rows)
                keys = {
                    str(row.get("task_id") or row.get("task_type") or row.get("title") or "").strip()
                    for row in visible_rows
                } - {""}
                unchanged_count = unchanged_count + 1 if keys and keys == previous_keys else 0
                previous_keys = keys
                if unchanged_count >= 2:
                    reached_boundaries[direction] = True
                    break
                if index >= max_scrolls:
                    break
                # The next iteration must read the post-scroll frame even
                # when the visual change detector returns False.
                yield from context.scroll_shape_content(view69, list_shape, direction=direction)

        scan_complete = all(reached_boundaries.values())

        incomplete = [row for row in rows if not bool(row.get("done"))]
        completed = [row for row in rows if bool(row.get("done"))]
        mapped_incomplete = [row for row in incomplete if str(row.get("task_id") or "")]
        unmapped_incomplete = [row for row in incomplete if not str(row.get("task_id") or "")]
        mapped_completed = [row for row in completed if str(row.get("task_id") or "")]
        unmapped_completed = [row for row in completed if not str(row.get("task_id") or "")]
        audit = {
            "updated_at": time.time(),
            "updated_at_text": job_now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_scene": 69,
            "scan_complete": scan_complete,
            "scan_boundaries": reached_boundaries,
            "row_count": len(rows),
            "rows": rows,
            "incomplete": incomplete,
            "completed": completed,
            "mapped_incomplete": mapped_incomplete,
            "unmapped_incomplete": unmapped_incomplete,
            "mapped_completed": mapped_completed,
            "unmapped_completed": unmapped_completed,
            "incomplete_task_ids": [str(row.get("task_id") or "") for row in mapped_incomplete if str(row.get("task_id") or "")],
            "completed_task_ids": [str(row.get("task_id") or "") for row in mapped_completed if str(row.get("task_id") or "")],
            "message": f"日常页复核{'完整' if scan_complete else '未完整'}：读取 {len(rows)} 条，已完成 {len(completed)} 条，未完成 {len(incomplete)} 条，未完成已映射 {len(mapped_incomplete)} 条",
        }
        self._record_daily_audit_result(audit)
        if not scan_complete:
            raise RuntimeError(
                f"{audit['message']}；达到每方向 {max_scrolls} 次滚动上限，"
                f"未确认全部边界 {reached_boundaries}，已保留部分复核事实"
            )
        with self._lock:
            self._set_status_locked("success", audit["message"], phase="daily_audit_done", current_scene=69)
            self._log_locked("success", audit["message"])
        return "success"
