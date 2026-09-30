"""升仙会玩法榜：打到举世无双，领取全部海选任务奖励后结束。

GUI 资产拥有定位。本组件只在 #893 已就绪后观察段位、次数和对手；
胜利不扣次数，因此不能使用道法争锋的“耗尽次数”作为完成目标。
不主动参加正赛、巅峰赛挑战；结束当晚单独采集最终巅峰榜。
"""

from __future__ import annotations

import re
import time
from typing import Any


QUALIFIER_SCENE = 893
QUALIFIER_TOP_TIER = "举世无双"
_TIER_PATTERN = re.compile("暂无段位|初窥门径|登堂入室|卓绝非凡|登峰造极|举世无双")
TASK_REWARDS_SCENE = 896
ENDED_SCENE = 897
RANK_SCENE = 898


def collect_shengxian_final_rankings(context: Any, *, occurrence=None):
    """Open this exact ended occurrence, load the final board and persist it."""
    from sqlmodel import Session
    from backend.db import engine
    from backend.core.fanxiu.activity.shengxian_hui import (
        current_shengxian_occurrence, store_shengxian_peak_rankings,
    )
    from backend.core.fanxiu.data_annotation.schedule_cards import inspect_schedule_cards, select_schedule_card
    live = current_shengxian_occurrence(instance_key=occurrence.instance_key if occurrence else None)
    # A fresh entry clears the client's previously loaded rank pages. Never
    # combine pages retained from an earlier, potentially unsettled collection.
    ready = yield from context.wait_scene([ENDED_SCENE, RANK_SCENE, 34, 661, 66], wait=5, required=False)
    if ready is not None and ready.scene_id in (ENDED_SCENE, RANK_SCENE):
        context.click_shape(ready.scene_id, '返回')
        yield from context.wait_scene([34, 661], wait=10)
    yield from context.go_scene(66)
    cards = yield from inspect_schedule_cards(context)
    matches = [row for row in cards['runtime']['items']
               if row['activity_id'] == live.activity_id and str(row['runtime_id']) == live.runtime_id]
    if len(matches) != 1:
        raise RuntimeError('升仙会轮播入口与本期实例不一致')
    yield from select_schedule_card(context, matches[0]['key'], state=cards)
    context.click_shape(66, '活动卡片/前往')
    yield from context.wait_scene([ENDED_SCENE], wait=10)
    context.click_shape(ENDED_SCENE, '排名')
    snapshot = yield from load_shengxian_peak_rankings(context)
    # Revalidate current instance independently after the multi-page read.
    current_shengxian_occurrence(instance_key=live.instance_key)
    with Session(engine) as session:
        result = store_shengxian_peak_rankings(session, occurrence=live, snapshot=snapshot)
    context.click_shape(RANK_SCENE, '返回')
    yield from context.wait_scene([34, 661], wait=10)
    return result


def execute_shengxian_peak_final_checkpoint(runner, ctx, stop_event, *, occurrence):
    context = runner._behavior_tree_context(ctx, ctx.get('asset_tree_path'), stop_event=stop_event)
    return (yield from collect_shengxian_final_rankings(context, occurrence=occurrence))


def load_shengxian_peak_rankings(context: Any, *, max_scrolls: int = 32):
    """Naturally load every rank page, retaining overlap with default scrolling.

    The UI defaults to the player's current stage; explicitly selecting 巅峰赛
    makes this safe when the account's default was 正赛. Completeness comes from
    the model's page offsets and contiguous rank rows, never a visible top ten.
    """
    from backend.core.fanxiu.instrumentation.shengxian_hui import read_shengxian_peak_rank_snapshot
    from backend.core.fanxiu.runtime_gui.text import normalize_ocr_name
    yield from context.wait_scene([RANK_SCENE], wait=10)
    context.click_shape(RANK_SCENE, '巅峰赛')
    yield from context.wait_action_settle(1)
    for _ in range(max_scrolls + 1):
        yield from context.wait_scene([RANK_SCENE], wait=5)
        snapshot = read_shengxian_peak_rank_snapshot()
        if snapshot['complete']:
            return snapshot
        if _ == max_scrolls:
            break
        visible = normalize_ocr_name(context.ocr_text_in_shapes(RANK_SCENE, ['列表']))
        visible_keys = [row['role_key'] for row in snapshot['rankings']
                        if normalize_ocr_name(row['name'])
                        and normalize_ocr_name(row['name']) in visible]
        if not context.observe_scroll_content(context.shape(RANK_SCENE, '列表'), visible_keys,
                                               direction='down', reset=_ == 0):
            raise RuntimeError('巅峰榜滚动无进展且分页未完整，保留现场')
        # Direction denotes the content being sought (down), not finger motion.
        # Equal backgrounds make image-similarity progress unreliable here;
        # consume semantic progress above and use the standard gesture/settle.
        yield from context.scroll_shape_content(RANK_SCENE, '列表', direction='down')
    raise RuntimeError('升仙会巅峰榜分页未完整加载，保留现场')


