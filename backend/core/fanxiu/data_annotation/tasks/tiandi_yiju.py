from __future__ import annotations

"""天地弈局 target selection, dialog configuration, and bounded execution."""

from collections.abc import Callable, Mapping
import re
import threading
import time
from typing import Any

from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens
from backend.core.fanxiu.data_annotation.tasks.gameplay_rank_task_rewards import (
    GameplayRankTaskAssets,
    GameplayRankTaskTab,
    claim_gameplay_rank_task_tabs,
)
from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju_config import (
    TIANDI_YIJU_AUTO_CONFIG_OPTIONS,
    plan_tiandi_yiju_auto_challenge_from_runtime,
)
from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju_count import (
    TIANDI_YIJU_MAX_BATCH_ROUNDS,
    TiandiYijuCountAssets,
    set_tiandi_yiju_round_count,
)
from backend.core.fanxiu.instrumentation.tiandi_yiju import (
    PLAYABLE_ACTIVITY_IDS,
    read_tiandi_yiju_recommended_target,
    read_tiandi_yiju_runtime_snapshot,
)
from backend.core.fanxiu.instrumentation.tiandi_yiju_task_rewards import (
    read_tiandi_yiju_task_reward_snapshot,
)
from backend.core.fanxiu.runtime_gui.activity_bottom_tab import (
    resolve_vertical_bottom_tab,
)


TIANDI_YIJU_HOME_SCENE = 677
TIANDI_YIJU_BOARD_SCENE = 678
TIANDI_YIJU_PIECE_INFO_SCENE = 679
TIANDI_YIJU_AUTO_DIALOG_SCENE = 680
TIANDI_YIJU_RESULT_SCENE = 681
# The live result overlay is not legacy #681. Keep the contract unresolved
# until the asset owner assigns its real scene id; production must not spend a
# round merely to rediscover an already-known missing asset.
TIANDI_YIJU_RESULT_OVERLAY_SCENE: int | None = None
TIANDI_YIJU_RESULT_CONFIRM_SHAPE = "确认"
TIANDI_YIJU_TASK_SCORE_SCENE = 683
TIANDI_YIJU_TASK_CULTIVATION_SCENE = 684
TIANDI_YIJU_POINT_LIST_SCENE = 686

RuntimeReader = Callable[[], dict[str, Any]]


def _plan_shared_exchange_batch(**kwargs: Any) -> Any:
    """Lazy adapter to the activity-neutral feedback batch planner."""

    from backend.core.fanxiu.data_annotation.tasks.bounded_batch_planning import (
        plan_feedback_batch,
    )

    return plan_feedback_batch(**kwargs)


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


