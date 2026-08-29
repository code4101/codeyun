from __future__ import annotations

"""Runtime-verified count control for the 天地弈局 #680 dialog."""

from dataclasses import dataclass
from typing import Any, Iterator

from backend.core.fanxiu.data_annotation.tasks.integer_count_control import (
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

    values, text = runtime.ocr_numbers_in_shapes(
        assets.settings_scene_id,
        [assets.count_region],
        padding=0,
        crop=True,
    )
    unique = sorted({int(value) for value in values if int(value) > 0})
    if len(unique) != 1:
        raise RuntimeError(f"天地弈局对弈次数无法唯一读回：{text!r}")
    return unique[0]


def set_tiandi_yiju_round_count(
    runtime: Any,
    target: int,
    *,
    assets: TiandiYijuCountAssets | None = None,
    max_adjustments: int = TIANDI_YIJU_MAX_BATCH_ROUNDS,
    force_bound_probe: bool = False,
) -> Iterator[Any]:
    """Set an exact 1..100 count for the exceptional one-round path."""

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
            count_reader=read_tiandi_yiju_round_count,
        )
    )


def set_tiandi_yiju_funded_rounds(
    runtime: Any,
    desired: int,
    available: int,
    *,
    assets: TiandiYijuCountAssets | None = None,
) -> Iterator[Any]:
    """Move once by the Runtime-proven funded fraction and read the real count."""

    target = int(desired)
    maximum = int(available)
    if target <= 0 or maximum <= 0 or target > maximum:
        raise ValueError("天地弈局目标次数必须位于 Runtime 可用次数内")
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
    span = right_x - left_x
    start_x = left_x + span * ((before - 1) / max(1, maximum - 1))
    target_x = left_x + span * ((target - 1) / max(1, maximum - 1))
    runtime.drag_frame_point(
        source.settings_scene_id,
        start_x,
        thumb_y,
        target_x,
        thumb_y,
        duration_ms=600,
    )
    yield from runtime.wait_action_settle(0.8)
    after = read_tiandi_yiju_round_count(runtime, source)
    if not 1 <= after <= maximum:
        raise RuntimeError(
            f"天地弈局比例拖动读回超出资源上限：after={after}, available={maximum}"
        )
    return {
        "before": before,
        "after": after,
        "target": target,
        "available": maximum,
        "slider_fraction": (target - 1) / max(1, maximum - 1),
        "drag_count": 1,
        "fine_adjustment_actions": 0,
    }


__all__ = [
    "TIANDI_YIJU_AUTO_DIALOG_SCENE",
    "TIANDI_YIJU_MAX_BATCH_ROUNDS",
    "TiandiYijuCountAssets",
    "read_tiandi_yiju_round_count",
    "set_tiandi_yiju_round_count",
    "set_tiandi_yiju_funded_rounds",
]
