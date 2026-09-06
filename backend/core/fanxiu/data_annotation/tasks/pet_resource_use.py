"""Manual-development pet resource components, shared by regular and peak ranks.

No scheduler registration. Each call consumes at most one explicit batch;
initialization replans from fresh task progress after every completed batch.
"""

from datetime import datetime
from typing import Any

from backend.core.fanxiu.activity.lingchong_jingwu import (
    collect_lingchong_jingwu_resource_snapshot, read_lingchong_task_milestones,
)
from backend.core.fanxiu.activity.pet_resource_planning import plan_pet_resource_batch
from backend.core.fanxiu.activity.pet_resource_receipts import record_pet_resource_receipt, read_pet_resource_receipts
from backend.core.fanxiu.instrumentation.pet_aptitude import read_pet_aptitude_runtime
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.core.fanxiu.instrumentation.item_batch_use_dialog import read_item_batch_use_dialog_snapshot
from backend.core.fanxiu.data_annotation.tasks.pet_aptitude_navigation import (
    open_pet_aptitude_pill, prepare_pet_pill_quantity,
)


def read_pet_resource_progress(activity_id: int, target: int = 6000) -> dict:
    snapshot = read_lingchong_task_milestones(activity_id)
    if not snapshot.get("complete"):
        raise RuntimeError("灵兽任务进度未完整加载")
    rows = [r for r in snapshot["milestones"] if r["target"] == target]
    if len(rows) != 1:
        raise RuntimeError("当前任务阶梯没有唯一的目标档")
    return {"progress": rows[0]["progress"], "target": target,
            "complete": rows[0]["finished"], "task_id": rows[0]["task_id"]}


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
            source_scene_id=34, ocr_shape_names=["左侧菜单"], expected_scene_ids=[701], target_gui_name="天道巅峰")
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
                           item_id: int, item_name: str, quantity: int, base_gain: int | None = None):
    """Use one explicit batch and verify its positive, settled pet/task effect.

    The active dialog identity includes the chosen pet. An uncertain result is
    never retried automatically; the caller must inspect current game facts.
    """
    before_pet = read_pet_aptitude_runtime(expected_pet_id=pet_id)
    if before_pet["pending_swallow_count"]:
        raise RuntimeError("已有灵兽吞噬进行中")
    before_progress = read_pet_resource_progress(activity_id)
    scene = int((yield from context.wait_scene([545, 706], wait=15)))
    initial_dialog = None
    if scene == 545:
        initial_dialog = yield from open_pet_aptitude_pill(context, item_name=item_name, item_id=item_id)
    elif scene != 706:
        raise RuntimeError("未处于资质页或使用弹窗")
    dialog = yield from prepare_pet_pill_quantity(context, item_id=item_id, quantity=quantity, initial_snapshot=initial_dialog)
    if dialog.get("pet_id") != pet_id:
        raise RuntimeError("使用弹窗选中的灵兽不符")
    final_dialog = dialog
    if final_dialog["current"] != quantity or final_dialog.get("pet_id") != pet_id:
        raise RuntimeError("提交前的道具数量或灵兽身份改变")
    occurrence = datetime.now().astimezone().date().isoformat()
    action_id = record_pet_resource_receipt(activity_id, occurrence,
        {"status": "submitted", "pet_id": pet_id, "item_id": item_id, "quantity": quantity,
         "task_before": before_progress["progress"], "inventory_before": dialog["owned_count"],
         "aptitude_before": before_pet["target"]["aptitude_total"]})
    yield from context.wait_click(706, "使用")
    yield from context.wait_action_settle(2)
    scene = int((yield from context.wait_scene([545], wait=15)))
    if scene != 545:
        raise RuntimeError("使用后出现未验证页面，保留现场")
    previous = None
    stable = 0
    for _ in range(20):
        after_pet = read_pet_aptitude_runtime(expected_pet_id=pet_id)
        inventory = read_backpack_item_counts([item_id], manager_key="lingchong-jingwu-resources")[0][item_id]
        after_progress = read_pet_resource_progress(activity_id)
        observation = (after_pet["target"]["aptitude_total"], inventory, after_progress["progress"])
        positive = observation[0] > before_pet["target"]["aptitude_total"]
        stable = stable + 1 if observation == previous and positive and not after_pet["pending_swallow_count"] else 0
        if stable >= 2:
            receipt = {"activity_id": activity_id, "pet_id": pet_id, "item_id": item_id,
                    "quantity": quantity, "inventory_before": dialog["owned_count"],
                    "inventory_after": inventory, "task_before": before_progress["progress"],
                    "task_after": after_progress["progress"],
                    "aptitude_delta": observation[0] - before_pet["target"]["aptitude_total"],
                    "base_total": quantity * base_gain if base_gain is not None else None,
                    "quantity_control": dialog.get("quantity_control"),
                    "captured_at": datetime.now().astimezone().isoformat(timespec="seconds")}
            record_pet_resource_receipt(activity_id, occurrence, {**receipt, "status": "verified"}, action_id=action_id)
            return receipt
        previous = observation
        yield from context.wait_action_settle(1)
    raise RuntimeError("资源使用尚未得到稳定结果，禁止重复提交")


