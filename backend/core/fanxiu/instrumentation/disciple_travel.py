"""Observe an invitation's native travel animation without advancing it.

DaoLvTravelView.ShowFinish runs only after both text and slider finish,
followed by _DelayShowFinish. Text advances one character per Update; the
configured interval therefore estimates duration, not a completion witness.
"""
from __future__ import annotations

import math
from typing import Any

from .runtime_memory import FanxiuRuntimeMemoryError
from .ui_runtime_context import (
    read_active_ui_window_panels, read_ui_object_field, read_ui_runtime_snapshot,
)


def read_disciple_travel_animation_snapshot() -> dict[str, Any]:
    """Read the current animation's timing inputs; never invoke game methods.

    Absent or uninitialized animation data stays incomplete. A complete
    projection does not prove recruitment, consumption or a closed result.
    ``estimated_remaining_seconds`` is a nominal duration, subject to frame
    rate and client/server delays; callers still need real result scenes.
    """
    def read(c):
        panels = read_active_ui_window_panels(c, 'DaoLvTravelView')
        if not panels:
            return {'complete': False, 'read_only': True,
                    'reason': 'disciple_travel_animation_not_open'}
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('同游动画窗口身份不唯一', code='identity_mismatch')
        panel = panels[0]

        def number(name):
            value = read_ui_object_field(c, panel.address, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise FanxiuRuntimeMemoryError(f'同游动画 {name} 未加载', code='data_not_loaded')
            return float(value)

        values, count = c.reader.list_items(read_ui_object_field(c, panel.address, '_CharList'))
        if count is None or not 0 <= count <= 4096 or count != len(values):
            raise FanxiuRuntimeMemoryError('同游剧情剩余字符未完整加载', code='data_not_loaded')
        interval = number('textShowTime')
        elapsed = number('_CurCountDown')
        total = number('_TotalTime')
        slider_elapsed = number('_CurSliderCountDown')
        delay = number('_DelayShowFinish')
        finished = read_ui_object_field(c, panel.address, '_HasFinish')
        if not isinstance(finished, bool) or interval < 0 or total < 0 or elapsed < 0 or slider_elapsed < 0:
            raise FanxiuRuntimeMemoryError('同游动画进度无效', code='runtime_incomplete')
        text_remaining = max(0.0, (count * interval - elapsed) / 1000.0)
        slider_remaining = max(0.0, total - slider_elapsed)
        estimate = max(0.0, delay) if finished else max(text_remaining, slider_remaining) + max(0.0, delay)
        return {'complete': True, 'read_only': True,
                'remaining_text_characters': count, 'character_interval_ms': interval,
                'slider_remaining_seconds': slider_remaining, 'animation_finished': finished,
                'finish_delay_seconds': max(0.0, delay),
                'estimated_remaining_seconds': estimate, 'result_verified': False,
                'source': 'active_DaoLvTravelView',
                'evidence': {'pid': c.binding.pid, 'process_start_ticks': c.binding.process_start_ticks}}
    return read_ui_runtime_snapshot([], read, fast=True)
