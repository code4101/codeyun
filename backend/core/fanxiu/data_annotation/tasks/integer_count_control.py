from __future__ import annotations

"""Activity-neutral, Runtime-verified integer count controls."""

from dataclasses import dataclass
from typing import Any, Callable, Iterator, Protocol


class IntegerCountAssets(Protocol):
    settings_scene_id: int
    count_region: str
    count_decrease: str
    count_increase: str
    count_slider_thumb: str
    count_minimum_marker: str | None
    count_slider_left_anchor: str | None
    count_slider_right_anchor: str | None


@dataclass(frozen=True)
class IntegerSliderAssets:
    """Named assets for a conventional positive-integer slider dialog."""

    settings_scene_id: int
    count_region: str = "挑战次数"
    count_decrease: str = "挑战次数_减少"
    count_increase: str = "挑战次数_增加"
    count_slider_thumb: str = "挑战次数_滑块"
    count_minimum_marker: str | None = None
    count_slider_left_anchor: str | None = None
    count_slider_right_anchor: str | None = None

    def __post_init__(self) -> None:
        if int(self.settings_scene_id) <= 0:
            raise ValueError("整数滑块缺少有效的设置场景")
        for title in (
            self.count_region,
            self.count_decrease,
            self.count_increase,
            self.count_slider_thumb,
        ):
            if not str(title).strip():
                raise ValueError("整数滑块的命名 Shape 不得为空")
        if self.count_minimum_marker is not None and not str(
            self.count_minimum_marker
        ).strip():
            raise ValueError("整数滑块最小值标记不得为空")
        if bool(self.count_slider_left_anchor) != bool(
            self.count_slider_right_anchor
        ):
            raise ValueError("整数滑块左右锚点必须成对提供")


def read_positive_integer_count(
    runtime: Any,
    assets: IntegerCountAssets,
    *,
    count_label: str,
) -> int:
    """Read one authoritative positive value from a named OCR region."""

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
        raise RuntimeError(f"{count_label}无法唯一读回：{text!r}")
    return unique[0]


def set_minimum_then_increment_count(
    runtime: Any,
    assets: IntegerCountAssets,
    desired: int,
    *,
    maximum: int,
    max_adjustments: int,
    count_label: str,
    count_reader: Callable[[Any, IntegerCountAssets], int] | None = None,
) -> Iterator[Any]:
    """Converge a bounded count with exact verified +/- steps.

    The historical function name is retained for callers.  The control no
    longer depends on a slider reset: it reads the current value, takes one
    step toward the target, waits for delayed repaint, and verifies an exact
    unit change before permitting another click.
    """

    if (
        isinstance(desired, bool)
        or not isinstance(desired, int)
        or desired <= 0
        or desired > int(maximum)
    ):
        raise ValueError(
            f"{count_label}必须为 1..{int(maximum)} 的整数；禁止使用原生最大值"
        )
    adjustment_budget = int(max_adjustments)
    if adjustment_budget < 0:
        raise ValueError(f"{count_label}调整预算不得为负数")

    read_current = count_reader or (
        lambda active_runtime, active_assets: read_positive_integer_count(
            active_runtime,
            active_assets,
            count_label=count_label,
        )
    )
    before = read_current(runtime, assets)
    required_actions = abs(int(desired) - before)
    if required_actions > adjustment_budget:
        raise ValueError(
            f"{count_label}调整预算不足：当前 {before}，目标 {desired}，"
            f"需要 {required_actions} 次，预算 {adjustment_budget} 次"
        )

    current = before
    increase_actions = 0
    decrease_actions = 0
    while current != desired:
        increasing = current < desired
        shape = assets.count_increase if increasing else assets.count_decrease
        action_label = "加号" if increasing else "减号"
        expected = current + (1 if increasing else -1)
        runtime.click_shape_center(
            assets.settings_scene_id,
            shape,
        )
        if increasing:
            increase_actions += 1
        else:
            decrease_actions += 1
        yield from runtime.wait_action_settle(0.12)
        updated = current
        for observation_attempt in range(4):
            updated = read_current(runtime, assets)
            if updated != current:
                break
            if observation_attempt < 3:
                # #680 can publish the Runtime count before its text repaint.
                # Treat an unchanged OCR frame as pending feedback, never as
                # permission to click again.
                yield from runtime.wait_action_settle(0.25)
        if updated != expected:
            raise RuntimeError(
                f"{count_label}{action_label}没有按单步变化，已停止："
                f"调整前 {current}，调整后 {updated}"
            )
        current = updated

    yield from runtime.wait_action_settle(0.4)
    verified = read_current(runtime, assets)
    if verified != desired:
        raise RuntimeError(
            f"{count_label}稳定读回失败：目标 {desired}，实际 {verified}"
        )
    return {
        "before": before,
        "after": verified,
        "target": desired,
        "reset_to_minimum": False,
        "increase_actions": increase_actions,
        "decrease_actions": decrease_actions,
        "native_maximum_probed": False,
    }


