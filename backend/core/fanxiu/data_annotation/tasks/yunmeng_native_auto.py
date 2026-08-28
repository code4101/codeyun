from __future__ import annotations

"""Fail-closed GUI driver for Yunmeng Trial's native auto-challenge dialog.

The caller is responsible for navigating to the verified Yunmeng home scene.
This module only consumes named scene/shape assets and remains an ordinary
behavior-tree generator; it does not introduce another execution channel.
"""

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Iterator

from backend.core.fanxiu.data_annotation.tasks.bounded_batch_planning import (
    FeedbackBatchPlan as YunmengNativeBatchPlan,
    plan_feedback_batch,
)
from backend.core.fanxiu.data_annotation.tasks.integer_count_control import (
    read_integer_slider_count as _read_count,
    set_verified_integer_slider_count,
)


YUNMENG_NATIVE_AUTO_MAX_BATCH_CHALLENGES = 500
YUNMENG_NATIVE_AUTO_PROBE_CHALLENGES = 10
YUNMENG_NATIVE_AUTO_FINAL_BATCH_THRESHOLD = 20
YUNMENG_NATIVE_AUTO_YIELD_STABILITY_TOLERANCE = 0.10
YUNMENG_TOGGLE_STATE_MATCH_THRESHOLD = 95.0


class YunmengAutoTerminal(StrEnum):
    COMPLETED = "completed"
    ACTIVITY_CLOSING = "activity_closing"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class YunmengToggleAsset:
    action: str
    selected: str
    unselected: str


@dataclass(frozen=True)
class YunmengNativeAutoAssets:
    """Names and scene ids that must be backed by verified Yunmeng assets."""

    home_scene_id: int
    settings_scene_id: int
    terminal_scene_ids: tuple[int, ...]
    toggle_state_scene_id: int | None = None
    open_settings: str = "自动挑战"
    start_auto: str = "开启自动"
    count_region: str = "挑战次数"
    count_decrease: str = "挑战次数_减少"
    count_increase: str = "挑战次数_增加"
    count_slider_thumb: str = "挑战次数_滑块"
    count_minimum_marker: str | None = None
    count_slider_left_anchor: str | None = None
    count_slider_right_anchor: str | None = None

    def __post_init__(self) -> None:
        required = (
            self.home_scene_id,
            self.settings_scene_id,
            int(self.toggle_state_scene_id or self.settings_scene_id),
            *self.terminal_scene_ids,
        )
        if not self.terminal_scene_ids or any(int(value) <= 0 for value in required):
            raise ValueError("云梦原生自动挑战缺少已验证的 Runtime 场景资产")
        for title in (
            self.open_settings,
            self.start_auto,
            self.count_region,
            self.count_decrease,
            self.count_increase,
            self.count_slider_thumb,
        ):
            if not str(title).strip():
                raise ValueError("云梦原生自动挑战的命名 Shape 不得为空")
        if self.count_minimum_marker is not None and not str(self.count_minimum_marker).strip():
            raise ValueError("整数滑块最小值标记不得为空")
        if bool(self.count_slider_left_anchor) != bool(self.count_slider_right_anchor):
            raise ValueError("整数滑块左右锚点必须成对提供")


@dataclass(frozen=True)
class YunmengNativeAutoRequest:
    requested_challenges: int
    use_high_power_boost: bool = True
    use_score_boost: bool = True
    use_chase_sword: bool = True
    skip_battle: bool = True
    auto_refill_stamina: bool = False
    fast_auto: bool = True
    skip_animation: bool = True
    max_count_adjustments: int = 200

    def __post_init__(self) -> None:
        if int(self.requested_challenges) <= 0:
            raise ValueError("云梦自动挑战次数必须大于 0")
        if int(self.requested_challenges) > YUNMENG_NATIVE_AUTO_MAX_BATCH_CHALLENGES:
            raise ValueError(
                "云梦原生自动挑战只允许有界小批次："
                f"请求 {int(self.requested_challenges)} 次，"
                f"单批上限 {YUNMENG_NATIVE_AUTO_MAX_BATCH_CHALLENGES} 次；"
                "必须在每批结束后回读绝对钱包并重新规划"
            )
        if int(self.max_count_adjustments) <= 0:
            raise ValueError("云梦自动挑战次数调整预算必须大于 0")
        if bool(self.auto_refill_stamina):
            raise ValueError("云梦分批挑战必须关闭论剑令自动补充体力")
        if not bool(self.skip_battle):
            raise ValueError("云梦分批挑战必须开启默认跳过战斗")
        if not bool(self.fast_auto):
            raise ValueError("云梦分批挑战必须开启快速自动挑战")
        if not bool(self.skip_animation):
            raise ValueError("云梦分批挑战必须开启跳过动画")


