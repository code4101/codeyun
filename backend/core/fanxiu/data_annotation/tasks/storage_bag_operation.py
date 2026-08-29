from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service
from backend.core.fanxiu.runtime_gui import validate_runtime_evidence


STANDARD_JOB_ID = "storage-bag-operation"
TASK_TYPE = "storage_bag_operation"
WORLD_SCENE = 34
STORAGE_BAG_SCENE = 525
QUICK_OPERATION_SCENE = 526
REWARD_SCENE = 227
DANYAO_REWARD_SCENE = 351
EMPTY_OPERATION_TOAST = "暂无可快捷操作的选项"
NO_REWARD_STABLE_SECONDS = 10.0
NO_REWARD_STABLE_POLLS = 3

EXPECTED_QUICK_SETTING_VALUES = {
    "1": 0,  # OpenBox OFF
    "2": 1,  # FenJie ON
    "3": 1,  # Merge ON
    "4": 1,  # Use ON
}

QUICK_OPERATION_PANEL_SHAPES = (
    "四项快捷标签",
    "执行快捷操作（高风险）",
)


def next_storage_bag_operation_at(now: datetime | None = None) -> datetime:
    """Schedule the next completed run for 01:00 on the following day."""

    current = now or datetime.now()
    return (current + timedelta(days=1)).replace(
        hour=1,
        minute=0,
        second=0,
        microsecond=0,
    )


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _quick_operation_panel_visible(runtime: Any, scene_id: Any, frame: str) -> bool:
    if scene_id == QUICK_OPERATION_SCENE:
        return True
    shape_matches = getattr(runtime, "shape_matches", None)
    if not callable(shape_matches):
        return False
    try:
        return all(
            shape_matches(
                QUICK_OPERATION_SCENE,
                title,
                frame_data_url=frame,
            )
            is not None
            for title in QUICK_OPERATION_PANEL_SHAPES
        )
    except (KeyError, RuntimeError, ValueError):
        return False


def _verify_quick_options_read_only() -> dict[str, Any]:
    """Validate the active #526 settings without visual inference or mutation."""

    snapshot = fanxiu_instrumentation_service.backpack_quick_settings_snapshot()
    validation = validate_runtime_evidence(snapshot, max_age_seconds=5.0)
    if not validation.ok:
        raise RuntimeError(
            "储物袋_操作：#526 严格只读设置快照缺失、过期或进程身份不完整，"
            f"拒绝执行（{validation.reason}）"
        )
    values = snapshot.get("values") or {}
    if values != EXPECTED_QUICK_SETTING_VALUES:
        raise RuntimeError(
            "储物袋_操作：快捷设置不符合 Use=ON/OpenBox=OFF/FenJie=ON/Merge=ON，"
            f"observed={values}"
        )
    return snapshot


def _wait_quick_operation_panel(runtime: Any, *, timeout: float):
    """Wait for #526 without depending solely on its decorative title.

    Some live frames render the four operation rows before (or without) the
    faint top title.  The four row labels are the stable panel contract; the
    subsequent Runtime snapshot remains the authority for checkbox values.
    """

    # Keep this entrance wait independent from the post-action monotonic clock,
    # whose tests deliberately advance in large steps to prove fixed points.
    deadline = time.perf_counter() + timeout
    last_text = ""
    while time.perf_counter() < deadline:
        frame = runtime.cur_frame(update=True)
        last_text = _compact_text(runtime.ocr_text(frame))
        scene_id, _score, _matched_frame = runtime.current_scene(
            (QUICK_OPERATION_SCENE,),
            frame_data_url=frame,
        )
        if scene_id == QUICK_OPERATION_SCENE:
            return {"evidence": "scene_526", "frame": frame}
        if _quick_operation_panel_visible(runtime, scene_id, frame):
            return {"evidence": "panel_shape_contract", "frame": frame}
        yield from runtime.wait_action_settle(0.25)
    raise TimeoutError(
        "储物袋_操作：等待快捷操作面板超时；"
        f"未命中 #526 或四项完整面板契约，last_ocr={last_text!r}"
    )


