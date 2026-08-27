from __future__ import annotations

"""Standard Kunlun Secret jobs."""

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from backend.core.fanxiu.data_annotation.tasks.kunlun_secret import (
    KunlunFirstRowDecision,
    KunlunFirstRowSelector,
    KunlunFirstRowUndecided,
    complete_kunlun_optional_reward_selection,
    decide_kunlun_first_row,
)
from backend.core.fanxiu.data_annotation.tasks.kunlun_secret_navigation import (
    KunlunActivityUnavailable,
    enter_kunlun,
    leave_kunlun,
    open_kunlun_optional_reward,
    open_kunlun_tab,
    read_kunlun_page,
)
from backend.core.fanxiu.data_annotation.tasks.kunlun_secret_store import (
    complete_kunlun_store,
)
from backend.core.fanxiu.data_annotation.tasks.kunlun_secret_lottery import (
    complete_kunlun_lottery,
)
from backend.core.fanxiu.data_annotation.tasks.kunlun_secret_tasks import (
    complete_kunlun_tasks,
)
from backend.core.fanxiu.instrumentation.bothdraw import (
    read_kunlun_first_row_runtime,
)


KUNLUN_CONFIG_TASK_TYPE = "kunlun_secret_config"
KUNLUN_CONFIG_TASK_ID = "kunlun-secret-config"
KUNLUN_LOTTERY_TASK_TYPE = "kunlun_secret_lottery"
KUNLUN_LOTTERY_TASK_ID = "kunlun-secret-lottery"
KUNLUN_JADE_PER_DRAW = 5
KUNLUN_LOTTERY_JOB_NOTES = (
    f"每抽可得 {KUNLUN_JADE_PER_DRAW} 个昆仑古玉",
    "后续需求：实现兑换宝阁相关功能",
)


@dataclass(frozen=True)
class KunlunFirstRowInputs:
    reward_items: tuple[dict[str, Any], ...]
    owned_items: tuple[dict[str, Any], ...]
    selected_big_reward: dict[str, Any] | None = None


def read_kunlun_first_row_inputs() -> KunlunFirstRowInputs:
    """Return four validated candidates and their comparable live ranks."""

    snapshot = read_kunlun_first_row_runtime()
    if snapshot.get("complete") is not True:
        raise KunlunFirstRowUndecided(
            str(snapshot.get("reason") or "昆仑秘藏第一排只读运行态数据不完整")
        )
    return KunlunFirstRowInputs(
        reward_items=tuple(snapshot.get("reward_items") or ()),
        owned_items=tuple(snapshot.get("owned_items") or ()),
        selected_big_reward=(
            dict(snapshot["selected_big_reward"])
            if isinstance(snapshot.get("selected_big_reward"), dict)
            else None
        ),
    )


SHANHE_WUJIANG_TARGET_ID = 2016
SHANHE_WANGUO_LAICHAO_STAGE = 39


def _select_kunlun_special_effect_milestone(
    candidates, progress
) -> KunlunFirstRowDecision:
    """Prioritize high-value special-effect milestones, not raw stats.

    Value order is: spirit-stone income, recurring item resources, panel
    percentages, ranking points, and finally combat-only effects.  古·山河无疆屏 starts at stage 30.  Runtime/config evidence shows that
    stages 36 -> 39 consume three copies and stage 39 unlocks 万国来朝.
    The user explicitly prefers continuing to build this long-term resource
    line after stage 39.  Production therefore selects it whenever present;
    absence of this exact candidate fails closed instead of choosing another.
    """

    candidate = next(
        (
            item
            for item in candidates
            if int(item.target_id or 0) == SHANHE_WUJIANG_TARGET_ID
        ),
        None,
    )
    current = next(
        (
            item
            for item in progress
            if int(item.target_id) == SHANHE_WUJIANG_TARGET_ID
        ),
        None,
    )
    if candidate is None or current is None:
        raise KunlunFirstRowUndecided("古·山河无疆屏候选或当前阶数缺失")
    stage = int(current.rank)
    milestone = (
        f"目标{SHANHE_WANGUO_LAICHAO_STAGE}阶解锁万国来朝持续道具资源"
        if stage < SHANHE_WANGUO_LAICHAO_STAGE
        else "已解锁39阶万国来朝，继续优先山河长期资源线"
    )
    return KunlunFirstRowDecision(
        column=int(candidate.column),
        reason=f"古·山河无疆屏当前{stage}阶；{milestone}",
    )


