"""Activity-neutral, closed-loop positive-integer slider control.

设计文档：C:/home/chenkunze/slns/skills/凡修/references/业务层/数值滑轨配置.md
按比例定位 → 回读反馈逼近 → 精调实现；资产描述与流程函数分别复用。
"""

from __future__ import annotations

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
    """滑轨语义资产及定位锚点；三阶段控制契约见模块设计文档。"""

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
    count_ocr_padding: int = 16

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
        assets.settings_scene_id, [assets.count_region], crop=True,
        padding=getattr(assets, "count_ocr_padding", 16),
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
    read_counts: dict[str, int] | None = None,
) -> int:
    """Prefer an explicit read-only Runtime fact, then fall back to OCR."""

    runtime_error: Exception | None = None
    if runtime_reader is not None:
        try:
            if read_counts is not None:
                read_counts["runtime"] += 1
            raw = runtime_reader()
            raw = raw.get("current") if isinstance(raw, Mapping) else raw
            value = int(raw)
            if value <= 0:
                raise ValueError("Runtime 回退值必须为正数")
            return value
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as exc:
            runtime_error = exc
    try:
        if read_counts is not None:
            read_counts["ocr"] += 1
        return _ocr_count(context, assets)
    except (AttributeError, KeyError, RuntimeError, ValueError) as ocr_error:
        detail = f"；Runtime={runtime_error}" if runtime_error is not None else ""
        raise RuntimeError(f"{count_label}无法读回：{ocr_error}{detail}") from ocr_error


def read_integer_slider_count(context: Any, assets: IntegerCountAssets) -> int:
    return read_positive_integer_count(context, assets, count_label="整数滑轨次数")


def _stable_read(context, assets, *, count_label, runtime_reader, read_counts=None) -> Iterator[Any]:
    """Return only after two adjacent bounded samples agree.

    Slider animation and queued input can expose an early transitional value,
    so one mismatching pair is evidence to keep sampling rather than immediate
    failure.  The complete trace is retained when the bounded read never
    settles.
    """

    max_samples = 6
    trace: list[int | str] = []
    previous: int | None = None
    yield from context.wait_action_settle(0.35)
    for index in range(max_samples):
        try:
            value = read_positive_integer_count(
                context,
                assets,
                count_label=count_label,
                runtime_reader=runtime_reader, read_counts=read_counts,
            )
        except RuntimeError as exc:
            # A transient empty OCR is an observation failure, not a reason
            # to repeat the previous click batch. Keep the same bounded budget.
            trace.append(str(exc))
            previous = None
        else:
            trace.append(value)
            if value == previous:
                return value
            previous = value
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
    context, assets, desired, *, before, maximum, geometry, count_label, runtime_reader, read_counts=None
) -> Iterator[Any]:
    range_probe = False
    if maximum is None:
        if runtime_reader is None:
            raise RuntimeError(f"{count_label}缺少可证明的滑轨最大值")
        if read_counts is not None:
            read_counts["runtime"] += 1
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
    position_tolerance = 2.0
    moved = abs(target_x - start[0]) > position_tolerance
    if moved:
        _drag_pixels(context, assets, start[0], target_x, start[1])
        yield from context.wait_action_settle(0.75)
    # Stage 1 makes one proportional estimate. Repeating that same estimate
    # cannot correct inaccurate track anchors: stage 2 measures the actual
    # local count/pixel relation and owns all subsequent approach drags.
    current = yield from _stable_read(
        context,
        assets,
        count_label=count_label,
        runtime_reader=runtime_reader, read_counts=read_counts,
    )
    landed = _live_thumb_center(context, assets, geometry)
    if moved:
        drag_attempts.append({
            "start_x": start[0],
            "commanded_end_x": target_x,
            "actual_end_x": landed[0],
            "remaining_pixels": target_x - landed[0],
        })
    return current, maximum, range_probe, fraction, {
        "before": before,
        "after": current,
        "commanded_start_x": start[0],
        "commanded_end_x": target_x,
        "commanded_pixels": abs(target_x - start[0]),
        "actual_start_x": start[0],
        "actual_end_x": landed[0],
        "actual_pixels": abs(landed[0] - start[0]),
        "stalled": moved and abs(landed[0] - start[0]) < 0.5,
        "drag_attempts": drag_attempts,
    }


