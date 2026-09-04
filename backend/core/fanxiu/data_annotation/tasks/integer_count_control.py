from __future__ import annotations

"""Activity-neutral, closed-loop positive-integer slider control."""

from dataclasses import dataclass
from math import ceil
from typing import Any, Callable, Iterator, Mapping, Protocol


class IntegerCountAssets(Protocol):
    settings_scene_id: int
    count_region: str
    count_decrease: str
    count_increase: str
    count_slider_thumb: str
    count_minimum_marker: str | None
    count_slider_left_anchor: str | None
    count_slider_right_anchor: str | None
    count_slider_left_center_offset: float
    count_slider_right_center_offset: float


@dataclass(frozen=True)
class IntegerSliderAssets:
    settings_scene_id: int
    count_region: str = "挑战次数"
    count_decrease: str = "挑战次数_减少"
    count_increase: str = "挑战次数_增加"
    count_slider_thumb: str = "挑战次数_滑块"
    count_minimum_marker: str | None = None
    count_slider_left_anchor: str | None = None
    count_slider_right_anchor: str | None = None
    count_slider_left_center_offset: float = 0.0
    count_slider_right_center_offset: float = 0.0

    def __post_init__(self) -> None:
        if self.settings_scene_id <= 0:
            raise ValueError("整数滑轨缺少有效设置场景")
        if any(not str(value).strip() for value in (
            self.count_region, self.count_decrease,
            self.count_increase, self.count_slider_thumb,
        )):
            raise ValueError("整数滑轨 Shape 名称不得为空")
        if bool(self.count_slider_left_anchor) != bool(self.count_slider_right_anchor):
            raise ValueError("整数滑轨左右锚点必须成对提供")


def _ocr_count(context: Any, assets: IntegerCountAssets) -> int:
    values, text = context.ocr_numbers_in_shapes(
        assets.settings_scene_id, [assets.count_region]
    )
    unique = sorted({int(value) for value in values if int(value) > 0})
    minimum_marker = getattr(assets, "count_minimum_marker", None)
    if not unique and minimum_marker:
        if context.shape_matches(assets.settings_scene_id, minimum_marker) is not None:
            return 1
    if len(unique) != 1:
        raise RuntimeError(f"整数滑轨 OCR 无法唯一读回：{text!r}")
    return unique[0]


def read_positive_integer_count(
    context: Any,
    assets: IntegerCountAssets,
    *,
    count_label: str,
    runtime_reader: Callable[[], int | Mapping[str, Any]] | None = None,
) -> int:
    """Read via OCR first and use an explicit read-only Runtime fallback."""

    try:
        return _ocr_count(context, assets)
    except RuntimeError as ocr_error:
        if runtime_reader is None:
            raise RuntimeError(f"{count_label}无法读回：{ocr_error}") from ocr_error
        raw = runtime_reader()
        raw = raw.get("current") if isinstance(raw, Mapping) else raw
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"{count_label} Runtime 回退值无效") from exc
        if value <= 0:
            raise RuntimeError(f"{count_label} Runtime 回退值必须为正数")
        return value


def read_integer_slider_count(context: Any, assets: IntegerCountAssets) -> int:
    return read_positive_integer_count(context, assets, count_label="整数滑轨次数")


def _stable_read(context, assets, *, count_label, runtime_reader) -> Iterator[Any]:
    first = read_positive_integer_count(
        context, assets, count_label=count_label, runtime_reader=runtime_reader
    )
    yield from context.wait_action_settle(0.25)
    second = read_positive_integer_count(
        context, assets, count_label=count_label, runtime_reader=runtime_reader
    )
    if first != second:
        raise RuntimeError(f"{count_label}稳定复读不一致：{first} -> {second}")
    return second