KUNLUN_FIRST_ROW_SELECTOR: KunlunFirstRowSelector = (
    _select_kunlun_special_effect_milestone
)


def _pending_research_result(
    runner: Any,
    *,
    task_id: str,
    task_label: str,
    next_time: Callable[[], str],
) -> dict[str, Any]:
    """Remain scheduler-safe until the real Kunlun workflow is implemented."""

    scheduled_at = next_time()
    runner._persist_scheduler_task_next_time(task_id, scheduled_at)
    message = f"{task_label}：业务流程待研发，本轮未操作游戏，下次 {scheduled_at}"
    runner._log("skip", message)
    return {
        "result": "skipped",
        "skip_reason": "workflow_pending_research",
        "message": message,
    }


def _runtime(runner: Any, ctx: dict[str, Any], stop_event: threading.Event) -> Any:
    asset_tree_path = ctx.get("asset_tree_path")
    if not isinstance(asset_tree_path, Path):
        raise RuntimeError("昆仑秘藏作业缺少资产树路径")
    return runner._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)


def _select_optional_reward(
    runtime: Any,
    *,
    inputs_reader: Callable[[], KunlunFirstRowInputs] | None = None,
    selector: KunlunFirstRowSelector | None = None,
) -> dict[str, Any]:
    inputs = (inputs_reader or read_kunlun_first_row_inputs)()
    decision = decide_kunlun_first_row(
        inputs.reward_items,
        inputs.owned_items,
        selector=selector,
    )
    chosen = inputs.reward_items[int(decision.column) - 1]
    selected = inputs.selected_big_reward or {}
    selected_item_id = int(selected.get("item_id") or 0)
    if selected_item_id > 0:
        if selected_item_id == int(chosen.get("item_id") or 0):
            return {
                "outcome": "already_configured",
                "column": int(decision.column),
                "reason": decision.reason,
                "confirmed": True,
            }
        raise KunlunFirstRowUndecided(
            "昆仑秘藏本期已配置为其它大奖，拒绝自动进入重新配置页面"
        )
    # Reading and deciding happen before opening #541.  Consequently an
    # incomplete reader or selector cannot leave a half-edited form onscreen.
    current = read_kunlun_page(runtime, update=True)
    if current is None or current.page != "自选":
        open_kunlun_optional_reward(runtime)
    result = complete_kunlun_optional_reward_selection(runtime, decision)
    return {
        "outcome": "configured",
        "column": int(decision.column),
        "reason": decision.reason,
        "confirmed": bool(result.confirmed),
    }


def _run_kunlun_config_workflow(
    runtime: Any,
    *,
    inputs_reader: Callable[[], KunlunFirstRowInputs] | None = None,
    selector: KunlunFirstRowSelector | None = None,
) -> dict[str, Any]:
    optional = _select_optional_reward(
        runtime,
        inputs_reader=inputs_reader,
        selector=selector if selector is not None else KUNLUN_FIRST_ROW_SELECTOR,
    )
    open_kunlun_tab(runtime, "商店")
    store = complete_kunlun_store(runtime)
    tasks = complete_kunlun_tasks(runtime)
    lottery = complete_kunlun_lottery(runtime, allow_single_draws=False)
    return {
        "optional": optional,
        "store_clicked_values": list(store.clicked_values),
        "task_clicked_count": tasks.clicked_count,
        "task_stop_reason": tasks.stop_reason,
        "lottery_outcome": lottery,
    }


