from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Any, Callable

from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang import (
    XianzangChoiceNotApplicableError,
    build_xianzang_reward_candidates,
    choose_xianzang_shenlian_candidate,
    complete_xianzang_optional_reward_selection,
)
from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation import (
    XianzangActivityUnavailable,
    enter_xianzang,
    leave_xianzang,
    open_xianzang_optional_reward,
    read_xianzang_page,
)
from backend.core.fanxiu.activity.penglai_xianzang_lottery import (
    record_xianzang_availability,
)
from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_store import (
    complete_xianzang_store,
)
from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_tasks import (
    complete_xianzang_tasks,
)
from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_lottery import (
    XIANZANG_DRAW_RESULT_SCENE_ID,
    close_xianzang_draw_result,
    complete_xianzang_config_ten_draws,
    complete_xianzang_lottery,
)
from backend.core.fanxiu.instrumentation.bothdraw import (
    read_bothdraw_optional_reward_runtime,
)
from backend.core.fanxiu.instrumentation.magic_treasure import (
    read_magic_treasure_hall_runtime,
)


XIANZANG_CONFIG_TASK_TYPE = "penglai_xianzang_config"
XIANZANG_CONFIG_TASK_ID = "penglai-xianzang-config"
XIANZANG_LOTTERY_TASK_TYPE = "penglai_xianzang_lottery"
XIANZANG_LOTTERY_TASK_ID = "penglai-xianzang-lottery"
_TALISMAN_NAME_UNRESOLVED_RE = re.compile(r"^法宝\s+\d+\s+的名称无法解析$")


@dataclass(frozen=True)
class XianzangStandardJobSpec:
    """Shared lifecycle contract for one standardized Xianzang job."""

    task_id: str
    task_label: str
    completion_summary: str


def _optional_selection(
    runtime: Any,
    runner: Any,
    *,
    already_on_optional_page: bool = False,
) -> dict[str, Any]:
    optional = read_bothdraw_optional_reward_runtime()
    if not optional.get("complete"):
        reason = str(optional.get("reason") or "当期第一排候选数据不完整")
        runner._log("skip", f"蓬莱仙藏_配置：跳过自选，{reason}")
        return {"outcome": "skipped", "reason": reason}

    selected = optional.get("selected_big_reward")
    if isinstance(selected, dict) and int(selected.get("item_id") or 0) > 0:
        runner._log(
            "skip",
            f"蓬莱仙藏_配置：已配置 {selected.get('name') or selected.get('item_id')}，不重复打开自选",
        )
        return {
            "outcome": "already_configured",
            "selected_big_reward": dict(selected),
            "confirmed": True,
        }

    candidates = build_xianzang_reward_candidates(optional.get("reward_items") or [])
    try:
        # This check intentionally happens before reading the player's talisman
        # snapshot.  Non-four-Shenlian weeks are a normal skip, not an error.
        if len(candidates) != 4 or any(
            candidate.target_talisman_id is None
            or candidate.kind != "talisman_refine_material"
            for candidate in candidates
        ):
            raise XianzangChoiceNotApplicableError("当期第一排不是四个神炼材料")

        talismans = read_magic_treasure_hall_runtime()
        if not talismans.get("complete"):
            reason = str(talismans.get("reason") or "当前法宝运行态数据不完整")
            # Optional-reward selection is only one phase of the configuration
            # job.  A newly added talisman whose localized name is absent does
            # not invalidate the authoritative identity/progress of the other
            # phases and must not block store/tasks/draw completion.  Keep all
            # other Runtime incompleteness fail-closed.
            if _TALISMAN_NAME_UNRESOLVED_RE.fullmatch(reason):
                raise XianzangChoiceNotApplicableError(
                    f"法宝目录存在暂未解析名称，无法安全比较神炼候选（{reason}）"
                )
            raise RuntimeError(reason)
        choice = choose_xianzang_shenlian_candidate(
            candidates,
            talismans.get("items") or [],
        )
    except XianzangChoiceNotApplicableError as exc:
        runner._log("skip", f"蓬莱仙藏_配置：跳过自选，{exc}")
        return {
            "outcome": "skipped",
            "reason": str(exc),
            "reward_items": optional.get("reward_items") or [],
        }

    candidate_evidence = [asdict(item) for item in choice.candidate_evidence]
    runner._log(
        "info",
        "蓬莱仙藏_配置：珍宝四候选决策 "
        f"candidates={candidate_evidence}；"
        f"selected_column={choice.candidate.column}；"
        f"selected_target_talisman_id={choice.candidate.target_talisman_id}；"
        f"rule={choice.selection_reason}",
    )
    if not already_on_optional_page:
        open_xianzang_optional_reward(runtime)
    result = complete_xianzang_optional_reward_selection(
        runtime,
        choice.candidate.column,
        # The selected column is already fixed by authoritative Runtime ids and
        # exact green-check geometry.  Full-frame OCR may omit the row fraction
        # when a newly shipped localized item name is unresolved; absence is
        # acceptable, while an observed contradictory fraction still fails.
        allow_missing_fraction_ocr=True,
    )
    runner._log(
        "success",
        f"蓬莱仙藏_配置：自选已配置，第 {choice.candidate.column} 列"
        f"（target_talisman_id={choice.candidate.target_talisman_id}，"
        f"rank={choice.rank}，wujing={choice.wujing_level}，"
        f"distance={choice.distance_to_next_stage}）",
    )
    return {
        "outcome": "configured",
        "column": choice.candidate.column,
        "target_talisman_id": choice.candidate.target_talisman_id,
        "rank": choice.rank,
        "wujing_level": choice.wujing_level,
        "distance_to_next_stage": choice.distance_to_next_stage,
        "selection_reason": choice.selection_reason,
        "candidate_evidence": candidate_evidence,
        "confirmed": result.confirmed,
    }


