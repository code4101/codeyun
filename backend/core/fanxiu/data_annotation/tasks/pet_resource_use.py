"""Manual-development pet resource components, shared by regular and peak ranks.

No scheduler registration. Each call consumes at most one explicit batch;
initialization replans from fresh task progress after every completed batch.
"""

from datetime import datetime
from time import perf_counter
from typing import Any

from backend.core.fanxiu.activity.lingchong_jingwu import (
    collect_lingchong_jingwu_resource_snapshot, read_lingchong_task_milestones,
)
from backend.core.fanxiu.activity.pet_resource_planning import (
    order_pet_resources_low_to_high, plan_initialization_batch,
)
from backend.core.fanxiu.activity.pet_resource_receipts import (
    read_pet_resource_receipts_range, read_pet_seed_usage, record_pet_resource_receipt,
    select_pet_resource_task_samples,
)
from backend.core.fanxiu.instrumentation.pet_aptitude import read_pet_aptitude_runtime
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.core.fanxiu.instrumentation.item_batch_use_dialog import read_item_batch_use_dialog_snapshot
from backend.core.fanxiu.data_annotation.tasks.pet_aptitude_navigation import (
    open_pet_aptitude_pill, prepare_pet_pill_quantity,
)


SEED_ITEM_ID = 8022009
SEED_ITEM_NAME = "兽神饲灵丸"
SEED_ALLOWANCE = 30


def _applicable_gain(pet: dict, gains: dict[int, int]) -> int:
    return sum(int(gain) for gift_id, gain in gains.items()
               if pet["gift_remaining"].get(int(gift_id), 0) > 0)


def read_pet_resource_progress(activity_id: int, target: int | None = None) -> dict:
    snapshot = read_lingchong_task_milestones(activity_id)
    if not snapshot.get("ok") or not snapshot.get("complete") or not snapshot.get("milestones"):
        raise RuntimeError(f"本期灵兽资质14档未完整加载：{snapshot.get('reason') or 'unknown'}")
    rows = snapshot["milestones"]
    if target is None:
        row = max(rows, key=lambda item: (item["target"], item["order"], item["task_id"]))
    else:
        matches = [item for item in rows if item["target"] == int(target)]
        if len(matches) != 1:
            raise RuntimeError("当前任务阶梯没有唯一的目标档")
        row = matches[0]
    return {"progress": row["progress"], "target": row["target"],
            "complete": row["finished"] or row["progress"] >= row["target"],
            "task_id": row["task_id"]}


def enter_peakrace_pet_resources(context: Any, *, pet_name: str):
    """Enter today's peak pet stage, then the first taught growing pet."""
    from backend.core.fanxiu.data_annotation.tasks.peakrace_stage import (
        current_peakrace_lingchong_stage, enter_current_peakrace_lingchong_stage,
        require_peakrace_stage_page,
    )
    from backend.core.fanxiu.data_annotation.tasks.activity_menu_navigation import open_loaded_activity_menu_item
    from backend.core.fanxiu.data_annotation.tasks.pet_aptitude_navigation import (
        prepare_pet_resource_page, enter_first_growing_pet_aptitude,
    )
    scene = int((yield from context.wait_scene([34, 701, 702, 483, 545], wait=15)))
    stage = current_peakrace_lingchong_stage()
    if scene == 34:
        yield from open_loaded_activity_menu_item(context, stage["outer_activity_id"], kind="world_left",
            source_scene_id=34, ocr_shape_names=["左侧菜单"], expected_scene_ids=[701], target_gui_name="天道巅峰",
            max_scrolls=12)
        scene = 701
    if scene == 701:
        yield from context.wait_action_settle(1)
        stage = yield from enter_current_peakrace_lingchong_stage(context)
        scene = 702
    if scene == 702:
        require_peakrace_stage_page(stage["activity_id"], tab_index=0)
        yield from context.wait_click(702, "前往吞噬")
        scene = int((yield from context.wait_scene([483], wait=15)))
    if scene == 483:
        yield from prepare_pet_resource_page(context)
        rows = context.ocr_fragments_in_shapes(483, ["第一个成长灵兽"], frame_data_url=context.cur_frame(update=True))
        if not any(pet_name in row.get("text", "") for row in rows):
            raise RuntimeError("首个成长灵兽与预期对象不符")
        yield from enter_first_growing_pet_aptitude(context)
    elif scene != 545:
        raise RuntimeError("尚未到达灵兽资质页")
    return stage


