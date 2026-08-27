from __future__ import annotations

"""Natural-strength-only 天地弈局 adapter for the ranking lifecycle."""

from collections.abc import Callable, Generator, Mapping
import threading
from typing import Any

from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.tasks.gameplay_rank_task_rewards import (
    GameplayRankTaskAssets,
    GameplayRankTaskTab,
    claim_gameplay_rank_task_tabs,
)
from backend.core.fanxiu.instrumentation.tiandi_yiju import (
    PLAYABLE_ACTIVITY_IDS,
    read_tiandi_yiju_runtime_snapshot,
    validate_tiandi_yiju_natural_play_transition,
)
from backend.core.fanxiu.instrumentation.tiandi_yiju_task_rewards import (
    read_tiandi_yiju_task_reward_snapshot,
)


TIANDI_YIJU_HOME_SCENE = 677
TIANDI_YIJU_BOARD_SCENE = 678
TIANDI_YIJU_PIECE_INFO_SCENE = 679
TIANDI_YIJU_AUTO_DIALOG_SCENE = 680
TIANDI_YIJU_RESULT_SCENE = 681
TIANDI_YIJU_TASK_SCORE_SCENE = 683
TIANDI_YIJU_TASK_CULTIVATION_SCENE = 684
MAIN_ACTIVITY_SCENE = 304

RuntimeReader = Callable[[], dict[str, Any]]


TIANDI_YIJU_TASK_ASSETS = GameplayRankTaskAssets(
    activity_label="天地弈局",
    home_scene_id=TIANDI_YIJU_HOME_SCENE,
    tabs=(
        GameplayRankTaskTab("修炼", 6, TIANDI_YIJU_TASK_CULTIVATION_SCENE, "修炼页签"),
        GameplayRankTaskTab("夺分", 7, TIANDI_YIJU_TASK_SCORE_SCENE, "夺分页签"),
    ),
)


def claim_tiandi_yiju_task_rewards(runtime: Any, *, activity_id: int):
    snapshot = read_tiandi_yiju_task_reward_snapshot(activity_id)
    return (
        yield from claim_gameplay_rank_task_tabs(
            runtime,
            snapshot,
            assets=TIANDI_YIJU_TASK_ASSETS,
            reader=lambda **options: read_tiandi_yiju_task_reward_snapshot(
                activity_id, **options
            ),
        )
    )


def _scene_id(value: Any) -> int:
    raw = getattr(value, "id", value)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("天地弈局页面等待结果缺少场景编号") from exc


def _assert_safe_snapshot(snapshot: Mapping[str, Any], *, label: str) -> None:
    if not snapshot.get("ok") or not snapshot.get("available") or not snapshot.get("complete"):
        raise RuntimeError(f"天地弈局 {label} Runtime 事实不完整")
    if not snapshot.get("choose_state_loaded"):
        raise RuntimeError(f"天地弈局 {label} 资源开关状态未加载")


def _enabled_resource_switches(snapshot: Mapping[str, Any]) -> list[str]:
    return [
        key
        for key, enabled in dict(snapshot.get("resource_spending_choices") or {}).items()
        if bool(enabled)
    ]