def _coarse_pixel_converge(
    context, assets, desired, *, current, maximum, threshold, geometry,
    count_label, runtime_reader, read_counts=None,
    max_button_actions=_MAX_DIRECT_BUTTON_ACTIONS,
) -> Iterator[Any]:
    """Reuse the observed thumb only between adjacent actions in this call.

    Each drag invalidates the position; stable count feedback is followed by
    a fresh local thumb observation. No yield or GUI action separates that
    observation from the next probe/correction. This avoids capturing the
    same unchanged control twice, without caching positions across ticks.
    尚未真实验收：待验证连续探测与校正的落点和耗时；若落点偏移，先查
    局部 Shape 定位和滑块是否仍在动画中，不延长位置缓存生命周期。
    """
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
                runtime_reader=runtime_reader, read_counts=read_counts,
            )
            landed = _live_thumb_center(context, assets, geometry)
            delta = current - before
            actual_delta = landed[0] - start_x
            start = landed
            probes.append({
                "commanded_pixels": abs(target_x - start_x),
                "actual_pixels": abs(actual_delta),
                "count_delta": delta,
            })
            # A probe is itself an action. Consume its successful result
            # before another drag; a zero-distance swipe can move the thumb.
            if abs(desired - current) <= threshold:
                return current, probes, interpolation_rows, "within_threshold"
            if delta * sign > 0 and actual_delta * sign > 0:
                # Calibrate against observed thumb movement. The game may
                # stop short of the commanded endpoint; using that command
                # would bias every subsequent count-to-pixel correction.
                effective = (actual_delta, delta)
                break
            # A sub-pixel/one-pixel gesture may be interpreted as a tap while
            # the thumb is still settling.  It is not yet a usable d/n probe;
            # keep increasing d and accept only the first same-direction move.
        if effective is None:
            raise RuntimeError(f"{count_label}递增像素拖拽未产生有效变化")
        probe_pixel_delta, probe_count_delta = effective
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
            runtime_reader=runtime_reader, read_counts=read_counts,
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
        # The probe is calibration, not the approach itself. Only after
        # applying D may a small residual enter the button phase.
        if (_estimated_button_actions(assets, current=current, desired=desired)
                <= max_button_actions
                and abs(desired - current) < abs(probe_count_delta)):
            return current, probes, interpolation_rows, "within_drag_grain"
    raise RuntimeError(
        f"{count_label}拖拽逼近未进入颗粒度范围："
        f"current={current}, target={desired}; probes={probes}; corrections={interpolation_rows}"
    )