def _slider_geometry(context: Any, assets: IntegerCountAssets) -> dict[str, float] | None:
    if not assets.count_slider_left_anchor or not assets.count_slider_right_anchor:
        return None
    if not all(callable(getattr(context, name, None)) for name in (
        "shape_center", "shape_box", "shape_center_in_box", "drag_frame_point"
    )):
        return None
    left = context.shape_center(assets.settings_scene_id, assets.count_slider_left_anchor)
    right = context.shape_center(assets.settings_scene_id, assets.count_slider_right_anchor)
    thumb = context.shape_box(assets.settings_scene_id, assets.count_slider_thumb)
    thumb_width = float(thumb.get("w") or 0.0)
    thumb_height = float(thumb.get("h") or 0.0)
    left_x = float(left[0]) + float(
        getattr(assets, "count_slider_left_center_offset", 0.0) or 0.0
    )
    right_x = (
        float(right[0])
        - float(thumb.get("w") or 0.0) * 0.5
        + float(getattr(assets, "count_slider_right_center_offset", 0.0) or 0.0)
    )
    if right_x <= left_x + 1:
        raise RuntimeError("整数滑轨像素范围无效")
    return {
        "left_x": left_x,
        "right_x": right_x,
        "y": float(thumb.get("y") or 0.0) + thumb_height * 0.5,
        "search_x": left_x - max(4.0, thumb_width),
        "search_y": float(thumb.get("y") or 0.0) - max(4.0, thumb_height * 0.25),
        "search_w": right_x - left_x + max(8.0, thumb_width * 2.0),
        "search_h": max(8.0, thumb_height * 1.5),
    }


def _live_thumb_center(context: Any, assets: IntegerCountAssets, geometry: Mapping[str, float]) -> tuple[float, float]:
    return context.shape_center_in_box(
        assets.settings_scene_id,
        assets.count_slider_thumb,
        {
            "x": geometry["search_x"],
            "y": geometry["search_y"],
            "w": geometry["search_w"],
            "h": geometry["search_h"],
        },
    )


def _drag_pixels(context, assets, start_x: float, target_x: float, y: float) -> None:
    context.drag_frame_point(
        assets.settings_scene_id, start_x, y, target_x, y,
        duration_ms=1000,
    )


def _proportional_position(
    context, assets, desired, *, before, maximum, geometry, count_label, runtime_reader
) -> Iterator[Any]:
    range_probe = False
    if maximum is None:
        if runtime_reader is None:
            raise RuntimeError(f"{count_label}缺少可证明的滑轨最大值")
        raw = runtime_reader()
        maximum = int(raw.get("maximum") or 0) if isinstance(raw, Mapping) else 0
    if maximum <= 1 or desired > maximum:
        raise RuntimeError(f"{count_label}超出滑轨范围：target={desired}, maximum={maximum}")
    fraction = (desired - 1) / (maximum - 1)
    if geometry is None:
        raise RuntimeError(f"{count_label}缺少局部滑块定位能力")
    start = _live_thumb_center(context, assets, geometry)
    target_x = geometry["left_x"] + fraction * (geometry["right_x"] - geometry["left_x"])
    drag_attempts: list[dict[str, float]] = []
    landed = start
    position_tolerance = 2.0
    stalled = False
    for _ in range(8):
        live = _live_thumb_center(context, assets, geometry)
        remaining = target_x - live[0]
        if abs(remaining) <= position_tolerance:
            break
        _drag_pixels(context, assets, live[0], target_x, live[1])
        yield from context.wait_action_settle(0.75)
        landed = _live_thumb_center(context, assets, geometry)
        drag_attempts.append({
            "start_x": live[0],
            "commanded_end_x": target_x,
            "actual_end_x": landed[0],
            "remaining_pixels": target_x - landed[0],
        })
        if abs(target_x - landed[0]) <= position_tolerance:
            break
        if abs(landed[0] - live[0]) < 0.5:
            # Some UIs swallow a short first drag.  That does not prove the
            # slider is unusable: stage 2 deliberately probes 1, 2, 4, ...
            # pixels and can discover the minimum effective gesture distance.
            stalled = True
            break
    else:
        raise RuntimeError(f"{count_label}比例定位未在有界次数内到达目标像素")
    # The thumb is now at the proportional target.  Require a stable value
    # before stage 2 measures its local Δd -> Δn relation.
    current = yield from _stable_read(
        context,
        assets,
        count_label=count_label,
        runtime_reader=runtime_reader,
    )
    landed = _live_thumb_center(context, assets, geometry)
    return current, maximum, range_probe, fraction, {
        "before": before,
        "after": current,
        "commanded_start_x": start[0],
        "commanded_end_x": target_x,
        "commanded_pixels": abs(target_x - start[0]),
        "actual_start_x": start[0],
        "actual_end_x": landed[0],
        "actual_pixels": abs(landed[0] - start[0]),
        "stalled": stalled,
        "drag_attempts": drag_attempts,
    }


