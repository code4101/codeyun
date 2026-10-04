"""Bounded Yaochi gifting controls and the real activity reward-list adapter.

The caller owns the occurrence, recipient and resource budget. Gift selection
is separate from irreversible submission; completing rewards never gifts more.
The first three must-get rows live outside QuestMgr's ordinary task list, so
that list cannot prove all Yaochi rewards claimed. Use the verified GUI rows.
"""
from backend.core.fanxiu.data_annotation.tasks.task_reward_rows import claim_task_rows_by_ocr
from backend.core.fanxiu.runtime_gui.integer_count_control import (
    IntegerSliderAssets, set_verified_integer_slider_count,
)


YAOCHI_LOTUS_ITEM_ID = 7020014
YAOCHI_LOTUS_FRIENDSHIP = 100


def select_yaochi_lotus_quantity(context, quantity: int, *, maximum: int):
    """At #920, select an exact approved lotus count without submitting a gift.

    The native maximum is limited by this NPC's remaining favor capacity, not
    backpack stock. The dialog must identify 瑶池玉莲 before any count changes.
    Unit buttons provide a bounded path for this NPC's 880-item maximum.
    """
    if not 0 < quantity <= maximum <= 1000:
        raise ValueError("送花数量或人物容量超出已验收预算")
    match = yield from context.wait_scene([920], wait=10)
    if int(match) != 920:
        raise RuntimeError("送花数量页未就绪")
    name = context.ocr_text_in_shapes(920, ["道具名称"], crop=True)
    if "瑶池玉莲" not in name:
        raise RuntimeError("当前选择的礼物不是瑶池玉莲")
    assets = IntegerSliderAssets(
        settings_scene_id=920, count_region="数量", count_decrease="减少",
        count_increase="增加", count_slider_thumb=None,
        count_slider_track="滑轨", count_ocr_padding=10,
    )
    result = yield from set_verified_integer_slider_count(
        context, assets, quantity, max_adjustments=maximum, maximum=maximum,
        max_button_actions=maximum, count_label="送花数量",
    )
    return {**result, "item_id": YAOCHI_LOTUS_ITEM_ID, "quantity": quantity,
            "friendship": quantity * YAOCHI_LOTUS_FRIENDSHIP}


def claim_yaochi_task_rewards(context):
    """At #461, claim reached rows and verify the claimed/unmet boundary.

    Each claim uses the progress strip, never the item icon. Already claimed
    rows move behind outstanding rows; a replay must produce zero clicks.
    """
    match = yield from context.wait_scene([461], wait=10)
    if int(match) != 461:
        raise RuntimeError("瑶池花会任务页未就绪")
    return (yield from claim_task_rows_by_ocr(
        context, scene_id=461, first_row_shape="首条任务领取区",
        observer_shape="第三行任务标题", progress_shape="首条任务状态",
        claimed_shape="首条任务已完成", claimed_texts=("已完成",),
        label="瑶池花会任务奖励", max_clicks=20,
    ))