def _compact_ocr(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _recommended_piece_shape(runtime: Any, piece_id: int) -> Any:
    """Resolve one #686 point by Runtime id without duplicating its title map."""

    prefix = f"棋点{int(piece_id):03d}-"
    candidates = [
        shape
        for shape in runtime.view(TIANDI_YIJU_POINT_LIST_SCENE).get_shapes()
        if str(getattr(shape, "title", "") or "").startswith(prefix)
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"天地弈局 Runtime 棋点 {int(piece_id)} 无法唯一对齐 #686 Shape："
            f"matches={len(candidates)}"
        )
    return candidates[0]


def _assert_dialog_shape_assets(runtime: Any, titles: list[str]) -> None:
    """Fail before GUI mutation when the formal #680 contract is incomplete."""

    available = {
        str(getattr(shape, "title", "") or "")
        for shape in runtime.view(TIANDI_YIJU_AUTO_DIALOG_SCENE).get_shapes()
    }
    missing = [title for title in titles if title not in available]
    if missing:
        raise RuntimeError(
            "天地弈局 #680 缺少正式 Shape，拒绝开始配置或次数调整："
            + ", ".join(missing)
        )


def _assert_tiandi_yiju_production_asset_contract(runtime: Any) -> None:
    """Reject the checkpoint before navigation while formal assets are incomplete."""

    count_assets = TiandiYijuCountAssets()
    _assert_dialog_shape_assets(
        runtime,
        [
            count_assets.count_region,
            count_assets.count_decrease,
            count_assets.count_increase,
            count_assets.count_slider_thumb,
            *[
                str(option["shape"])
                for option in TIANDI_YIJU_AUTO_CONFIG_OPTIONS
            ],
        ],
    )
    result_scene = TIANDI_YIJU_RESULT_OVERLAY_SCENE
    if result_scene is None:
        raise RuntimeError(
            "天地弈局新版『对弈/确认』结果浮层尚未接入正式 scene，"
            "拒绝在生产 checkpoint 中开始对弈"
        )
    available = {
        str(getattr(shape, "title", "") or "")
        for shape in runtime.view(result_scene).get_shapes()
    }
    if TIANDI_YIJU_RESULT_CONFIRM_SHAPE not in available:
        raise RuntimeError(
            f"天地弈局新版结果 scene #{result_scene} 缺少正式"
            f"『{TIANDI_YIJU_RESULT_CONFIRM_SHAPE}』Shape，拒绝开始对弈"
        )


def open_tiandi_yiju_recommended_target(
    runtime: Any,
    *,
    target_reader: RuntimeReader | None = None,
    recommendation_override: Mapping[str, Any] | None = None,
):
    """Open the exact Runtime-selected point through the verified #686 route."""

    landed = yield from runtime.wait_click_then_view(
        TIANDI_YIJU_BOARD_SCENE,
        "弈局",
        TIANDI_YIJU_POINT_LIST_SCENE,
        timeout=20.0,
        label="天地弈局：打开精确棋点列表",
    )
    if _scene_id(landed) != TIANDI_YIJU_POINT_LIST_SCENE:
        raise RuntimeError("天地弈局未进入精确棋点列表")

    recommendation = (
        dict(recommendation_override)
        if recommendation_override is not None
        else (target_reader or read_tiandi_yiju_recommended_target)()
    )
    if not bool(recommendation.get("ok") and recommendation.get("complete")):
        raise RuntimeError("天地弈局 Runtime 推荐棋点事实不完整")
    target = dict(recommendation.get("target") or {})
    piece_id = int(target.get("piece_id") or 0)
    if piece_id <= 0:
        raise RuntimeError("天地弈局 Runtime 推荐棋点缺少有效 piece_id")
    runtime.click_shape_center(
        TIANDI_YIJU_POINT_LIST_SCENE,
        _recommended_piece_shape(runtime, piece_id),
    )
    yield from runtime.wait_action_settle(0.8)
    runtime.click_shape_center(TIANDI_YIJU_POINT_LIST_SCENE, "跳转")
    # The board camera transition is visibly asynchronous.  Waiting before
    # scene recognition prevents reading the still-present #686 frame.
    yield from runtime.wait_action_settle(2.5)
    landed = yield from runtime.wait_scene(
        TIANDI_YIJU_PIECE_INFO_SCENE,
        timeout=20.0,
        label=f"天地弈局：跳转棋点 {piece_id}",
    )
    if _scene_id(landed) != TIANDI_YIJU_PIECE_INFO_SCENE:
        raise RuntimeError(f"天地弈局未进入棋点 {piece_id} 信息页")
    return {"recommendation": dict(recommendation), "target": target}


def configure_tiandi_yiju_auto_dialog(
    runtime: Any,
    *,
    cross_count: int,
    reader: RuntimeReader | None = None,
):
    """Apply only Runtime-proven switch differences and verify the result."""

    resolved_reader = reader or read_tiandi_yiju_runtime_snapshot
    before = resolved_reader()
    plan = plan_tiandi_yiju_auto_challenge_from_runtime(
        before,
        cross_count=int(cross_count),
    )
    _assert_dialog_shape_assets(
        runtime,
        [str(action["shape"]) for action in plan["actions"]],
    )
    for action in plan["actions"]:
        runtime.click_shape_center(
            TIANDI_YIJU_AUTO_DIALOG_SCENE,
            str(action["shape"]),
        )
        yield from runtime.wait_action_settle(0.6)
    after = resolved_reader()
    verified = plan_tiandi_yiju_auto_challenge_from_runtime(
        after,
        cross_count=int(cross_count),
    )
    if not verified["already_configured"]:
        pending = ", ".join(str(item["label"]) for item in verified["actions"])
        raise RuntimeError(f"天地弈局自动对弈配置未稳定生效：{pending}")
    return {"before": dict(before), "plan": plan, "after": dict(after)}


def run_tiandi_yiju_bounded_batch(
    runtime: Any,
    *,
    requested_rounds: int,
    cross_count: int,
    snapshot_reader: RuntimeReader | None = None,
    target_reader: RuntimeReader | None = None,
    recommendation_override: Mapping[str, Any] | None = None,
):
    """Execute one bounded batch and return to #678 for re-planning."""

    requested = int(requested_rounds)
    if requested <= 0 or requested > TIANDI_YIJU_MAX_BATCH_ROUNDS:
        raise ValueError(
            f"天地弈局单批必须为 1..{TIANDI_YIJU_MAX_BATCH_ROUNDS} 次"
        )
    _assert_tiandi_yiju_production_asset_contract(runtime)
    opened = yield from open_tiandi_yiju_recommended_target(
        runtime,
        target_reader=target_reader,
        recommendation_override=recommendation_override,
    )
    target = dict(opened["target"])
    # An unoccupied point needs exactly one round to establish ownership.
    if int(target.get("total_score") or 0) == 0:
        requested = 1

    landed = yield from runtime.wait_click_then_view(
        TIANDI_YIJU_PIECE_INFO_SCENE,
        "对弈",
        TIANDI_YIJU_AUTO_DIALOG_SCENE,
        timeout=20.0,
        label="天地弈局：打开自动对弈设置",
    )
    if _scene_id(landed) != TIANDI_YIJU_AUTO_DIALOG_SCENE:
        raise RuntimeError("天地弈局未进入自动对弈设置")
    configured = yield from configure_tiandi_yiju_auto_dialog(
        runtime,
        cross_count=int(cross_count),
        reader=snapshot_reader,
    )
    count_result = yield from set_tiandi_yiju_round_count(runtime, requested)
    result = yield from _start_one_tiandi_yiju_round_and_wait_result(runtime)

    terminal_kind = str(result.get("terminal_kind") or "")
    result_scene = result.get("scene_id")
    if terminal_kind == "legacy_scene":
        runtime.click_shape_center(TIANDI_YIJU_RESULT_SCENE, "点击屏幕继续")
    elif (
        terminal_kind == "new_result_overlay"
        and TIANDI_YIJU_RESULT_OVERLAY_SCENE is not None
        and result_scene == TIANDI_YIJU_RESULT_OVERLAY_SCENE
    ):
        runtime.click_shape_center(
            TIANDI_YIJU_RESULT_OVERLAY_SCENE,
            TIANDI_YIJU_RESULT_CONFIRM_SHAPE,
        )
    else:
        raise RuntimeError(
            "天地弈局新版『对弈/确认』结果浮层缺少正式 scene/shape；"
            "已确认业务结果但保留现场，禁止按旧 #681 继续点击"
        )
    yield from runtime.wait_action_settle(1.0)
    landed = yield from runtime.wait_scene(
        TIANDI_YIJU_AUTO_DIALOG_SCENE,
        timeout=20.0,
        label="天地弈局：批次结果返回设置",
    )
    if _scene_id(landed) != TIANDI_YIJU_AUTO_DIALOG_SCENE:
        raise RuntimeError("天地弈局批次结果未返回设置页")
    landed = yield from runtime.wait_click_then_view(
        TIANDI_YIJU_AUTO_DIALOG_SCENE,
        "关闭",
        TIANDI_YIJU_BOARD_SCENE,
        timeout=20.0,
        label="天地弈局：批次后返回棋盘",
    )
    if _scene_id(landed) != TIANDI_YIJU_BOARD_SCENE:
        raise RuntimeError("天地弈局批次后未返回棋盘")
    return {
        "requested_rounds": requested,
        "recommendation": dict(opened["recommendation"]),
        "target": target,
        "configuration": configured,
        "count": count_result,
        "result": result,
    }


def _required_closing_currency(detail: Any, *, activity_id: str) -> int:
    """Consume the unified exchange plan without recalculating its target."""

    if detail is None or str(getattr(detail, "id", "")) != str(activity_id):
        raise RuntimeError("天地弈局兑换计划活动实例发生切换")
    if str(getattr(detail, "activity_type", "")) != "tiandi-yiju":
        raise RuntimeError("天地弈局兑换计划活动类型错误")
    if not bool(getattr(detail, "is_active", False)):
        raise RuntimeError("天地弈局兑换活动已不在有效期")
    plan = dict(getattr(detail, "exchange_plan", None) or {})
    if not bool(getattr(detail, "budget_ready", False)) or not bool(
        plan.get("budget_ready")
    ):
        reason = str(
            getattr(detail, "budget_block_reason", "")
            or plan.get("budget_block_reason")
            or "原因未知"
        )
        raise RuntimeError(f"天地弈局收尾道具预算 freshness 门禁失效：{reason}")
    closing = dict(dict(plan.get("target_budgets") or {}).get("收尾道具") or {})
    if "required_new_currency" not in closing:
        raise RuntimeError("天地弈局统一兑换计划缺少收尾道具 required_new_currency")
    gap = int(closing["required_new_currency"])
    if gap < 0:
        raise RuntimeError("天地弈局统一兑换计划的收尾道具缺口无效")
    return gap


def _wallet_identity(snapshot: Mapping[str, Any], *, currency_type: int) -> tuple[Any, ...]:
    evidence = dict(snapshot.get("evidence") or {})
    identity = (
        int(snapshot.get("currency_type") or 0),
        str(snapshot.get("source") or ""),
        int(evidence.get("pid") or 0),
        int(evidence.get("process_start_ticks") or 0),
    )
    if (
        identity[0] != int(currency_type)
        or identity[1] != "runtime_memory"
        or min(identity[2:]) <= 0
    ):
        raise RuntimeError(f"天地弈局钱包 Runtime 身份不完整：{identity!r}")
    return identity


def run_tiandi_yiju_exchange_target_loop(
    runtime: Any,
    *,
    occurrence: RankingOccurrence,
    stop_event: threading.Event,
    max_batches: int = 1000,
):
    """Reach the unified closing-goods target through bounded feedback batches."""

    from sqlmodel import Session, select

    from backend.core.fanxiu.activity.tiandi_yiju import (
        collect_and_store_tiandi_yiju_activity,
    )
    from backend.core.fanxiu.instrumentation.wallet import (
        read_wallet_currency_snapshot,
    )
    from backend.db import engine
    from backend.models import FanxiuExchangeActivity

    with Session(engine) as session:
        activity = session.exec(
            select(FanxiuExchangeActivity).where(
                FanxiuExchangeActivity.activity_type == "tiandi-yiju",
                FanxiuExchangeActivity.instance_key == occurrence.instance_key,
            )
        ).first()
        if activity is None:
            raise RuntimeError("天地弈局缺少当前 occurrence 的通用活动实例")
        activity_id = str(activity.id)
        detail = collect_and_store_tiandi_yiju_activity(
            session,
            activity_id=activity_id,
        )
        currency_type = int(getattr(detail, "currency_type", 0) or 0)
        required_new_currency = _required_closing_currency(
            detail,
            activity_id=activity_id,
        )
    if currency_type <= 0:
        raise RuntimeError("天地弈局通用活动实例缺少棋符货币类型")

    wallet_before = read_wallet_currency_snapshot(currency_type, allow_discovery=True)
    wallet_identity = _wallet_identity(wallet_before, currency_type=currency_type)
    measured_delta: int | None = None
    measured_rounds: int | None = None
    previous_delta: int | None = None
    previous_rounds: int | None = None
    total_rounds = 0
    batches: list[dict[str, Any]] = []
    locked_tianyuan_recommendation: dict[str, Any] | None = None

    for _batch_index in range(max(1, int(max_batches))):
        if required_new_currency == 0:
            break
        if stop_event.is_set():
            raise InterruptedError()
        shared_plan = _plan_shared_exchange_batch(
            required_new_currency=required_new_currency,
            measured_currency_delta=measured_delta,
            measured_challenges=measured_rounds,
            previous_currency_delta=previous_delta,
            previous_challenges=previous_rounds,
        )
        requested = min(
            int(shared_plan.requested_challenges),
            TIANDI_YIJU_MAX_BATCH_ROUNDS,
        )
        if requested <= 0:
            raise RuntimeError("天地弈局统一分批规划器返回了无效次数")
        batch = yield from run_tiandi_yiju_bounded_batch(
            runtime,
            requested_rounds=requested,
            cross_count=int(occurrence.cross_count),
            recommendation_override=locked_tianyuan_recommendation,
        )
        actual_rounds = int(batch["requested_rounds"])
        batch_target = dict(batch["target"])
        if (
            int(batch_target.get("piece_id") or 0) == 1
            and locked_tianyuan_recommendation is None
        ):
            recommendation = dict(batch.get("recommendation") or {})
            if recommendation:
                locked_tianyuan_recommendation = recommendation

        wallet_after = read_wallet_currency_snapshot(
            currency_type,
            allow_discovery=False,
        )
        if _wallet_identity(wallet_after, currency_type=currency_type) != wallet_identity:
            raise RuntimeError("天地弈局批次前后游戏进程或钱包身份变化")
        current_delta = int(wallet_after["exchange_currency"]) - int(
            wallet_before["exchange_currency"]
        )
        cumulative_delta = int(wallet_after["cumulative_currency"]) - int(
            wallet_before["cumulative_currency"]
        )
        if current_delta <= 0 or cumulative_delta <= 0:
            raise RuntimeError("天地弈局批次后棋符钱包没有正向增长")
        if current_delta != cumulative_delta:
            raise RuntimeError("天地弈局批次余额与累计棋符增量不一致")

        with Session(engine) as session:
            detail = collect_and_store_tiandi_yiju_activity(
                session,
                activity_id=activity_id,
            )
            required_new_currency = _required_closing_currency(
                detail,
                activity_id=activity_id,
            )
        batches.append(
            {
                "requested_rounds": actual_rounds,
                "piece_id": int(batch_target.get("piece_id") or 0),
                "currency_delta": current_delta,
                "required_new_currency_after": required_new_currency,
            }
        )
        total_rounds += actual_rounds
        previous_delta = measured_delta
        previous_rounds = measured_rounds
        measured_delta = current_delta
        measured_rounds = actual_rounds
        wallet_before = wallet_after

    reached = required_new_currency == 0
    return {
        "status": "completed" if reached else "incomplete",
        "target_reached": reached,
        "rounds": total_rounds,
        "batch_count": len(batches),
        "batches": batches,
        "required_new_currency": required_new_currency,
        "message": (
            f"天地弈局收尾道具目标已满足，共完成 {total_rounds} 次对弈"
            if reached
            else f"天地弈局达到批次数上限，仍缺棋符 {required_new_currency}"
        ),
    }


def _wait_tiandi_yiju_home_ready(
    runtime: Any,
    *,
    reader: RuntimeReader = read_tiandi_yiju_runtime_snapshot,
    timeout: float = 35.0,
):
    """Accept the Runtime-proven live home even when the old title art changed."""

    deadline = time.monotonic() + float(timeout)
    last_text = ""
    while True:
        frame = runtime.cur_frame(update=True)
        lines = runtime.ocr_fragments_in_shapes(
            TIANDI_YIJU_HOME_SCENE,
            ["进入弈局"],
            frame_data_url=frame,
            crop=True,
        )
        last_text = " ".join(str(item.get("text") or "") for item in lines)
        if "进入弈局" in _compact_ocr(last_text):
            snapshot = reader()
            _assert_safe_snapshot(snapshot, label="活动主页")
            return {"snapshot": snapshot, "ocr": last_text}
        if time.monotonic() >= deadline:
            raise TimeoutError(f"天地弈局主页未出现『进入弈局』：{last_text!r}")
        yield from runtime.wait_action_settle(0.8)


def _refresh_tiandi_yiju_exchange_facts(
    runtime: Any,
    *,
    occurrence: RankingOccurrence,
    timeout: float = 20.0,
):
    """Load the live exchange manager, persist it, then return to the home tab."""

    from sqlmodel import Session, select

    from backend.core.fanxiu.activity.tiandi_yiju import (
        collect_and_store_tiandi_yiju_activity,
    )
    from backend.core.fanxiu.instrumentation.runtime_memory import (
        FanxiuRuntimeMemoryError,
    )
    from backend.db import engine
    from backend.models import FanxiuExchangeActivity

    runtime.click_shape_center(TIANDI_YIJU_HOME_SCENE, "兑换宝阁")
    yield from runtime.wait_action_settle(0.8)
    deadline = time.monotonic() + float(timeout)
    last_error = ""
    detail: Any | None = None
    while detail is None:
        try:
            with Session(engine) as session:
                activity = session.exec(
                    select(FanxiuExchangeActivity).where(
                        FanxiuExchangeActivity.activity_type == "tiandi-yiju",
                        FanxiuExchangeActivity.instance_key == occurrence.instance_key,
                    )
                ).first()
                if activity is None:
                    raise RuntimeError("天地弈局缺少当前 occurrence 的通用活动实例")
                detail = collect_and_store_tiandi_yiju_activity(
                    session,
                    activity_id=str(activity.id),
                )
        except (FanxiuRuntimeMemoryError, ValueError) as exc:
            last_error = str(exc)
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"天地弈局兑换宝阁 Runtime 未在期限内完整加载：{last_error}"
                ) from exc
            yield from runtime.wait_action_settle(0.8)

    frame = runtime.cur_frame(update=True)
    lines = group_ocr_tokens(runtime.full_frame_ocr_tokens(frame))
    frame_width, frame_height = runtime.runner._frame_size(
        runtime.view(TIANDI_YIJU_HOME_SCENE).raw
    )
    target = resolve_vertical_bottom_tab(
        lines,
        tab_name="天地弈局",
        frame_width=frame_width,
        frame_height=frame_height,
    )
    runtime.click_frame_point(
        TIANDI_YIJU_HOME_SCENE,
        target.x,
        target.y,
    )
    yield from runtime.wait_action_settle(0.8)
    yield from _wait_tiandi_yiju_home_ready(runtime)
    return {
        "activity_id": str(getattr(detail, "id", "") or ""),
        "instance_key": str(getattr(detail, "instance_key", "") or ""),
        "shop_item_count": len(getattr(detail, "shop_items", None) or []),
        "current_currency": int(getattr(detail, "current_currency", 0) or 0),
        "cumulative_currency": int(
            getattr(detail, "cumulative_currency", 0) or 0
        ),
    }


