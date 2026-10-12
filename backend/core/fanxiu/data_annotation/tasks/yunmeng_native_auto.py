from __future__ import annotations

"""Fail-closed GUI driver for Yunmeng Trial's native auto-challenge dialog.

The caller is responsible for navigating to the verified Yunmeng home scene.
This module only consumes named scene/shape assets and remains an ordinary
behavior-tree generator; it does not introduce another execution channel.
"""

from dataclasses import dataclass, replace
from enum import StrEnum
import re
from typing import Any, Iterator

from backend.core.fanxiu.data_annotation.tasks.bounded_batch_planning import (
    FeedbackBatchPlan as YunmengNativeBatchPlan,
    plan_feedback_batch,
)
from backend.core.fanxiu.runtime_gui.integer_count_control import (
    read_positive_integer_count,
    set_verified_integer_slider_count,
)


YUNMENG_NATIVE_AUTO_MAX_BATCH_CHALLENGES = 500
YUNMENG_NATIVE_COUNT_FINE_THRESHOLD = 10
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
    count_decrease_large: str | None = None
    count_increase_large: str | None = None
    count_large_step: int | None = None
    count_slider_thumb: str = "挑战次数_滑块"
    count_slider_track: str | None = None
    count_minimum_marker: str | None = None
    # #560 实拍（900x1600 参考帧）证明的真实轨道：y≈1273、x≈240..700。
    # 游标中心样本 值2490↔x457、值4856↔x673，两点线性外推得游标中心行程
    # x≈230..673。左/右锚点即该行程的两个端点；不再把「−按钮」或滑块模板
    # 的中心当轨道端点（旧锚点与真实像素不符，精调像素拖拽会打空）。
    count_slider_left_anchor: str | None = "挑战次数_滑轨左端"
    count_slider_right_anchor: str | None = "挑战次数_滑轨右端"
    count_slider_left_center_offset: float = 0.0
    count_slider_right_center_offset: float = 0.0

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


def _count_assets_with_proven_bounds(
    context: Any,
    assets: YunmengNativeAutoAssets,
) -> YunmengNativeAutoAssets:
    """Calibrate #560's verified cursor travel endpoints for the integer slider.

    左右锚点 Shape 直接落在实拍游标中心行程的端点上（左≈x230、右≈x673，见
    #560 证据）。``integer_count_control`` 读取右锚点时会先减去半个滑块宽度，
    因此这里按实测滑块宽度补回，使闭环拿到真实可拖拽区间；左锚点无需偏移。
    """

    if not assets.count_slider_left_anchor or not assets.count_slider_right_anchor:
        raise RuntimeError("云梦挑战次数缺少已证明的滑轨左右边界")
    if not assets.count_slider_thumb:
        raise RuntimeError("云梦挑战次数滑轨缺少滑块 Shape")
    thumb = context.shape_box(assets.settings_scene_id, assets.count_slider_thumb)
    thumb_width = float(thumb.get("w") or 0.0)
    if thumb_width <= 0:
        raise RuntimeError("云梦挑战次数滑轨滑块几何无效")
    return replace(
        assets,
        count_slider_left_center_offset=0.0,
        count_slider_right_center_offset=thumb_width * 0.5,
    )


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
    # 只花当日可用次数：把本批请求夹到面板原生上限，读不到可用次数就当作已用完。
    # 规划得到的目标次数可以更大，调用方无需先开面板读次数。
    maximize_available: bool = False
    # 统一规划的整轮次数；以真实面板最大值准入，禁止夹成不足100的小批。
    whole_milestone_batch: bool = False

    def __post_init__(self) -> None:
        if int(self.requested_challenges) <= 0:
            raise ValueError("云梦自动挑战次数必须大于 0")
        if self.whole_milestone_batch and (self.requested_challenges < 100 or self.maximize_available):
            raise ValueError("云梦主动补档必须完整执行至少100次，禁止裁剪次数")
        if not self.whole_milestone_batch and int(self.requested_challenges) > YUNMENG_NATIVE_AUTO_MAX_BATCH_CHALLENGES:
            raise ValueError(
                "云梦原生自动挑战只允许有界小批次："
                f"请求 {int(self.requested_challenges)} 次，"
                f"单批上限 {YUNMENG_NATIVE_AUTO_MAX_BATCH_CHALLENGES} 次；"
                "必须在每批结束后回读绝对钱包并重新规划"
            )
        if int(self.max_count_adjustments) <= 0:
            raise ValueError("云梦自动挑战次数调整预算必须大于 0")
        # 是否用论剑令补体力是可逆性最强的消耗决策：由调用方通过 payload
        # ``use_refill_items`` 显式授权后才会置真，这里只做类型上的收敛。
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