def _observe_known_scene(
    runtime: Any,
    scene_ids: tuple[int, ...],
    *,
    deadline: float,
    accept_empty_toast: bool = True,
):
    """Observe only; unknown/toast-obscured frames never trigger a click."""

    while time.monotonic() < deadline:
        frame = runtime.cur_frame(update=True)
        text = _compact_text(runtime.ocr_text(frame))
        if EMPTY_OPERATION_TOAST in text and accept_empty_toast:
            return "empty", frame
        if EMPTY_OPERATION_TOAST in text:
            yield from runtime.wait_action_settle(0.25)
            continue
        scene_id, _score, _matched_frame = runtime.current_scene(
            list(scene_ids),
            frame_data_url=frame,
        )
        if (
            QUICK_OPERATION_SCENE in scene_ids
            and _quick_operation_panel_visible(runtime, scene_id, frame)
        ):
            scene_id = QUICK_OPERATION_SCENE
        if scene_id in scene_ids:
            return int(scene_id), frame
        yield from runtime.wait_action_settle(0.25)
    raise TimeoutError(
        f"储物袋_操作：等待已知场景 {scene_ids} 超时；unknown 期间未执行点击"
    )


def _finish_reward_chain(runtime: Any, *, deadline: float):
    stable_since: float | None = None
    stable_polls = 0
    stable_scene: int | None = None
    while time.monotonic() < deadline:
        frame = runtime.cur_frame(update=True)
        text = _compact_text(runtime.ocr_text(frame))
        if EMPTY_OPERATION_TOAST in text:
            return "empty_toast"
        landed, _score, _matched_frame = runtime.current_scene(
            (REWARD_SCENE, DANYAO_REWARD_SCENE, STORAGE_BAG_SCENE, QUICK_OPERATION_SCENE),
            frame_data_url=frame,
        )
        if _quick_operation_panel_visible(runtime, landed, frame):
            landed = QUICK_OPERATION_SCENE
        if landed in (REWARD_SCENE, DANYAO_REWARD_SCENE):
            break
        if landed in (STORAGE_BAG_SCENE, QUICK_OPERATION_SCENE):
            now = time.monotonic()
            if stable_scene != landed:
                stable_scene = int(landed)
                stable_since = now
                stable_polls = 0
            stable_polls += 1
            if (
                stable_polls >= NO_REWARD_STABLE_POLLS
                and now - stable_since >= NO_REWARD_STABLE_SECONDS
            ):
                return (
                    "empty_fixed_point"
                    if landed == QUICK_OPERATION_SCENE
                    else "storage_fixed_point"
                )
        else:
            # Unknown/toast-obscured frames do not count toward the fixed-point
            # window and never cause a click.
            stable_scene = None
            stable_since = None
            stable_polls = 0
        yield from runtime.wait_action_settle(0.25)
    else:
        raise TimeoutError(
            "储物袋_操作：执行后未进入奖励链，也未形成稳定 #526 无奖励固定点"
        )
    if landed == REWARD_SCENE:
        yield from runtime.wait_click(REWARD_SCENE, "继续", timeout=8.0)
        landed, _frame = yield from _observe_known_scene(
            runtime,
            (DANYAO_REWARD_SCENE, STORAGE_BAG_SCENE),
            deadline=deadline,
        )
    if landed == DANYAO_REWARD_SCENE:
        yield from runtime.wait_click(DANYAO_REWARD_SCENE, "继续", timeout=8.0)
        landed, _frame = yield from _observe_known_scene(
            runtime,
            (STORAGE_BAG_SCENE,),
            deadline=deadline,
        )
    if landed != STORAGE_BAG_SCENE:
        raise RuntimeError(f"储物袋_操作：奖励链落点非法：{landed}")
    return "reward_complete"


