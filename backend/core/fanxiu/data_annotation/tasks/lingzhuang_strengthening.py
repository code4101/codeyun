from __future__ import annotations

import threading
from datetime import date
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from backend.core.fanxiu.activity.exchange_event import is_exchange_activity_active
from backend.core.fanxiu.activity.resource_ranking import (
    LINGZHUANG_HUADAO_ACTIVITY_TYPE,
)
from backend.core.fanxiu.data_annotation.equipment import (
    EquipmentStrengtheningResourceExhausted,
    complete_equipment_strengthening_tasks,
)
from backend.db import engine
from backend.models import FanxiuExchangeActivity


DEFAULT_TARGET_TIER = 10
STANDARD_JOB_ID = "lingzhuang-strengthening"


def resolve_lingzhuang_strengthening_activity(
    session: Session,
    *,
    activity_id: str | None = None,
    today: date | None = None,
) -> FanxiuExchangeActivity | None:
    """Resolve the requested or latest currently active activity instance."""

    current_day = today or date.today()
    requested_id = str(activity_id or "").strip()
    if requested_id:
        activity = session.get(FanxiuExchangeActivity, requested_id)
        if activity is None or activity.activity_type != LINGZHUANG_HUADAO_ACTIVITY_TYPE:
            raise ValueError("灵装化道_强化：指定活动实例不存在")
        if not is_exchange_activity_active(activity, today=current_day):
            raise ValueError("灵装化道_强化：指定活动实例不在有效日期内")
        return activity

    activities = list(
        session.exec(
            select(FanxiuExchangeActivity)
            .where(
                FanxiuExchangeActivity.activity_type
                == LINGZHUANG_HUADAO_ACTIVITY_TYPE
            )
            .order_by(
                col(FanxiuExchangeActivity.start_date).desc(),
                col(FanxiuExchangeActivity.end_date).desc(),
                col(FanxiuExchangeActivity.cross_count).desc(),
            )
        ).all()
    )
    return next(
        (
            activity
            for activity in activities
            if is_exchange_activity_active(activity, today=current_day)
        ),
        None,
    )


def execute_lingzhuang_strengthening_task(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
):
    """Enter strengthening and consume resources up to one equipment-task tier."""

    with Session(engine) as session:
        activity = resolve_lingzhuang_strengthening_activity(
            session,
            activity_id=str(payload.get("activity_id") or "").strip() or None,
        )
    if activity is None:
        message = "灵装化道_强化：当前没有有效活动实例，未操作游戏"
        runner._log("skip", message)
        return {"ok": False, "outcome": "no_active_activity", "message": message}

    asset_tree_path = ctx.get("asset_tree_path")
    if not isinstance(asset_tree_path, Path):
        raise RuntimeError("灵装化道_强化：缺少资产树路径，无法执行")
    runtime = runner._fanxiu_runtime(
        ctx,
        asset_tree_path,
        stop_event=stop_event,
    )

    raw_target_progress = payload.get("target_progress")
    target_progress = (
        int(raw_target_progress)
        if raw_target_progress is not None and str(raw_target_progress).strip()
        else None
    )
    raw_target_tier = payload.get("target_tier")
    target_tier = (
        int(raw_target_tier)
        if raw_target_tier is not None and str(raw_target_tier).strip()
        else (None if target_progress is not None else DEFAULT_TARGET_TIER)
    )

    try:
        result = yield from complete_equipment_strengthening_tasks(
            runtime,
            activity_id=activity.id,
            target_progress=target_progress,
            target_tier=target_tier,
            cross_count=int(activity.cross_count),
            max_clicks=max(1, int(payload.get("max_clicks") or 200)),
        )
    except EquipmentStrengtheningResourceExhausted as exc:
        message = f"灵装化道_强化：{exc}，按当前存量正常停止"
        runner._log("skip", message)
        return {
            "ok": False,
            "outcome": "insufficient_resource",
            "message": message,
            "activity_id": activity.id,
            "target_tier": target_tier,
            "target_progress": exc.target_progress,
            "equipment_progress": exc.equipment_progress,
            "cumulative_material": exc.cumulative_material,
        }

    message = (
        f"灵装化道_强化：装备任务已到 {int(result['equipment_progress'])}"
        f" / {int(result['target_progress'])}"
    )
    runner._log("success", message)
    return {
        **result,
        "outcome": "target_reached",
        "message": message,
        "activity_id": activity.id,
    }


__all__ = [
    "DEFAULT_TARGET_TIER",
    "STANDARD_JOB_ID",
    "execute_lingzhuang_strengthening_task",
    "resolve_lingzhuang_strengthening_activity",
]
