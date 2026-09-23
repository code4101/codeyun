"""丹道问鼎：按本期任务进度分批使用炼丹资源。

Runtime 只在每批前后读取 QuestMgr 的本期 MedicalExp；配方、容量、
基础熟练度和滑轨反馈均来自当前游戏画面。每批按剩余进度的一半规划，
最少尝试一万基础熟练度。目标来自本期任务梯度，达成后复用领奖流程。
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from math import ceil
from typing import Any
from zoneinfo import ZoneInfo

from backend.core.fanxiu.data_annotation.tasks.dandao_task_rewards import (
    run_dandao_task_rewards_flow,
    resolve_active_dandao_activity,
)
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.tasks.integer_count_control import (
    IntegerSliderAssets,
    set_verified_integer_slider_count,
)
from backend.core.fanxiu.data_annotation.tasks.resource_rank_daily_gift import (
    RESOURCE_RANK_GIFT_ADAPTERS,
    open_resource_rank_activity_page,
)
from backend.core.fanxiu.instrumentation.dandao_task_rewards import (
    read_dandao_task_reward_snapshot,
)
from backend.core.fanxiu.runtime_gui.alchemy_red_dots import (
    first_alchemy_category_red_dot,
)


DANDAO_MIN_BASE_BATCH = 10_000
DANDAO_RECIPE_ROWS = tuple(f"配方{index}" for index in range(1, 7))


@dataclass(frozen=True)
class AlchemyRecipe:
    category: str
    row: str
    maximum: int
    current_count: int
    base_per_item: int


def plan_dandao_base_batch(
    remaining_activity_exp: int,
    observed_rate: Fraction | None,
    *,
    minimum_base: int = DANDAO_MIN_BASE_BATCH,
) -> int:
    """Spend half the estimated remaining need, with a fixed probing floor.

    Before the first observed batch there is no defensible conversion rate;
    use only the minimum batch to measure it. This also makes a restarted Task
    safe when its previous in-memory rate was lost.
    """
    if remaining_activity_exp < 0 or minimum_base <= 0:
        raise ValueError("丹道剩余进度或基础批次下限无效")
    if remaining_activity_exp == 0:
        return 0
    if observed_rate is None:
        return minimum_base
    if observed_rate <= 0:
        raise ValueError("丹道批次加成率必须为正数")
    half_remaining_base = Fraction(remaining_activity_exp, 2) / observed_rate
    return max(minimum_base, ceil(half_remaining_base))


def plan_dandao_recipe_count(remaining_base: int, base_per_item: int, maximum: int) -> int:
    """Round upward to whole pills; an overshoot of one pill is intentional."""
    if remaining_base <= 0 or base_per_item <= 0 or maximum <= 0:
        raise ValueError("炼丹次数规划需要正数剩余量、单个熟练度和可炼上限")
    return min(maximum, ceil(Fraction(remaining_base, base_per_item)))


def _require_progress(activity_id: int) -> dict[str, Any]:
    snapshot = read_dandao_task_reward_snapshot(activity_id)
    if not snapshot.get("ok") or not snapshot.get("complete"):
        raise RuntimeError(f"丹道本期任务 Runtime 不完整：{snapshot.get('reason')}")
    if not snapshot.get("all_tasks_complete"):
        progress = snapshot.get("activity_progress")
        if not isinstance(progress, int) or progress < 0:
            raise RuntimeError("丹道本期 MedicalExp 没有唯一进度值")
    return snapshot


def _read_recipe_count_and_base(context: Any) -> tuple[int, int]:
    """Read both #627 values from two agreeing frames before planning spend.

    A clipped trailing zero in the points OCR can still be a valid integer and
    therefore bypass the usual invalid-text retry. Agreement across fresh
    frames keeps that error from becoming a tenfold wrong per-pill estimate.
    """
    readings: list[tuple[int, int]] = []
    for _ in range(3):
        frame = context.cur_frame(update=True)
        values: list[int] = []
        for shape in ("炼制数量", "本次基础熟练度"):
            numbers, text = context.ocr_numbers_in_shapes(
                627, [shape], crop=True, padding=0, expected_count=1,
                frame_data_url=frame,
            )
            if len(numbers) != 1 or numbers[0] <= 0:
                raise RuntimeError(f"#627「{shape}」数字无法唯一读回：{text!r}")
            values.append(int(numbers[0]))
        reading = values[0], values[1]
        readings.append(reading)
        if len(readings) >= 2 and readings[-1] == readings[-2]:
            return reading
    raise RuntimeError(f"丹道炼制数量与基础熟练度换帧读数不稳定：{readings}")


def _wait_alchemy_scene(context: Any, scenes: list[int], *, wait: float, label: str):
    """A global fallback match is diagnostic evidence, not the expected landing."""
    match = yield from context.wait_scene(scenes, wait=wait, label=label)
    if match.scene_id not in scenes:
        raise RuntimeError(f"{label}：实际落点 #{match.scene_id} 不在 {scenes}")
    return match


def _read_recipe_capacity(context: Any, row: str) -> tuple[int, str]:
    """Read the rendered capacity label from the recipe row OCR cache."""
    fragments = context.ocr_fragments_in_shapes(625, [row])
    row_text = "".join(str(item.get("text") or "") for item in fragments)
    capacities = {
        int(match.group(1))
        for item in fragments
        for match in [re.search(r"可炼\s*[:：]?\s*(\d+)", str(item.get("text") or ""))]
        if match is not None
    }
    if len(capacities) == 1:
        return capacities.pop(), row_text
    if capacities:
        raise RuntimeError(f"丹道「{row}」可炼数量不唯一：{row_text!r}")
    if "已达" in row_text and "上限" in row_text:
        return 0, row_text
    # Full-frame OCR can omit the small capacity label while retaining the
    # recipe name. Retry only its annotated ROI; missing text is never zero.
    values, capacity_text = context.ocr_numbers_in_shapes(
        625, [f"{row}/可炼数量"], crop=True, expected_count=1,
    )
    if len(values) == 1 and values[0] >= 0:
        return int(values[0]), f"{row_text} {capacity_text}"
    raise RuntimeError(f"丹道「{row}」既无可炼数量也无上限标记：{row_text!r}")


def _open_alchemy_selection(context: Any, activity_id: int, now: datetime):
    match = yield from context.wait_scene([625, 624, 597], wait=5, required=False)
    scene_id = match.scene_id if match is not None else None
    if scene_id == 625:
        return
    if scene_id == 624:
        context.click_shape_center(624, "前往炼丹")
        yield from _wait_alchemy_scene(context, [625], wait=20, label="丹道：等待丹方选择")
        return
    if scene_id == 597:
        context.click_shape_center(597, "前往炼丹")
        yield from _wait_alchemy_scene(context, [624], wait=25, label="丹道：等待炼丹首页")
        context.click_shape_center(624, "前往炼丹")
        yield from _wait_alchemy_scene(context, [625], wait=20, label="丹道：等待丹方选择")
        return
    adapter = next(item for item in RESOURCE_RANK_GIFT_ADAPTERS if item.key == "dandao-wending")
    scene = yield from open_resource_rank_activity_page(
        context,
        adapter,
        activity_id=activity_id,
        now=now,
    )
    if scene != 597:
        context.click_shape_center(scene, "榜")
        yield from _wait_alchemy_scene(context, [597], wait=20, label="丹道：等待资源榜")
    context.click_shape_center(597, "前往炼丹")
    yield from _wait_alchemy_scene(context, [624], wait=25, label="丹道：等待炼丹首页")
    context.click_shape_center(624, "前往炼丹")
    yield from _wait_alchemy_scene(context, [625], wait=20, label="丹道：等待丹方选择")


def _first_available_recipe_row(context: Any):
    """Use the first red category and the first craftable visible recipe.

    The category marker is read from one #625 image. Recipe capacity is local
    OCR. If six visible rows have no capacity, scroll the recipe panel in order
    and stop after three distinct pages; never guess from a red dot alone.
    """
    frame = context.cur_frame(update=True)
    category = first_alchemy_category_red_dot(base64.b64decode(frame.split(",", 1)[1]))
    if category is None:
        return None
    name = str(category["name"])
    context.click_shape_center(625, name)
    yield from context.wait_action_settle(0.6)
    previous_page: tuple[str, ...] | None = None
    for page in range(4):
        page_text: list[str] = []
        for row in DANDAO_RECIPE_ROWS:
            maximum, text = _read_recipe_capacity(context, row)
            page_text.append(text)
            if maximum > 0:
                return name, row, maximum
        if tuple(page_text) == previous_page:
            break
        previous_page = tuple(page_text)
        if page < 3:
            context.drag_frame_point(625, 700, 1190, 700, 410, duration_ms=600)
            yield from context.wait_action_settle(0.7)
    raise RuntimeError(f"丹道「{name}」有红点，但四页丹方未读到可炼配方")


def _open_recipe(context: Any, category: str, row: str, maximum: int):
    context.click_shape_center(625, row)
    yield from _wait_alchemy_scene(context, [810], wait=15, label="丹道：等待丹方提示")
    context.click_shape_center(810, "选择丹方")
    yield from _wait_alchemy_scene(context, [627], wait=15, label="丹道：等待炼制数量")
    current_count, current_base = _read_recipe_count_and_base(context)
    if current_count <= 0 or current_count > maximum or current_base <= 0:
        raise RuntimeError("丹道详情页次数、可炼上限或基础熟练度不一致")
    unit, remainder = divmod(current_base, current_count)
    if remainder or unit <= 0:
        raise RuntimeError("丹道单个配方基础熟练度无法整除")
    return AlchemyRecipe(category, row, maximum, current_count, unit)


def craft_alchemy_recipe(context: Any, recipe: AlchemyRecipe, desired: int):
    """Craft a selected #627 recipe after verifying its count and base points.

    ``recipe`` must come from the current selection's visible capacity and
    total-points/count quotient. The result is confirmed base points spent.
    """
    assets = IntegerSliderAssets(
        settings_scene_id=627,
        count_region="炼制数量",
        count_decrease="减少炼制",
        count_increase="增加炼制",
        count_slider_thumb="炼制数量滑块本体",
        count_slider_left_anchor="炼制数量左端",
        count_slider_right_anchor="炼制数量右端",
        count_ocr_padding=0,
    )
    yield from set_verified_integer_slider_count(
        context, assets, desired,
        maximum=recipe.maximum,
        initial_count=recipe.current_count,
        max_adjustments=5,
        count_label="炼丹次数",
    )
    expected_base = desired * recipe.base_per_item
    actual_count, actual_base = _read_recipe_count_and_base(context)
    if actual_count != desired or actual_base != expected_base:
        raise RuntimeError(
            f"丹道点击前次数/基础熟练度 {actual_count}/{actual_base} "
            f"与计划 {desired}/{expected_base} 不一致"
        )
    context.click_shape_center(627, "开始炼制")
    yield from _wait_alchemy_scene(context, [811], wait=30, label="丹道：等待炼制奖励")
    context.click_shape_center(811, "点击屏幕继续")
    landing = yield from _wait_alchemy_scene(context, [624, 629], wait=20, label="丹道：等待炼制落点")
    if int(landing.id) == 629:
        context.click_shape_center(629, "关闭")
        yield from _wait_alchemy_scene(context, [624], wait=15, label="丹道：关闭炼制提示")
    return expected_base


def _spend_one_base_batch(context: Any, target_base: int):
    spent = 0
    recipes: list[dict[str, Any]] = []
    for _recipe_attempt in range(20):
        if spent >= target_base:
            return spent, recipes
        context.click_shape_center(624, "前往炼丹") if spent else None
        if spent:
            yield from _wait_alchemy_scene(context, [625], wait=20, label="丹道：返回丹方选择")
        choice = yield from _first_available_recipe_row(context)
        if choice is None:
            raise RuntimeError("丹道批次尚未达到基础目标，但分类已无红点")
        recipe = yield from _open_recipe(context, *choice)
        count = plan_dandao_recipe_count(target_base - spent, recipe.base_per_item, recipe.maximum)
        actual_base = yield from craft_alchemy_recipe(context, recipe, count)
        spent += actual_base
        recipes.append({
            "category": recipe.category,
            "row": recipe.row,
            "count": count,
            "base": actual_base,
        })
    raise RuntimeError("丹道单批配方遍历超过20次，尚未达到基础目标")


def run_dandao_resource_use_flow(
    context: Any,
    *,
    activity_id: int,
    now: datetime | None = None,
    max_batches: int = 12,
    return_to_world: bool = True,
):
    """Reach the live occurrence's final task tier and claim every reward.

    Activity identity and effective business time belong to the caller; the
    final threshold comes from the exact QuestMgr task membership. Reentry
    first reads that membership, so completed tasks never consume resources.
    """
    activity_id = int(activity_id)
    current = now or job_now()
    zone = ZoneInfo("Asia/Shanghai")
    current = current.replace(tzinfo=zone) if current.tzinfo is None else current.astimezone(zone)
    active = resolve_active_dandao_activity(current)
    if active is None or active[1] != activity_id:
        raise RuntimeError("丹道资源使用活动不是当前业务时间的唯一开放实例")
    adapter = next(item for item in RESOURCE_RANK_GIFT_ADAPTERS if item.key == "dandao-wending")
    if activity_id not in adapter.activity_ids:
        raise ValueError(f"丹道问鼎活动身份无效：{activity_id}")
    snapshot = _require_progress(activity_id)
    target = int(snapshot["task_target"])
    observed_rate: Fraction | None = None
    batches: list[dict[str, Any]] = []
    while not snapshot["all_tasks_complete"]:
        if len(batches) >= max_batches:
            raise RuntimeError("丹道本期任务在批次上限内未完成")
        before = int(snapshot["activity_progress"])
        if before >= target:
            raise RuntimeError("丹道任务状态与活动熟练度读数冲突")
        target_base = plan_dandao_base_batch(target - before, observed_rate)
        yield from _open_alchemy_selection(context, activity_id, current)
        spent_base, recipes = yield from _spend_one_base_batch(context, target_base)
        after_snapshot = _require_progress(activity_id)
        if after_snapshot["task_target"] != target:
            raise RuntimeError("丹道炼制期间本期任务梯度发生变化")
        if after_snapshot["all_tasks_complete"]:
            # All rows can be capped/removed once complete. This is a lower
            # bound, not an invented exact observation of the final score.
            after = target
        else:
            after = int(after_snapshot["activity_progress"])
        gained = after - before
        if gained <= 0:
            raise RuntimeError("丹道炼制后本期熟练度没有增长")
        observed_rate = None if after_snapshot["all_tasks_complete"] else Fraction(gained, spent_base)
        batches.append({
            "before": before,
            "after": after,
            "after_is_lower_bound": after_snapshot["all_tasks_complete"],
            "planned_base": target_base,
            "spent_base": spent_base,
            "observed_rate": float(observed_rate) if observed_rate is not None else None,
            "recipes": recipes,
        })
        snapshot = after_snapshot
    rewards = yield from run_dandao_task_rewards_flow(
        context,
        now=current,
        expected_activity_id=activity_id,
        initial_snapshot=snapshot,
        max_claims=int(snapshot["expected_task_count"]),
        manage_schedule=False,
        include_schedule_hint=False,
        return_to_world=return_to_world,
    )
    if rewards.get("result") != "success" or rewards.get("boundary") != "already_claimed":
        raise RuntimeError(f"丹道任务奖励未完成：{rewards}")
    exit_warning = None
    if return_to_world and rewards.get("current_scene") != 34:
        try:
            yield from context.go_scene(34)
        except (InterruptedError, GeneratorExit):
            raise
        except Exception as exc:
            # The business result is already proven. Navigation must not
            # turn a completed consumption/claim into a repeated action.
            exit_warning = f"{type(exc).__name__}: {exc}"
    return {
        "result": "success",
        "goal": target,
        "batch_count": len(batches),
        "batches": batches,
        "rewards": rewards,
        "exit_warning": exit_warning,
        "message": f"丹道本期 {target} 任务已全部达成并领奖；本次炼制 {len(batches)} 批",
    }


__all__ = [
    "AlchemyRecipe",
    "craft_alchemy_recipe",
    "DANDAO_MIN_BASE_BATCH",
    "plan_dandao_base_batch",
    "plan_dandao_recipe_count",
    "run_dandao_resource_use_flow",
]