def claim_shengxian_task_rewards(context: Any):
    """Claim the five qualifier tiers; claimed rows move down and say 已完成."""
    from .task_reward_rows import claim_task_rows_by_ocr

    ready = yield from context.wait_scene(
        [QUALIFIER_SCENE, TASK_REWARDS_SCENE], wait=10, required=False,
    )
    if ready is None:
        raise RuntimeError("升仙会：未在海选或任务页，保留现场")
    if ready.scene_id == QUALIFIER_SCENE:
        if not read_shengxian_qualifier(context)["at_top"]:
            raise RuntimeError("升仙会：尚未达到举世无双，不能宣告玩法榜完成")
        context.click_shape(QUALIFIER_SCENE, "任务")
        yield from context.wait_scene([TASK_REWARDS_SCENE], wait=10)
    return (yield from claim_task_rows_by_ocr(
        context, scene_id=TASK_REWARDS_SCENE, first_row_shape="首行领取",
        observer_shape="首行标题", progress_shape="首行进度",
        label="升仙会任务奖励", claimed_texts=("已完成",), max_clicks=8,
    ))


def read_shengxian_qualifier(context: Any) -> dict[str, Any]:
    """Read the already-ready qualifier page; performs no click or navigation."""
    frame = context.cur_frame(update=True)
    def text(shape: str) -> str:
        return re.sub(r"\s+", "", context.ocr_text_in_shapes(
            QUALIFIER_SCENE, [shape], padding=0, frame_data_url=frame,
        ))

    tier = _TIER_PATTERN.search(text("自身段位"))
    count_text = text("剩余次数")
    count = re.search(r"剩余次数[:：]?(\d+)", count_text)
    if not tier or count is None:
        raise RuntimeError("升仙会：段位或剩余次数未读完整，保留现场")
    return {"tier": tier[0], "remaining": int(count[1]),
            "at_top": QUALIFIER_TOP_TIER == tier[0]}


def climb_shengxian_qualifier(context: Any, *, max_challenges: int = 20):
    """Climb the open qualifier from #893, stopping at its actual top tier.

    Already at the top is a no-action terminal. Each submitted challenge must
    produce changed page facts before another click; a timeout is an error,
    never permission to repeat the last challenge. Scene entry and scheduling
    belong to the caller. Same-tier wins require a future Runtime rank adapter;
    unchanged tier/count stops with an error instead of guessing from animated
    opponent text. Only the top-tier no-action terminal has passed replay in
    this component; it is not yet a production scheduled Job.
    """
    completed = 0
    while True:
        ready = yield from context.wait_scene([QUALIFIER_SCENE], wait=10, required=False)
        if ready is None or ready.scene_id != QUALIFIER_SCENE:
            raise RuntimeError("升仙会：海选页未就绪，保留现场")
        before = read_shengxian_qualifier(context)
        if before["at_top"] or before["remaining"] == 0:
            return {"status": "at_top" if before["at_top"] else "attempts_exhausted",
                    "challenges": completed, **before}
        if completed >= max_challenges:
            raise RuntimeError("升仙会：达到研发批次上限，尚未完成海选")

        # 左侧是当前画面更高位置的候选；Shape OCR 失败时不发出动作。
        context.click_shape(QUALIFIER_SCENE, "挑战左")
        deadline = time.monotonic() + 190
        yield from context.wait_action_settle(1)
        while time.monotonic() < deadline:
            landed = yield from context.wait_scene(
                [QUALIFIER_SCENE], wait=5, required=False,
            )
            if landed is None or landed.scene_id != QUALIFIER_SCENE:
                continue
            after = read_shengxian_qualifier(context)
            if after != before:
                completed += 1
                break
            yield from context.wait_action_settle(1)
        else:
            raise RuntimeError("升仙会：挑战后事实未推进，保留现场诊断，不重复点击")