def read_integer_slider_count(runtime: Any, assets: IntegerCountAssets) -> int:
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
        raise RuntimeError(f"云梦自动挑战次数无法唯一读回：{text!r}")
    return unique[0]


def _read_integer_count_after_motion(
    runtime: Any,
    assets: IntegerCountAssets,
    *,
    before: int,
    direction: str,
) -> Iterator[Any]:
    """Ignore transient redraw values that contradict the commanded direction."""

    last = before
    for observation_attempt in range(4):
        last = read_integer_slider_count(runtime, assets)
        forward = last >= before if direction == "increase" else last <= before
        # One equal frame can still be the pre-action cached frame.  Require a
        # second observation before treating equality as a real bound/no-op.
        if forward and (last != before or observation_attempt >= 1):
            return last
        if observation_attempt < 3:
            yield from runtime.wait_action_settle(0.35)
    raise RuntimeError(
        f"整数滑块动作后读值方向异常：before={before}, after={last}, direction={direction}"
    )


def set_verified_integer_slider_count(
    runtime: Any,
    assets: IntegerCountAssets,
    desired: int,
    *,
    max_adjustments: int,
    force_bound_probe: bool = False,
    count_label: str = "云梦自动挑战次数",
) -> Iterator[Any]:
    current = read_integer_slider_count(runtime, assets)
    before = current
    observed_maximum: int | None = None
    bound_drag_count = 0
    coarse_drag_count = 0
    fine_adjustment_actions = 0
    minimum_thumb_center: tuple[float, float] | None = None
    maximum_thumb_center: tuple[float, float] | None = None
    coarse_target_x: float | None = None
    coarse_readback: int | None = None
    coarse_refinement_count = 0
    coarse_response_gains: list[float] = []
    coarse_readbacks: list[int] = []
    boundary_gains: list[float] = []
    fixed_left_center: tuple[float, float] | None = None
    fixed_right_center: tuple[float, float] | None = None
    needed = abs(int(desired) - current)
    if force_bound_probe or needed > int(max_adjustments):
        # Discover this occurrence's real upper bound instead of trusting the
        # remembered number or a historical maximum.
        if force_bound_probe:
            if not assets.count_slider_left_anchor or not assets.count_slider_right_anchor:
                raise RuntimeError(f"{count_label}严格边界探测缺少固定滑轨端点")
            fixed_left_center = runtime.shape_center(
                assets.settings_scene_id,
                assets.count_slider_left_anchor,
            )
            fixed_right_center = runtime.shape_center(
                assets.settings_scene_id,
                assets.count_slider_right_anchor,
            )
            thumb_box = runtime.shape_box(
                assets.settings_scene_id,
                assets.count_slider_thumb,
            )
            thumb_width = float(thumb_box.get("w") or 0.0)
            if thumb_width <= 1.0:
                raise RuntimeError(f"{count_label}滑块游标参考宽度无效")
            # The right anchor marks the track's outer edge.  The value maps
            # to the thumb centre, so remove half the verified thumb width.
            fixed_right_center = (
                fixed_right_center[0] - thumb_width * 0.5,
                fixed_right_center[1],
            )
            track_span = fixed_right_center[0] - fixed_left_center[0]
            if track_span <= 1.0:
                raise RuntimeError(f"{count_label}固定滑轨端点无效")
            # Keep the pointer inside a range derived only from the verified
            # track.  The modest overshoot lets a damped gesture reach a bound
            # without introducing screen- or activity-specific coordinates.
            safe_left_x = fixed_left_center[0] - track_span * 0.35
            safe_right_x = fixed_right_center[0] + track_span * 0.35
            maximum = current
            for _bound_attempt in range(8):
                start_center = runtime.shape_center(
                    assets.settings_scene_id,
                    assets.count_slider_thumb,
                    live=True,
                    strict_live=True,
                )
                runtime.drag_frame_point(
                    assets.settings_scene_id,
                    start_center[0],
                    start_center[1],
                    safe_right_x,
                    start_center[1],
                    duration_ms=600,
                )
                bound_drag_count += 1
                yield from runtime.wait_action_settle(0.5)
                updated = yield from _read_integer_count_after_motion(
                    runtime,
                    assets,
                    before=maximum,
                    direction="increase",
                )
                end_center = runtime.shape_center(
                    assets.settings_scene_id,
                    assets.count_slider_thumb,
                    live=True,
                    strict_live=True,
                )
                if updated < maximum:
                    raise RuntimeError(f"{count_label}向右探测上限未单调增加")
                if updated > maximum and end_center[0] < fixed_right_center[0] - 1.0:
                    commanded = safe_right_x - start_center[0]
                    actual = end_center[0] - start_center[0]
                    gain = actual / commanded if commanded > 1.0 else 0.0
                    if not 0.05 <= gain <= 2.0:
                        raise RuntimeError(f"{count_label}边界探针手势响应率异常：{gain:.3f}")
                    boundary_gains.append(gain)
                if updated == maximum:
                    break
                maximum = updated
            else:
                raise RuntimeError(f"{count_label}在有界拖动内未确认单次上限")
        else:
            runtime.drag_shape_to_frame_edge(
                assets.settings_scene_id,
                assets.count_slider_thumb,
                direction="right",
                duration=0.6,
            )
            bound_drag_count += 1
            yield from runtime.wait_action_settle(0.5)
            maximum = read_integer_slider_count(runtime, assets)
        observed_maximum = maximum
        if force_bound_probe:
            maximum_thumb_center = fixed_right_center
        if maximum < int(desired):
            raise RuntimeError(
                f"{count_label}超过本期滑块上限："
                f"目标 {int(desired)}，上限 {maximum}"
            )

        # Reset to the verified lower bound, then use the slider as the coarse
        # preset.  Named +/- shapes provide stable horizontal anchors; OCR and
        # exact buttons remain the authority for the final value.
        if force_bound_probe:
            current = maximum
            for _bound_attempt in range(8):
                start_center = runtime.shape_center(
                    assets.settings_scene_id,
                    assets.count_slider_thumb,
                    live=True,
                    strict_live=True,
                )
                runtime.drag_frame_point(
                    assets.settings_scene_id,
                    start_center[0],
                    start_center[1],
                    safe_left_x,
                    start_center[1],
                    duration_ms=600,
                )
                bound_drag_count += 1
                yield from runtime.wait_action_settle(0.5)
                updated = yield from _read_integer_count_after_motion(
                    runtime,
                    assets,
                    before=current,
                    direction="decrease",
                )
                end_center = runtime.shape_center(
                    assets.settings_scene_id,
                    assets.count_slider_thumb,
                    live=True,
                    strict_live=True,
                )
                if updated > current:
                    raise RuntimeError(f"{count_label}向左复位未单调减少")
                if updated < current and end_center[0] > fixed_left_center[0] + 1.0:
                    commanded = safe_left_x - start_center[0]
                    actual = end_center[0] - start_center[0]
                    gain = actual / commanded if commanded < -1.0 else 0.0
                    if not 0.05 <= gain <= 2.0:
                        raise RuntimeError(f"{count_label}边界探针手势响应率异常：{gain:.3f}")
                    boundary_gains.append(gain)
                current = updated
                if current == 1:
                    break
            else:
                raise RuntimeError(f"{count_label}在有界拖动内未回到最小值 1")
        else:
            runtime.drag_shape_to_frame_edge(
                assets.settings_scene_id,
                assets.count_slider_thumb,
                direction="left",
                duration=0.6,
            )
            bound_drag_count += 1
            yield from runtime.wait_action_settle(0.5)
            current = read_integer_slider_count(runtime, assets)
        if current != 1:
            raise RuntimeError(
                f"{count_label}滑块归零后读回异常："
                f"期望 1，实际 {current}"
            )
        if force_bound_probe:
            minimum_thumb_center = fixed_left_center
            if not boundary_gains:
                raise RuntimeError(f"{count_label}边界探针未取得未触边手势响应率")
        if int(desired) > 1 and maximum > 1:
            fraction = (int(desired) - 1) / (maximum - 1)
            if force_bound_probe:
                assert minimum_thumb_center is not None
                assert maximum_thumb_center is not None
                min_x, _min_y = minimum_thumb_center
                max_x, _max_y = maximum_thumb_center
                if max_x <= min_x:
                    raise RuntimeError(f"{count_label}固定滑轨边界像素无效")
                coarse_target_x = min_x + (max_x - min_x) * fraction
                sorted_gains = sorted(boundary_gains)
                gesture_gain = sorted_gains[len(sorted_gains) // 2]
                start_center = runtime.shape_center(
                    assets.settings_scene_id,
                    assets.count_slider_thumb,
                    live=True,
                    strict_live=True,
                )
                compensated_x = start_center[0] + (
                    coarse_target_x - start_center[0]
                ) / gesture_gain
                compensated_x = min(safe_right_x, max(safe_left_x, compensated_x))
                runtime.drag_frame_point(
                    assets.settings_scene_id,
                    start_center[0],
                    start_center[1],
                    compensated_x,
                    start_center[1],
                    duration_ms=450,
                )
            else:
                runtime.drag_shape_between_shapes_fraction(
                    assets.settings_scene_id,
                    assets.count_slider_thumb,
                    assets.count_slider_left_anchor or assets.count_decrease,
                    assets.count_slider_right_anchor or assets.count_increase,
                    fraction=fraction,
                    duration=0.4,
                )
            coarse_drag_count += 1
            yield from runtime.wait_action_settle(0.5)
            current = yield from _read_integer_count_after_motion(
                runtime,
                assets,
                before=current,
                direction="increase",
            )
            coarse_readback = current
            coarse_readbacks.append(current)
            if (
                force_bound_probe
                and abs(int(desired) - current) > int(max_adjustments)
            ):
                assert minimum_thumb_center is not None
                assert maximum_thumb_center is not None
                assert coarse_target_x is not None
                current_thumb_center = runtime.shape_center(
                    assets.settings_scene_id,
                    assets.count_slider_thumb,
                    live=True,
                    strict_live=True,
                )
                commanded_delta = compensated_x - start_center[0]
                actual_delta = current_thumb_center[0] - start_center[0]
                expected_direction = 1 if commanded_delta > 0 else -1
                if abs(commanded_delta) < 1.0 or actual_delta * expected_direction <= 0:
                    raise RuntimeError(
                        f"{count_label}粗定位游标没有按命令方向移动，拒绝逐单位补偿"
                    )
                response_gain = abs(actual_delta / commanded_delta)
                if not 0.05 <= response_gain <= 2.0:
                    raise RuntimeError(
                        f"{count_label}粗定位游标响应率异常：{response_gain:.3f}"
                    )
                coarse_response_gains.append(response_gain)
                remaining_thumb_delta = coarse_target_x - current_thumb_center[0]
                if abs(remaining_thumb_delta) >= 0.5:
                    feedback_x = current_thumb_center[0] + (
                        remaining_thumb_delta / response_gain
                    )
                    feedback_x = min(safe_right_x, max(safe_left_x, feedback_x))
                    direction = "increase" if remaining_thumb_delta > 0 else "decrease"
                    previous_count = current
                    runtime.drag_frame_point(
                        assets.settings_scene_id,
                        current_thumb_center[0],
                        current_thumb_center[1],
                        feedback_x,
                        current_thumb_center[1],
                        duration_ms=450,
                    )
                    coarse_drag_count += 1
                    coarse_refinement_count = 1
                    yield from runtime.wait_action_settle(0.5)
                    current = yield from _read_integer_count_after_motion(
                        runtime,
                        assets,
                        before=previous_count,
                        direction=direction,
                    )
                    coarse_readbacks.append(current)
        needed = abs(int(desired) - current)
        if needed > int(max_adjustments):
            raise RuntimeError(
                f"{count_label}按比例预设后超出有界校正预算："
                f"预设后当前 {current}，目标 {int(desired)}，"
                f"需要 {needed} 次，预算 {int(max_adjustments)} 次"
            )
    seen = {current}
    stagnant_attempts = 0
    observed_button_step: int | None = None
    while fine_adjustment_actions < int(max_adjustments):
        if current == int(desired):
            # A native slider can still apply one delayed +/- repaint after an
            # OCR observation.  Do not publish an exact result until a second,
            # settled read agrees; otherwise continue correcting the drift.
            yield from runtime.wait_action_settle(0.4)
            verified = read_integer_slider_count(runtime, assets)
            if verified != current:
                current = verified
                seen.add(current)
                continue
            return {
                "before": before,
                "after": current,
                "maximum": observed_maximum,
                "bound_drag_count": bound_drag_count,
                "coarse_drag_count": coarse_drag_count,
                "fine_adjustment_actions": fine_adjustment_actions,
                "minimum_thumb_center": minimum_thumb_center,
                "maximum_thumb_center": maximum_thumb_center,
                "coarse_target_x": coarse_target_x,
                "coarse_readback": coarse_readback,
                "coarse_refinement_count": coarse_refinement_count,
                "coarse_response_gains": coarse_response_gains,
                "coarse_readbacks": coarse_readbacks,
                "boundary_gains": boundary_gains,
            }
        direction = "increase" if current < desired else "decrease"
        action = assets.count_increase if direction == "increase" else assets.count_decrease
        residual = abs(int(desired) - current)
        remaining_budget = int(max_adjustments) - fine_adjustment_actions
        if observed_button_step is None:
            batch_size = 1
        else:
            batch_size = max(1, residual // observed_button_step)
            batch_size = min(batch_size, remaining_budget, 25)
        for _click in range(batch_size):
            runtime.click_shape_center(assets.settings_scene_id, action)
            fine_adjustment_actions += 1
            yield from runtime.wait_action_settle(0.08)
        yield from runtime.wait_action_settle(0.25)
        updated = yield from _read_integer_count_after_motion(
            runtime,
            assets,
            before=current,
            direction=direction,
        )
        if updated == current:
            stagnant_attempts += 1
            if stagnant_attempts <= 3:
                continue
            raise RuntimeError(
                f"{count_label}连续 {stagnant_attempts} 次调整无变化："
                f"当前 {current}，目标 {int(desired)}"
            )
        moving_forward = updated > current if current < desired else updated < current
        if not moving_forward or updated in seen:
            raise RuntimeError(
                f"{count_label}调整未单调收敛："
                f"调整前 {current}，目标 {int(desired)}，实际 {updated}"
            )
        stagnant_attempts = 0
        delta_per_action = abs(updated - current) / max(1, batch_size)
        rounded_step = max(1, round(delta_per_action))
        if abs(delta_per_action - rounded_step) <= 0.15:
            observed_button_step = rounded_step
        else:
            observed_button_step = None
        seen.add(updated)
        current = updated
    if current == int(desired):
        yield from runtime.wait_action_settle(0.4)
        verified = read_integer_slider_count(runtime, assets)
        if verified != current:
            raise RuntimeError(
                f"{count_label}精确值稳定复读失败："
                f"首次 {current}，稳定复读 {verified}"
            )
        return {
            "before": before,
            "after": current,
            "maximum": observed_maximum,
            "bound_drag_count": bound_drag_count,
            "coarse_drag_count": coarse_drag_count,
            "fine_adjustment_actions": fine_adjustment_actions,
            "minimum_thumb_center": minimum_thumb_center,
            "maximum_thumb_center": maximum_thumb_center,
            "coarse_target_x": coarse_target_x,
            "coarse_readback": coarse_readback,
            "coarse_refinement_count": coarse_refinement_count,
            "coarse_response_gains": coarse_response_gains,
            "coarse_readbacks": coarse_readbacks,
            "boundary_gains": boundary_gains,
        }
    raise RuntimeError(f"{count_label}在有界操作内未收敛")





__all__ = [
    "IntegerCountAssets",
    "IntegerSliderAssets",
    "read_integer_slider_count",
    "read_positive_integer_count",
    "set_minimum_then_increment_count",
    "set_verified_integer_slider_count",
]