@dataclass(frozen=True)
class YunmengNativeAutoSettings:
    requested_challenges: int
    use_high_power_boost: bool
    use_score_boost: bool
    use_chase_sword: bool
    skip_battle: bool
    auto_refill_stamina: bool
    fast_auto: bool
    skip_animation: bool


@dataclass(frozen=True)
class YunmengNativeAutoResult:
    terminal: YunmengAutoTerminal
    scene_id: int | None
    ocr_text: str
    settings: YunmengNativeAutoSettings


def _mapping_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump())
    raise RuntimeError(f"云梦试剑：无法读取兑换计划：{type(value).__name__}")


def _validated_closing_goods_budget(
    detail: Any,
    *,
    expected_activity_id: str,
    context: str,
) -> tuple[int, int]:
    """Validate that one activity detail still authorizes more challenges."""

    if detail is None:
        raise RuntimeError(f"云梦试剑：{context}活动快照丢失")
    if str(getattr(detail, "id", "")) != str(expected_activity_id):
        raise RuntimeError(f"云梦试剑：{context}活动实例发生切换")
    if str(getattr(detail, "activity_type", "")) != "yunmeng-trial":
        raise RuntimeError(f"云梦试剑：{context}活动类型不再是云梦试剑")
    if not bool(getattr(detail, "is_active", False)):
        raise RuntimeError(f"云梦试剑：{context}活动已不在有效期")
    if not bool(getattr(detail, "budget_ready", False)):
        reason = str(getattr(detail, "budget_block_reason", "") or "原因未知")
        raise RuntimeError(f"云梦试剑：{context}收尾道具预算 freshness 门禁失效：{reason}")

    plan = _mapping_value(getattr(detail, "exchange_plan", None))
    if not bool(plan.get("budget_ready")):
        raise RuntimeError(
            f"云梦试剑：{context}收尾道具预算 freshness 门禁失效："
            f"{plan.get('budget_block_reason') or '原因未知'}"
        )
    target_budgets = _mapping_value(plan.get("target_budgets"))
    closing_goods = _mapping_value(target_budgets.get("收尾道具"))
    target_total_tokens = int(closing_goods.get("target_total_tokens") or 0)
    target_remaining_tokens = int(closing_goods.get("target_remaining_tokens") or 0)
    if target_total_tokens <= 0 or target_remaining_tokens < 0:
        raise RuntimeError(f"云梦试剑：{context}收尾道具预算缺少有效的当期目标金额")
    return target_total_tokens, target_remaining_tokens


def plan_yunmeng_native_batch(
    *,
    required_new_currency: int,
    measured_currency_delta: int | None = None,
    measured_challenges: int | None = None,
    previous_currency_delta: int | None = None,
    previous_challenges: int | None = None,
    final_batch_threshold: int = YUNMENG_NATIVE_AUTO_FINAL_BATCH_THRESHOLD,
    yield_stability_tolerance: float = YUNMENG_NATIVE_AUTO_YIELD_STABILITY_TOLERANCE,
) -> YunmengNativeBatchPlan:
    """Plan one geometric batch; the caller must re-read absolute wallet.

    After the initial probe, run at most half of the currently estimated
    remaining attempts.  Only a small remainder with two sufficiently stable
    adjacent yield samples may be finished in one final batch.
    """

    return plan_feedback_batch(
        required_new_currency=required_new_currency,
        measured_currency_delta=measured_currency_delta,
        measured_challenges=measured_challenges,
        previous_currency_delta=previous_currency_delta,
        previous_challenges=previous_challenges,
        probe_challenges=YUNMENG_NATIVE_AUTO_PROBE_CHALLENGES,
        maximum_batch_challenges=YUNMENG_NATIVE_AUTO_MAX_BATCH_CHALLENGES,
        final_batch_threshold=final_batch_threshold,
        yield_stability_tolerance=yield_stability_tolerance,
    )


