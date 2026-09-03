from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation.schedule_navigation import (
    select_schedule_activity,
)


XUTIAN_RANKING_TASK_TYPE = "xutian_palace_rankings"
XUTIAN_RANKING_TASK_ID = "xutian-palace-rankings"
XUTIAN_MAIN_SCENE_ID = 452
XUTIAN_PERSONAL_RANK_SCENE_ID = 453
XUTIAN_PLANE_RANK_SCENE_ID = 454
XUTIAN_SCENE_IDS = (
    XUTIAN_MAIN_SCENE_ID,
    XUTIAN_PERSONAL_RANK_SCENE_ID,
    XUTIAN_PLANE_RANK_SCENE_ID,
)


def _behavior_tree_context(runner: Any, ctx: dict[str, Any], stop_event: threading.Event) -> Any:
    asset_tree_path = ctx.get("asset_tree_path")
    if not isinstance(asset_tree_path, Path):
        raise RuntimeError("虚天殿榜单作业缺少资产树路径")
    return runner._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)


def _wait_scene(
    context: Any,
    target: int,
    *,
    timeout_seconds: float = 10.0,
    minimum_score: float = 80.0,
) -> tuple[int, float, str]:
    deadline = time.monotonic() + max(0.5, float(timeout_seconds))
    last_scene: int | None = None
    last_score = 0.0
    last_text = ""
    while True:
        last_scene, last_score, frame = context.current_scene(
            list(XUTIAN_SCENE_IDS), update=True
        )
        last_text = context.ocr_text(frame)
        if int(last_scene or 0) == int(target) and float(last_score) >= minimum_score:
            return int(last_scene), float(last_score), last_text
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"等待虚天殿场景 #{target} 超时：scene={last_scene}, "
                f"score={float(last_score):.1f}, ocr={last_text[:120]}"
            )
        time.sleep(0.25)


def _wait_one_of(
    context: Any, targets: tuple[int, ...], *, timeout_seconds: float = 10.0
) -> tuple[int, float, str]:
    deadline = time.monotonic() + max(0.5, float(timeout_seconds))
    last_scene: int | None = None
    last_score = 0.0
    last_text = ""
    while True:
        last_scene, last_score, frame = context.current_scene(list(targets), update=True)
        last_text = context.ocr_text(frame)
        if int(last_scene or 0) in targets and float(last_score) >= 80.0:
            return int(last_scene), float(last_score), last_text
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"等待虚天殿返回落点超时：scene={last_scene}, "
                f"score={float(last_score):.1f}, ocr={last_text[:120]}"
            )
        time.sleep(0.25)


def _enter_rankings(context: Any) -> None:
    scene, score, _frame = context.current_scene(
        [34, 66, *XUTIAN_SCENE_IDS], update=True
    )
    if int(scene or 0) not in XUTIAN_SCENE_IDS:
        if int(scene or 0) != 66:
            result = context.go_scene(66)
            if hasattr(result, "send"):
                yield from result
        scene, score, _frame = context.current_scene([66], update=True)
        if int(scene or 0) != 66 or float(score) < 90.0:
            raise RuntimeError(
                f"#66 未可靠识别日程页：scene={scene}, score={float(score):.1f}"
            )
        yield from select_schedule_activity(
            context, r"虚天(殿)?", enter=True
        )
        _wait_scene(context, XUTIAN_MAIN_SCENE_ID)

    scene, _score, frame = context.current_scene(list(XUTIAN_SCENE_IDS), update=True)
    if int(scene or 0) == XUTIAN_PERSONAL_RANK_SCENE_ID:
        return
    if int(scene or 0) == XUTIAN_PLANE_RANK_SCENE_ID:
        context.click_shape(XUTIAN_PLANE_RANK_SCENE_ID, "个人", frame_data_url=frame)
        _wait_scene(context, XUTIAN_PERSONAL_RANK_SCENE_ID)
        return
    frame = context.cur_frame(update=True)
    context.click_shape(XUTIAN_MAIN_SCENE_ID, "虚天榜", frame_data_url=frame)
    _wait_scene(context, XUTIAN_PERSONAL_RANK_SCENE_ID)


def _store_rankings() -> tuple[str, int, int]:
    from sqlmodel import Session, select

    from backend.core.fanxiu.activity.xutian_palace_instrumentation import (
        collect_and_store_xutian_palace_rankings,
        ensure_xutian_palace_activity,
    )
    from backend.db import engine
    from backend.models import FanxiuExchangeActivity, FanxiuExchangeRanking

    with Session(engine) as session:
        current_activity_id = ensure_xutian_palace_activity(session)
        activity = session.get(FanxiuExchangeActivity, current_activity_id)
        if activity is None:
            raise RuntimeError("数据库中没有可更新的虚天殿活动")
        collect_and_store_xutian_palace_rankings(
            session, activity_id=activity.id, allow_discovery=True
        )
        rows = session.exec(
            select(FanxiuExchangeRanking).where(
                FanxiuExchangeRanking.activity_id == activity.id
            )
        ).all()
        personal = sum(row.ranking_scope == "personal" for row in rows)
        plane = sum(row.ranking_scope == "plane" for row in rows)
        return activity.id, personal, plane


def _return_world(context: Any) -> tuple[int, float]:
    scene, _score, frame = context.current_scene(list(XUTIAN_SCENE_IDS), update=True)
    if int(scene or 0) != XUTIAN_MAIN_SCENE_ID:
        context.click_shape(int(scene), "返回", frame_data_url=frame)
        scene, score, _text = _wait_one_of(context, (34, 66, XUTIAN_MAIN_SCENE_ID))
        frame = context.cur_frame()
    else:
        score = 0.0
    if int(scene or 0) == XUTIAN_MAIN_SCENE_ID:
        context.click_shape(XUTIAN_MAIN_SCENE_ID, "返回", frame_data_url=frame)
        scene, score, _text = _wait_one_of(context, (34, 66))
    if int(scene or 0) != 34:
        result = context.go_scene(34)
        if hasattr(result, "send"):
            yield from result
        scene, score, _frame = context.current_scene([34], update=True)
    if int(scene or 0) != 34 or float(score) < 90.0:
        raise RuntimeError(
            f"虚天殿榜单作业收尾未可靠回到 #34：scene={scene}, score={float(score):.1f}"
        )
    return int(scene), float(score)


def execute_xutian_palace_rankings_job(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> dict[str, Any]:
    del payload
    context = _behavior_tree_context(runner, ctx, stop_event)
    yield from _enter_rankings(context)

    # Both tabs are opened and verified deliberately.  The memory reader then
    # consumes the stable ActivityrankMgr model; OCR is navigation evidence only.
    frame = context.cur_frame(update=True)
    context.click_shape(XUTIAN_PERSONAL_RANK_SCENE_ID, "位面", frame_data_url=frame)
    _wait_scene(context, XUTIAN_PLANE_RANK_SCENE_ID)
    activity_id, personal_count, plane_count = _store_rankings()
    final_scene, final_score = yield from _return_world(context)

    message = (
        f"虚天殿_榜单数据：个人榜 {personal_count} 条、位面榜 {plane_count} 条已更新"
    )
    runner._log("success", message)
    return {
        "result": "success",
        "message": message,
        "activity_id": activity_id,
        "personal_count": personal_count,
        "plane_count": plane_count,
        "final_scene": final_scene,
        "final_scene_score": final_score,
    }


__all__ = [
    "XUTIAN_RANKING_TASK_ID",
    "XUTIAN_RANKING_TASK_TYPE",
    "execute_xutian_palace_rankings_job",
]