def _run_kunlun_lottery_workflow(runtime: Any) -> dict[str, Any]:
    tasks = complete_kunlun_tasks(runtime)
    lottery = complete_kunlun_lottery(runtime, allow_single_draws=True)
    return {
        "task_clicked_count": tasks.clicked_count,
        "task_stop_reason": tasks.stop_reason,
        "lottery_outcome": lottery,
    }


def execute_kunlun_config_job(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> dict[str, Any]:
    del payload
    runtime = _runtime(runner, ctx, stop_event)
    try:
        enter_kunlun(runtime)
    except KunlunActivityUnavailable as exc:
        runner._persist_scheduler_task_next_time(KUNLUN_CONFIG_TASK_ID, None)
        message = (
            "昆仑秘藏_配置：未发现活动，已清空 next_time，"
            "等待活动_每日清单同步再次触发"
        )
        runner._log("skip", message)
        return {
            "result": "skipped",
            "skip_reason": "activity_unavailable",
            "reason": str(exc),
            "message": message,
            "final_scene": 34,
        }

    details = _run_kunlun_config_workflow(runtime)
    final_scene, final_score = leave_kunlun(runtime)
    if int(final_scene) != 34 or float(final_score) < 90.0:
        raise RuntimeError("昆仑秘藏_配置收尾未可靠回到 #34")
    runner._persist_scheduler_task_next_time(KUNLUN_CONFIG_TASK_ID, None)
    message = (
        "昆仑秘藏_配置：自选、商店、任务、首奖抽取与返回流程已闭环，"
        "已清空 next_time，等待活动_每日清单同步再次触发"
    )
    runner._log("success", message)
    return {
        "result": "success",
        "message": message,
        **details,
        "final_scene": int(final_scene),
        "final_scene_score": float(final_score),
    }


def execute_kunlun_lottery_job(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> dict[str, Any]:
    del payload
    runtime = _runtime(runner, ctx, stop_event)
    try:
        enter_kunlun(runtime)
    except KunlunActivityUnavailable as exc:
        runner._persist_scheduler_task_next_time(KUNLUN_LOTTERY_TASK_ID, None)
        message = (
            "昆仑秘藏_抽奖：未发现活动，已清空 next_time，"
            "等待活动_每日清单同步再次触发"
        )
        runner._log("skip", message)
        return {
            "result": "skipped",
            "skip_reason": "activity_unavailable",
            "reason": str(exc),
            "message": message,
            "job_notes": list(KUNLUN_LOTTERY_JOB_NOTES),
            "final_scene": 34,
        }

    details = _run_kunlun_lottery_workflow(runtime)
    final_scene, final_score = leave_kunlun(runtime)
    if int(final_scene) != 34 or float(final_score) < 90.0:
        raise RuntimeError("昆仑秘藏_抽奖收尾未可靠回到 #34")
    runner._persist_scheduler_task_next_time(KUNLUN_LOTTERY_TASK_ID, None)
    message = (
        "昆仑秘藏_抽奖：晚间任务与首奖续抽已处理，已清空 next_time，"
        "等待活动_每日清单同步再次触发"
    )
    runner._log("success", message)
    return {
        "result": "success",
        "message": message,
        "job_notes": list(KUNLUN_LOTTERY_JOB_NOTES),
        **details,
        "final_scene": int(final_scene),
        "final_scene_score": float(final_score),
    }


__all__ = [
    "KUNLUN_CONFIG_TASK_ID",
    "KUNLUN_CONFIG_TASK_TYPE",
    "KUNLUN_JADE_PER_DRAW",
    "KUNLUN_LOTTERY_JOB_NOTES",
    "KUNLUN_LOTTERY_TASK_ID",
    "KUNLUN_LOTTERY_TASK_TYPE",
    "execute_kunlun_config_job",
    "execute_kunlun_lottery_job",
]
