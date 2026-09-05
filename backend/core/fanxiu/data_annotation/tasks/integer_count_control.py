from __future__ import annotations

"""Activity-neutral, closed-loop positive-integer slider control."""

from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping, Protocol


_MAX_DIRECT_BUTTON_ACTIONS = 30


class IntegerCountAssets(Protocol):
    settings_scene_id: int
    count_region: str
    count_decrease: str
    count_increase: str
    count_decrease_large: str | None
    count_increase_large: str | None
    count_large_step: int | None
    count_slider_thumb: str | None
    count_slider_track: str | None
    count_minimum_marker: str | None
    count_slider_left_anchor: str | None
    count_slider_right_anchor: str | None
    count_slider_left_center_offset: float
    count_slider_right_center_offset: float


@dataclass(frozen=True)
class IntegerButtonAssets:
    """Positive-integer control whose UI exposes only +/- buttons."""

    settings_scene_id: int
    count_region: str = "数量"
    count_decrease: str = "-"
    count_increase: str = "+"
    count_decrease_large: str | None = None
    count_increase_large: str | None = None
    count_large_step: int | None = None

    def __post_init__(self) -> None:
        if self.settings_scene_id <= 0:
            raise ValueError("整数按钮缺少有效设置场景")
        if any(not str(value).strip() for value in (
            self.count_region, self.count_decrease, self.count_increase,
        )):
            raise ValueError("整数按钮 Shape 名称不得为空")
        if bool(self.count_decrease_large) != bool(self.count_increase_large):
            raise ValueError("整数按钮大步减少与增加 Shape 必须成对提供")
        if self.count_decrease_large and (
            self.count_large_step is None or self.count_large_step <= 1
        ):
            raise ValueError("整数按钮大步操作必须提供大于1的步长")
        if not self.count_decrease_large and self.count_large_step is not None:
            raise ValueError("整数按钮未提供大步操作时不得设置大步步长")


@dataclass(frozen=True)
class IntegerSliderAssets:
    settings_scene_id: int
    count_region: str = "挑战次数"
    count_decrease: str = "挑战次数_减少"
    count_increase: str = "挑战次数_增加"
    count_decrease_large: str | None = None
    count_increase_large: str | None = None
    count_large_step: int | None = None
    count_slider_thumb: str | None = "挑战次数_滑块"
    count_slider_track: str | None = None
    count_minimum_marker: str | None = None
    count_slider_left_anchor: str | None = None
    count_slider_right_anchor: str | None = None
    count_slider_left_center_offset: float = 0.0
    count_slider_right_center_offset: float = 0.0

    def __post_init__(self) -> None:
        if self.settings_scene_id <= 0:
            raise ValueError("整数滑轨缺少有效设置场景")
        if any(not str(value).strip() for value in (
            self.count_region, self.count_decrease, self.count_increase,
        )):
            raise ValueError("整数滑轨 Shape 名称不得为空")
        if not self.count_slider_thumb and not self.count_slider_track:
            raise ValueError("整数滑轨必须提供滑块或完整滑条 Shape")
        if bool(self.count_decrease_large) != bool(self.count_increase_large):
            raise ValueError("整数滑轨大步减少与增加 Shape 必须成对提供")
        if self.count_decrease_large and (
            self.count_large_step is None or self.count_large_step <= 1
        ):
            raise ValueError("整数滑轨大步按钮必须提供大于1的步长")
        if not self.count_decrease_large and self.count_large_step is not None:
            raise ValueError("整数滑轨未提供大步按钮时不得设置大步步长")
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
    except (AttributeError, KeyError, RuntimeError, ValueError) as ocr_error:
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
    """Return only after two adjacent bounded samples agree.

    Slider animation and queued input can expose an early transitional value,
    so one mismatching pair is evidence to keep sampling rather than immediate
    failure.  The complete trace is retained when the bounded read never
    settles.
    """

    max_samples = 6
    trace: list[int] = []
    yield from context.wait_action_settle(0.35)
    for index in range(max_samples):
        try:
            value = read_positive_integer_count(
                context,
                assets,
                count_label=count_label,
                runtime_reader=runtime_reader,
            )
        except RuntimeError as exc:
            raise RuntimeError(
                f"{count_label}稳定读回失败，观测轨迹={trace}：{exc}"
            ) from exc
        trace.append(value)
        if len(trace) >= 2 and trace[-1] == trace[-2]:
            return value
        if index + 1 < max_samples:
            yield from context.wait_action_settle(0.25)
    raise RuntimeError(
        f"{count_label}在{max_samples}次有界采样内未稳定，观测轨迹={trace}"
    )