def _coarse_pixel_converge(
    context, assets, desired, *, current, maximum, threshold, geometry,
    count_label, runtime_reader,
) -> Iterator[Any]:
    probes: list[dict[str, Any]] = []
    interpolation_rows: list[dict[str, Any]] = []
    if geometry is None:
        return current, probes, interpolation_rows, "no_pixel_geometry"
    for _ in range(5):
        error = desired - current
        if abs(error) <= threshold:
            return current, probes, interpolation_rows, "within_threshold"
        sign = 1 if error > 0 else -1
        effective: tuple[float, int] | None = None
        start = _live_thumb_center(context, assets, geometry)
        available_pixels = (
            geometry["right_x"] - start[0]
            if sign > 0
            else start[0] - geometry["left_x"]
        )
        distances: list[float] = []
        distance = 1.0
        while distance < available_pixels:
            distances.append(distance)
            distance *= 2.0
        if available_pixels >= 0.5:
            distances.append(float(available_pixels))
        for distance in distances:
            before = current
            start = _live_thumb_center(context, assets, geometry)
            start_x = start[0]
            target_x = min(geometry["right_x"], max(geometry["left_x"], start_x + sign * distance))
            if abs(target_x - start_x) < 0.5:
                break
            _drag_pixels(context, assets, start_x, target_x, geometry["y"])
            yield from context.wait_action_settle(0.5)
            current = yield from _stable_read(
                context,
                assets,
                count_label=count_label,
                runtime_reader=runtime_reader,
            )
            landed = _live_thumb_center(context, assets, geometry)
            delta = current - before
            actual_delta = landed[0] - start_x
            probes.append({
                "commanded_pixels": abs(target_x - start_x),
                "actual_pixels": abs(actual_delta),
                "count_delta": delta,
            })
            commanded_delta = target_x - start_x
            if delta * sign > 0 and actual_delta * sign > 0:
                effective = (commanded_delta, delta)
                break
            # A sub-pixel/one-pixel gesture may be interpreted as a tap while
            # the thumb is still settling.  It is not yet a usable d/n probe;
            # keep increasing d and accept only the first same-direction move.
        if effective is None:
            raise RuntimeError(f"{count_label}递增像素拖拽未产生有效变化")
        probe_pixel_delta, probe_count_delta = effective
        if abs(desired - current) < abs(probe_count_delta):
            return current, probes, interpolation_rows, "within_drag_grain"
        start = _live_thumb_center(context, assets, geometry)
        # A probe establishes the local linear relation Δd -> Δn.  With
        # e = y - x, the next signed drag is D = e / Δn * Δd.  The signed
        # probe values make the error itself determine the drag direction.
        error = desired - current
        drag_delta = error / probe_count_delta * probe_pixel_delta
        target_x = min(
            geometry["right_x"],
            max(geometry["left_x"], start[0] + drag_delta),
        )
        before = current
        _drag_pixels(context, assets, start[0], target_x, start[1])
        yield from context.wait_action_settle(0.75)
        current = yield from _stable_read(
            context,
            assets,
            count_label=count_label,
            runtime_reader=runtime_reader,
        )
        landed = _live_thumb_center(context, assets, geometry)
        interpolation_rows.append({
            "before": before,
            "after": current,
            "commanded_pixels": abs(target_x - start[0]),
            "actual_pixels": abs(landed[0] - start[0]),
            "probe_pixel_delta": probe_pixel_delta,
            "probe_count_delta": probe_count_delta,
            "calculated_drag_delta": drag_delta,
        })
    raise RuntimeError(
        f"{count_label}拖拽逼近未进入颗粒度范围："
        f"current={current}, target={desired}"
    )