def _runtime(runner: Any, ctx: dict[str, Any], stop_event: threading.Event) -> Any:
    asset_tree_path = ctx.get("asset_tree_path")
    if not isinstance(asset_tree_path, Path):
        raise RuntimeError("蓬莱仙藏作业缺少资产树路径")
    return runner._fanxiu_runtime(
        ctx,
        asset_tree_path,
        stop_event=stop_event,
    )


def _record_availability(*, available: bool, reason: str) -> None:
    from sqlmodel import Session

    from backend.db import engine

    with Session(engine) as session:
        record_xianzang_availability(
            session,
            available=available,
            reason=reason,
        )


def _unavailable_result(
    runner: Any,
    *,
    task_id: str,
    task_label: str,
    reason: str,
) -> dict[str, Any]:
    _record_availability(available=False, reason=reason)
    runner._persist_scheduler_task_next_time(task_id, None)
    message = f"{task_label}：未发现活动，已清空 next_time，等待活动_每日清单同步再次触发"
    runner._log("skip", message)
    return {
        "result": "skipped",
        "message": message,
        "skip_reason": "activity_unavailable",
        "reason": reason,
        "final_scene": 34,
    }


def _execute_xianzang_standard_job(
    runner: Any,
    ctx: dict[str, Any],
    stop_event: threading.Event,
    *,
    spec: XianzangStandardJobSpec,
    workflow: Callable[[Any, Any, dict[str, Any]], dict[str, Any]],
    resume_optional_page: bool = False,
    resume_draw_result_page: bool = False,
) -> dict[str, Any]:
    """Run the common enter/work/return/schedule lifecycle.

    Activity absence is the only normal skip.  All other failures propagate so
    the Scheduler can apply its normal retry policy.  In particular, next_time
    is not advanced until #447[返回] has been clicked and #34 verified.
    """

    runtime = _runtime(runner, ctx, stop_event)
    resumed: dict[str, Any] = {}
    if resume_draw_result_page and callable(getattr(runtime, "current_scene", None)):
        scene_id, score, _frame = runtime.current_scene(
            [XIANZANG_DRAW_RESULT_SCENE_ID],
            update=True,
        )
        if (
            int(scene_id or 0) == XIANZANG_DRAW_RESULT_SCENE_ID
            and float(score or 0) >= 90.0
        ):
            runner._log(
                "info",
                "蓬莱仙藏：启动现场为可靠 #451，先关闭已发生抽奖的结果页，不重复抽取",
            )
            resumed["draw_result"] = close_xianzang_draw_result(runtime)
    if resume_optional_page:
        current = read_xianzang_page(runtime, update=True)
        if (
            current is not None
            and current.page == "自选"
            and int(current.scene_id or 0) == 448
            and float(current.score or 0) >= 80.0
        ):
            runner._log(
                "info",
                "蓬莱仙藏_配置：启动现场为可靠 #448，先按权威计划幂等续做自选",
            )
            resumed["optional"] = _optional_selection(
                runtime,
                runner,
                already_on_optional_page=True,
            )
    try:
        enter_xianzang(runtime)
    except XianzangActivityUnavailable as exc:
        return _unavailable_result(
            runner,
            task_id=spec.task_id,
            task_label=spec.task_label,
            reason=str(exc),
        )

    _record_availability(available=True, reason="已进入 #447")
    details = workflow(runtime, runner, resumed)
    if isinstance(resumed.get("draw_result"), dict):
        details["resumed_draw_result"] = resumed["draw_result"]
    final_scene, final_score = leave_xianzang(runtime)
    if int(final_scene) != 34 or float(final_score) < 90.0:
        raise RuntimeError(
            f"{spec.task_label} 收尾未可靠回到 #34："
            f"scene={final_scene}, score={float(final_score):.1f}"
        )

    runner._persist_scheduler_task_next_time(spec.task_id, None)
    message = (
        f"{spec.task_label}：{spec.completion_summary}，已清空 next_time，"
        "等待活动_每日清单同步再次触发"
    )
    runner._log("success", message)
    return {
        "result": "success",
        "message": message,
        **details,
        "final_scene": int(final_scene),
        "final_scene_score": float(final_score),
    }


