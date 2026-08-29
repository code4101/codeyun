from __future__ import annotations

"""Verified round-count control for the 天地弈局 #680 dialog.

The adapter reuses the activity-neutral integer-slider transaction established
by Yunmeng.  It only supplies #680's semantic Shape names and adds a bounded
hard safety ceiling of 100 rounds.  All values are read back from the current
OCR region; neither saved screenshots nor slider pixel positions are treated
as values.  Deliberately setting the native maximum is not a supported
operation: the live control can expose several thousand irreversible rounds.
"""

from dataclasses import dataclass
from typing import Any, Iterator

from backend.core.fanxiu.data_annotation.tasks.integer_count_control import (
    read_positive_integer_count,
    set_minimum_then_increment_count,
)


TIANDI_YIJU_AUTO_DIALOG_SCENE = 680
TIANDI_YIJU_MAX_BATCH_ROUNDS = 100


@dataclass(frozen=True)
class TiandiYijuCountAssets:
    """Named #680 controls required by the shared integer-slider transaction."""

    settings_scene_id: int = TIANDI_YIJU_AUTO_DIALOG_SCENE
    # Reuse the current #680 OCR region.  Its title is historical; OCR readback
    # still consumes the live number rendered inside that region.
    count_region: str = "单次对弈"
    count_decrease: str = "对弈次数_减少"
    count_increase: str = "对弈次数_增加"
    count_slider_thumb: str = "对弈次数_滑块"
    count_minimum_marker: str | None = None
    count_slider_left_anchor: str | None = None
    count_slider_right_anchor: str | None = None

    def __post_init__(self) -> None:
        if int(self.settings_scene_id) <= 0:
            raise ValueError("天地弈局次数配置缺少场景编号")
        for title in (
            self.count_region,
            self.count_decrease,
            self.count_increase,
            self.count_slider_thumb,
        ):
            if not str(title).strip():
                raise ValueError("天地弈局次数配置的 Shape 名称不得为空")
        if bool(self.count_slider_left_anchor) != bool(self.count_slider_right_anchor):
            raise ValueError("天地弈局次数滑杆左右锚点必须成对提供")


def read_tiandi_yiju_round_count(
    runtime: Any,
    assets: TiandiYijuCountAssets,
) -> int:
    """Read one positive round count from #680's current OCR region."""

    return read_positive_integer_count(
        runtime,
        assets,
        count_label="天地弈局对弈次数",
    )


def set_tiandi_yiju_round_count(
    runtime: Any,
    target: int,
    *,
    assets: TiandiYijuCountAssets | None = None,
    max_adjustments: int = TIANDI_YIJU_MAX_BATCH_ROUNDS,
    force_bound_probe: bool = False,
) -> Iterator[Any]:
    """Set an exact, bounded batch count and verify its live readback.

    :param runtime: The behavior-tree Runtime that owns OCR and GUI actions.
    :param target: A positive integer no larger than 100.
    :param assets: Named #680 controls; defaults never imply pixel positions.
    :param max_adjustments: Maximum verified ``+`` actions after resetting to 1.
    :param force_bound_probe: Unsupported.  天地弈局 must never probe the native
        right bound because the live maximum can be several thousand rounds.
    :return dict: OCR-verified adjustment evidence.
    """

    if force_bound_probe:
        raise ValueError("天地弈局禁止向右探测原生滑杆上限")
    return (
        yield from set_minimum_then_increment_count(
            runtime,
            assets or TiandiYijuCountAssets(),
            target,
            maximum=TIANDI_YIJU_MAX_BATCH_ROUNDS,
            max_adjustments=max_adjustments,
            count_label="天地弈局单批次数",
        )
    )


def set_tiandi_yiju_all_funded_rounds(
    runtime: Any,
    expected_maximum: int,
    *,
    assets: TiandiYijuCountAssets | None = None,
) -> Iterator[Any]:
    """Select the Runtime-proven funded maximum with coarse slider gestures.

    This is the explicit exhaust-resources path.  It is allowed only when the
    caller has already read the exact natural-strength plus strength-item
    budget and supplies that value as ``expected_maximum``.  The GUI's live
    right bound must read back to the same integer before 对弈 is permitted.
    """

    expected = int(expected_maximum)
    if expected <= 0:
        raise ValueError("天地弈局可用挑战次数必须为正整数")
    source = assets or TiandiYijuCountAssets()
    before = read_tiandi_yiju_round_count(runtime, source)
    left_x, thumb_y = runtime.shape_center(
        source.settings_scene_id,
        source.count_slider_thumb,
    )
    right_button_x, _right_button_y = runtime.shape_center(
        source.settings_scene_id,
        source.count_increase,
    )
    thumb_box = runtime.shape_box(
        source.settings_scene_id,
        source.count_slider_thumb,
    )
    right_x = right_button_x - float(thumb_box.get("w") or 0.0) * 0.5
    if right_x <= left_x:
        raise RuntimeError("天地弈局次数滑轨几何无效")
    safe_right_x = right_button_x + (right_x - left_x) * 0.15
    current = before
    drag_count = 0
    while current != expected and drag_count < 4:
        fraction = (current - 1) / max(1, expected - 1)
        estimated_thumb_x = left_x + (right_x - left_x) * fraction
        runtime.drag_frame_point(
            source.settings_scene_id,
            estimated_thumb_x,
            thumb_y,
            safe_right_x,
            thumb_y,
            duration_ms=650,
        )
        drag_count += 1
        yield from runtime.wait_action_settle(0.8)
        updated = read_tiandi_yiju_round_count(runtime, source)
        if updated <= current:
            raise RuntimeError(
                f"天地弈局滚动条未向目标推进：调整前={current}，调整后={updated}"
            )
        current = updated
    after = current
    if after != expected:
        raise RuntimeError(
            "天地弈局滑杆上限与 Runtime 可用资源不一致："
            f"Runtime={expected}，GUI读回={after}"
        )
    return {
        "before": before,
        "after": after,
        "target": expected,
        "slider_fraction": 1.0,
        "drag_count": drag_count,
        "fine_adjustment_actions": 0,
    }


__all__ = [
    "TIANDI_YIJU_AUTO_DIALOG_SCENE",
    "TIANDI_YIJU_MAX_BATCH_ROUNDS",
    "TiandiYijuCountAssets",
    "read_tiandi_yiju_round_count",
    "set_tiandi_yiju_round_count",
    "set_tiandi_yiju_all_funded_rounds",
]