def _wallet_runtime_identity(snapshot: dict[str, Any]) -> tuple[int, str, int, int]:
    evidence = dict(snapshot.get("evidence") or {})
    identity = (
        int(snapshot.get("currency_type") or 0),
        str(snapshot.get("source") or ""),
        int(evidence.get("pid") or 0),
        int(evidence.get("process_start_ticks") or 0),
    )
    if identity[0] != 19 or identity[1] != "runtime_memory" or min(identity[2:]) <= 0:
        raise RuntimeError(f"云梦试剑：钱包 Runtime 身份不完整：{identity!r}")
    return identity


TOGGLES: dict[str, YunmengToggleAsset] = {
    "use_high_power_boost": YunmengToggleAsset(
        "高战力对手使用四倍属性增幅令",
        "高战力对手使用四倍属性增幅令_已选",
        "高战力对手使用四倍属性增幅令_未选",
    ),
    "use_score_boost": YunmengToggleAsset(
        "自动使用云梦·四倍积分令",
        "自动使用云梦·四倍积分令_已选",
        "自动使用云梦·四倍积分令_未选",
    ),
    "use_chase_sword": YunmengToggleAsset(
        "自动使用云梦·追影剑",
        "自动使用云梦·追影剑_已选",
        "自动使用云梦·追影剑_未选",
    ),
    "skip_battle": YunmengToggleAsset(
        "默认跳过战斗",
        "默认跳过战斗_已选",
        "默认跳过战斗_未选",
    ),
    "auto_refill_stamina": YunmengToggleAsset(
        "自动使用云梦·论剑令补充挑战体力",
        "自动使用云梦·论剑令补充挑战体力_已选",
        "自动使用云梦·论剑令补充挑战体力_未选",
    ),
    "fast_auto": YunmengToggleAsset(
        "开启快速自动挑战",
        "开启快速自动挑战_已选",
        "开启快速自动挑战_未选",
    ),
    "skip_animation": YunmengToggleAsset(
        "跳过动画",
        "跳过动画_已选",
        "跳过动画_未选",
    ),
}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def classify_yunmeng_auto_terminal(text: str) -> YunmengAutoTerminal:
    value = _compact(text)
    if any(
        phrase in value
        for phrase in (
            "已完成预设的自动挑战次数",
            "自动挑战已完成",
            "已完成自动挑战",
        )
    ):
        return YunmengAutoTerminal.COMPLETED
    if any(
        phrase in value
        for phrase in (
            "活动结束前30分钟停止使用",
            "活动结束前30分钟无法使用",
            "活动即将结束，无法使用自动挑战",
        )
    ):
        return YunmengAutoTerminal.ACTIVITY_CLOSING
    if any(
        phrase in value
        for phrase in (
            "挑战体力不足",
            "论剑令不足",
            "挑战次数不足",
            "可用挑战资源不足",
        )
    ):
        return YunmengAutoTerminal.RESOURCE_EXHAUSTED
    return YunmengAutoTerminal.UNKNOWN


def _observe(runtime: Any, scene_ids: tuple[int, ...], anchors: tuple[str, ...]) -> tuple[int, str]:
    scene_id, _score, frame = runtime.current_scene(list(scene_ids), update=True)
    text = runtime.ocr_text(frame)
    if scene_id not in scene_ids or not all(
        _compact(anchor) in _compact(text) for anchor in anchors
    ):
        raise RuntimeError(
            "云梦 Runtime-GUI 对齐失败："
            f"scene={scene_id!r}, expected={scene_ids}, ocr={text!r}"
        )
    return int(scene_id), text


def _shape_matches(
    runtime: Any,
    scene_id: int,
    title: str,
    *,
    threshold: float = YUNMENG_TOGGLE_STATE_MATCH_THRESHOLD,
) -> bool:
    condition = runtime.shape_visible(scene_id, title, threshold=threshold)
    result = condition.check(runtime, runtime.cur_frame())
    return bool(result.matched)