def _run_xianzang_config_workflow(
    runtime: Any,
    runner: Any,
    resumed: dict[str, Any],
) -> dict[str, Any]:
    optional = resumed.get("optional")
    if not isinstance(optional, dict):
        optional = _optional_selection(runtime, runner)
    store = complete_xianzang_store(runtime)
    tasks = complete_xianzang_tasks(runtime)
    # The first phase consumes only complete ten-draw batches and preserves the
    # 0..9 remainder.  The 21:10 job claims late tasks, then switches between
    # ten/single draws as needed to exhaust the same Thursday-scoped instance.
    lottery = complete_xianzang_config_ten_draws(runtime)
    return {
        "optional": optional,
        "store_clicked_values": list(store.clicked_values),
        "task_clicked_count": tasks.clicked_count,
        "task_stop_reason": tasks.stop_reason,
        "lottery_outcome": lottery,
    }


def _run_xianzang_lottery_workflow(
    runtime: Any,
    _runner: Any,
    _resumed: dict[str, Any],
) -> dict[str, Any]:
    tasks = complete_xianzang_tasks(runtime)
    lottery = complete_xianzang_lottery(runtime)
    return {
        "task_clicked_count": tasks.clicked_count,
        "task_stop_reason": tasks.stop_reason,
        "lottery_outcome": lottery,
    }


def execute_xianzang_config_job(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> dict[str, Any]:
    del payload
    return _execute_xianzang_standard_job(
        runner,
        ctx,
        stop_event,
        spec=XianzangStandardJobSpec(
            task_id=XIANZANG_CONFIG_TASK_ID,
            task_label="蓬莱仙藏_配置",
            completion_summary="自选、商店、任务、鉴宝、累抽奖励与返回流程已闭环",
        ),
        workflow=_run_xianzang_config_workflow,
        resume_optional_page=True,
        resume_draw_result_page=True,
    )


def execute_xianzang_lottery_job(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> dict[str, Any]:
    del payload
    return _execute_xianzang_standard_job(
        runner,
        ctx,
        stop_event,
        spec=XianzangStandardJobSpec(
            task_id=XIANZANG_LOTTERY_TASK_ID,
            task_label="蓬莱仙藏_抽奖",
            completion_summary="任务、鉴宝、累抽奖励与返回流程已闭环",
        ),
        workflow=_run_xianzang_lottery_workflow,
        resume_draw_result_page=True,
    )


__all__ = [
    "XIANZANG_CONFIG_TASK_ID",
    "XIANZANG_CONFIG_TASK_TYPE",
    "XIANZANG_LOTTERY_TASK_ID",
    "XIANZANG_LOTTERY_TASK_TYPE",
    "XianzangStandardJobSpec",
    "complete_xianzang_lottery",
    "execute_xianzang_config_job",
    "execute_xianzang_lottery_job",
]