def use_pet_resource_batch(context: Any, *, activity_id: int, pet_id: int,
                           item_id: int, item_name: str, quantity: int, base_gain: int | None = None,
                           verify_task_progress: bool = True):
    """Use one explicit batch and verify its positive, settled pet/task effect.

    The active dialog identity includes the chosen pet. An uncertain result is
    never retried automatically; the caller must inspect current game facts.
    Ranking after initialization may omit task progress reads explicitly;
    inventory and settled positive pet effects are always observed.
    """
    started = perf_counter()
    timings = {}
    phase = 'before_state'
    phase_started = started
    try:
        before_pet = read_pet_aptitude_runtime(expected_pet_id=pet_id)
        if before_pet["pending_swallow_count"]:
            raise RuntimeError("已有灵兽吞噬进行中")
        before_progress = read_pet_resource_progress(activity_id) if verify_task_progress else None
        timings["before_state"] = perf_counter() - started
        phase = 'open_dialog'
        phase_started = perf_counter()
        scene_match = yield from context.wait_scene([545, 706], wait=15)
        scene = int(scene_match)
        initial_dialog = None
        if scene == 545:
            initial_dialog = yield from open_pet_aptitude_pill(
                context, item_name=item_name, item_id=item_id, initial_scene=scene_match,
            )
        elif scene == 706:
            initial_dialog = read_item_batch_use_dialog_snapshot(expected_item_id=item_id)
        else:
            raise RuntimeError("未处于资质页或使用弹窗")
        if initial_dialog.get("pet_id") != pet_id:
            raise RuntimeError("使用弹窗所属灵兽与预期不符")
        timings["open_dialog"] = perf_counter() - phase_started
        phase = 'configure_quantity'
        phase_started = perf_counter()
        dialog = yield from prepare_pet_pill_quantity(context, item_id=item_id, quantity=quantity, initial_snapshot=initial_dialog)
        timings["configure_quantity"] = perf_counter() - phase_started
        if dialog["current"] != quantity or dialog.get("pet_id") != pet_id:
            raise RuntimeError("提交前的道具数量或灵兽身份改变")
        occurrence = datetime.now().astimezone().date().isoformat()
        action_id = record_pet_resource_receipt(activity_id, occurrence,
            {"status": "submitted", "pet_id": pet_id, "item_id": item_id, "quantity": quantity,
             "task_before": before_progress["progress"] if before_progress is not None else None,
             "inventory_before": dialog["owned_count"],
             "aptitude_before": before_pet["target"]["aptitude_total"]})
        phase = 'submit'
        phase_started = perf_counter()
        yield from context.wait_click(706, "使用")
        yield from context.wait_action_settle(2)
        scene = int((yield from context.wait_scene([545], wait=15)))
        if scene != 545:
            raise RuntimeError("使用后出现未验证页面，保留现场")
        timings["submit"] = perf_counter() - phase_started
        phase = 'verify_effect'
        phase_started = perf_counter()
        previous = None
        stable = 0
        for _ in range(20):
            after_pet = read_pet_aptitude_runtime(expected_pet_id=pet_id)
            observation = after_pet["target"]["aptitude_total"]
            positive = observation > before_pet["target"]["aptitude_total"]
            stable = stable + 1 if observation == previous and positive and not after_pet["pending_swallow_count"] else 0
            if stable >= 2:
                # Wait on the pet's actual completion, then capture the other
                # effects once. Avoid rescanning three Runtime models per tick.
                inventory = read_backpack_item_counts([item_id], manager_key="lingchong-jingwu-resources")[0][item_id]
                after_progress = read_pet_resource_progress(activity_id) if verify_task_progress else None
                timings["verify_effect"] = perf_counter() - phase_started
                timings["total"] = perf_counter() - started
                receipt = {"activity_id": activity_id, "pet_id": pet_id, "item_id": item_id,
                        "quantity": quantity, "inventory_before": dialog["owned_count"],
                        "inventory_after": inventory,
                        "task_before": before_progress["progress"] if before_progress is not None else None,
                        "task_after": after_progress["progress"] if after_progress is not None else None,
                        "aptitude_delta": observation - before_pet["target"]["aptitude_total"],
                        "base_total": quantity * base_gain if base_gain is not None else None,
                        "quantity_control": dialog.get("quantity_control"),
                        "timings_seconds": timings,
                        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds")}
                record_pet_resource_receipt(activity_id, occurrence, {**receipt, "status": "verified"}, action_id=action_id)
                return receipt
            previous = observation
            yield from context.wait_action_settle(1)
        raise RuntimeError("资源使用尚未得到稳定结果，禁止重复提交")
    except Exception as exc:
        timings[phase] = perf_counter() - phase_started
        timings['total'] = perf_counter() - started
        diagnostic = f'灵兽单批停在 {phase}；耗时秒：{timings}'
        print(diagnostic, flush=True)
        exc.add_note(diagnostic)
        raise


def initialize_pet_resources(context: Any, *, activity_id: int, pet_id: int,
                             start_date: str, end_date: str, max_batches: int = 1):
    """Advance the current ladder with one explicit batch per call.

    ``start_date``/``end_date`` bound this event instance; the activity ID
    alone is not an instance, so the seed budget and any unresolved receipt
    inside that window (including cross-day ones) are authoritative. A
    resource with no valid sample for this instance/pet probes with exactly one
    unit; with a measured per-unit task gain the mid-section batches about 50%
    of the estimated remainder and the last <=3 units go one at a time. The
    seed 兽神饲灵丸 uses the same feedback, is capped at 30 per instance and by
    stock, and stops immediately once the ladder's highest target is reached;
    only then is the gap filled lowest-quality usable resource first. Samples
    are recovered from this instance's verified receipts on every call and are
    never mixed across resources or pets, and an aptitude delta is never used
    as a task sample. A failed or unresolved batch is never retried here.
    """
    if isinstance(max_batches, bool) or not isinstance(max_batches, int) or max_batches < 1:
        raise ValueError("初始化批数必须为正整数")
    receipts = []
    occurrence = datetime.now().astimezone().date().isoformat()
    history = read_pet_resource_receipts_range(
        activity_id, start_date=start_date, end_date=end_date,
    )
    if any(r.get("status") == "submitted" for r in history):
        raise RuntimeError("上一批使用结果尚未核实")
    if any(r.get("status") == "rank_observation_pending" for r in history):
        raise RuntimeError("上一批排名积分尚未核实")
    samples = select_pet_resource_task_samples(
        activity_id, pet_id=pet_id, start_date=start_date, end_date=end_date,
    )
    seed_used = read_pet_seed_usage(activity_id, item_id=SEED_ITEM_ID,
                                    start_date=start_date, end_date=end_date)
    resources = collect_lingchong_jingwu_resource_snapshot(activity_id=str(activity_id))
    inventory = {item.item_id: int(item.count) for item in resources.items}
    items_by_id = {item.item_id: item for item in resources.items}
    for _ in range(max_batches):
        progress = read_pet_resource_progress(activity_id)
        if progress["complete"]:
            return {"status": "complete", "progress": progress, "receipts": receipts,
                    "seed_used": seed_used}
        gap = progress["target"] - progress["progress"]
        pet = read_pet_aptitude_runtime(expected_pet_id=pet_id)["target"]
        seed_item = items_by_id.get(SEED_ITEM_ID)
        seed_option = None
        if seed_item is not None and inventory.get(SEED_ITEM_ID, 0) > 0:
            seed_gain = _applicable_gain(pet, seed_item.aptitude_gain_by_gift_id)
            if seed_gain > 0:
                seed_option = {"resource_id": SEED_ITEM_ID, "base_gain": seed_gain,
                               "available": inventory.get(SEED_ITEM_ID, 0)}
        supplement_options = []
        for item in order_pet_resources_low_to_high(resources.items):
            if item.item_id == SEED_ITEM_ID:
                continue
            count = inventory.get(item.item_id, 0)
            if count <= 0:
                continue
            base_gain = _applicable_gain(pet, item.aptitude_gain_by_gift_id)
            if base_gain <= 0:
                continue
            supplement_options.append({"resource_id": item.item_id, "base_gain": base_gain,
                                       "available": count})
        plan = plan_initialization_batch(gap=gap, seed_option=seed_option,
                                         supplement_options=supplement_options,
                                         seed_used=seed_used, samples=samples)
        if plan["quantity"] <= 0 or plan["resource_id"] is None:
            return {"status": "resource_exhausted", "progress": progress, "receipts": receipts,
                    "seed_used": seed_used}
        item = items_by_id[plan["resource_id"]]
        receipt = yield from use_pet_resource_batch(
            context, activity_id=activity_id, pet_id=pet_id, item_id=item.item_id,
            item_name=item.name, quantity=plan["quantity"], base_gain=plan["base_gain"],
        )
        receipts.append(receipt)
        if item.item_id == SEED_ITEM_ID:
            seed_used += int(receipt.get("quantity") or plan["quantity"])
        inventory[item.item_id] = int(receipt.get("inventory_after")
                                      if receipt.get("inventory_after") is not None
                                      else max(0, inventory.get(item.item_id, 0) - plan["quantity"]))
        quantity = int(receipt.get("quantity") or plan["quantity"])
        before_task, after_task = receipt.get("task_before"), receipt.get("task_after")
        if quantity > 0 and before_task is not None and after_task is not None:
            measured = (int(after_task) - int(before_task)) / quantity
            if measured > 0:
                samples[item.item_id] = measured
    progress = read_pet_resource_progress(activity_id)
    return {"status": "complete" if progress["complete"] else "batch_limit",
            "progress": progress, "receipts": receipts, "seed_used": seed_used}
