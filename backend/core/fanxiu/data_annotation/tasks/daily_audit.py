"""读取原生日常显示集合并记录审计；作业映射由 observations 模块提供。"""
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

    def _execute_daily_audit_task(self, ctx, stop_event, payload=None):
        """Audit the live selected daily list, without scrolling or claiming.

        ScreenTask already evaluates native visibility conditions. Its full
        input list, server membership and localized titles replace repeated
        OCR scrolling. These are UI task facts, not completion certificates
        for every mapped Job's independent business objective.
        """
        from backend.core.fanxiu.instrumentation.daily_task_panel import read_daily_task_display_snapshot
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(
            ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None,
            stop_event=stop_event)
        yield from context.enter_daily_list_direct(label="日常_复核")
        snapshot = read_daily_task_display_snapshot()
        if snapshot.get('complete') is not True or snapshot.get('title_mapping_verified') is not True:
            raise RuntimeError('日常_复核：当前显示集合或原生标题不完整')
        rows = []
        for native in snapshot['rows']:
            title = normalize_daily_audit_title(native['title'])
            identity = daily_audit_task_identity(title) or {}
            progress = None if native['turn'] is None else {
                'current': native['turn'], 'total': native['target_turn']}
            rows.append({**identity, 'title': title, 'text': title,
                'progress': progress, 'row_complete': True, 'done': native['done'],
                'native_task_id': native['task_id'], 'native_status': native['status'],
                'progress_source': native['progress_source']})
        incomplete = [row for row in rows if not row['done']]
        completed = [row for row in rows if row['done']]
        mapped_incomplete = [row for row in incomplete if row.get('task_id')]
        mapped_completed = [row for row in completed if row.get('task_id')]
        audit = {
            'updated_at': time.time(),
            'updated_at_text': job_now().strftime('%Y-%m-%d %H:%M:%S'),
            'source_scene': 69, 'source': snapshot['source'],
            'scan_complete': True, 'scan_boundaries': None,
            'scroll_boundary_verification_required': False,
            'native_evidence': {k: snapshot[k] for k in ('pid', 'process_start_ticks')},
            'row_count': len(rows), 'rows': rows, 'incomplete': incomplete,
            'completed': completed, 'mapped_incomplete': mapped_incomplete,
            'unmapped_incomplete': [row for row in incomplete if not row.get('task_id')],
            'mapped_completed': mapped_completed,
            'unmapped_completed': [row for row in completed if not row.get('task_id')],
            'incomplete_task_ids': [row['task_id'] for row in mapped_incomplete],
            'completed_task_ids': [row['task_id'] for row in mapped_completed],
            'message': f'日常页原生复核完整：读取 {len(rows)} 条，已完成 {len(completed)} 条，未完成 {len(incomplete)} 条',
        }
        self._record_daily_audit_result(audit)
        self._log('info', audit['message'])
        yield from context.go_scene(34)
        with self._lock:
            self._set_status_locked('success', audit['message'], phase='daily_audit_done', current_scene=34)
        return {'result': 'success', 'audit': audit, 'current_scene': 34}