def _fine_tune_batches(
    context, assets, desired, *, current, count_label, runtime_reader, read_counts=None,
    max_button_actions=_MAX_DIRECT_BUTTON_ACTIONS,
) -> Iterator[Any]:
    batches: list[dict[str, int]] = []
    remaining_clicks = max_button_actions
    no_progress_batches = 0
    click = getattr(context, "click_shape_center_fast", None)
    if not callable(click):
        click = context.click_shape_center
    large_decrease = getattr(assets, "count_decrease_large", None)
    large_increase = getattr(assets, "count_increase_large", None)
    # Every nonterminal batch spends at least one click, bounding even partial
    # input acceptance by the explicit action budget. Observe after <=100 taps.
    for _index in range(max_button_actions):
        if current == desired:
            return current, batches
        increasing = current < desired
        unit_action = assets.count_increase if increasing else assets.count_decrease
        large_action = large_increase if increasing else large_decrease
        large_clicks, unit_clicks = _button_click_counts(
            assets, current=current, desired=desired,
        )
        if large_clicks + unit_clicks > remaining_clicks:
            raise RuntimeError(
                f"{count_label}尚需 {large_clicks + unit_clicks} 次加减，"
                f"超过剩余精调预算 {remaining_clicks}；必须继续拖拽逼近，未继续逐个点击"
            )
        batch_budget = min(100, remaining_clicks)
        large_clicks = min(large_clicks, batch_budget)
        unit_clicks = min(unit_clicks, batch_budget - large_clicks)
        remaining_clicks -= large_clicks + unit_clicks
        before = current
        for _ in range(large_clicks):
            click(assets.settings_scene_id, large_action)
            # The game drops back-to-back ADB taps while the slider is still
            # animating.  Pace the burst locally so one batch expresses the
            # requested delta instead of burning repeated OCR-heavy retries.
            yield from context.wait_action_settle(0.18)
        for _ in range(unit_clicks):
            click(assets.settings_scene_id, unit_action)
            yield from context.wait_action_settle(0.18)
        # Fast clicks are queued by the game UI.  Read once only after the
        # whole batch has drained; otherwise a transient x == y can be
        # followed by late clicks from the same batch.
        yield from context.wait_action_settle(0.75)
        current = yield from _stable_read(
            context,
            assets,
            count_label=count_label,
            runtime_reader=runtime_reader, read_counts=read_counts,
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
            # click consumes the action budget. Three unchanged batches prove
            # this attempt is not progressing; do not spend the whole budget.
            no_progress_batches += 1
            if no_progress_batches >= 3:
                raise RuntimeError(f"{count_label}连续3批精调无进展：{current}")
            continue
        no_progress_batches = 0
        if (desired - before) * (current - before) < 0:
            raise RuntimeError(f"{count_label}批量精调向反方向变化")
    if current != desired:
        raise RuntimeError(f"{count_label}在{max_button_actions}次精调预算内未收敛：{current} != {desired}")
    return current, batches


def _button_click_counts(
    assets: IntegerCountAssets,
    *,
    current: int,
    desired: int,
) -> tuple[int, int]:
    """Plan large and unit taps without overshooting; shared by budget and execution."""

    residual = abs(desired - current)
    if residual == 0:
        return 0, 0
    increasing = current < desired
    large_action = (
        getattr(assets, "count_increase_large", None)
        if increasing
        else getattr(assets, "count_decrease_large", None)
    )
    large_step = int(getattr(assets, "count_large_step", 0) or 0)
    if not large_action or large_step <= 1:
        return 0, residual
    return divmod(residual, large_step)


def _estimated_button_actions(
    assets: IntegerCountAssets,
    *,
    current: int,
    desired: int,
) -> int:
    return sum(_button_click_counts(assets, current=current, desired=desired))


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
    read_counts: dict[str, int] | None = None,
    max_button_actions: int = _MAX_DIRECT_BUTTON_ACTIONS,
) -> Iterator[Any]:
    """Control a CommonShop-style track whose thumb has no separate asset."""

    if maximum is None and runtime_reader is not None:
        if read_counts is not None:
            read_counts["runtime"] += 1
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
        runtime_reader=runtime_reader, read_counts=read_counts,
    )
    probes: list[dict[str, Any]] = []
    interpolation_rows: list[dict[str, Any]] = []
    position_x = initial_target_x
    coarse_exit = "within_threshold"
    for _ in range(5):
        error = desired - current
        if abs(error) <= threshold:
            coarse_exit = "within_threshold"
            break
        sign = 1 if error > 0 else -1
        count_x = position_x
        available = left + width - count_x if sign > 0 else count_x - left
        effective: tuple[float, int] | None = None
        distance = 1.0
        while distance <= max(1.0, available):
            count_x = position_x
            available = left + width - count_x if sign > 0 else count_x - left
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
                runtime_reader=runtime_reader, read_counts=read_counts,
            )
            position_x = probe_x
            delta = current - before_probe
            probes.append({
                "commanded_pixels": commanded,
                "count_delta": delta,
            })
            if abs(desired - current) <= threshold:
                effective = (sign * commanded, delta)
                break
            if delta * sign > 0:
                effective = (sign * commanded, delta)
                break
            distance *= 2.0
        if effective is None:
            raise RuntimeError(f"{count_label}递增像素拖拽未产生有效变化")
        probe_pixels, probe_delta = effective
        error = desired - current
        if abs(error) <= threshold:
            coarse_exit = "within_threshold"
            break
        # Keep the last actual commanded position. Reconstructing it from
        # the original proportional model discards the local calibration.
        start_x = position_x
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
            runtime_reader=runtime_reader, read_counts=read_counts,
        )
        position_x = end_x
        interpolation_rows.append({
            "before": before_interpolation,
            "after": current,
            "commanded_pixels": abs(end_x - start_x),
            "probe_pixel_delta": probe_pixels,
            "probe_count_delta": probe_delta,
        })
        if (_estimated_button_actions(assets, current=current, desired=desired)
                <= max_button_actions
                and abs(desired - current) < abs(probe_delta)):
            coarse_exit = "within_drag_grain"
            break
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
        runtime_reader=runtime_reader, read_counts=read_counts,
        max_button_actions=max_button_actions,
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
    initial_count: int | None = None,
    max_button_actions: int = _MAX_DIRECT_BUTTON_ACTIONS,
) -> Iterator[Any]:
    """Proportional positioning, pixel feedback, then budgeted +/- batches.

    ``initial_count`` reuses the caller's freshly verified positive integer;
    no GUI action may intervene before this call. Pass no Runtime reader to
    use local OCR for feedback, retaining business identity checks at the caller.
    ``count_reads`` counts attempted OCR/Runtime reads inside this invocation.
    ``max_adjustments`` is the absolute count-error threshold m (at least 1):
    an initially exact count returns immediately; an initial error <= m skips
    positioning and pixel feedback and enters fine tuning directly. Larger
    errors start with proportional positioning, even with large-step buttons.
    An explicit maximum gates every path, including already-exact and short
    button adjustments. ``max_button_actions`` is the separate total +/-
    action budget (default 30), for both thumb and track-only sliders. After
    applying measured pixel feedback, a residual below that probe's value
    grain may enter fine tuning if it fits this budget. Each fine batch sends
    at most 100 clicks and then rereads; every nonterminal batch spends at
    least one action, so the total batch count is bounded by the same budget.
    Three consecutive unchanged batches fail. The target remains exact;
    neither parameter is an acceptance tolerance.
    """

    if isinstance(desired, bool) or not isinstance(desired, int) or desired <= 0:
        raise ValueError(f"{count_label}必须为正整数")
    if (isinstance(max_button_actions, bool)
            or not isinstance(max_button_actions, int) or max_button_actions <= 0):
        raise ValueError(f"{count_label}精调动作预算必须为正整数")
    if maximum is not None:
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
            raise ValueError(f"{count_label}上限必须为正整数")
        if desired > maximum:
            raise ValueError(f"{count_label}目标 {desired} 超出上限 {maximum}")
    threshold = max(1, int(max_adjustments))
    read_counts = {"ocr": 0, "runtime": 0}
    if initial_count is not None:
        if isinstance(initial_count, bool) or not isinstance(initial_count, int) or initial_count <= 0:
            raise ValueError(f"{count_label}初始值必须为正整数")
        if maximum is not None and initial_count > maximum:
            raise ValueError(f"{count_label}初始值超出上限")
        before = initial_count
    else:
        before = read_positive_integer_count(
            context, assets, count_label=count_label, runtime_reader=runtime_count_reader, read_counts=read_counts,
        )
    if before == desired:
        return {"before": before, "after": before, "phase": "already_exact", "count_reads": read_counts}
    direct_actions = _estimated_button_actions(
        assets,
        current=before,
        desired=desired,
    )
    # m gates entry by count error; the separate click budget bounds execution.
    # Large-step buttons change fine-tuning cost, not the phase boundary.
    if abs(desired - before) <= threshold:
        current, batches = yield from _fine_tune_batches(
            context,
            assets,
            desired,
            current=before,
            count_label=count_label,
            runtime_reader=runtime_count_reader, read_counts=read_counts,
            max_button_actions=max_button_actions,
        )
        return {
            "before": before,
            "after": current,
            "maximum": maximum,
            "phase": "button_fast_path",
            "count_reads": read_counts,
            "estimated_button_actions": direct_actions,
            "fine_batches": batches,
            "fine_adjustment_actions": sum(row["clicks"] for row in batches),
        }
    if not assets.count_slider_thumb and getattr(assets, "count_slider_track", None):
        result = yield from _set_track_only_count(
            context,
            assets,
            desired,
            before=before,
            maximum=maximum,
            count_label=count_label,
            runtime_reader=runtime_count_reader, read_counts=read_counts,
            threshold=threshold,
            max_button_actions=max_button_actions,
        )
        return {**result, "count_reads": read_counts}
    geometry = _slider_geometry(context, assets)
    current, observed_maximum, range_probe, fraction, proportional = yield from _proportional_position(
        context, assets, desired, before=before, maximum=maximum, geometry=geometry,
        count_label=count_label, runtime_reader=runtime_count_reader, read_counts=read_counts,
    )
    current, probes, interpolation_rows, coarse_exit = yield from _coarse_pixel_converge(
        context, assets, desired, current=current, maximum=observed_maximum,
        threshold=threshold,
        geometry=geometry, count_label=count_label, runtime_reader=runtime_count_reader, read_counts=read_counts,
        max_button_actions=max_button_actions,
    )
    current, batches = yield from _fine_tune_batches(
        context, assets, desired, current=current,
        count_label=count_label, runtime_reader=runtime_count_reader, read_counts=read_counts,
        max_button_actions=max_button_actions,
    )
    return {
        "before": before, "after": current, "maximum": observed_maximum,
        "count_reads": read_counts,
        "initial_fraction": fraction, "range_probe": range_probe,
        "proportional_drag": proportional,
        "pixel_probes": probes, "interpolation_drags": interpolation_rows,
        "coarse_exit": coarse_exit, "fine_batches": batches,
        "fine_adjustment_actions": sum(row["clicks"] for row in batches),
    }