def execute_storage_bag_operation_task(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
):
    """Run at most three safe quick-operation batches to a proven terminal state."""

    runtime = runner._fanxiu_runtime(
        ctx,
        ctx.get("asset_tree_path"),
        stop_event=stop_event,
    )
    max_rounds = min(3, max(1, int(payload.get("max_rounds") or 3)))
    result_timeout_seconds = min(
        180.0,
        max(15.0, float(payload.get("quick_operation_result_timeout_seconds") or 60.0)),
    )

    yield from runtime.goto_view(WORLD_SCENE)
    yield from runtime.wait_click(
        WORLD_SCENE,
        "右侧菜单/储物袋",
        timeout=10.0,
    )
    yield from runtime.wait_scene(
        STORAGE_BAG_SCENE,
        timeout=10.0,
        label="储物袋_操作：等待储物袋主页",
    )

    completed_rounds = 0
    for _round_index in range(max_rounds):
        yield from runtime.wait_click(
            STORAGE_BAG_SCENE,
            "快捷操作",
            timeout=8.0,
        )
        yield from _wait_quick_operation_panel(runtime, timeout=8.0)
        _verify_quick_options_read_only()
        yield from runtime.wait_click(
            QUICK_OPERATION_SCENE,
            "执行快捷操作（高风险）",
            timeout=8.0,
        )
        # BackPackQuickView.configBtnFunc emits BackPack_12 and returns without
        # sending CM_ItemOneKeyOperate when all selected lists are empty.
        yield from runtime.wait_action_settle(0.15)
        # This deadline belongs only to the current click's result transition.
        # The Job has no arbitrary five-minute business cutoff: each navigation
        # and click already has its own timeout, while this probe distinguishes
        # reward, exact empty toast, and a stable no-result fixed point.
        result_deadline = time.monotonic() + result_timeout_seconds
        outcome = yield from _finish_reward_chain(runtime, deadline=result_deadline)
        if outcome == "storage_fixed_point":
            # The underlying bag can be recognizable before a delayed reward
            # overlay appears.  Only a fresh, stable #525 may count as the
            # direct post-action landing and advance to the next batch.
            completed_rounds += 1
            continue
        if outcome in {"empty_toast", "empty_fixed_point"}:
            # The toast can temporarily obscure scene identity.  Observe until
            # #526 is recognizable again, then close through its formal shape.
            if outcome == "empty_toast":
                landed, _frame = yield from _observe_known_scene(
                        runtime,
                        (QUICK_OPERATION_SCENE,),
                        deadline=result_deadline,
                        accept_empty_toast=False,
                )
                if landed != QUICK_OPERATION_SCENE:
                    raise RuntimeError("储物袋_操作：空列表提示后未恢复 #526")
            yield from runtime.wait_click(
                QUICK_OPERATION_SCENE,
                "外部顶部空白",
                timeout=8.0,
            )
            yield from runtime.wait_scene(
                STORAGE_BAG_SCENE,
                timeout=8.0,
                label="储物袋_操作：关闭快捷操作面板",
            )
            yield from runtime.wait_click(STORAGE_BAG_SCENE, "返回", timeout=8.0)
            yield from runtime.wait_scene(
                WORLD_SCENE,
                timeout=10.0,
                label="储物袋_操作：返回世界",
            )
            return {
                "ok": True,
                "outcome": "complete",
                "completed_rounds": completed_rounds,
                "terminal_evidence": (
                    EMPTY_OPERATION_TOAST
                    if outcome == "empty_toast"
                    else "stable_fresh_scene_526"
                ),
            }
        completed_rounds += 1

    raise RuntimeError(
        f"储物袋_操作：已执行 {completed_rounds} 轮快捷操作仍未取得空列表或稳定终态，停止"
    )


__all__ = [
    "EMPTY_OPERATION_TOAST",
    "STANDARD_JOB_ID",
    "TASK_TYPE",
    "execute_storage_bag_operation_task",
    "next_storage_bag_operation_at",
]