def initialize_pet_resources(context: Any, *, activity_id: int, pet_id: int,
                             seed_already_used: bool = False, max_batches: int = 12):
    """Current aptitude page -> 30 beast pills once -> task progress >=6000.

    ``seed_already_used`` requires an existing verified receipt for this
    occurrence. Partial task progress alone is not proof of that seed batch.
    The generator yields one batch before reading/replanning the next.
    """
    receipts = []
    occurrence = datetime.now().astimezone().date().isoformat()
    history = read_pet_resource_receipts(activity_id, occurrence)
    if any(r.get("status") == "submitted" for r in history):
        raise RuntimeError("上一批使用结果尚未核实")
    verified = [r for r in history if r.get("status") == "verified" and r.get("pet_id") == pet_id]
    seed_verified = any(r.get("item_id") == 8022009 and r.get("quantity") == 30 for r in verified)
    if seed_already_used and not seed_verified:
        raise RuntimeError("缺少本期30个兽神的已核实记录")
    seed_already_used = seed_verified
    samples = {r["item_id"]: r["aptitude_delta"] / r["base_total"] for r in verified
               if r.get("base_total") and r.get("aptitude_delta", 0) > 0 and r["item_id"] != 8022009}
    progress = read_pet_resource_progress(activity_id)
    if progress["complete"]:
        return {"status": "complete", "progress": progress, "receipts": receipts}
    if not seed_already_used:
        receipt = yield from use_pet_resource_batch(context, activity_id=activity_id, pet_id=pet_id,
                                                    item_id=8022009, item_name="兽神饲灵丸", quantity=30, base_gain=100)
        receipts.append(receipt)
    for _ in range(max_batches):
        progress = read_pet_resource_progress(activity_id)
        if progress["complete"]:
            return {"status": "complete", "progress": progress, "receipts": receipts}
        pet = read_pet_aptitude_runtime(expected_pet_id=pet_id)["target"]
        resources = collect_lingchong_jingwu_resource_snapshot(activity_id=str(activity_id))
        options = [(item, sum(gain for gift, gain in item.aptitude_gain_by_gift_id.items()
                              if pet["gift_remaining"].get(gift, 0) > 0)) for item in resources.items if item.count]
        options = [(item, gain) for item, gain in options if gain > 0]
        if not options:
            return {"status": "resource_exhausted", "progress": progress, "receipts": receipts}
        item, base_gain = options[0]
        plan = plan_pet_resource_batch(gap=6000-progress["progress"], base_gain=base_gain,
                                       available=item.count, measured_multiplier=samples.get(item.item_id))
        receipt = yield from use_pet_resource_batch(context, activity_id=activity_id, pet_id=pet_id,
                                                    item_id=item.item_id, item_name=item.name, quantity=plan["quantity"], base_gain=base_gain)
        receipt["base_total"] = base_gain * plan["quantity"]
        samples[item.item_id] = receipt["aptitude_delta"] / receipt["base_total"]
        receipts.append(receipt)
    return {"status": "batch_limit", "progress": read_pet_resource_progress(activity_id), "receipts": receipts}
