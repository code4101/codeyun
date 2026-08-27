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
from types import SimpleNamespace
from typing import Any, Iterator

from backend.core.fanxiu.data_annotation.tasks.yunmeng_native_auto import (
    set_verified_integer_slider_count,
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


def _shared_slider_assets(assets: TiandiYijuCountAssets) -> SimpleNamespace:
    """Project #680 names onto the existing activity-neutral slider contract."""

    return SimpleNamespace(
        settings_scene_id=int(assets.settings_scene_id),
        count_region=assets.count_region,
        count_decrease=assets.count_decrease,
        count_increase=assets.count_increase,
        count_slider_thumb=assets.count_slider_thumb,
        count_minimum_marker=assets.count_minimum_marker,
        count_slider_left_anchor=assets.count_slider_left_anchor,
        count_slider_right_anchor=assets.count_slider_right_anchor,
    )


def read_tiandi_yiju_round_count(
    runtime: Any,
    assets: TiandiYijuCountAssets,
) -> int:
    """Read one positive round count from #680's current OCR region."""

    values, text = runtime.ocr_numbers_in_shapes(
        assets.settings_scene_id,
        [assets.count_region],
    )
    unique = sorted({int(value) for value in values if int(value) > 0})
    if not unique and assets.count_minimum_marker:
        if runtime.shape_matches(
            assets.settings_scene_id,
            assets.count_minimum_marker,
        ) is not None:
            return 1
    if len(unique) != 1:
        raise RuntimeError(f"天地弈局对弈次数无法唯一读回：{text!r}")
    return unique[0]


def set_tiandi_yiju_round_count(
    runtime: Any,
    target: int,
    *,
    assets: TiandiYijuCountAssets | None = None,
    max_adjustments: int = 10,
    force_bound_probe: bool = False,
) -> Iterator[Any]:
    """Set an exact, bounded batch count and verify its live readback.

    :param runtime: The behavior-tree Runtime that owns OCR and GUI actions.
    :param target: A positive integer no larger than 100.
    :param assets: Named #680 controls; defaults never imply pixel positions.
    :param max_adjustments: Maximum +/- correction actions after coarse drag.
    :param force_bound_probe: Use fixed track anchors for calibrated exact mode.
    :return dict: OCR-verified adjustment evidence.
    """

    resolved_assets = assets or TiandiYijuCountAssets()
    if (
        isinstance(target, bool)
        or not isinstance(target, int)
        or target <= 0
        or target > TIANDI_YIJU_MAX_BATCH_ROUNDS
    ):
        raise ValueError(
            "天地弈局单批次数必须为 1.."
            f"{TIANDI_YIJU_MAX_BATCH_ROUNDS} 的整数；禁止使用原生最大值"
        )

    evidence = yield from set_verified_integer_slider_count(
        runtime,
        _shared_slider_assets(resolved_assets),
        int(target),
        max_adjustments=int(max_adjustments),
        force_bound_probe=bool(force_bound_probe),
        count_label="天地弈局对弈次数",
    )
    result = dict(evidence or {})
    try:
        verified = int(result["after"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("天地弈局对弈次数缺少稳定读回证据") from exc
    if verified != int(target):
        raise RuntimeError(
            f"天地弈局对弈次数验证失败：目标 {int(target)}，实际 {verified}"
        )
    result["target"] = int(target)
    return result


__all__ = [
    "TIANDI_YIJU_AUTO_DIALOG_SCENE",
    "TIANDI_YIJU_MAX_BATCH_ROUNDS",
    "TiandiYijuCountAssets",
    "read_tiandi_yiju_round_count",
    "set_tiandi_yiju_round_count",
]