def _toggle_reference_scene(assets: YunmengNativeAutoAssets) -> int:
    return int(assets.toggle_state_scene_id or assets.settings_scene_id)


def _set_required_toggle(
    runtime: Any,
    assets: YunmengNativeAutoAssets,
    asset: YunmengToggleAsset,
    desired: bool,
) -> Iterator[Any]:
    """Set a safety-critical toggle using one authoritative state sample.

    The verified #561 reference supplies the desired state for each safety
    toggle.  If that state is absent, click once and require it to appear.
    A failed verification is rolled back before aborting.
    """

    reference_title = asset.selected if desired else asset.unselected
    reference_scene = _toggle_reference_scene(assets)
    if _shape_matches(runtime, reference_scene, reference_title):
        return desired
    runtime.click_shape_center(assets.settings_scene_id, asset.action)
    yield from runtime.wait_action_settle(0.5)
    if _shape_matches(runtime, reference_scene, reference_title):
        return desired
    runtime.click_shape_center(assets.settings_scene_id, asset.action)
    yield from runtime.wait_action_settle(0.5)
    raise RuntimeError(f"云梦安全开关「{asset.action}」设置后无法验证，已回滚")


def _attempt_optional_boost(
    runtime: Any,
    assets: YunmengNativeAutoAssets,
    asset: YunmengToggleAsset,
) -> Iterator[Any]:
    """Best-effort enable without ever toggling a possibly-selected boost off."""

    reference_scene = _toggle_reference_scene(assets)
    if not _shape_matches(runtime, reference_scene, asset.unselected):
        return True
    runtime.click_shape_center(assets.settings_scene_id, asset.action)
    yield from runtime.wait_action_settle(0.5)
    return not _shape_matches(runtime, reference_scene, asset.unselected)


# Preserve Yunmeng's established private entry point while exposing the
# activity-neutral integer-slider contract for other native dialogs.
_set_count = set_verified_integer_slider_count


def run_yunmeng_native_auto(
    runtime: Any,
    assets: YunmengNativeAutoAssets,
    request: YunmengNativeAutoRequest,
    *,
    locked_settings: YunmengNativeAutoSettings | None = None,
    terminal_polls: int = 300,
    poll_seconds: float = 1.0,
) -> Iterator[Any]:
    """Configure and start Yunmeng's native auto challenge from its home page."""

    # The calligraphic first two characters are frequently obscured by the
    # character model.  The stable suffix is sufficient when Runtime scene
    # identity independently agrees with the expected Yunmeng home scene.
    _observe(runtime, (assets.home_scene_id,), ("试剑",))
    runtime.click_shape_center(assets.home_scene_id, assets.open_settings)
    yield from runtime.wait_action_settle(0.5)
    _observe(runtime, (assets.settings_scene_id,), ("自动挑战设置", "开启自动"))

    desired = {
        "use_high_power_boost": request.use_high_power_boost,
        "use_score_boost": request.use_score_boost,
        "use_chase_sword": request.use_chase_sword,
        "skip_battle": request.skip_battle,
        "auto_refill_stamina": request.auto_refill_stamina,
        "fast_auto": request.fast_auto,
        "skip_animation": request.skip_animation,
    }
    best_effort_enable = {
        "use_high_power_boost",
        "use_score_boost",
        "use_chase_sword",
    }
    if locked_settings is None:
        actual_toggles: dict[str, bool] = {}
        for name, value in desired.items():
            if name in best_effort_enable:
                actual_toggles[name] = yield from _attempt_optional_boost(
                    runtime,
                    assets,
                    TOGGLES[name],
                )
            else:
                actual_toggles[name] = yield from _set_required_toggle(
                    runtime,
                    assets,
                    TOGGLES[name],
                    value,
                )
    else:
        if (
            locked_settings.auto_refill_stamina
            or not locked_settings.skip_battle
            or not locked_settings.fast_auto
            or not locked_settings.skip_animation
        ):
            raise RuntimeError("云梦自动挑战拒绝复用不安全的锁定设置")
        # Native settings persist between batches.  Consumable boosts are
        # read-only after the probe: depletion may turn them off, but the
        # driver must never re-enable them and change yield halfway through.
        # Safety-critical switches are still uniquely verified every batch.
        actual_toggles = {}
        for name, value in desired.items():
            if name in best_effort_enable:
                actual_toggles[name] = not _shape_matches(
                    runtime,
                    _toggle_reference_scene(assets),
                    TOGGLES[name].unselected,
                )
                continue
            actual_toggles[name] = yield from _set_required_toggle(
                runtime,
                assets,
                TOGGLES[name],
                value,
            )
    yield from _set_count(
        runtime,
        assets,
        request.requested_challenges,
        max_adjustments=request.max_count_adjustments,
    )
    settings = YunmengNativeAutoSettings(
        requested_challenges=_read_count(runtime, assets),
        **actual_toggles,
    )
    runtime.click_shape_center(assets.settings_scene_id, assets.start_auto)

    last_scene: int | None = None
    last_text = ""
    for _poll in range(max(1, int(terminal_polls))):
        yield from runtime.wait_action_settle(poll_seconds)
        scene_id, _score, frame = runtime.current_scene(
            list(assets.terminal_scene_ids),
            update=True,
        )
        last_scene = int(scene_id) if scene_id in assets.terminal_scene_ids else None
        last_text = runtime.ocr_text(frame)
        terminal = classify_yunmeng_auto_terminal(last_text)
        if last_scene is not None and terminal is not YunmengAutoTerminal.UNKNOWN:
            return YunmengNativeAutoResult(terminal, last_scene, last_text, settings)
    return YunmengNativeAutoResult(
        YunmengAutoTerminal.UNKNOWN,
        last_scene,
        last_text,
        settings,
    )