def _start_one_tiandi_yiju_round_and_wait_result(runtime: Any, *, timeout: float = 120.0):
    """Click once and accept either legacy #681 or the live 对弈/确认 terminal."""

    yield from runtime.wait_click(TIANDI_YIJU_AUTO_DIALOG_SCENE, "对弈")
    yield from runtime.wait_action_settle(1.0)
    deadline = time.monotonic() + float(timeout)
    last_text = ""
    while True:
        candidates = [TIANDI_YIJU_RESULT_SCENE]
        if TIANDI_YIJU_RESULT_OVERLAY_SCENE is not None:
            candidates.append(TIANDI_YIJU_RESULT_OVERLAY_SCENE)
        scene_id, score, frame = runtime.current_scene(candidates, update=True)
        last_text = runtime.ocr_text(frame)
        compact = _compact_ocr(last_text)
        if scene_id == TIANDI_YIJU_RESULT_SCENE:
            return {
                "terminal_kind": "legacy_scene",
                "scene_id": scene_id,
                "score": score,
                "ocr": last_text,
            }
        if (
            TIANDI_YIJU_RESULT_OVERLAY_SCENE is not None
            and scene_id == TIANDI_YIJU_RESULT_OVERLAY_SCENE
        ):
            return {
                "terminal_kind": "new_result_overlay",
                "scene_id": scene_id,
                "score": score,
                "ocr": last_text,
            }
        if "体力消耗" in compact and "确认" in compact:
            return {
                "terminal_kind": "new_result_overlay",
                "scene_id": None,
                "score": score,
                "ocr": last_text,
            }
        if time.monotonic() >= deadline:
            raise TimeoutError(f"天地弈局唯一一局未出现结果终态：{last_text[:500]!r}")
        yield from runtime.wait_action_settle(1.0)


