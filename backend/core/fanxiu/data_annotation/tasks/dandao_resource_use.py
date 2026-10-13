"""丹道问鼎：按本期任务进度分批使用炼丹资源。

Runtime 读取本期 MedicalExp 与当前炼丹面板的数量、配方和熟练度；
GUI 提供列表、滑轨与消费入口。每批按剩余进度的一半规划，
最少尝试一万基础熟练度。目标来自本期任务梯度，达成后复用领奖流程。
"""

from __future__ import annotations

import base64
import re
import time
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
from backend.core.fanxiu.runtime_gui.integer_count_control import (
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
from backend.core.fanxiu.instrumentation.alchemy_selection import read_active_alchemy_selection
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
    recipe_id: int
    item_id: int


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


def _require_progress(activity_id: int, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = read_dandao_task_reward_snapshot(activity_id) if snapshot is None else snapshot
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
    match = yield from context.wait_scene([625, 624, 597, 811], wait=5, required=False)
    scene_id = match.scene_id if match is not None else None
    if scene_id == 811:
        # 上次消费已生效但领奖层未关闭：只关闭当前奖励，不重复炼制。
        context.click_shape_center(811, "点击屏幕继续")
        yield from _wait_alchemy_scene(context, [624], wait=20, label="丹道：关闭已结算奖励")
        scene_id = 624
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
        # Category clicks and scrolling invalidate the previous frame's OCR.
        context.cur_frame(update=True)
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
    # 两张已标注的丹方详情覆盖不同配方；使用真实落点的同义按钮。
    detail = yield from _wait_alchemy_scene(context, [626, 810], wait=15, label="丹道：等待丹方提示")
    context.click_shape_center(detail.scene_id, "选择丹方")
    yield from _wait_alchemy_scene(context, [627], wait=15, label="丹道：等待炼制数量")
    native = read_active_alchemy_selection()
    yield from context.wait_action_settle(0.5)
    verified = read_active_alchemy_selection()
    if any(native[key] != verified[key] for key in ('current', 'maximum', 'recipe_id', 'item_id', 'base_points')):
        raise RuntimeError('丹道已选配方原生读数不稳定')
    if verified['maximum'] != maximum:
        raise RuntimeError(f"丹道当前列表上限{maximum}与已选配方上限{verified['maximum']}不一致")
    current_count, current_base = verified['current'], verified['base_points']
    if current_count <= 0 or current_count > maximum or current_base <= 0:
        raise RuntimeError("丹道详情页次数、可炼上限或基础熟练度不一致")
    unit, remainder = divmod(current_base, current_count)
    if remainder or unit <= 0:
        raise RuntimeError("丹道单个配方基础熟练度无法整除")
    return AlchemyRecipe(category, row, maximum, current_count, unit,
                         verified['recipe_id'], verified['item_id'])


def inspect_dandao_alchemy_selection(context: Any, *, activity_id: int):
    """Open one available recipe, read its native quantity twice, then leave.

    This diagnostic never starts crafting. It records any listing/native
    capacity disagreement; two stable native reads are observation evidence,
    never spending permission.
    """
    from backend.core.fanxiu.instrumentation.alchemy_selection import read_active_alchemy_selection
    current = job_now()
    active = resolve_active_dandao_activity(current)
    if active is None or active[1] != activity_id:
        raise ValueError('炼丹取证需要本期唯一开放丹道活动')
    yield from context.go_scene(34)
    yield from _open_alchemy_selection(context, activity_id, current)
    selected = yield from _first_available_recipe_row(context)
    if selected is None:
        yield from context.go_scene(34)
        return {'available': False, 'reason': 'no_available_recipe', 'consumption_authorized': False}
    category, row, maximum = selected
    listing_frame = context.cur_frame(update=True)
    listing_maximum, listing_text = _read_recipe_capacity(context, row)
    context.click_shape_center(625, row)
    detail = yield from _wait_alchemy_scene(context, [626, 810], wait=15, label='只读取证：等待丹方')
    context.click_shape_center(detail.scene_id, '选择丹方')
    yield from _wait_alchemy_scene(context, [627], wait=15, label='只读取证：等待已选丹方')
    before = read_active_alchemy_selection()
    yield from context.wait_action_settle(0.5)
    after = read_active_alchemy_selection()
    if any(before[key] != after[key] for key in ('current', 'maximum', 'recipe_id', 'item_id', 'base_points')):
        raise RuntimeError('炼丹取证面板未形成连续稳定选择')
    frame = context.cur_frame(update=True)
    yield from context.go_scene(34)
    return {'available': True, 'category': category, 'recipe_row': row,
            'native_selection': after, 'listing_capacity': maximum,
            'listing_capacity_agrees': after['maximum'] == maximum,
            'listing_recheck_capacity': listing_maximum, 'listing_text': listing_text,
            'listing_frame_data_url': listing_frame,
            'consumption_authorized': False,
            'frame_data_url': frame, 'final_scene': 34}


def craft_alchemy_recipe(context: Any, recipe: AlchemyRecipe, desired: int, *, activity_id: int | None = None,
                         before_start: Any = None):
    """Craft a selected #627 recipe after verifying its count and base points.

    Bind every quantity read to the freshly selected recipe identity and
    capacity. The result is confirmed base points spent.
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
    def read_selected():
        snapshot = read_active_alchemy_selection()
        if (snapshot['recipe_id'], snapshot['item_id'], snapshot['maximum'], snapshot['base_per_item']) != (
                recipe.recipe_id, recipe.item_id, recipe.maximum, recipe.base_per_item):
            raise RuntimeError('丹道调整数量期间配方身份、上限或单位熟练度发生变化')
        return snapshot

    initial = read_selected()
    yield from set_verified_integer_slider_count(
        context, assets, desired,
        maximum=recipe.maximum,
        initial_count=initial['current'],
        runtime_count_reader=read_selected,
        max_adjustments=5,
        count_label="炼丹次数",
    )
    expected_base = desired * recipe.base_per_item
    actual = read_selected()
    actual_count, actual_base = actual['current'], actual['base_points']
    if actual_count != desired or actual_base != expected_base:
        raise RuntimeError(
            f"丹道点击前次数/基础熟练度 {actual_count}/{actual_base} "
            f"与计划 {desired}/{expected_base} 不一致"
        )
    before = _require_progress(activity_id) if activity_id is not None else None
    yield from context.wait_scene_exact([627], timeout=15, label="丹道：确认已选丹方与开始炼制按钮")
    if before_start is not None:
        before_start(actual)
    context.click_shape_center(627, "开始炼制")
    reward = yield from context.wait_scene([811], wait=45, label="丹道：等待炼制奖励")
    if reward.scene_id not in ([811, 624, 629] if before is not None else [811]):
        raise RuntimeError(f"丹道炼制后落点 #{reward.scene_id} 尚未验证")
    if reward.scene_id == 811:
        context.click_shape_center(811, "点击屏幕继续")
        landing = yield from _wait_alchemy_scene(context, [624, 629], wait=20, label="丹道：等待炼制落点")
    else:
        # 原生礼包可覆盖并收起奖励页；仅首页落点不能证明炼制成功。
        landing = reward
    if before is not None:
        deadline = time.monotonic() + 60
        while True:
            after = _require_progress(activity_id)
            if (after["all_tasks_complete"]
                    or int(after["activity_progress"]) > int(before["activity_progress"])):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("丹道炼制后等待60秒仍未增加本期熟练度，保留现场")
            # 服务端进度应答晚于页面动画；只等事实，绝不再次开始炼制。
            yield from context.wait_action_settle(2.0)
        before_identity = before.get("evidence") or {}
        after_identity = after.get("evidence") or {}
        if any(before_identity.get(key) != after_identity.get(key)
               for key in ("pid", "process_start_ticks")):
            raise RuntimeError("丹道炼制前后进程身份变化，拒绝重复提交")
    if landing.scene_id == 629:
        context.click_shape_center(629, "关闭")
        yield from _wait_alchemy_scene(context, [624], wait=15, label="丹道：关闭炼制提示")
    return expected_base


def run_dandao_score_resource_batch(context: Any, *, activity_id: int,
                                   max_crafts: int = 12,
                                   target_scope: str = 'next_milestone',
                                   allow_final_threshold: bool = False):
    """Advance one earned-score milestone with the accepted 太清本源 recipe.

    The 441 rule assigns both alternative herbs 2500 points each; this recipe
    consumes twelve, so the predicted gain is 30000 per craft, independent of
    which herb the client picks. Final capped progress needs explicit native
    milestone proof; it is reported as a lower bound. A completed current-round
    batch also claims its earned summary chest. This component never buys.
    """
    from backend.core.fanxiu.activity.dandao_score_receipts import (
        read_dandao_score_receipts, arm_dandao_score_batch, settle_dandao_score_batch)
    from backend.core.fanxiu.instrumentation.dandao_task_rewards import read_dandao_score_task_observation
    from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
    from backend.core.fanxiu.data_annotation.tasks.dandao_task_rewards import run_dandao_score_rewards_flow

    if type(max_crafts) is not int or max_crafts <= 0:
        raise ValueError('丹道积分批次需要正整数炼制上限')
    if target_scope not in ('next_milestone', 'current_round'):
        raise ValueError('丹道积分目标须为下一档或当前整轮')
    active = resolve_active_dandao_activity(job_now())
    if active is None or active[1] != activity_id:
        raise RuntimeError('丹道积分消费需绑定当前唯一活动')
    if any(row['status'] == 'armed' for row in read_dandao_score_receipts(activity_id)):
        raise RuntimeError('丹道积分有未结批次，须先验收其实际效果')
    rewards = yield from run_dandao_score_rewards_flow(context,
        expected_activity_id=activity_id, claim_round_rewards=target_scope == 'current_round',
        return_to_world=target_scope != 'current_round')
    if (rewards['all_row_rewards_claimed'] and rewards.get('round_snapshot')
            and rewards['round_snapshot']['round_reward']['native_state'] == 3):
        yield from context.go_scene(34)
        return dict(result='success', crafted_count=0, rewards=rewards,
                    boundary='all_score_and_round_rewards_claimed', current_scene=34)
    score = read_dandao_score_task_observation(activity_id)
    if not score.get('complete') or type(score.get('current_score')) is not int:
        raise RuntimeError('丹道积分没有唯一未封顶原生进度')
    remaining = [row for row in score['rows'] if not row['claimed'] and row['target'] > score['current_score']]
    if not remaining:
        return dict(result='success', crafted_count=0, rewards=rewards, boundary='no_pending_score_rows')
    target = (min(row['target'] for row in remaining) if target_scope == 'next_milestone'
              else max(row['target'] for row in score['rows'] if row['round'] == score['current_round']))
    count = min(max_crafts, ceil(Fraction(target - score['current_score'], 30000)))
    final_target = max(row['target'] for row in score['rows'])
    crosses_final = score['current_score'] + count * 30000 >= final_target
    if crosses_final and not allow_final_threshold:
        raise RuntimeError('积分最终封顶消费需独立终态契约，禁止沿用未封顶读数')
    if target_scope == 'current_round':
        page = rewards['round_snapshot']
        round_reward = page.get('round_reward') or {}
        if (not page.get('complete') or page.get('subtype') != 999
                or round_reward.get('current_round') != score['current_round']
                or round_reward.get('native_state') != 1):
            raise RuntimeError('丹道本轮原生状态未对齐或上一轮总奖待领，禁止跨轮追加消费')
    yield from context.go_scene(34)
    yield from _open_alchemy_selection(context, activity_id, job_now())
    choice = yield from _first_available_recipe_row(context)
    if choice is None:
        raise RuntimeError('丹道积分暂无可用配方')
    recipe = yield from _open_recipe(context, *choice)
    native = read_active_alchemy_selection()
    if (recipe.recipe_id != 711070 or native.get('alternative_material_formula') != 'AnyItem|12_9031021_9031022'
            or native.get('material_formula') not in (None, '')):
        raise RuntimeError('当前首个配方不是已验收积分材料配方，禁止消费')
    count = min(count, recipe.maximum)
    intent = {}
    def observe():
        current_score = read_dandao_score_task_observation(activity_id)
        materials, evidence = read_backpack_item_counts([9031021, 9031022], manager_key='dandao-score-batch')
        identity = {key: evidence[key] for key in ('pid', 'process_start_ticks')}
        ids = [row['task_id'] for row in current_score.get('rows', [])]
        satisfied = sorted(set(current_score.get('claimed_task_ids', [])) | set(current_score.get('claimable_task_ids', [])))
        final_complete = (crosses_final and allow_final_threshold and not current_score.get('pending_task_ids')
                          and sorted(ids) == sorted(row['task_id'] for row in score['rows'])
                          and satisfied == sorted(ids)
                          and max((row['target'] for row in current_score.get('rows', [])), default=0) == final_target)
        if (not current_score.get('complete')
                or (type(current_score.get('current_score')) is not int and not final_complete)
                or any(current_score['evidence'].get(key) != identity[key] for key in identity)):
            raise RuntimeError('丹道积分及材料观测没有一致进程与完整进度')
        result = dict(score=current_score['current_score'], materials=materials, evidence=identity,
                      recipe_id=recipe.recipe_id, formula=native['alternative_material_formula'])
        if final_complete:
            result['final_threshold_proof'] = dict(complete=True, threshold=final_target,
                                                   task_ids=ids, satisfied_task_ids=satisfied)
        return result
    def arm(actual):
        fresh = observe()
        if fresh['score'] != score['current_score'] or actual['current'] != count:
            raise RuntimeError('丹道积分或选定数量在开始前变化，禁止消费')
        if crosses_final:
            fresh.update(final_threshold=final_target, task_ids=[row['task_id'] for row in score['rows']])
        intent['action_id'] = arm_dandao_score_batch(activity_id, fresh, count)
    yield from craft_alchemy_recipe(context, recipe, count, activity_id=activity_id, before_start=arm)
    after = observe()
    receipt = settle_dandao_score_batch(activity_id, intent['action_id'], after)
    yield from context.go_scene(34)
    needs_round_reward = target_scope == 'current_round' and (
        after['score'] is not None and after['score'] >= target or after.get('final_threshold_proof', {}).get('complete') is True)
    rewards = yield from run_dandao_score_rewards_flow(context, expected_activity_id=activity_id,
                                                      return_to_world=not needs_round_reward)
    round_rewards = None
    if needs_round_reward:
        from backend.core.fanxiu.instrumentation.revenue_task_page import read_revenue_task_page_snapshot
        from backend.core.fanxiu.data_annotation.tasks.revenue_task_claims import claim_open_activity_round_reward
        page = read_revenue_task_page_snapshot(activity_id, include_round_rewards=True)
        summary = page.get('round_reward') or {}
        if (not page.get('complete') or summary.get('current_round') != score['current_round']
                or not summary.get('can_claim')):
            raise RuntimeError('丹道本轮任务已领奖，但轮次总奖尚未形成准确可领权限')
        round_rewards = yield from claim_open_activity_round_reward(context, activity_id=activity_id,
            expected_reward_id=summary['authorized_reward_id'], scene_id=598,
            round_reward_shape='积分轮次总奖领取区')
        yield from context.go_scene(34)
    exact = receipt['score_verification'] == 'exact'
    return dict(result='success', crafted_count=count, score_before=receipt['before']['score'],
                score_after=after['score'], score_gained=count * 30000 if exact else None,
                predicted_score_gain=count * 30000, score_is_exact=exact,
                score_lower_bound=final_target if not exact else None,
                target_scope=target_scope, target=target,
                materials_consumed=count * 12, receipt=receipt, rewards=rewards,
                round_rewards=round_rewards, round_closed=bool(round_rewards),
                current_scene=34, message=(f'丹道积分炼制{count}次，准确增加{count * 30000}积分并核验材料'
                    if exact else f'丹道积分炼制{count}次，最终{final_target}积分目标原生达成并核验材料，精确累计值不可读'))


def _spend_one_base_batch(context: Any, target_base: int, *, activity_id: int):
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
        actual_base = yield from craft_alchemy_recipe(context, recipe, count, activity_id=activity_id)
        spent += actual_base
        recipes.append({
            "category": recipe.category,
            "row": recipe.row,
            "count": count,
            "base": actual_base,
        })
        if _require_progress(activity_id)["all_tasks_complete"]:
            return spent, recipes
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
    snapshot = read_dandao_task_reward_snapshot(activity_id)
    if snapshot.get("reason_code") == "unsupported_live_task_metric":
        # Do not replay a failed spending attempt or reinterpret score as exp.
        # Runtime has proved this ladder's membership, but its resource binding
        # still needs real research; pending preserves that distinction.
        return {"status": "pending", "reason_code": "unsupported_live_task_metric",
                "message": snapshot["reason"], "performed_actions": False,
                "consumption_authorized": False,
                "unsupported_task_conditions": snapshot["unsupported_task_conditions"]}
    snapshot = _require_progress(activity_id, snapshot)
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
        spent_base, recipes = yield from _spend_one_base_batch(context, target_base, activity_id=activity_id)
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
    # 已完成目标后先关闭仍展示的炼制奖励；不再打开或提交配方。
    current_reward = yield from context.wait_scene([811], wait=3, required=False)
    if current_reward is not None and current_reward.scene_id == 811:
        context.click_shape_center(811, "点击屏幕继续")
        yield from _wait_alchemy_scene(context, [624], wait=20, label="丹道：关闭已完成炼制奖励")
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
    "run_dandao_score_resource_batch",
]