def execute_yunmeng_native_auto_job(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: Any,
) -> Iterator[Any]:
    """Reach stage 9 through fresh-wallet, bounded-batch reconciliation.

    Every invocation starts with a fresh absolute wallet read.  Its first batch
    is a small probe; later batches may use only evidence produced earlier in
    this same invocation.  Each batch is capped, settled back to the Yunmeng
    home page, persisted, and re-planned from a new absolute wallet snapshot.
    """

    from datetime import date, datetime

    from sqlmodel import Session, select

    from backend.db import engine
    from backend.models import FanxiuExchangeActivity
    from backend.core.fanxiu.activity.exchange_event import (
        list_exchange_activity_snapshot,
    )
    from backend.core.fanxiu.data_annotation.schedule_navigation import (
        select_schedule_activity,
    )

    with Session(engine) as session:
        activity = session.exec(
            select(FanxiuExchangeActivity).where(
                FanxiuExchangeActivity.activity_type == "yunmeng-trial",
                FanxiuExchangeActivity.start_date <= date.today().isoformat(),
                FanxiuExchangeActivity.end_date >= date.today().isoformat(),
            )
        ).first()
        if activity is None:
            raise RuntimeError("云梦试剑：今天尚无通用活动实例")
        activity_id = str(activity.id)
        detail = list_exchange_activity_snapshot(
            session,
            activity_type="yunmeng-trial",
            activity_id=activity_id,
        ).selected_activity
        target_total_tokens, target_remaining_tokens = _validated_closing_goods_budget(
            detail,
            expected_activity_id=activity_id,
            context="执行前",
        )

    from backend.core.fanxiu.activity.exchange_planning import (
        calculate_exchange_currency_gap,
    )
    from backend.core.fanxiu.instrumentation.wallet import (
        read_wallet_currency_snapshot,
    )

    wallet_before = read_wallet_currency_snapshot(19, allow_discovery=True)
    wallet_identity = _wallet_runtime_identity(wallet_before)
    fresh_gap = calculate_exchange_currency_gap(
        target_total_tokens=target_total_tokens,
        target_remaining_tokens=target_remaining_tokens,
        current_currency=int(wallet_before["exchange_currency"]),
        cumulative_currency=int(wallet_before["cumulative_currency"]),
    )
    if fresh_gap.required_new_currency == 0:
        return {
            "result": "success",
            "message": "云梦试剑收尾道具绝对钱包预算已经满足，无需重复挑战",
            "activity_id": activity_id,
            "current_currency": wallet_before["exchange_currency"],
            "cumulative_currency": wallet_before["cumulative_currency"],
        }

    if stop_event.is_set():
        raise InterruptedError()

    runtime = runner._fanxiu_runtime(ctx)
    yield from runtime.goto_view(66)
    yield from select_schedule_activity(
        runtime,
        r"云梦试剑",
        enter=True,
        require_runtime_alignment=True,
        now=datetime.now().astimezone(),
    )
    yield from runtime.wait_view(558, timeout=30.0, label="云梦试剑：等待活动主页 #558")
    assets = YunmengNativeAutoAssets(
        home_scene_id=558,
        settings_scene_id=560,
        terminal_scene_ids=(562,),
        toggle_state_scene_id=561,
    )
    explicit_limit = max(0, int(payload.get("requested_challenges") or 0))
    max_batches = max(1, int(payload.get("max_batches") or 1000))
    measured_delta: int | None = None
    measured_challenges: int | None = None
    previous_delta: int | None = None
    previous_challenges: int | None = None
    total_challenges = 0
    batches: list[dict[str, int | str]] = []
    wallet_after = wallet_before
    remaining_gap = fresh_gap
    locked_settings: YunmengNativeAutoSettings | None = None
    incomplete_reason = ""

    for _batch_index in range(max_batches):
        if remaining_gap.required_new_currency == 0:
            break
        if stop_event.is_set():
            yield from runtime.goto_view(34)
            raise InterruptedError()

        batch_plan = plan_yunmeng_native_batch(
            required_new_currency=remaining_gap.required_new_currency,
            measured_currency_delta=measured_delta,
            measured_challenges=measured_challenges,
            previous_currency_delta=previous_delta,
            previous_challenges=previous_challenges,
        )
        requested = batch_plan.requested_challenges
        if explicit_limit:
            limit_remaining = explicit_limit - total_challenges
            if limit_remaining <= 0:
                incomplete_reason = "已达到本次显式挑战次数上限"
                break
            requested = min(requested, limit_remaining)
        native_request = YunmengNativeAutoRequest(
            requested_challenges=requested,
            max_count_adjustments=min(100, max(20, requested // 5)),
            use_high_power_boost=bool(payload.get("use_high_power_boost", True)),
            use_score_boost=bool(payload.get("use_score_boost", True)),
            use_chase_sword=bool(payload.get("use_chase_sword", True)),
            auto_refill_stamina=False,
        )
        result = yield from run_yunmeng_native_auto(
            runtime,
            assets,
            native_request,
            locked_settings=locked_settings,
            terminal_polls=max(1, int(payload.get("terminal_polls") or 1800)),
        )
        if result.terminal is not YunmengAutoTerminal.COMPLETED:
            if result.terminal is not YunmengAutoTerminal.UNKNOWN:
                yield from runtime.goto_view(34)
            raise RuntimeError(
                "云梦试剑自动挑战未正常完成："
                f"{result.terminal.value}；"
                + (
                    "原生批次状态未知，保留现场，禁止盲目导航"
                    if result.terminal is YunmengAutoTerminal.UNKNOWN
                    else "已按验证终态安全归位"
                )
            )
        if locked_settings is None:
            locked_settings = result.settings
        runtime.click_shape_center(562, "确认")
        yield from runtime.wait_action_settle(0.5)
        yield from runtime.wait_view(
            563,
            timeout=20.0,
            label="云梦试剑：等待挑战结算 #563",
        )
        runtime.click_shape_center(563, "点击屏幕关闭")
        yield from runtime.wait_action_settle(0.5)
        yield from runtime.wait_view(
            558,
            timeout=20.0,
            label="云梦试剑：结算后回到主页 #558",
        )

        # From here until the next batch starts, #558 has been explicitly
        # verified above.  Only errors inside this safe-home reconciliation
        # boundary are allowed to navigate back to #34.
        try:
            wallet_after = read_wallet_currency_snapshot(19, allow_discovery=False)
            if _wallet_runtime_identity(wallet_after) != wallet_identity:
                raise RuntimeError("云梦试剑：批次前后游戏进程或钱包身份变化，拒绝对账")
            current_delta = int(wallet_after["exchange_currency"]) - int(
                wallet_before["exchange_currency"]
            )
            cumulative_delta = int(wallet_after["cumulative_currency"]) - int(
                wallet_before["cumulative_currency"]
            )
            if current_delta <= 0 or cumulative_delta <= 0:
                raise RuntimeError("云梦试剑：本批结算后绝对钱包没有正向增长，停止后续挑战")
            if current_delta != cumulative_delta:
                raise RuntimeError(
                    "云梦试剑：本批余额增量与累计增量不一致，疑似同期发生消费；"
                    "拒绝继续自动规划"
                )
        except Exception:
            yield from runtime.goto_view(34)
            raise

        from backend.core.fanxiu.activity.standard_observation import (
            store_runtime_currency_fact,
        )
        from backend.core.fanxiu.activity.yunmeng_exchange import (
            collect_and_store_yunmeng_exchange_activity,
        )

        batch_record: dict[str, int | str] = {
            "requested_challenges": requested,
            "currency_before": int(wallet_before["exchange_currency"]),
            "currency_after": int(wallet_after["exchange_currency"]),
            "cumulative_before": int(wallet_before["cumulative_currency"]),
            "cumulative_after": int(wallet_after["cumulative_currency"]),
            "currency_delta": current_delta,
            "captured_at": str(wallet_after.get("captured_at") or ""),
        }
        try:
            with Session(engine) as session:
                store_runtime_currency_fact(session, wallet_after)
                session.commit()
                # The collector's return value is the freshly materialized
                # authority for this exact activity occurrence.  Do not issue
                # a second, potentially divergent snapshot lookup.
                latest_detail = collect_and_store_yunmeng_exchange_activity(
                    session,
                    activity_id=activity_id,
                    collect_runtime_shop=False,
                )
                persisted = session.get(FanxiuExchangeActivity, activity_id)
                if persisted is None:
                    raise RuntimeError("云梦试剑：批后通用活动实例丢失")
                evidence = dict(persisted.evidence or {})
                history = list(evidence.get("yunmeng_native_auto_batches") or [])
                history.append(batch_record)
                evidence["yunmeng_native_auto_batches"] = history[-100:]
                persisted.evidence = evidence
                session.add(persisted)
                session.commit()

                try:
                    target_total_tokens, target_remaining_tokens = (
                        _validated_closing_goods_budget(
                            latest_detail,
                            expected_activity_id=activity_id,
                            context="批后",
                        )
                    )
                except RuntimeError as exc:
                    incomplete_reason = str(exc).removeprefix("云梦试剑：")
        except Exception:
            yield from runtime.goto_view(34)
            raise

        batches.append(batch_record)
        total_challenges += requested
        previous_delta = measured_delta
        previous_challenges = measured_challenges
        measured_delta = current_delta
        measured_challenges = requested
        wallet_before = wallet_after
        if incomplete_reason:
            break
        remaining_gap = calculate_exchange_currency_gap(
            target_total_tokens=target_total_tokens,
            target_remaining_tokens=target_remaining_tokens,
            current_currency=int(wallet_after["exchange_currency"]),
            cumulative_currency=int(wallet_after["cumulative_currency"]),
        )

    yield from runtime.goto_view(34)
    reached = remaining_gap.required_new_currency == 0
    if not reached and not incomplete_reason:
        incomplete_reason = f"已达到本次批次数上限 {max_batches}"
    return {
        "result": "success" if reached else "incomplete",
        "message": (
            f"云梦试剑收尾道具已满足，共完成 {total_challenges} 次有界挑战并归位 #34"
            if reached
            else (
                f"云梦试剑已完成受限的 {total_challenges} 次挑战并归位 #34，"
                f"目标尚未满足：{incomplete_reason}"
            )
        ),
        "activity_id": activity_id,
        "target_reached": reached,
        "requested_challenges": total_challenges,
        "batch_count": len(batches),
        "batches": batches,
        "currency_after": int(wallet_after["exchange_currency"]),
        "cumulative_after": int(wallet_after["cumulative_currency"]),
        "required_new_currency": int(remaining_gap.required_new_currency),
        "incomplete_reason": incomplete_reason,
    }
