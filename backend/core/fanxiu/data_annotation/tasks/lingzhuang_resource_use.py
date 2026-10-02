"""每期一次：基础任务 → 积分整轮 → 整轮预算不足时正常结束。"""
from __future__ import annotations

import re
from typing import Any

from sqlmodel import Session

from backend.db import engine
from backend.core.fanxiu.activity.lingzhuang_tasks import read_lingzhuang_task_progress
from backend.core.fanxiu.activity.lingzhuang_strengthening import read_lingzhuang_strengthening_runtime_snapshot
from backend.core.fanxiu.activity.lingzhuang_relationship import list_lingzhuang_relationship_samples
from backend.core.fanxiu.data_annotation.equipment import (
    complete_equipment_strengthening_tasks, complete_lingzhuang_score_round,
    plan_equipment_strengthening_route, EquipmentStrengtheningResourceExhausted,
)
from backend.core.fanxiu.data_annotation.tasks.lingzhuang_strengthening import (
    claim_lingzhuang_equipment_rewards, open_lingzhuang_task_page, LINGZHUANG_SCORE_TASK_SCENE,
)
from backend.core.fanxiu.data_annotation.tasks.task_reward_rows import claim_task_rows_by_ocr
from backend.core.fanxiu.data_annotation.ocr_spatial import query_ocr_lines


def estimate_round_material(*, remaining_score: int, consumed: int, score_gained: int) -> int:
    """按已确认动作的平均积分/玄铁估算，整数向上取整，未知收益不允许开轮。"""
    if remaining_score < 0 or consumed <= 0 or score_gained <= 0:
        raise ValueError("整轮预算需要有效的实际消耗与积分增量")
    return (remaining_score * consumed + score_gained - 1) // score_gained


def parse_lingzhuang_round_header(text: str, state_text: str) -> dict[str, Any] | None:
    match = re.search(r"当前轮次(\d+)[/／](\d+)", re.sub(r"\s+", "", text))
    state = re.sub(r"\s+", "", state_text)
    if not match or state not in {"可领取", "未完成", "已领取", "已完成"}:
        return None
    number, total = map(int, match.groups())
    return dict(round=number, total=total, state=state) if 0 < number <= total else None


def claim_lingzhuang_round_rewards(context: Any, *, expected_round: int, total_rounds: int):
    """十档任务与顶部轮次礼包是两份领奖事实；顶部推进后才算整轮结束。"""
    def read_header():
        for attempt in range(5):
            context.clear_frame()
            frame = context.cur_frame(update=True)
            lines = context.ocr_lines_in_shapes(LINGZHUANG_SCORE_TASK_SCENE, ("当前轮次",),
                padding=80, frame_data_url=frame, crop=True)
            states = query_ocr_lines(lines, context.shape_box(LINGZHUANG_SCORE_TASK_SCENE, "轮次奖励状态"))
            header = parse_lingzhuang_round_header(
                " ".join(row.get("text", "") for row in lines),
                " ".join(row.get("text", "") for row in states),
            )
            if header is not None and header["total"] == total_rounds:
                return header
            if attempt < 4:
                yield from context.wait_action_settle(2)
        raise RuntimeError("灵装轮次礼包状态未形成可靠 OCR 结论")

    header = yield from read_header()
    claimed = []
    while header["state"] == "可领取":
        previous = header
        yield from context.wait_click(LINGZHUANG_SCORE_TASK_SCENE, "轮次奖励状态")
        yield from context.wait_action_settle(3)
        # 观察服务器回写和奖励动画，未确认推进前不重复点击。
        for attempt in range(5):
            header = yield from read_header()
            if header != previous:
                break
            if attempt < 4:
                yield from context.wait_action_settle(2)
        else:
            raise RuntimeError("灵装轮次礼包点击后未推进，保留现场")
        claimed.append(previous["round"])
        if len(claimed) > total_rounds:
            raise RuntimeError("灵装轮次礼包未收敛")
    if header["round"] != min(expected_round, total_rounds):
        raise RuntimeError(f"灵装任务轮次 {expected_round} 与顶部礼包轮次 {header['round']} 不一致")
    if expected_round > total_rounds and header["state"] not in {"已领取", "已完成"}:
        raise RuntimeError("灵装最后一轮顶部礼包尚未确认已领取")
    return dict(claimed_rounds=claimed, header=header)


def claim_lingzhuang_score_rewards(context: Any, *, game_task_activity_id: int):
    yield from open_lingzhuang_task_page(context, game_task_activity_id=game_task_activity_id, tab="积分")
    before = read_lingzhuang_task_progress(game_task_activity_id)
    if any(row["finished"] and not row["claimed"] for row in before["score_tasks"]):
        result = yield from claim_task_rows_by_ocr(context, scene_id=LINGZHUANG_SCORE_TASK_SCENE,
            first_row_shape="首条任务领取区", observer_shape="首行任务标题",
            progress_shape="首行任务进度", progress_context_shape="首条任务领取区",
            progress_context_padding=48,
            label="灵装化道积分奖励", claimed_texts=("已完成", "已领取"), max_clicks=20)
    else:
        result = dict(clicks=0)
    after = read_lingzhuang_task_progress(game_task_activity_id)
    if any(row["finished"] and not row["claimed"] for row in after["score_tasks"]):
        raise RuntimeError("灵装积分奖励仍有可领任务，保留现场")
    round_rewards = yield from claim_lingzhuang_round_rewards(context,
        expected_round=after["score_round"], total_rounds=after["score_total_rounds"])
    yield from context.wait_click_then_scene(LINGZHUANG_SCORE_TASK_SCENE, "榜单", [676], timeout=20)
    return {**result, "round_rewards": round_rewards,
            "round_before": before["score_round"], "round_after": after["score_round"]}