def _slider_geometry(context: Any, assets: IntegerCountAssets) -> dict[str, float] | None:
    if not assets.count_slider_thumb:
        return None
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
    if not assets.count_slider_thumb:
        raise RuntimeError("整数滑轨缺少可定位滑块")
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
    large_step = int(getattr(assets, "count_large_step", 0) or 0)
    large_decrease = getattr(assets, "count_decrease_large", None)
    large_increase = getattr(assets, "count_increase_large", None)
    for _index in range(5):
        if current == desired:
            return current, batches
        residual = abs(desired - current)
        increasing = current < desired
        unit_action = assets.count_increase if increasing else assets.count_decrease
        large_action = large_increase if increasing else large_decrease
        large_clicks = residual // large_step if large_action and large_step > 1 else 0
        unit_clicks = residual - large_clicks * large_step
        before = current
        for _ in range(large_clicks):
            click(assets.settings_scene_id, large_action)
        for _ in range(unit_clicks):
            click(assets.settings_scene_id, unit_action)
        # Fast clicks are queued by the game UI.  Read once only after the
        # whole batch has drained; otherwise a transient x == y can be
        # followed by late clicks from the same batch.
        yield from context.wait_action_settle(1.5)
        current = yield from _stable_read(
            context,
            assets,
            count_label=count_label,
            runtime_reader=runtime_reader,
        )
        batches.append({
            "before": before,
            "after": current,
            "clicks": large_clicks + unit_clicks,
            "large_clicks": large_clicks,
            "unit_clicks": unit_clicks,
        })
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


def _estimated_button_actions(
    assets: IntegerCountAssets,
    *,
    current: int,
    desired: int,
) -> int:
    """Return the minimum known +/- button actions for an exact target."""

    residual = abs(desired - current)
    if residual == 0:
        return 0
    increasing = current < desired
    large_action = (
        getattr(assets, "count_increase_large", None)
        if increasing
        else getattr(assets, "count_decrease_large", None)
    )
    large_step = int(getattr(assets, "count_large_step", 0) or 0)
    if not large_action or large_step <= 1:
        return residual
    return residual // large_step + residual % large_step


def _set_track_only_count(
    context: Any,
    assets: IntegerCountAssets,
    desired: int,
    *,
    before: int,
    maximum: int | None,
    count_label: str,
    runtime_reader: Callable[[], int | Mapping[str, Any]] | None,
    threshold: int,
) -> Iterator[Any]:
    """Control a CommonShop-style track whose thumb has no separate asset."""

    if maximum is None and runtime_reader is not None:
        raw = runtime_reader()
        maximum = int(raw.get("maximum") or 0) if isinstance(raw, Mapping) else 0
    if maximum is None or maximum <= 1 or desired > maximum:
        raise RuntimeError(
            f"{count_label}超出滑轨范围：target={desired}, maximum={maximum}"
        )
    track_name = str(getattr(assets, "count_slider_track", "") or "").strip()
    if not track_name:
        raise RuntimeError(f"{count_label}缺少完整滑条 Shape")
    track = context.shape_box(assets.settings_scene_id, track_name)
    left = float(track.get("x") or 0.0)
    width = float(track.get("w") or 0.0)
    height = float(track.get("h") or 0.0)
    top = float(track.get("y") or 0.0)
    if width <= 1 or height <= 0:
        raise RuntimeError(f"{count_label}完整滑条像素范围无效")
    initial_start_x = left + width * ((before - 1) / (maximum - 1))
    initial_target_x = left + width * ((desired - 1) / (maximum - 1))
    y = top + height / 2.0
    context.drag_frame_point(
        assets.settings_scene_id,
        initial_start_x,
        y,
        initial_target_x,
        y,
        duration_ms=1000,
    )
    yield from context.wait_action_settle(0.75)
    current = yield from _stable_read(
        context,
        assets,
        count_label=count_label,
        runtime_reader=runtime_reader,
    )
    probes: list[dict[str, Any]] = []
    interpolation_rows: list[dict[str, Any]] = []
    coarse_exit = "within_threshold"
    for _ in range(5):
        error = desired - current
        if abs(error) <= threshold:
            coarse_exit = "within_threshold"
            break
        sign = 1 if error > 0 else -1
        count_x = left + width * ((current - 1) / (maximum - 1))
        available = left + width - count_x if sign > 0 else count_x - left
        effective: tuple[float, int] | None = None
        distance = 1.0
        while distance <= max(1.0, available):
            commanded = min(distance, available)
            if commanded < 0.5:
                break
            before_probe = current
            probe_x = min(left + width, max(left, count_x + sign * commanded))
            context.drag_frame_point(
                assets.settings_scene_id,
                count_x,
                y,
                probe_x,
                y,
                duration_ms=1000,
            )
            yield from context.wait_action_settle(0.5)
            current = yield from _stable_read(
                context,
                assets,
                count_label=count_label,
                runtime_reader=runtime_reader,
            )
            delta = current - before_probe
            probes.append({
                "commanded_pixels": commanded,
                "count_delta": delta,
            })
            if delta * sign > 0:
                effective = (sign * commanded, delta)
                break
            distance *= 2.0
        if effective is None:
            raise RuntimeError(f"{count_label}递增像素拖拽未产生有效变化")
        probe_pixels, probe_delta = effective
        error = desired - current
        if abs(error) < abs(probe_delta):
            coarse_exit = "within_drag_grain"
            break
        start_x = left + width * ((current - 1) / (maximum - 1))
        drag_delta = error / probe_delta * probe_pixels
        end_x = min(left + width, max(left, start_x + drag_delta))
        before_interpolation = current
        context.drag_frame_point(
            assets.settings_scene_id,
            start_x,
            y,
            end_x,
            y,
            duration_ms=1000,
        )
        yield from context.wait_action_settle(0.75)
        current = yield from _stable_read(
            context,
            assets,
            count_label=count_label,
            runtime_reader=runtime_reader,
        )
        interpolation_rows.append({
            "before": before_interpolation,
            "after": current,
            "commanded_pixels": abs(end_x - start_x),
            "probe_pixel_delta": probe_pixels,
            "probe_count_delta": probe_delta,
        })
    else:
        raise RuntimeError(
            f"{count_label}拖拽逼近未进入颗粒度范围：current={current}, target={desired}"
        )
    current, batches = yield from _fine_tune_batches(
        context,
        assets,
        desired,
        current=current,
        count_label=count_label,
        runtime_reader=runtime_reader,
    )
    return {
        "before": before,
        "after": current,
        "maximum": maximum,
        "phase": "track_only_closed_loop",
        "proportional_drag": {
            "start_x": initial_start_x,
            "target_x": initial_target_x,
            "fraction": (desired - 1) / (maximum - 1),
        },
        "pixel_probes": probes,
        "interpolation_drags": interpolation_rows,
        "coarse_exit": coarse_exit,
        "fine_batches": batches,
        "fine_adjustment_actions": sum(row["clicks"] for row in batches),
    }