def _fine_tune_batches(
    context, assets, desired, *, current, count_label, runtime_reader
) -> Iterator[Any]:
    batches: list[dict[str, int]] = []
    click = getattr(context, "click_shape_center_fast", None)
    if not callable(click):
        click = context.click_shape_center
    for index in range(5):
        if current == desired:
            return current, batches
        residual = abs(desired - current)
        clicks = residual if residual < 5 else max(5, ceil(residual / (5 - index)))
        action = assets.count_increase if current < desired else assets.count_decrease
        before = current
        for _ in range(clicks):
            click(assets.settings_scene_id, action)
        # Fast clicks are queued by the game UI.  Read once only after the
        # whole batch has drained; otherwise a transient x == y can be
        # followed by late clicks from the same batch.
        yield from context.wait_action_settle(1.5)
        current = read_positive_integer_count(
            context, assets, count_label=count_label, runtime_reader=runtime_reader
        )
        batches.append({"before": before, "after": current, "clicks": clicks})
        if current == before:
            # GUI input can occasionally drop an isolated +/- click.  A lost
            # click is safe and consumes one of the five bounded batches; the
            # next batch recomputes the residual from a fresh read.
            continue
        if (desired - before) * (current - before) < 0:
            raise RuntimeError(f"{count_label}批量精调向反方向变化")
    if current != desired:
        raise RuntimeError(f"{count_label}在5批精调内未收敛：{current} != {desired}")
    return current, batches


def set_verified_integer_slider_count(
    context: Any,
    assets: IntegerCountAssets,
    desired: int,
    *,
    max_adjustments: int,
    force_bound_probe: bool = False,
    count_label: str = "整数滑轨次数",
    maximum: int | None = None,
    runtime_count_reader: Callable[[], int | Mapping[str, Any]] | None = None,
) -> Iterator[Any]:
    """Proportional positioning, pixel feedback, then at most five +/- batches."""

    del force_bound_probe  # source-compatible; historical bound round trip is retired
    if isinstance(desired, bool) or not isinstance(desired, int) or desired <= 0:
        raise ValueError(f"{count_label}必须为正整数")
    threshold = max(1, int(max_adjustments))
    before = read_positive_integer_count(
        context, assets, count_label=count_label, runtime_reader=runtime_count_reader
    )
    if before == desired:
        return {"before": before, "after": before, "phase": "already_exact"}
    geometry = _slider_geometry(context, assets)
    current, observed_maximum, range_probe, fraction, proportional = yield from _proportional_position(
        context, assets, desired, before=before, maximum=maximum, geometry=geometry,
        count_label=count_label, runtime_reader=runtime_count_reader,
    )
    current, probes, interpolation_rows, coarse_exit = yield from _coarse_pixel_converge(
        context, assets, desired, current=current, maximum=observed_maximum,
        threshold=threshold,
        geometry=geometry, count_label=count_label, runtime_reader=runtime_count_reader,
    )
    current, batches = yield from _fine_tune_batches(
        context, assets, desired, current=current,
        count_label=count_label, runtime_reader=runtime_count_reader,
    )
    return {
        "before": before, "after": current, "maximum": observed_maximum,
        "initial_fraction": fraction, "range_probe": range_probe,
        "proportional_drag": proportional,
        "pixel_probes": probes, "interpolation_drags": interpolation_rows,
        "coarse_exit": coarse_exit, "fine_batches": batches,
        "fine_adjustment_actions": sum(row["clicks"] for row in batches),
    }


def set_minimum_then_increment_count(
    context: Any,
    assets: IntegerCountAssets,
    desired: int,
    *,
    maximum: int,
    max_adjustments: int,
    count_label: str,
    count_reader: Callable[[Any, IntegerCountAssets], int] | None = None,
) -> Iterator[Any]:
    runtime_reader = (
        (lambda: count_reader(context, assets)) if count_reader is not None else None
    )
    return (yield from set_verified_integer_slider_count(
        context, assets, desired, maximum=maximum,
        max_adjustments=max_adjustments, count_label=count_label,
        runtime_count_reader=runtime_reader,
    ))


__all__ = [
    "IntegerCountAssets", "IntegerSliderAssets", "read_integer_slider_count",
    "read_positive_integer_count", "set_minimum_then_increment_count",
    "set_verified_integer_slider_count",
]
