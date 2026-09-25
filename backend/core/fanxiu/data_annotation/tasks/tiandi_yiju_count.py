from __future__ import annotations

"""Runtime-verified count control for the 天地弈局 #680 dialog."""

from dataclasses import dataclass, replace
from typing import Any, Callable, Iterator, Mapping

from backend.core.fanxiu.runtime_gui.integer_count_control import (
    set_verified_integer_slider_count,
)


TIANDI_YIJU_AUTO_DIALOG_SCENE = 680
TIANDI_YIJU_MAX_BATCH_ROUNDS = 100
TIANDI_YIJU_COUNT_FINE_THRESHOLD = 10


@dataclass(frozen=True)
class TiandiYijuCountAssets:
    """Named #680 controls required by the shared integer-slider transaction."""

    settings_scene_id: int = TIANDI_YIJU_AUTO_DIALOG_SCENE
    # Reuse the current #680 OCR region.  Its title is historical; OCR readback
    # still consumes the live number rendered inside that region.
    count_region: str = "单次对弈"
    count_decrease: str = "对弈次数_减少"
    count_increase: str = "对弈次数_增加"
    count_decrease_large: str | None = None
    count_increase_large: str | None = None
    count_large_step: int | None = None
    count_slider_thumb: str = "对弈次数_滑块"
    count_slider_track: str | None = None
    count_minimum_marker: str | None = None
    # The retained #680 reference explicitly records the thumb at minimum.
    # The increase button's left edge proves the opposite endpoint.
    count_slider_left_anchor: str | None = "对弈次数_滑块"
    count_slider_right_anchor: str | None = "对弈次数_增加"
    count_slider_left_center_offset: float = 0.0
    count_slider_right_center_offset: float = 0.0

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


RuntimeCountReader = Callable[[], int | Mapping[str, Any]]


def _default_runtime_count_reader() -> Mapping[str, Any]:
    from backend.core.fanxiu.instrumentation.tiandi_yiju import (
        read_tiandi_yiju_auto_count_snapshot,
    )

    return read_tiandi_yiju_auto_count_snapshot()


def _count_assets_with_proven_bounds(
    context: Any,
    assets: TiandiYijuCountAssets,
) -> TiandiYijuCountAssets:
    if not assets.count_slider_left_anchor or not assets.count_slider_right_anchor:
        raise RuntimeError("天地弈局次数缺少已证明的滑轨左右边界")
    increase = context.shape_box(assets.settings_scene_id, assets.count_increase)
    increase_width = float(increase.get("w") or 0.0)
    if increase_width <= 0:
        raise RuntimeError("天地弈局次数滑轨右边界 Shape 几何无效")
    return replace(
        assets,
        count_slider_right_center_offset=-increase_width * 0.5,
    )


def _runtime_count_range(reader: RuntimeCountReader) -> tuple[int, int]:
    raw = reader()
    if not isinstance(raw, Mapping):
        raise RuntimeError("天地弈局次数 Runtime 未返回原生范围")
    current = int(raw.get("current") or 0)
    maximum = int(raw.get("maximum") or 0)
    if current <= 0 or maximum < current:
        raise RuntimeError(
            "天地弈局次数 Runtime 原生范围无效："
            f"current={current}, maximum={maximum}"
        )
    return current, maximum


def _positive_requested_rounds(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label}必须为正整数")
    return int(value)


def read_tiandi_yiju_round_count(
    context: Any,
    assets: TiandiYijuCountAssets,
) -> int:
    """Read one positive round count from #680's current OCR region."""

    values, text = context.ocr_numbers_in_shapes(
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
    context: Any,
    target: int,
    *,
    assets: TiandiYijuCountAssets | None = None,
    runtime_count_reader: RuntimeCountReader | None = None,
) -> Iterator[Any]:
    """Set an exact count within #680's live native slider range."""

    requested = _positive_requested_rounds(target, label="天地弈局单批次数")
    reader = runtime_count_reader or _default_runtime_count_reader
    _current, native_maximum = _runtime_count_range(reader)
    if requested > native_maximum:
        raise RuntimeError(
            "天地弈局单批次数超过当前面板原生上限："
            f"target={requested}, maximum={native_maximum}"
        )
    source = _count_assets_with_proven_bounds(
        context, assets or TiandiYijuCountAssets()
    )
    result = yield from set_verified_integer_slider_count(
        context,
        source,
        requested,
        maximum=native_maximum,
        max_adjustments=TIANDI_YIJU_COUNT_FINE_THRESHOLD,
        count_label="天地弈局单批次数",
        runtime_count_reader=reader,
    )
    runtime_after, native_after = _runtime_count_range(reader)
    if runtime_after != requested or native_after != native_maximum:
        raise RuntimeError(
            "天地弈局次数设置后 Runtime 复验失败："
            f"target={requested}, current={runtime_after}, "
            f"maximum={native_after}, initial_maximum={native_maximum}"
        )
    return result


def set_tiandi_yiju_funded_rounds(
    context: Any,
    desired: int,
    available: int,
    *,
    assets: TiandiYijuCountAssets | None = None,
    runtime_count_reader: RuntimeCountReader | None = None,
) -> Iterator[Any]:
    """Set an exact count under separate resource and native-range ceilings."""

    target = _positive_requested_rounds(
        desired, label="天地弈局资源保障批次目标次数"
    )
    maximum = _positive_requested_rounds(
        available, label="天地弈局资源可供次数上限"
    )
    if target > maximum:
        raise ValueError("天地弈局目标次数必须位于 Runtime 可用次数内")
    reader = runtime_count_reader or _default_runtime_count_reader
    _current, native_maximum = _runtime_count_range(reader)
    if target > native_maximum:
        raise RuntimeError(
            "天地弈局目标次数超过当前面板原生上限："
            f"target={target}, native_maximum={native_maximum}, "
            f"resource_capacity={maximum}"
        )
    source = _count_assets_with_proven_bounds(
        context, assets or TiandiYijuCountAssets()
    )
    result = yield from set_verified_integer_slider_count(
        context,
        source,
        target,
        maximum=native_maximum,
        max_adjustments=TIANDI_YIJU_COUNT_FINE_THRESHOLD,
        count_label="天地弈局资源保障批次",
        runtime_count_reader=reader,
    )
    runtime_after, native_after = _runtime_count_range(reader)
    if runtime_after != target or native_after != native_maximum:
        raise RuntimeError(
            "天地弈局资源保障批次 Runtime 复验失败："
            f"target={target}, current={runtime_after}, "
            f"maximum={native_after}, initial_maximum={native_maximum}"
        )
    return {
        **result,
        "target": target,
        "resource_capacity": maximum,
        "native_maximum": native_maximum,
    }


__all__ = [
    "TIANDI_YIJU_AUTO_DIALOG_SCENE",
    "TIANDI_YIJU_MAX_BATCH_ROUNDS",
    "TiandiYijuCountAssets",
    "read_tiandi_yiju_round_count",
    "set_tiandi_yiju_round_count",
    "set_tiandi_yiju_funded_rounds",
]