def set_verified_integer_slider_count(
    context: Any,
    assets: IntegerCountAssets,
    desired: int,
    *,
    max_adjustments: int,
    count_label: str = "整数滑轨次数",
    maximum: int | None = None,
    runtime_count_reader: Callable[[], int | Mapping[str, Any]] | None = None,
) -> Iterator[Any]:
    """Proportional positioning, pixel feedback, then at most five +/- batches."""

    if isinstance(desired, bool) or not isinstance(desired, int) or desired <= 0:
        raise ValueError(f"{count_label}必须为正整数")
    threshold = max(1, int(max_adjustments))
    before = read_positive_integer_count(
        context, assets, count_label=count_label, runtime_reader=runtime_count_reader
    )
    if before == desired:
        return {"before": before, "after": before, "phase": "already_exact"}
    direct_actions = _estimated_button_actions(
        assets,
        current=before,
        desired=desired,
    )
    # A short burst of +10/-10 plus the exact unit remainder is both faster
    # and less fragile than repeatedly locating and calibrating a live thumb.
    # The stable reread in _fine_tune_batches absorbs dropped queued clicks.
    if direct_actions <= _MAX_DIRECT_BUTTON_ACTIONS:
        current, batches = yield from _fine_tune_batches(
            context,
            assets,
            desired,
            current=before,
            count_label=count_label,
            runtime_reader=runtime_count_reader,
        )
        return {
            "before": before,
            "after": current,
            "maximum": maximum,
            "phase": "button_fast_path",
            "estimated_button_actions": direct_actions,
            "fine_batches": batches,
            "fine_adjustment_actions": sum(row["clicks"] for row in batches),
        }
    if not assets.count_slider_thumb and getattr(assets, "count_slider_track", None):
        return (yield from _set_track_only_count(
            context,
            assets,
            desired,
            before=before,
            maximum=maximum,
            count_label=count_label,
            runtime_reader=runtime_count_reader,
            threshold=threshold,
        ))
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


def set_verified_integer_button_count(
    context: Any,
    assets: IntegerButtonAssets,
    desired: int,
    *,
    count_label: str = "整数数量",
    runtime_count_reader: Callable[[], int | Mapping[str, Any]] | None = None,
    initial_count: int | None = None,
) -> Iterator[Any]:
    """Set an exact count when the dialog has buttons but no slider.

    This is the fine-tuning stage of the integer controller as a standalone
    path.  It deliberately does not invent slider geometry for legacy dialogs.
    Every click batch is followed by a stable authoritative reread.
    """

    if isinstance(desired, bool) or not isinstance(desired, int) or desired <= 0:
        raise ValueError(f"{count_label}必须为正整数")
    if initial_count is None:
        current = yield from _stable_read(
            context,
            assets,
            count_label=count_label,
            runtime_reader=runtime_count_reader,
        )
    else:
        current = int(initial_count)
        if current <= 0:
            raise ValueError(f"{count_label}初始值必须为正整数")
    before = current
    current, batches = yield from _fine_tune_batches(
        context,
        assets,
        desired,
        current=current,
        count_label=count_label,
        runtime_reader=runtime_count_reader,
    )
    return {
        "before": before,
        "after": current,
        "phase": "button_only_closed_loop" if before != desired else "already_exact",
        "fine_batches": batches,
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
    "IntegerButtonAssets", "IntegerCountAssets", "IntegerSliderAssets", "read_integer_slider_count",
    "read_positive_integer_count", "set_minimum_then_increment_count",
    "set_verified_integer_button_count", "set_verified_integer_slider_count",
]
