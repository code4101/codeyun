from __future__ import annotations

"""天地弈局 target selection, dialog configuration, and bounded execution."""

from collections.abc import Callable, Iterable, Mapping
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
    MASTER_SKILL_ITEM,
    QUADRUPLE_CHESS_TOKEN_ITEM,
    TIANDI_YIJU_AUTO_CONFIG_OPTIONS,
    plan_tiandi_yiju_auto_challenge_from_runtime,
)
from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju_count import (
    TIANDI_YIJU_MAX_BATCH_ROUNDS,
    TiandiYijuCountAssets,
    read_tiandi_yiju_round_count,
    set_tiandi_yiju_funded_rounds,
    set_tiandi_yiju_round_count,
)
from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju_yield import (
    append_tiandi_yiju_yield_evidence,
    load_tiandi_yiju_yield_samples,
    plan_tiandi_yiju_batch_rounds,
)
from backend.core.fanxiu.instrumentation.tiandi_yiju import (
    PLAYABLE_ACTIVITY_IDS,
    read_tiandi_yiju_auto_dialog_snapshot,
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
# The live result page closes only from its bottom safe area.
TIANDI_YIJU_RESULT_OVERLAY_SCENE: int | None = 692
TIANDI_YIJU_RESULT_CONFIRM_SHAPE = "点击屏幕关闭"
TIANDI_YIJU_TASK_SCORE_SCENE = 683
TIANDI_YIJU_TASK_CULTIVATION_SCENE = 684
TIANDI_YIJU_POINT_LIST_SCENE = 686
TIANDI_YIJU_ALLY_CONFIRM_SCENE = 687
TIANDI_YIJU_AUTO_RUNNING_SCENE = 690
TIANDI_YIJU_AUTO_COMPLETED_SCENE = 691
TIANDI_YIJU_ALLY_NO_REMINDER_SHAPE = "不再提醒"
TIANDI_YIJU_ALLY_CONFIRM_SHAPE = "仍要对弈"
TIANDI_YIJU_MASTER_SKILL_ITEM_ID = 100000008
TIANDI_YIJU_QUADRUPLE_TOKEN_ITEM_ID = 100000002

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
    return (
        yield from claim_gameplay_rank_task_tabs(
            runtime,
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


def _full_frame_compact_ocr(runtime: Any, frame: Any) -> str:
    """Join authoritative Paddle lines when the legacy OCR string is empty."""

    reader = getattr(runtime, "full_frame_ocr_tokens", None)
    if not callable(reader):
        return ""
    return _compact_ocr(
        "".join(
            str(line.get("text") or "")
            for line in group_ocr_tokens(reader(frame))
        )
    )


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
    ally_confirm_shapes = {
        str(getattr(shape, "title", "") or "")
        for shape in runtime.view(TIANDI_YIJU_ALLY_CONFIRM_SCENE).get_shapes()
    }
    missing_ally_shapes = [
        title
        for title in (
            TIANDI_YIJU_ALLY_NO_REMINDER_SHAPE,
            TIANDI_YIJU_ALLY_CONFIRM_SHAPE,
        )
        if title not in ally_confirm_shapes
    ]
    if missing_ally_shapes:
        raise RuntimeError(
            "天地弈局 #687 盟友确认弹窗缺少正式 Shape："
            + ", ".join(missing_ally_shapes)
        )
    result_scene = TIANDI_YIJU_RESULT_OVERLAY_SCENE
    if result_scene is None:
        raise RuntimeError(
            "天地弈局『批战结束』结果浮层尚未接入正式 scene，"
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


def _wait_tiandi_yiju_auto_dialog_ready(runtime: Any, *, timeout: float = 20.0):
    """Accept #680 by its tightly cropped count, not the changing score bars."""

    deadline = time.monotonic() + float(timeout)
    while True:
        try:
            if read_tiandi_yiju_round_count(runtime, TiandiYijuCountAssets()) > 0:
                return TIANDI_YIJU_AUTO_DIALOG_SCENE
        except RuntimeError:
            pass
        if time.monotonic() >= deadline:
            raise TimeoutError("天地弈局挑战配置页未读到合法次数")
        yield from runtime.wait_action_settle(0.5)


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
    feature_item_available: Mapping[str, bool] | None = None,
):
    """Apply only Runtime-proven switch differences and verify the result."""

    resolved_reader = reader or read_tiandi_yiju_auto_dialog_snapshot
    before = resolved_reader()
    plan = plan_tiandi_yiju_auto_challenge_from_runtime(
        before,
        cross_count=int(cross_count),
        feature_item_available=feature_item_available,
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
        feature_item_available=feature_item_available,
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
    verified_available_rounds: int | None = None,
    feature_item_available: Mapping[str, bool] | None = None,
):
    """Execute one bounded batch and return to #678 for re-planning."""

    requested = int(requested_rounds)
    available = int(verified_available_rounds or 0)
    if requested <= 0 or (
        requested > TIANDI_YIJU_MAX_BATCH_ROUNDS
        and (available <= 0 or requested > available)
    ):
        raise ValueError(
            f"天地弈局普通单批必须为 1..{TIANDI_YIJU_MAX_BATCH_ROUNDS} 次；"
            "更大批次必须位于 Runtime 精确证明的可用次数内"
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

    yield from runtime.wait_click(TIANDI_YIJU_PIECE_INFO_SCENE, "对弈")
    yield from runtime.wait_action_settle(0.8)
    landed = yield from _wait_tiandi_yiju_auto_dialog_ready(runtime)
    if _scene_id(landed) != TIANDI_YIJU_AUTO_DIALOG_SCENE:
        raise RuntimeError("天地弈局未进入自动对弈设置")
    configured = yield from configure_tiandi_yiju_auto_dialog(
        runtime,
        cross_count=int(cross_count),
        reader=snapshot_reader,
        feature_item_available=feature_item_available,
    )
    # Every normal batch already carries a Runtime-proven resource ceiling.
    # Use one proportional drag even for the final 100 rounds; the live OCR
    # readback, rather than the requested number, is the consumed batch size.
    if available > 0:
        count_result = yield from set_tiandi_yiju_funded_rounds(
            runtime, requested, available
        )
    else:
        count_result = yield from set_tiandi_yiju_round_count(runtime, requested)
    requested = int(count_result["after"])
    result = yield from _start_one_tiandi_yiju_round_and_wait_result(
        runtime,
        timeout=max(120.0, float(requested) * 3.0),
    )

    terminal_kind = str(result.get("terminal_kind") or "")
    result_scene = result.get("scene_id")
    direct_board = (
        terminal_kind == "direct_board"
        and result_scene == TIANDI_YIJU_BOARD_SCENE
    )
    if direct_board:
        landed = TIANDI_YIJU_BOARD_SCENE
    elif terminal_kind == "legacy_scene":
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
            "天地弈局『批战结束』结果浮层缺少正式 scene/shape；"
            "已确认业务结果但保留现场，禁止按旧 #681 继续点击"
        )
    if not direct_board:
        yield from runtime.wait_action_settle(1.0)
        post_result_scenes = [
            TIANDI_YIJU_AUTO_DIALOG_SCENE,
            TIANDI_YIJU_BOARD_SCENE,
        ]
        # After closing #692, do not accept the same stale frame as progress.
        # Legacy #681 may legitimately advance straight to the total #692.
        if (
            terminal_kind == "legacy_scene"
            and TIANDI_YIJU_RESULT_OVERLAY_SCENE is not None
        ):
            post_result_scenes.append(TIANDI_YIJU_RESULT_OVERLAY_SCENE)
        landed = yield from runtime.wait_scene(
            *post_result_scenes,
            timeout=20.0,
            label="天地弈局：关闭批战结果",
        )
    scene_id = _scene_id(landed)
    if scene_id == TIANDI_YIJU_AUTO_DIALOG_SCENE:
        yield from runtime.wait_click(TIANDI_YIJU_AUTO_DIALOG_SCENE, "关闭")
        yield from runtime.wait_action_settle(0.8)
        landed = yield from runtime.wait_scene(
            TIANDI_YIJU_BOARD_SCENE,
            TIANDI_YIJU_RESULT_OVERLAY_SCENE,
            timeout=20.0,
            label="天地弈局：批次后返回棋盘",
        )
        scene_id = _scene_id(landed)
    if scene_id == TIANDI_YIJU_RESULT_OVERLAY_SCENE:
        yield from runtime.wait_click(
            TIANDI_YIJU_RESULT_OVERLAY_SCENE,
            TIANDI_YIJU_RESULT_CONFIRM_SHAPE,
        )
        yield from runtime.wait_action_settle(0.8)
        landed = yield from runtime.wait_scene(
            TIANDI_YIJU_BOARD_SCENE,
            timeout=20.0,
            label="天地弈局：关闭总结果",
        )
        scene_id = _scene_id(landed)
    if scene_id != TIANDI_YIJU_BOARD_SCENE:
        raise RuntimeError("天地弈局关闭批战结果后未返回设置页或棋盘")
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


def _closing_currency_targets(detail: Any, *, activity_id: str) -> tuple[int, int]:
    """Read the persisted target amounts captured while the shop was open."""

    _required_closing_currency(detail, activity_id=activity_id)
    plan = dict(getattr(detail, "exchange_plan", None) or {})
    closing = dict(dict(plan.get("target_budgets") or {}).get("收尾道具") or {})
    target_total = int(closing.get("target_total_tokens") or 0)
    target_remaining = int(closing.get("target_remaining_tokens") or 0)
    if target_total <= 0 or target_remaining < 0:
        raise RuntimeError("天地弈局统一兑换计划缺少有效的收尾道具目标金额")
    return target_total, target_remaining


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


def _run_tiandi_yiju_exchange_target_loop(
    runtime: Any,
    *,
    occurrence: RankingOccurrence,
    stop_event: threading.Event,
    max_batches: int = 1000,
    supply_executor: Callable[..., Any] | None = None,
    yield_samples: Iterable[Any] = (),
    yield_feature_specs: Iterable[Any] = (),
    feature_item_fractions: Mapping[str, float] | None = None,
):
    """Reach the unified closing-goods target through bounded feedback batches."""

    from sqlmodel import Session, select

    from backend.core.fanxiu.activity.exchange_event import (
        list_exchange_activity_snapshot,
    )
    from backend.core.fanxiu.activity.exchange_planning import (
        ExchangeYieldScatterSample,
        calculate_exchange_currency_gap,
    )
    from backend.core.fanxiu.instrumentation.backpack import (
        read_backpack_item_counts,
    )
    from backend.core.fanxiu.instrumentation.wallet import (
        read_wallet_currency_snapshot,
    )
    from backend.db import engine
    from backend.models import FanxiuExchangeActivity

    scatter_feature_specs = tuple(yield_feature_specs)
    allowed_feature_keys = {str(spec.key) for spec in scatter_feature_specs}
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
        detail = list_exchange_activity_snapshot(
            session,
            activity_type="tiandi-yiju",
            activity_id=activity_id,
        ).selected_activity
        currency_type = int(getattr(detail, "currency_type", 0) or 0)
        target_total_tokens, target_remaining_tokens = _closing_currency_targets(
            detail,
            activity_id=activity_id,
        )
        persisted_samples = load_tiandi_yiju_yield_samples(
            dict(activity.evidence or {}),
            occurrence_instance_key=occurrence.instance_key,
            allowed_feature_keys=allowed_feature_keys,
        )
    if currency_type <= 0:
        raise RuntimeError("天地弈局通用活动实例缺少棋符货币类型")

    wallet_before = read_wallet_currency_snapshot(currency_type, allow_discovery=True)
    wallet_identity = _wallet_identity(wallet_before, currency_type=currency_type)
    remaining_gap = calculate_exchange_currency_gap(
        target_total_tokens=target_total_tokens,
        target_remaining_tokens=target_remaining_tokens,
        current_currency=int(wallet_before["exchange_currency"]),
        cumulative_currency=int(wallet_before["cumulative_currency"]),
    )
    required_new_currency = int(remaining_gap.required_new_currency)
    total_rounds = 0
    batches: list[dict[str, Any]] = []
    locked_recommendation: dict[str, Any] | None = None
    scatter_samples = [*persisted_samples, *yield_samples]

    for _batch_index in range(max(1, int(max_batches))):
        if required_new_currency == 0:
            break
        if stop_event.is_set():
            raise InterruptedError()
        board = read_tiandi_yiju_runtime_snapshot()
        strength_item_id = int(board.get("strength_item_id") or 0)
        if strength_item_id <= 0:
            raise RuntimeError("天地弈局 Runtime 缺少仙弈盒物品 ID")
        item_counts, _ = read_backpack_item_counts(
            [
                strength_item_id,
                TIANDI_YIJU_MASTER_SKILL_ITEM_ID,
                TIANDI_YIJU_QUADRUPLE_TOKEN_ITEM_ID,
            ],
            manager_key="tiandi-yiju-batch-items",
        )
        available_rounds = int(board.get("natural_play_budget") or 0) + int(
            item_counts.get(strength_item_id, 0)
        )
        batch_plan = plan_tiandi_yiju_batch_rounds(
            required_currency=required_new_currency,
            yield_samples=scatter_samples,
            feature_specs=scatter_feature_specs,
            feature_item_fractions=feature_item_fractions,
        )
        requested = int(batch_plan.challenge_batch_rounds)
        if requested <= 0:
            raise RuntimeError("天地弈局统一分批规划器返回了无效次数")
        if available_rounds < int(batch_plan.supply_target_rounds):
            if supply_executor is None:
                from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju_supply import (
                    ensure_tiandi_yiju_round_supply,
                )

                supply_executor = ensure_tiandi_yiju_round_supply
            yield from supply_executor(
                runtime,
                required_boxes=max(
                    0,
                    int(batch_plan.supply_target_rounds)
                    - int(board.get("natural_play_budget") or 0),
                ),
            )
            yield from runtime.goto_view(TIANDI_YIJU_HOME_SCENE)
            runtime.click_shape_center(TIANDI_YIJU_HOME_SCENE, "进入弈局")
            yield from runtime.wait_scene(
                TIANDI_YIJU_BOARD_SCENE,
                timeout=40.0,
                label="天地弈局：补给后返回棋盘",
            )
            board = read_tiandi_yiju_runtime_snapshot()
            if int(board.get("strength_item_id") or 0) != strength_item_id:
                raise RuntimeError("天地弈局补给前后仙弈盒物品 ID 变化")
            item_counts, _ = read_backpack_item_counts(
                [
                    strength_item_id,
                    TIANDI_YIJU_MASTER_SKILL_ITEM_ID,
                    TIANDI_YIJU_QUADRUPLE_TOKEN_ITEM_ID,
                ],
                manager_key="tiandi-yiju-batch-items-after-supply",
            )
            available_rounds = int(board.get("natural_play_budget") or 0) + int(
                item_counts.get(strength_item_id, 0)
            )
            if available_rounds <= 0:
                raise RuntimeError("天地弈局补给后仍无可用对弈次数")
            # Supply is intentionally capped by real tree stock and shop
            # limits.  When it cannot cover the estimated batch, consume the
            # verified remainder in one run instead of abandoning usable
            # boxes or reopening the bag for another impossible attempt.
            requested = min(requested, available_rounds)
        feature_item_available = {
            MASTER_SKILL_ITEM: int(occurrence.cross_count) > 1
            and item_counts[TIANDI_YIJU_MASTER_SKILL_ITEM_ID] > 0,
            QUADRUPLE_CHESS_TOKEN_ITEM: int(occurrence.cross_count) > 1
            and item_counts[TIANDI_YIJU_QUADRUPLE_TOKEN_ITEM_ID] > 0,
        }
        batch = yield from run_tiandi_yiju_bounded_batch(
            runtime,
            requested_rounds=requested,
            cross_count=int(occurrence.cross_count),
            recommendation_override=locked_recommendation,
            verified_available_rounds=available_rounds,
            feature_item_available=feature_item_available,
        )
        actual_rounds = int(batch["requested_rounds"])
        batch_target = dict(batch["target"])
        if locked_recommendation is None:
            recommendation = dict(batch.get("recommendation") or {})
            if recommendation:
                locked_recommendation = recommendation

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

        feature_usage: dict[str, int] | None = {}
        if any(feature_item_available.values()):
            after_feature_counts, _ = read_backpack_item_counts(
                [
                    TIANDI_YIJU_MASTER_SKILL_ITEM_ID,
                    TIANDI_YIJU_QUADRUPLE_TOKEN_ITEM_ID,
                ],
                manager_key="tiandi-yiju-feature-items-after-batch",
            )
            feature_usage = {
                MASTER_SKILL_ITEM: int(
                    item_counts[TIANDI_YIJU_MASTER_SKILL_ITEM_ID]
                )
                - int(after_feature_counts[TIANDI_YIJU_MASTER_SKILL_ITEM_ID]),
                QUADRUPLE_CHESS_TOKEN_ITEM: int(
                    item_counts[TIANDI_YIJU_QUADRUPLE_TOKEN_ITEM_ID]
                )
                - int(after_feature_counts[TIANDI_YIJU_QUADRUPLE_TOKEN_ITEM_ID]),
            }
            if any(value < 0 or value > actual_rounds for value in feature_usage.values()):
                feature_usage = None
            elif feature_usage is not None:
                feature_usage = {
                    key: value for key, value in feature_usage.items() if value > 0
                }

        if feature_usage is not None and set(feature_usage).issubset(
            allowed_feature_keys
        ):
            scatter_samples.append(
                ExchangeYieldScatterSample(
                    exchange_currency_delta=current_delta,
                    attempt_count=actual_rounds,
                    feature_item_usage=tuple(sorted(feature_usage.items())),
                )
            )
        with Session(engine) as session:
            stored_activity = session.get(FanxiuExchangeActivity, activity_id)
            if (
                stored_activity is None
                or str(stored_activity.instance_key) != occurrence.instance_key
            ):
                raise RuntimeError("天地弈局兑币样本写入时活动实例发生变化")
            stored_activity.evidence = append_tiandi_yiju_yield_evidence(
                dict(stored_activity.evidence or {}),
                occurrence_instance_key=occurrence.instance_key,
                rounds=actual_rounds,
                currency_delta=current_delta,
                process_identity=wallet_identity,
                feature_item_usage=feature_usage,
            )
            session.add(stored_activity)
            session.commit()

        remaining_gap = calculate_exchange_currency_gap(
            target_total_tokens=target_total_tokens,
            target_remaining_tokens=target_remaining_tokens,
            current_currency=int(wallet_after["exchange_currency"]),
            cumulative_currency=int(wallet_after["cumulative_currency"]),
        )
        required_new_currency = int(remaining_gap.required_new_currency)
        batches.append(
            {
                "requested_rounds": actual_rounds,
                "piece_id": int(batch_target.get("piece_id") or 0),
                "currency_delta": current_delta,
                "required_new_currency_after": required_new_currency,
            }
        )
        total_rounds += actual_rounds
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


def run_tiandi_yiju_exchange_target_loop(
    runtime: Any,
    *,
    occurrence: RankingOccurrence,
    stop_event: threading.Event,
    max_batches: int = 1000,
    yield_samples: Iterable[Any] = (),
    yield_feature_specs: Iterable[Any] = (),
    feature_item_fractions: Mapping[str, float] | None = None,
):
    """Run the taught reward -> shop facts -> challenge sequence from home."""

    # The opponent portrait changes the full-frame score of #677.  The caller
    # has already returned to the activity home, so validate the stable action
    # and Runtime facts instead of re-navigating by the volatile scene score.
    yield from _wait_tiandi_yiju_home_ready(runtime)
    task_rewards = yield from claim_tiandi_yiju_task_rewards(
        runtime,
        activity_id=occurrence.activity_id,
    )
    # Rewards can change both the current wallet and the exchange plan.  The
    # shop snapshot is authoritative only after the idempotent reward gate.
    exchange_facts = yield from _refresh_tiandi_yiju_exchange_facts(
        runtime,
        occurrence=occurrence,
    )
    from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju_tail import (
        execute_tiandi_yiju_exchange_tail,
    )

    if int(exchange_facts.get("required_new_currency", -1)) == 0:
        result = {
            "status": "completed",
            "target_reached": True,
            "rounds": 0,
            "batch_count": 0,
            "batches": [],
            "required_new_currency": 0,
            "message": "天地弈局收尾道具目标已满足，无需再次进入棋盘",
        }
        result["exchange_tail"] = yield from execute_tiandi_yiju_exchange_tail(
            None,
            {},
            occurrence=occurrence,
            stop_event=stop_event,
            runtime=runtime,
            start="home",
        )
        result["task_rewards"] = task_rewards
        result["exchange_facts"] = exchange_facts
        return result
    _assert_tiandi_yiju_production_asset_contract(runtime)
    runtime.click_shape_center(TIANDI_YIJU_HOME_SCENE, "进入弈局")
    yield from runtime.wait_scene(
        TIANDI_YIJU_BOARD_SCENE,
        timeout=40.0,
        label="天地弈局：进入棋盘",
    )
    result = yield from _run_tiandi_yiju_exchange_target_loop(
        runtime,
        occurrence=occurrence,
        stop_event=stop_event,
        max_batches=max_batches,
        yield_samples=yield_samples,
        yield_feature_specs=yield_feature_specs,
        feature_item_fractions=feature_item_fractions,
    )
    if result.get("target_reached"):
        yield from runtime.goto_view(TIANDI_YIJU_HOME_SCENE)
        result["exchange_tail"] = yield from execute_tiandi_yiju_exchange_tail(
            None,
            {},
            occurrence=occurrence,
            stop_event=stop_event,
            runtime=runtime,
            start="home",
        )
    result["task_rewards"] = task_rewards
    result["exchange_facts"] = exchange_facts
    return result


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


def _goto_tiandi_yiju_schedule(runtime: Any):
    """Open #66 through the observed #34 -> #477 -> #66 schedule route."""

    yield from runtime.goto_view(34)
    landed = yield from runtime.wait_click_then_view(
        34,
        "日程",
        [66, 477],
        settle_seconds=0.8,
        timeout=25.0,
        label="天地弈局：打开日程入口",
    )
    if _scene_id(landed) == 477:
        landed = yield from runtime.wait_click_then_view(
            477,
            "返回",
            [66],
            settle_seconds=0.8,
            timeout=25.0,
            label="天地弈局：从日程封面进入活动列表",
        )
    if _scene_id(landed) != 66:
        raise RuntimeError("天地弈局日程入口未到达 #66")


def enter_tiandi_yiju_occurrence_home(
    runtime: Any,
    *,
    occurrence: RankingOccurrence,
):
    """Enter the exact playable occurrence through the authoritative schedule."""

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
        raise RuntimeError("天地弈局 Runtime 日程不可用或不完整")
    yield from _goto_tiandi_yiju_schedule(runtime)
    yield from select_schedule_activity(
        runtime,
        r"天地弈局",
        enter=True,
        runtime_schedule=schedule,
        require_runtime_alignment=True,
        expected_activity_id=occurrence.activity_id,
        now=job_now(),
    )


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
        "required_new_currency": _required_closing_currency(
            detail,
            activity_id=str(getattr(detail, "id", "") or ""),
        ),
    }


def _start_one_tiandi_yiju_round_and_wait_result(runtime: Any, *, timeout: float = 120.0):
    """Click once and accept a result overlay or the live direct-board terminal."""

    scene_id, _score, _frame = runtime.current_scene(
        [TIANDI_YIJU_ALLY_CONFIRM_SCENE], update=True
    )
    if scene_id != TIANDI_YIJU_ALLY_CONFIRM_SCENE:
        yield from runtime.wait_click(TIANDI_YIJU_AUTO_DIALOG_SCENE, "对弈")
        yield from runtime.wait_action_settle(1.0)
    deadline = time.monotonic() + float(timeout)
    last_text = ""
    ally_confirmation_handled = False
    running_seen = False
    board_seen_at: float | None = None
    while True:
        candidates = [
            TIANDI_YIJU_BOARD_SCENE,
            TIANDI_YIJU_ALLY_CONFIRM_SCENE,
            TIANDI_YIJU_RESULT_SCENE,
            TIANDI_YIJU_AUTO_RUNNING_SCENE,
            TIANDI_YIJU_AUTO_COMPLETED_SCENE,
        ]
        if TIANDI_YIJU_RESULT_OVERLAY_SCENE is not None:
            candidates.append(TIANDI_YIJU_RESULT_OVERLAY_SCENE)
        scene_id, score, frame = runtime.current_scene(candidates, update=True)
        last_text = runtime.ocr_text(frame)
        compact = _compact_ocr(last_text)
        if "批战结束" not in compact:
            compact = _full_frame_compact_ocr(runtime, frame)
        if scene_id == TIANDI_YIJU_ALLY_CONFIRM_SCENE:
            if ally_confirmation_handled:
                raise RuntimeError("天地弈局盟友棋点确认后弹窗仍未关闭")
            yield from runtime.wait_click(
                TIANDI_YIJU_ALLY_CONFIRM_SCENE,
                TIANDI_YIJU_ALLY_NO_REMINDER_SHAPE,
            )
            yield from runtime.wait_action_settle(0.4)
            yield from runtime.wait_click(
                TIANDI_YIJU_ALLY_CONFIRM_SCENE,
                TIANDI_YIJU_ALLY_CONFIRM_SHAPE,
            )
            ally_confirmation_handled = True
            yield from runtime.wait_action_settle(1.0)
            continue
        if scene_id == TIANDI_YIJU_AUTO_COMPLETED_SCENE:
            running_seen = True
            yield from runtime.wait_click(TIANDI_YIJU_AUTO_COMPLETED_SCENE, "确认")
            yield from runtime.wait_action_settle(0.8)
            continue
        if scene_id == TIANDI_YIJU_AUTO_RUNNING_SCENE:
            running_seen = True
        if scene_id == TIANDI_YIJU_RESULT_SCENE:
            return {
                "terminal_kind": "legacy_scene",
                "scene_id": scene_id,
                "score": score,
                "ocr": last_text,
            }
        if running_seen and scene_id == TIANDI_YIJU_BOARD_SCENE:
            if board_seen_at is None:
                board_seen_at = time.monotonic()
            elif time.monotonic() - board_seen_at >= 5.0:
                return {
                    "terminal_kind": "direct_board",
                    "scene_id": scene_id,
                    "score": score,
                    "ocr": last_text,
                }
        else:
            board_seen_at = None
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
        if "批战结束" in compact and "体力消耗" in compact:
            return {
                "terminal_kind": "new_result_overlay",
                "scene_id": TIANDI_YIJU_RESULT_OVERLAY_SCENE,
                "score": score,
                "ocr": compact,
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
    runtime = runner._fanxiu_runtime(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
    yield from enter_tiandi_yiju_occurrence_home(
        runtime,
        occurrence=occurrence,
    )
    result = yield from run_tiandi_yiju_exchange_target_loop(
        runtime,
        occurrence=occurrence,
        stop_event=stop_event,
        max_batches=max(1, int(payload.get("max_batches") or 1000)),
    )
    if result.get("status") in {"completed", "incomplete"}:
        yield from runtime.goto_view(34)
    return result


__all__ = [
    "execute_tiandi_yiju_checkpoint",
    "enter_tiandi_yiju_occurrence_home",
    "claim_tiandi_yiju_task_rewards",
    "configure_tiandi_yiju_auto_dialog",
    "open_tiandi_yiju_recommended_target",
    "run_tiandi_yiju_bounded_batch",
    "run_tiandi_yiju_exchange_target_loop",
]