def run_lingzhuang_resource_use_flow(context: Any, *, activity_id: str,
                                     cross_count: int, game_task_activity_id: int,
                                     max_clicks: int = 200):
    """整单重入消费当前游戏事实；领奖引起轮次变化后才预算下一轮。"""
    options = dict(activity_id=activity_id, cross_count=cross_count,
                   game_task_activity_id=game_task_activity_id)
    try:
        initial = read_lingzhuang_task_progress(game_task_activity_id)
    except RuntimeError:
        # 尚未加载任务时才打开页面；完整已领事实的重入无需再绕装备页。
        yield from open_lingzhuang_task_page(context, game_task_activity_id=game_task_activity_id, tab="装备")
        initial = read_lingzhuang_task_progress(game_task_activity_id)
    target = max(row["target"] for row in initial["equipment_tasks"])
    if initial["equipment_current"] >= target:
        basic = dict(ok=True, outcome="already_complete", equipment_progress=initial["equipment_current"],
                     target_progress=target, actions=[])
    else:
        try:
            basic = yield from complete_equipment_strengthening_tasks(context, **options,
                target_progress=target, max_clicks=max_clicks)
        except EquipmentStrengtheningResourceExhausted as exc:
            rewards = yield from claim_lingzhuang_equipment_rewards(context,
                game_task_activity_id=game_task_activity_id, cross_count=cross_count)
            return dict(ok=True, status="completed", outcome="insufficient_resource",
                        equipment_progress=exc.equipment_progress, target_progress=target, rewards=rewards,
                        message="灵装化道本期资源使用结束：基础任务资源不足")
    if all(row["claimed"] for row in initial["equipment_tasks"]):
        rewards = dict(ok=True, clicks=0, reason="runtime_already_claimed",
                       claimed_task_ids=[row["task_id"] for row in initial["equipment_tasks"]])
    else:
        rewards = yield from claim_lingzhuang_equipment_rewards(context,
            game_task_activity_id=game_task_activity_id, cross_count=cross_count)
    rounds, budgets = [], []
    if cross_count < 16:
        return dict(ok=True, status="completed", outcome="basic_rewards_complete", basic=basic, rewards=rewards,
                    message="灵装化道本期基础档次与奖励已完成")
    while True:
        score_rewards = yield from claim_lingzhuang_score_rewards(context, game_task_activity_id=game_task_activity_id)
        facts = read_lingzhuang_task_progress(game_task_activity_id)
        number = facts["score_round"]
        if not number or number > facts["score_total_rounds"]:
            outcome = "all_rounds_complete"
            break
        with Session(engine) as session:
            samples = list_lingzhuang_relationship_samples(session, activity_id=activity_id).samples
        # 首尾差分去掉外部已有积分的截距，跨 Cell 重入仍使用本期真实样本。
        if len(samples) < 2:
            raise RuntimeError("灵装积分预算缺少两笔真实消耗样本")
        consumed = int(samples[-1].x - samples[0].x)
        gained = int(samples[-1].values["task_score"] - samples[0].values["task_score"])
        required_score = max(row["target"] for row in facts["score_tasks"]) - facts["score_current"]
        estimate = estimate_round_material(remaining_score=max(0, required_score), consumed=consumed, score_gained=gained)
        snapshot = read_lingzhuang_strengthening_runtime_snapshot(cross_count=cross_count,
            game_task_activity_id=game_task_activity_id)
        available = sum(row.material_count for row in plan_equipment_strengthening_route(snapshot))
        budgets.append(dict(round=number, remaining_score=required_score, estimated_material=estimate,
                            available_material=available, average_score_numerator=gained,
                            average_material_denominator=consumed))
        if available < estimate:
            outcome = "insufficient_for_whole_round"
            break
        result = yield from complete_lingzhuang_score_round(context, **options,
            target_round=number, max_clicks=max_clicks)
        rounds.append({**result, "rewards_before": score_rewards})
        if not result["ok"]:
            yield from claim_lingzhuang_score_rewards(context, game_task_activity_id=game_task_activity_id)
            outcome = "insufficient_resource"
            break
    total = facts["score_total_rounds"]
    completed = min(total, number - 1)
    if outcome == "insufficient_for_whole_round":
        message = (f"灵装化道积分已完成 {completed}/{total} 轮；剩余玄铁 {available}，"
                   f"下一整轮预计需 {estimate}，本期停止")
    elif outcome == "all_rounds_complete":
        message = f"灵装化道积分 {total} 轮及奖励全部完成"
    else:
        message = f"灵装化道第 {number} 轮资源不足，本期停止；已完成 {completed}/{total} 轮"
    return dict(ok=True, status="completed", outcome=outcome, basic=basic, rewards=rewards,
                rounds=rounds, budgets=budgets, completed_rounds=completed, total_rounds=total,
                message=message)
