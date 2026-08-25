from __future__ import annotations

"""19:00 Magic Invasion adapter owned by the shared ranking lifecycle Job."""

import threading
from typing import Any

from backend.core.fanxiu.activity.magic_invasion_explore import (
    MAGIC_INVASION_EXPLORE_BATCH_SIZE,
    MAGIC_INVASION_TARGET_BATCHES,
)
from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.tasks.magic_invasion import (
    execute_magic_invasion_explore_job,
    load_magic_invasion_occurrence_progress,
)
from backend.core.fanxiu.data_annotation.tasks.magic_invasion_supply import (
    TIANYAN_ITEM_ID,
    ensure_magic_tianyan_supply,
)
from backend.core.fanxiu.data_annotation.tasks.magic_invasion_task_rewards import (
    claim_magic_invasion_task_rewards,
)
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts


def _remaining_tianyan_requirement(
    payload: dict[str, Any], occurrence: RankingOccurrence
) -> int:
    del payload
    from backend.core.fanxiu.data_annotation.tasks.magic_invasion import (
        MagicInvasionOccurrence,
    )

    progress = load_magic_invasion_occurrence_progress(
        MagicInvasionOccurrence(
            occurrence_id=occurrence.runtime_id,
            activity_id=occurrence.activity_id,
            runtime_id=int(occurrence.runtime_id),
            start_time_ms=int(occurrence.start_at.timestamp() * 1000),
            end_time_ms=int(occurrence.end_at.timestamp() * 1000),
            server_count=occurrence.cross_count,
            mode="cross" if occurrence.cross_count > 1 else "server",
        )
    )
    progress_occurrence = str(progress.get("occurrence_id") or "")
    state = str(progress.get("state") or "")
    if (
        progress_occurrence
        and progress_occurrence != occurrence.runtime_id
        and state not in {"", "complete"}
    ):
        raise RuntimeError("上一魔道 occurrence 的不可逆进度尚未闭合")
    if progress_occurrence == occurrence.runtime_id and state not in {"", "ready", "complete"}:
        raise RuntimeError("魔道存在未闭合不可逆证据，禁止从 GUI 中间步骤恢复")
    confirmed = (
        list(progress.get("confirmed_batches") or [])
        if progress_occurrence == occurrence.runtime_id
        else []
    )
    remaining_batches = max(0, MAGIC_INVASION_TARGET_BATCHES - len(confirmed))
    return remaining_batches * MAGIC_INVASION_EXPLORE_BATCH_SIZE


def execute_magic_invasion_compound_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: RankingOccurrence,
):
    """One activity visit: task rewards → needed supply → 3×500 exploration."""

    required_tianyan = _remaining_tianyan_requirement(payload, occurrence)

    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )
    from backend.core.fanxiu.data_annotation.schedule_navigation import (
        select_schedule_activity,
    )

    schedule = read_fanxiu_activity_runtime_schedule(
        allow_discovery=True,
        force_refresh=True,
    )
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError("魔道任务步骤的 Runtime 日程不可用")
    runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
    yield from runtime.goto_view(66)
    yield from select_schedule_activity(
        runtime,
        r"魔道入侵",
        enter=True,
        runtime_schedule=schedule,
        require_runtime_alignment=True,
        now=job_now(),
    )
    yield from runtime.wait_scene(
        509,
        timeout=30.0,
        label="魔道入侵：等待活动主页领取任务",
    )
    task_result = yield from claim_magic_invasion_task_rewards(
        runtime,
        activity_id=occurrence.activity_id,
    )
    counts, inventory_evidence = read_backpack_item_counts(
        (TIANYAN_ITEM_ID,),
        manager_key="magic-invasion-compound",
    )
    tianyan_before_supply = int(counts.get(TIANYAN_ITEM_ID) or 0)
    if not required_tianyan:
        supply_result = {
            "status": "not_needed",
            "tianyan_before": tianyan_before_supply,
            "tianyan_after": tianyan_before_supply,
            "evidence": inventory_evidence,
            "executions": [],
        }
        already_on_main_scene = True
    elif tianyan_before_supply >= required_tianyan:
        supply_result = {
            "status": "sufficient",
            "tianyan_before": tianyan_before_supply,
            "tianyan_after": tianyan_before_supply,
            "required_tianyan": required_tianyan,
            "evidence": inventory_evidence,
            "executions": [],
        }
        already_on_main_scene = True
    else:
        supply_result = yield from ensure_magic_tianyan_supply(
            runner,
            ctx,
            stop_event,
            required_tianyan=required_tianyan,
        )
        supply_result = {
            **dict(supply_result),
            "activity_page_tianyan_before": tianyan_before_supply,
            "activity_page_inventory_evidence": inventory_evidence,
        }
        already_on_main_scene = False

    explore_payload = {
        **{
            key: value
            for key, value in payload.items()
            if key != "magic_invasion_progress"
        },
        "target_batches": MAGIC_INVASION_TARGET_BATCHES,
        "batch_size": MAGIC_INVASION_EXPLORE_BATCH_SIZE,
        "expected_occurrence_id": occurrence.runtime_id,
    }
    explore_result = yield from execute_magic_invasion_explore_job(
        runner,
        ctx,
        explore_payload,
        stop_event,
        manage_schedule=False,
        prepared_runtime=runtime,
        prepared_schedule=schedule,
        already_on_main_scene=already_on_main_scene,
    )
    explore_progress = (
        dict(explore_result.get("progress") or {})
        if isinstance(explore_result, dict)
        else {}
    )
    confirmed_batches = explore_progress.get("confirmed_batches")
    confirmed_batches = (
        list(confirmed_batches) if isinstance(confirmed_batches, list) else []
    )
    if (
        str(explore_progress.get("occurrence_id") or "")
        != occurrence.runtime_id
        or str(explore_progress.get("state") or "") != "complete"
        or int(explore_progress.get("base_explore_count") or 0) != 1500
        or len(confirmed_batches) != MAGIC_INVASION_TARGET_BATCHES
    ):
        raise RuntimeError(
            "魔道探查缺少同一 occurrence 的 3×500 完成证据，拒绝提交复合 checkpoint"
        )
    return {
        "status": "completed",
        "message": (
            f"魔道 occurrence {occurrence.runtime_id}：任务、补给、"
            "3×500 探查闭环完成"
        ),
        "tasks": task_result,
        "supply": supply_result,
        "exploration": explore_result,
    }


__all__ = ["execute_magic_invasion_compound_checkpoint"]