def play_one_tiandi_yiju_natural_round(
    runtime: Any,
    *,
    reader: RuntimeReader = read_tiandi_yiju_runtime_snapshot,
) -> Generator[Any, Any, dict[str, Any]]:
    """Select the proven own point and execute exactly one item-free round."""

    before = reader()
    _assert_safe_snapshot(before, label="动作前")
    consume = int(before.get("consume_per_play") or 0)
    strength = int(before.get("strength") or 0)
    if consume <= 0 or strength < consume:
        return {
            "status": "no_natural_strength",
            "performed_actions": False,
            "before": dict(before),
            "message": "天地弈局当前无可用自然弈力",
        }

    landed = yield from runtime.wait_click_then_view(
        TIANDI_YIJU_BOARD_SCENE,
        "己方中心棋点候选",
        TIANDI_YIJU_PIECE_INFO_SCENE,
        timeout=20.0,
        label="天地弈局：打开已验证己方棋点",
    )
    if _scene_id(landed) != TIANDI_YIJU_PIECE_INFO_SCENE:
        raise RuntimeError("天地弈局未进入己方棋点信息页")
    landed = yield from runtime.wait_click_then_view(
        TIANDI_YIJU_PIECE_INFO_SCENE,
        "对弈",
        TIANDI_YIJU_AUTO_DIALOG_SCENE,
        timeout=20.0,
        label="天地弈局：打开单次对弈设置",
    )
    if _scene_id(landed) != TIANDI_YIJU_AUTO_DIALOG_SCENE:
        raise RuntimeError("天地弈局未进入单次对弈设置")

    configured = reader()
    _assert_safe_snapshot(configured, label="设置页")
    switch_shapes = {
        "multiple_score_item": "四倍棋符开关",
        "double_reward_item": "妙手珠开关",
        "auto_use_strength_item": "自动使用仙弈盒开关",
    }
    for key in _enabled_resource_switches(configured):
        shape = switch_shapes.get(key)
        if not shape:
            raise RuntimeError(f"天地弈局发现未知资源开关：{key}")
        runtime.click_shape_center(TIANDI_YIJU_AUTO_DIALOG_SCENE, shape)
        yield from runtime.wait_action_settle(0.8)
    if _enabled_resource_switches(configured):
        configured = reader()
        _assert_safe_snapshot(configured, label="关闭资源开关后")
    remaining = _enabled_resource_switches(configured)
    if remaining:
        raise RuntimeError(f"天地弈局资源开关未关闭：{remaining[0]}")
    if int(configured.get("strength") or 0) < int(configured.get("consume_per_play") or 0):
        raise RuntimeError("天地弈局设置页自然弈力不足")

    result = yield from runtime.wait_click_then_view(
        TIANDI_YIJU_AUTO_DIALOG_SCENE,
        "对弈",
        TIANDI_YIJU_RESULT_SCENE,
        timeout=120.0,
        label="天地弈局：执行一次自然对弈",
    )
    if _scene_id(result) != TIANDI_YIJU_RESULT_SCENE:
        raise RuntimeError("天地弈局缺少胜利数据统计终态")
    after = reader()
    transition = validate_tiandi_yiju_natural_play_transition(
        configured,
        after,
        expected_plays=1,
        success_terminal=True,
    )

    landed = yield from runtime.wait_click_then_view(
        TIANDI_YIJU_RESULT_SCENE,
        "点击屏幕继续",
        TIANDI_YIJU_AUTO_DIALOG_SCENE,
        timeout=20.0,
        label="天地弈局：关闭对弈结果",
    )
    if _scene_id(landed) != TIANDI_YIJU_AUTO_DIALOG_SCENE:
        raise RuntimeError("天地弈局结果页未返回对弈设置")
    landed = yield from runtime.wait_click_then_view(
        TIANDI_YIJU_AUTO_DIALOG_SCENE,
        "关闭",
        TIANDI_YIJU_BOARD_SCENE,
        timeout=20.0,
        label="天地弈局：关闭对弈设置",
    )
    if _scene_id(landed) != TIANDI_YIJU_BOARD_SCENE:
        raise RuntimeError("天地弈局设置页未返回棋盘")
    landed = yield from runtime.wait_click_then_view(
        TIANDI_YIJU_BOARD_SCENE,
        "离开",
        MAIN_ACTIVITY_SCENE,
        timeout=25.0,
        label="天地弈局：离开棋盘",
    )
    if _scene_id(landed) != MAIN_ACTIVITY_SCENE:
        raise RuntimeError("天地弈局未回到主活动场景")
    return {
        "status": "completed",
        "performed_actions": True,
        "rounds": 1,
        "transition": transition,
        "before": dict(before),
        "configured": dict(configured),
        "after": dict(after),
        "message": "天地弈局完成 1 次自然弈力对弈",
    }


def execute_tiandi_yiju_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: RankingOccurrence,
):
    """Enter the exact Runtime occurrence and run the basic board transaction."""

    del payload
    if occurrence.activity_id not in PLAYABLE_ACTIVITY_IDS:
        raise RuntimeError(f"天地弈局 activityId={occurrence.activity_id} 不是可操作棋盘")
    from backend.core.fanxiu.activity.runtime_schedule import read_fanxiu_activity_runtime_schedule
    from backend.core.fanxiu.data_annotation.schedule_navigation import select_schedule_activity

    schedule = read_fanxiu_activity_runtime_schedule(allow_discovery=True, force_refresh=True)
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError("天地弈局 Runtime 日程不可用或不完整")
    runtime = runner._fanxiu_runtime(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
    yield from runtime.goto_view(66)
    yield from select_schedule_activity(
        runtime,
        r"天地弈局",
        enter=True,
        runtime_schedule=schedule,
        require_runtime_alignment=True,
        now=job_now(),
    )
    yield from runtime.wait_scene(
        TIANDI_YIJU_HOME_SCENE,
        timeout=35.0,
        label="天地弈局：等待活动主页",
    )
    task_rewards = yield from claim_tiandi_yiju_task_rewards(
        runtime,
        activity_id=occurrence.activity_id,
    )
    yield from runtime.wait_click_then_view(
        TIANDI_YIJU_HOME_SCENE,
        "进入弈局",
        TIANDI_YIJU_BOARD_SCENE,
        timeout=40.0,
        label="天地弈局：进入棋盘",
    )
    result = yield from play_one_tiandi_yiju_natural_round(runtime)
    result["task_rewards"] = task_rewards
    yield from runtime.goto_view(34)
    return result


__all__ = [
    "execute_tiandi_yiju_checkpoint",
    "claim_tiandi_yiju_task_rewards",
    "play_one_tiandi_yiju_natural_round",
]