def set_verified_integer_slider_range(
    context: Any, assets: IntegerCountAssets, *, minimum: int, maximum: int,
    control_maximum: int, count_label: str,
    runtime_count_reader: Callable[[], int | Mapping[str, Any]],
    initial_count: int | None = None,
) -> Iterator[Any]:
    """Set a caller-authorized interval, without unit taps or relaxing exact APIs.

    Resource batch estimates are intervals, unlike exact challenge counts. Reuse
    proportional positioning and measured pixel feedback, then prove the actual
    value is inside the interval. Failure never authorizes the current value.
    """
    if not all(type(v) is int for v in (minimum, maximum, control_maximum)) or not 1 <= minimum <= maximum <= control_maximum:
        raise ValueError('整数滑轨预算区间无效')
    counts = {'ocr': 0, 'runtime': 0}
    before = initial_count if initial_count is not None else read_positive_integer_count(
        context, assets, count_label=count_label, runtime_reader=runtime_count_reader)
    if minimum <= before <= maximum:
        return {'before': before, 'after': before, 'phase': 'already_in_range'}
    desired = (minimum + maximum) // 2
    geometry = _slider_geometry(context, assets)
    current, observed_maximum, _, _, proportional = yield from _proportional_position(
        context, assets, desired, before=before, maximum=control_maximum,
        geometry=geometry, count_label=count_label, runtime_reader=runtime_count_reader, read_counts=counts)
    probes, corrections = [], []
    if not minimum <= current <= maximum:
        current, probes, corrections, _ = yield from _coarse_pixel_converge(
            context, assets, desired, current=current, maximum=observed_maximum,
            threshold=min(desired-minimum, maximum-desired), geometry=geometry,
            count_label=count_label, runtime_reader=runtime_count_reader,
            read_counts=counts, max_button_actions=0)
    if not minimum <= current <= maximum:
        raise RuntimeError(f'{count_label}未进入授权范围：{current}, [{minimum}, {maximum}]')
    return {'before': before, 'after': current, 'phase': 'verified_range',
            'minimum': minimum, 'maximum': maximum, 'proportional_drag': proportional,
            'pixel_probes': probes, 'interpolation_drags': corrections, 'count_reads': counts}


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
        if isinstance(initial_count, bool) or not isinstance(initial_count, int) or initial_count <= 0:
            raise ValueError(f"{count_label}初始值必须为正整数")
        current = initial_count
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