def execute_tiandi_yiju_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: RankingOccurrence,
):
    """Enter the exact Runtime occurrence and run the exchange-target loop."""

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
    yield from _wait_tiandi_yiju_home_ready(runtime)
    exchange_facts = yield from _refresh_tiandi_yiju_exchange_facts(
        runtime,
        occurrence=occurrence,
    )
    _assert_tiandi_yiju_production_asset_contract(runtime)
    task_rewards = yield from claim_tiandi_yiju_task_rewards(
        runtime,
        activity_id=occurrence.activity_id,
    )
    runtime.click_shape_center(TIANDI_YIJU_HOME_SCENE, "进入弈局")
    yield from runtime.wait_scene(
        TIANDI_YIJU_BOARD_SCENE,
        timeout=40.0,
        label="天地弈局：进入棋盘",
    )
    result = yield from run_tiandi_yiju_exchange_target_loop(
        runtime,
        occurrence=occurrence,
        stop_event=stop_event,
        max_batches=max(1, int(payload.get("max_batches") or 1000)),
    )
    result["task_rewards"] = task_rewards
    result["exchange_facts"] = exchange_facts
    if result.get("status") in {"completed", "incomplete"}:
        yield from runtime.goto_view(34)
    return result


__all__ = [
    "execute_tiandi_yiju_checkpoint",
    "claim_tiandi_yiju_task_rewards",
    "configure_tiandi_yiju_auto_dialog",
    "open_tiandi_yiju_recommended_target",
    "run_tiandi_yiju_bounded_batch",
    "run_tiandi_yiju_exchange_target_loop",
]
