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


def plan_kunlun_first_row(
    inputs: KunlunFirstRowInputs, *, log: Callable[[str], None] | None = None,
) -> KunlunFirstRowDecision:
    """Adapt channel geometry to the activity-neutral documented-item planner.

    Only an unconfigured instance is analysed. A committed selection is final;
    neither ranks nor documents need re-evaluation until a new activity instance.
    """
    from backend.core.fanxiu.activity.cultivation_choice import (
        plan_single_cultivation_choice, committed_cultivation_choice,
    )
    from backend.core.fanxiu.catalog.cultivation_rules import prepare_cultivation_choice_rules

    committed = committed_cultivation_choice(inputs.reward_items,
        int((inputs.selected_big_reward or {}).get("item_id") or 0))
    if committed is not None:
        return KunlunFirstRowDecision(column=committed, reason="本期已选择，直接复用；不再读取阶数或重新规划")
    rules = prepare_cultivation_choice_rules(inputs.reward_items, inputs.owned_items, log=log)
    plan = plan_single_cultivation_choice(inputs.reward_items, inputs.owned_items, rules)
    return KunlunFirstRowDecision(column=plan.column, reason=plan.reason)


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


def _behavior_tree_context(runner: Any, ctx: dict[str, Any], stop_event: threading.Event) -> Any:
    asset_tree_path = ctx.get("asset_tree_path")
    if not isinstance(asset_tree_path, Path):
        raise RuntimeError("昆仑秘藏作业缺少资产树路径")
    return runner._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)


def _select_optional_reward(
    context: Any,
    *,
    inputs_reader: Callable[[], KunlunFirstRowInputs] | None = None,
    selector: KunlunFirstRowSelector | None = None,
) -> dict[str, Any]:
    inputs = (inputs_reader or read_kunlun_first_row_inputs)()
    # The game's committed selection is authoritative. Re-entry must not
    # compile a new policy and replace a choice already made for this instance.
    selected = inputs.selected_big_reward or {}
    selected_item_id = int(selected.get("item_id") or 0)
    from backend.core.fanxiu.activity.cultivation_choice import committed_cultivation_choice
    committed_column = committed_cultivation_choice(inputs.reward_items, selected_item_id)
    if committed_column is not None:
        return {"outcome": "already_configured", "column": committed_column,
                "reason": "Runtime 已确认本期大奖配置，复用游戏事实", "confirmed": True}
    if selector is None:
        decision = plan_kunlun_first_row(inputs, log=print)
    else:
        decision = decide_kunlun_first_row(inputs.reward_items, inputs.owned_items, selector=selector)
    print(f"昆仑自选规划：{decision.reason}")
    # Reading and deciding happen before opening #541.  Consequently an
    # incomplete reader or selector cannot leave a half-edited form onscreen.
    current = yield from read_kunlun_page(context, update=True)
    if current is None or current.page != "自选":
        yield from open_kunlun_optional_reward(context)
    result = yield from complete_kunlun_optional_reward_selection(context, decision)
    return {
        "outcome": "configured",
        "column": int(decision.column),
        "reason": decision.reason,
        "confirmed": bool(result.confirmed),
    }


def _run_kunlun_config_workflow(
    context: Any,
    *,
    inputs_reader: Callable[[], KunlunFirstRowInputs] | None = None,
    selector: KunlunFirstRowSelector | None = None,
) -> dict[str, Any]:
    optional = yield from _select_optional_reward(
        context,
        inputs_reader=inputs_reader,
        selector=selector,
    )
    yield from open_kunlun_tab(context, "商店")
    store = yield from complete_kunlun_store(context)
    tasks = yield from complete_kunlun_tasks(context)
    lottery = yield from complete_kunlun_lottery(context, allow_single_draws=False)
    return {
        "optional": optional,
        "store_clicked_values": list(store.clicked_values),
        "task_clicked_count": tasks.clicked_count,
        "task_stop_reason": tasks.stop_reason,
        "lottery_outcome": lottery,
    }


def _run_kunlun_lottery_workflow(context: Any) -> dict[str, Any]:
    tasks = yield from complete_kunlun_tasks(context)
    lottery = yield from complete_kunlun_lottery(context, allow_single_draws=True)
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
    context = _behavior_tree_context(runner, ctx, stop_event)
    try:
        yield from enter_kunlun(context)
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

    details = yield from _run_kunlun_config_workflow(context)
    final_scene, final_score = yield from leave_kunlun(context)
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
    context = _behavior_tree_context(runner, ctx, stop_event)
    try:
        yield from enter_kunlun(context)
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

    details = yield from _run_kunlun_lottery_workflow(context)
    final_scene, final_score = yield from leave_kunlun(context)
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
    "plan_kunlun_first_row",
    "read_kunlun_first_row_inputs",
    "execute_kunlun_config_job",
    "execute_kunlun_lottery_job",
]