def validate_yunmeng_reused_settings(
    request: YunmengNativeAutoRequest,
    settings: YunmengNativeAutoSettings,
) -> None:
    """Validate a prior batch against this request before touching the GUI.

    The Task converts its payload into request fields once. The dialog driver
    neither reads an ambient payload nor inherits consumable authorization
    from a previous batch's persisted checkbox state.
    """
    if (
        (settings.auto_refill_stamina and not request.auto_refill_stamina)
        or not settings.skip_battle
        or not settings.fast_auto
        or not settings.skip_animation
    ):
        raise RuntimeError("云梦自动挑战拒绝复用不安全的锁定设置")


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


def _observe(context: Any, scene_ids: tuple[int, ...], anchors: tuple[str, ...]):
    _wait_scene_match = yield from context.wait_scene(list(scene_ids), wait=5.0, required=False)
    (scene_id, _score, frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    text = context.ocr_text(frame)
    if scene_id not in scene_ids or not all(
        _compact(anchor) in _compact(text) for anchor in anchors
    ):
        raise RuntimeError(
            "云梦 Runtime-GUI 对齐失败："
            f"scene={scene_id!r}, expected={scene_ids}, ocr={text!r}"
        )
    return int(scene_id), text


def _shape_matches(
    context: Any,
    scene_id: int,
    title: str,
    *,
    threshold: float = YUNMENG_TOGGLE_STATE_MATCH_THRESHOLD,
) -> bool:
    condition = context.shape_visible(scene_id, title, threshold=threshold)
    result = condition.check(context, context.cur_frame())
    return bool(result.matched)


def _toggle_reference_scene(assets: YunmengNativeAutoAssets) -> int:
    return int(assets.toggle_state_scene_id or assets.settings_scene_id)


def _set_required_toggle(
    context: Any,
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
    if _shape_matches(context, reference_scene, reference_title):
        return desired
    context.click_shape_center(assets.settings_scene_id, asset.action)
    yield from context.wait_action_settle(0.5)
    if _shape_matches(context, reference_scene, reference_title):
        return desired
    context.click_shape_center(assets.settings_scene_id, asset.action)
    yield from context.wait_action_settle(0.5)
    raise RuntimeError(f"云梦安全开关「{asset.action}」设置后无法验证，已回滚")


def _set_refill_toggle_by_panel(
    context: Any,
    assets: YunmengNativeAutoAssets,
    desired: bool,
) -> Iterator[Any]:
    """用面板原生上限验证「论剑令补体力」开关。

    #561 参考帧里这一行只有「未选」模板，没有「已选」模板，图像验证无法证明开启
    成功。该开关的语义可以被面板自身证明：开启后 ``UpdateMaxUse`` 会把背包里的
    云梦·论剑令计入 ``_MaxSliderValue``，上限随之抬升；关闭后回落。这里点一次、
    比较上限变化、必要时回滚一次，仍不一致就失败关闭。
    """

    from backend.core.fanxiu.instrumentation.yunmeng_trial import (
        read_yunmeng_auto_count_snapshot,
    )

    baseline = int(read_yunmeng_auto_count_snapshot().get("maximum") or 0)
    context.click_shape_center(assets.settings_scene_id, TOGGLES["auto_refill_stamina"].action)
    yield from context.wait_action_settle(0.6)
    after_first = int(read_yunmeng_auto_count_snapshot().get("maximum") or 0)
    state = after_first > baseline
    if state == desired:
        return desired
    context.click_shape_center(assets.settings_scene_id, TOGGLES["auto_refill_stamina"].action)
    yield from context.wait_action_settle(0.6)
    after_second = int(read_yunmeng_auto_count_snapshot().get("maximum") or 0)
    if (after_second > baseline) != desired:
        raise RuntimeError(
            "云梦论剑令补体力开关无法通过面板上限证明："
            f"baseline={baseline}, first={after_first}, second={after_second}"
        )
    return desired


def _attempt_optional_boost(
    context: Any,
    assets: YunmengNativeAutoAssets,
    asset: YunmengToggleAsset,
) -> Iterator[Any]:
    """Best-effort enable without ever toggling a possibly-selected boost off."""

    reference_scene = _toggle_reference_scene(assets)
    if not _shape_matches(context, reference_scene, asset.unselected):
        return True
    context.click_shape_center(assets.settings_scene_id, asset.action)
    yield from context.wait_action_settle(0.5)
    return not _shape_matches(context, reference_scene, asset.unselected)


# Preserve Yunmeng's established private entry point while exposing the
# activity-neutral integer-slider contract for other native dialogs.
_set_count = set_verified_integer_slider_count


def run_yunmeng_native_auto(
    context: Any,
    assets: YunmengNativeAutoAssets,
    request: YunmengNativeAutoRequest,
    *,
    locked_settings: YunmengNativeAutoSettings | None = None,
    terminal_polls: int = 300,
    poll_seconds: float = 1.0,
    before_start: Any = None,
) -> Iterator[Any]:
    """Configure and start Yunmeng's native auto challenge from its home page."""

    if locked_settings is not None:
        validate_yunmeng_reused_settings(request, locked_settings)

    # The calligraphic first two characters are frequently obscured by the
    # character model.  The stable suffix is sufficient when Runtime scene
    # identity independently agrees with the expected Yunmeng home scene.
    yield from _observe(context, (assets.home_scene_id,), ("试剑",))
    context.click_shape_center(assets.home_scene_id, assets.open_settings)
    yield from context.wait_action_settle(0.5)
    yield from _observe(context, (assets.settings_scene_id,), ("自动挑战设置", "开启自动"))

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
                    context,
                    assets,
                    TOGGLES[name],
                )
            elif name == "auto_refill_stamina" and value:
                actual_toggles[name] = yield from _set_refill_toggle_by_panel(
                    context,
                    assets,
                    True,
                )
            else:
                actual_toggles[name] = yield from _set_required_toggle(
                    context,
                    assets,
                    TOGGLES[name],
                    value,
                )
    else:
        # Native settings persist between batches.  Consumable boosts are
        # read-only after the probe: depletion may turn them off, but the
        # driver must never re-enable them and change yield halfway through.
        # Safety-critical switches are still uniquely verified every batch.
        actual_toggles = {}
        for name, value in desired.items():
            if name in best_effort_enable:
                actual_toggles[name] = not _shape_matches(
                    context,
                    _toggle_reference_scene(assets),
                    TOGGLES[name].unselected,
                )
                continue
            if name == "auto_refill_stamina" and value:
                actual_toggles[name] = yield from _set_refill_toggle_by_panel(
                    context,
                    assets,
                    True,
                )
                continue
            actual_toggles[name] = yield from _set_required_toggle(
                context,
                assets,
                TOGGLES[name],
                value,
            )
    from backend.core.fanxiu.instrumentation.yunmeng_trial import (
        read_yunmeng_auto_count_snapshot,
    )

    count_assets = _count_assets_with_proven_bounds(context, assets)
    live_count = read_yunmeng_auto_count_snapshot()
    native_maximum = int(live_count.get("maximum") or 0)
    requested_challenges = int(request.requested_challenges)
    if request.maximize_available:
        requested_challenges = min(requested_challenges, native_maximum)
        if requested_challenges < 1:
            # 面板已打开但今日可用次数为 0：这是正常业务边界，不是运行故障。
            return YunmengNativeAutoResult(
                terminal=YunmengAutoTerminal.RESOURCE_EXHAUSTED,
                scene_id=assets.settings_scene_id,
                ocr_text="云梦自动挑战面板可用次数为 0",
                settings=YunmengNativeAutoSettings(
                    requested_challenges=0,
                    use_high_power_boost=bool(actual_toggles.get("use_high_power_boost", False)),
                    use_score_boost=bool(actual_toggles.get("use_score_boost", False)),
                    use_chase_sword=bool(actual_toggles.get("use_chase_sword", False)),
                    skip_battle=bool(actual_toggles.get("skip_battle", False)),
                    auto_refill_stamina=bool(actual_toggles.get("auto_refill_stamina", False)),
                    fast_auto=bool(actual_toggles.get("fast_auto", False)),
                    skip_animation=bool(actual_toggles.get("skip_animation", False)),
                ),
            )
    if requested_challenges > native_maximum:
        raise RuntimeError(
            "云梦自动挑战请求超过当前面板原生上限："
            f"target={requested_challenges}, maximum={native_maximum}"
        )
    yield from _set_count(
        context,
        count_assets,
        requested_challenges,
        # The shared parameter is the residual error threshold for entering
        # exact +/- convergence, not a total click budget.
        max_adjustments=YUNMENG_NATIVE_COUNT_FINE_THRESHOLD,
        count_label="云梦自动挑战次数",
        maximum=native_maximum,
        runtime_count_reader=read_yunmeng_auto_count_snapshot,
    )
    final_count = read_yunmeng_auto_count_snapshot()
    runtime_count = int(final_count.get("current") or 0)
    final_maximum = int(final_count.get("maximum") or 0)
    gui_count = read_positive_integer_count(
        context,
        count_assets,
        count_label="云梦自动挑战次数",
        runtime_reader=read_yunmeng_auto_count_snapshot,
    )
    if (
        runtime_count != requested_challenges
        or gui_count != runtime_count
        or final_maximum != native_maximum
    ):
        raise RuntimeError(
            "云梦自动挑战次数 GUI 与 Runtime 未对齐："
            f"target={requested_challenges}, gui={gui_count}, "
            f"runtime={runtime_count}, maximum={final_maximum}, "
            f"initial_maximum={native_maximum}"
        )
    settings = YunmengNativeAutoSettings(
        requested_challenges=runtime_count,
        **actual_toggles,
    )
    if before_start is not None:
        before_start(settings)
    context.click_shape_center(assets.settings_scene_id, assets.start_auto)

    last_scene: int | None = None
    last_text = ""
    for _poll in range(max(1, int(terminal_polls))):
        yield from context.wait_action_settle(poll_seconds)
        _wait_scene_match = yield from context.wait_scene(list(assets.terminal_scene_ids), wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        last_scene = int(scene_id) if scene_id in assets.terminal_scene_ids else None
        last_text = context.ocr_text(frame)
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
    """Execute exact native rounds and reconcile fresh wallets after settlement.

    Formal gameplay ranking callers select milestone_strategy: first 100, then
    one next shop tier with the shared 100-run floor and final-day gate. Native
    panel capacity admits the entire round before starting; it never truncates
    a milestone round into a smaller resource-clearing batch.
    """

    from datetime import date, datetime, timedelta

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
        query = select(FanxiuExchangeActivity).where(
                FanxiuExchangeActivity.activity_type == "yunmeng-trial",
                FanxiuExchangeActivity.start_date <= date.today().isoformat(),
                FanxiuExchangeActivity.end_date >= date.today().isoformat(),
            )
        if payload.get("activity_id"):
            query = query.where(FanxiuExchangeActivity.id == str(payload["activity_id"]))
        candidates = list(session.exec(query).all())
        if len(candidates) != 1:
            raise RuntimeError(f"云梦试剑：当前活动实例未唯一确定：{len(candidates)}")
        activity = candidates[0]
        if payload.get("instance_key") and activity.instance_key != payload["instance_key"]:
            raise RuntimeError("云梦试剑：正式作业实例身份不一致，拒绝挑战")
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
    from backend.core.fanxiu.activity.yunmeng_exchange import (
        read_yunmeng_currency_snapshot,
    )
    from backend.core.fanxiu.activity.exchange_challenge_planning import (
        exchange_challenge_milestones, plan_exchange_challenge_batch,
    )
    from backend.core.fanxiu.instrumentation.yunmeng_trial import read_yunmeng_challenge_resources

    milestone_strategy = bool(payload.get("milestone_strategy"))
    if milestone_strategy and dict(activity.evidence or {}).get("yunmeng_pending_round"):
        raise RuntimeError("云梦存在未结整轮意图；先核验原 action_id 与原生效果，禁止重放")
    samples = []
    for row in list(dict(activity.evidence or {}).get("yunmeng_native_auto_batches") or []):
        if row.get("strategy") == "milestone" and int(row.get("requested_challenges") or 0) >= 100:
            samples.append({"requested_attempts": int(row["requested_challenges"]),
                            "completed_attempts": int(row["completed_challenges"]),
                            "currency_delta": int(row["currency_delta"]),
                            "elapsed_seconds": row.get("elapsed_seconds")})
    milestone_plan = None
    admission = None
    initial_reward_gate = payload.get("initial_reward_gate")
    if milestone_strategy and not callable(initial_reward_gate):
        raise ValueError("云梦正式逐档挑战缺少首次100次后领奖门禁")

    wallet_before = read_yunmeng_currency_snapshot(allow_discovery=True)
    wallet_identity = _wallet_runtime_identity(wallet_before)
    fresh_gap = calculate_exchange_currency_gap(
        target_total_tokens=target_total_tokens,
        target_remaining_tokens=target_remaining_tokens,
        current_currency=int(wallet_before["exchange_currency"]),
        cumulative_currency=int(wallet_before["cumulative_currency"]),
    )
    if fresh_gap.required_new_currency == 0 and not milestone_strategy:
        return {
            "result": "success",
            "message": "云梦试剑收尾道具绝对钱包预算已经满足，无需重复挑战",
            "activity_id": activity_id,
            "current_currency": wallet_before["exchange_currency"],
            "cumulative_currency": wallet_before["cumulative_currency"],
        }

    if stop_event.is_set():
        raise InterruptedError()

    context = runner._behavior_tree_context(ctx)
    yield from context.go_scene(66)
    yield from select_schedule_activity(
        context,
        r"云梦试剑",
        enter=True,
        require_runtime_alignment=True,
        now=datetime.now().astimezone(),
    )
    yield from context.wait_scene([558], wait=30.0, label="云梦试剑：等待活动主页 #558")
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
        if remaining_gap.required_new_currency == 0 and not milestone_strategy:
            break
        if stop_event.is_set():
            yield from context.go_scene(34)
            raise InterruptedError()

        if milestone_strategy:
            if samples:
                reward_gate = yield from initial_reward_gate()
                if not reward_gate.get("skipped"):
                    # 首次领奖可能直接补足后续档次。先消费新钱包，再决定下一轮。
                    wallet_before = read_yunmeng_currency_snapshot(allow_discovery=False)
                    if _wallet_runtime_identity(wallet_before) != wallet_identity:
                        raise RuntimeError("云梦首次领奖后钱包进程身份变化")
                    wallet_after = wallet_before
                    from backend.core.fanxiu.activity.standard_observation import store_runtime_currency_fact
                    from backend.core.fanxiu.activity.yunmeng_exchange import collect_and_store_yunmeng_exchange_activity
                    with Session(engine) as session:
                        store_runtime_currency_fact(session, wallet_before)
                        session.commit()
                        detail = collect_and_store_yunmeng_exchange_activity(
                            session, activity_id=activity_id, collect_runtime_shop=False)
                        session.commit()
            milestones = exchange_challenge_milestones([row.model_dump() for row in detail.shop_items])
            highest = milestones[-1]
            highest_gap = calculate_exchange_currency_gap(
                target_total_tokens=highest["target_total_tokens"],
                target_remaining_tokens=highest["target_remaining_tokens"],
                current_currency=int(wallet_before["exchange_currency"]),
                cumulative_currency=int(wallet_before["cumulative_currency"]),
            ).required_new_currency
            if highest_gap == 0:
                # 既有收益或免费领奖已满足目标时不再为测速消耗100次。
                milestone_plan = {"status": "completed", "reason": "all_milestones_funded", "count": 0}
                break
            resources = read_yunmeng_challenge_resources(
                allow_refill_items=bool(payload.get("use_refill_items", False)))
            if any((resources.get("evidence") or {}).get(key) !=
                   (wallet_before.get("evidence") or {}).get(key)
                   for key in ("pid", "process_start_ticks")):
                raise RuntimeError("云梦本轮资源与钱包进程身份不一致")
            milestone_plan = plan_exchange_challenge_batch(
                milestones=milestones,
                current_currency=int(wallet_before["exchange_currency"]),
                cumulative_currency=int(wallet_before["cumulative_currency"]), samples=samples,
                capacity=int(resources["capacity"]), now=datetime.now().astimezone(),
                activity_end_at=datetime.fromisoformat(detail.end_at),
                # 原生 StartAutoActivity 在玩法结束前30分钟拒绝启动；
                # 这是补币截止，与末日最高档解锁时间分开。
                funding_deadline=datetime.fromisoformat(detail.end_at) - timedelta(minutes=30),
            )
            if milestone_plan["status"] != "ready":
                incomplete_reason = str(milestone_plan["reason"])
                break
            requested = int(milestone_plan["count"])
            from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_scheduler_batch_admission
            # 首次100次预留5分钟；随后使用实际完整批次耗时，并留50%裕量。
            last = samples[-1] if samples else {}
            native_seconds = (float(last["elapsed_seconds"]) / int(last["completed_attempts"])
                              * requested * 1.5 if last.get("elapsed_seconds") else 300.0)
            admission = read_scheduler_batch_admission(
                task_id=str(payload["scheduler_task_id"]),
                batch_seconds=native_seconds + 180, release_seconds=120,
            )
            if not admission.get("admitted"):
                incomplete_reason = "higher_priority_trigger"
                break
            if datetime.now().astimezone() + timedelta(seconds=native_seconds + 180) >= (
                    datetime.fromisoformat(detail.end_at) - timedelta(minutes=30)):
                incomplete_reason = "whole_round_exceeds_funding_deadline"
                break
        else:
            batch_plan = plan_yunmeng_native_batch(
                required_new_currency=remaining_gap.required_new_currency,
                measured_currency_delta=measured_delta,
                measured_challenges=measured_challenges,
                previous_currency_delta=previous_delta,
                previous_challenges=previous_challenges,
            )
            requested = batch_plan.requested_challenges
        if explicit_limit and not milestone_strategy:
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
            # 论剑令补体力默认关闭；只有调用方显式授权 use_refill_items 才会开启，
            # 每个论剑令换 1 次挑战次数，属于不可逆消耗。
            auto_refill_stamina=(bool(payload.get("use_refill_items", False))
                                and (not milestone_strategy or resources["free_attempts"] < requested)),
            maximize_available=bool(payload.get("use_available_attempts", False)) and not milestone_strategy,
            whole_milestone_batch=milestone_strategy,
        )
        intent = None
        if locked_settings is not None and locked_settings.auto_refill_stamina and not native_request.auto_refill_stamina:
            # 自然恢复次数已足够时显式重新校验全部设置，关闭旧补给开关。
            locked_settings = None
        def before_start(settings):
            nonlocal intent
            from backend.core.fanxiu.activity.yunmeng_exchange import begin_yunmeng_challenge_round
            with Session(engine) as session:
                intent = begin_yunmeng_challenge_round(
                    session, activity_id=activity_id, instance_key=detail.instance_key,
                    requested=settings.requested_challenges,
                    wallet_before=wallet_before, plan=milestone_plan)
        result = yield from run_yunmeng_native_auto(
            context,
            assets,
            native_request,
            locked_settings=locked_settings,
            terminal_polls=max(1, int(payload.get("terminal_polls") or 1800)),
            before_start=before_start if milestone_strategy else None,
        )
        if result.terminal is not YunmengAutoTerminal.COMPLETED:
            if (
                result.terminal is YunmengAutoTerminal.RESOURCE_EXHAUSTED
                and int(result.settings.requested_challenges or 0) == 0
            ):
                # 面板已打开但今日可用次数为 0：正常业务边界，如实收尾而不是报错。
                yield from context.go_scene(34)
                incomplete_reason = "今日可用挑战次数已用完"
                break
            if result.terminal is not YunmengAutoTerminal.UNKNOWN:
                yield from context.go_scene(34)
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
        completion = None
        if milestone_strategy:
            from backend.core.fanxiu.instrumentation.yunmeng_trial import read_yunmeng_native_completion_snapshot
            completion = read_yunmeng_native_completion_snapshot()
            if (completion["completed_attempts"] != requested
                    or result.settings.requested_challenges != requested):
                raise RuntimeError(f"云梦整轮结算次数与规划不一致：{requested}/{completion}")
        context.click_shape_center(562, "确认")
        yield from context.wait_action_settle(0.5)
        yield from context.wait_scene(
            [563],
            wait=20.0,
            label="云梦试剑：等待挑战结算 #563",
        )
        context.click_shape_center(563, "点击屏幕关闭")
        yield from context.wait_action_settle(0.5)
        yield from context.wait_scene(
            [558],
            wait=20.0,
            label="云梦试剑：结算后回到主页 #558",
        )

        # From here until the next batch starts, #558 has been explicitly
        # verified above.  Only errors inside this safe-home reconciliation
        # boundary are allowed to navigate back to #34.
        try:
            wallet_after = read_yunmeng_currency_snapshot(allow_discovery=False)
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
            yield from context.go_scene(34)
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
        if milestone_strategy:
            batch_record.update(strategy="milestone", completed_challenges=completion["completed_attempts"],
                                phase=str(milestone_plan["phase"]),
                                target_goods_id=int((milestone_plan.get("target") or {}).get("goods_id") or 0))
        try:
            with Session(engine) as session:
                if milestone_strategy:
                    from backend.core.fanxiu.activity.yunmeng_exchange import settle_yunmeng_challenge_round
                    batch_record = settle_yunmeng_challenge_round(
                        session, activity_id=activity_id, action_id=intent["action_id"],
                        wallet_after=wallet_after, completion=completion)
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
                detail = latest_detail
                persisted = session.get(FanxiuExchangeActivity, activity_id)
                if persisted is None:
                    raise RuntimeError("云梦试剑：批后通用活动实例丢失")
                evidence = dict(persisted.evidence or {})
                if not milestone_strategy:
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
            yield from context.go_scene(34)
            raise

        batches.append(batch_record)
        if milestone_strategy:
            samples.append({"requested_attempts": requested, "completed_attempts": requested,
                            "currency_delta": current_delta,
                            "elapsed_seconds": batch_record.get("elapsed_seconds")})
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

    yield from context.go_scene(34)
    reached = (milestone_plan is not None and milestone_plan["status"] == "completed"
               if milestone_strategy else remaining_gap.required_new_currency == 0)
    if not reached and not incomplete_reason:
        incomplete_reason = f"已达到本次批次数上限 {max_batches}"
    return {
        "result": "success" if reached else "incomplete",
        "message": (
            f"云梦试剑有限宝阁档次已满足，共完成 {total_challenges} 次挑战并归位 #34"
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
        "required_new_currency": (int(milestone_plan.get("gap") or 0)
                                  if milestone_strategy and milestone_plan is not None
                                  else int(remaining_gap.required_new_currency)),
        "incomplete_reason": incomplete_reason,
        "milestone_plan": milestone_plan,
        "yield_reason": "higher_priority_trigger" if incomplete_reason == "higher_priority_trigger" else "",
        "retry_at": admission.get("retry_at") if admission and not admission.get("admitted") else None,
    }
