from __future__ import annotations

"""Fail-closed GUI driver for Beast Abyss' native auto-explore dialog.

The driver is deliberately an ordinary behavior-tree generator.  It only
clicks named asset shapes and treats OCR as corroborating evidence for a
Runtime-recognized scene; it does not provide a second command channel.
"""

from dataclasses import dataclass
from enum import StrEnum
import logging
import re
import time
from types import SimpleNamespace
from typing import Any, Iterator

from backend.core.fanxiu.activity.beast_abyss_challenge_planning import (
    BEAST_ABYSS_MEASUREMENT_EXPLORES,
    BeastAbyssAutoSettings,
    validate_beast_abyss_auto_settings,
)
from backend.core.fanxiu.runtime_gui.integer_count_control import (
    set_verified_integer_slider_count,
)
from backend.core.fanxiu.data_annotation.tasks.beast_abyss_task_rewards import (
    claim_beast_abyss_task_rewards,
)


_LOGGER = logging.getLogger(__name__)


class BeastAbyssAutoTerminal(StrEnum):
    COMPLETED = "completed"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    MONSTER_BLOCKED = "monster_blocked"
    KILLED = "killed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BeastAbyssToggleAsset:
    alias: str
    display_text: str
    action: str


@dataclass(frozen=True)
class BeastAbyssNativeAutoAssets:
    """Asset names proven by screenshots; inner scene ids must never be guessed."""

    explore_scene_id: int
    help_view_scene_id: int
    terminal_scene_ids: tuple[int, ...]
    completed_notice_scene_id: int = 662
    completed_notice_confirm: str = "确定"
    running_scene_id: int = 876
    home_scene_id: int = 535
    enter_activity: str = "进入活动"
    open_auto: str = "自动探查"
    open_quick: str = "快捷处理"
    start_auto: str = "开启自动"
    count_region: str = "自动探查次数"
    count_decrease: str = "自动探查次数_减少"
    count_increase: str = "自动探查次数_增加"
    count_slider_thumb: str = "自动探查次数_滑块游标"
    count_slider_left_anchor: str = "自动探查次数_滑轨左端"
    count_slider_right_anchor: str = "自动探查次数_滑轨右端"
    count_slider_left_center_offset: float = 8.75
    count_slider_right_center_offset: float = 0.5
    cutscene_scene_id: int = 185
    skip_confirm_scene_id: int = 654
    npc_entry_scene_id: int = 655
    region_map_scene_id: int = 656
    skip_cutscene: str = "跳过"
    confirm_skip: str = "确认跳过"
    enter_beast_abyss: str = "进入兽渊"
    enter_outer_region: str = "兽渊外围"

    def __post_init__(self) -> None:
        required = (self.home_scene_id, self.explore_scene_id, self.help_view_scene_id)
        if any(int(value) <= 0 for value in required):
            raise ValueError("兽渊原生自动探查缺少已验证的入口/设置页场景资产")
        if any(int(value) <= 0 for value in self.terminal_scene_ids):
            raise ValueError("兽渊原生自动探查包含无效的终态场景资产")


@dataclass(frozen=True)
class BeastAbyssNativeAutoRequest:
    auto_use_explore_items: bool
    measurement: bool = True
    requested_explores: int = BEAST_ABYSS_MEASUREMENT_EXPLORES
    maximum_explores: int | None = None
    fairy_events: bool = False
    beast_events: bool = True
    player_events: bool = True
    stop_when_killed: bool = False
    fast_auto: bool = True
    skip_animation: bool = True
    use_find_demon_talisman: bool = False

    def __post_init__(self) -> None:
        if self.measurement and self.requested_explores != BEAST_ABYSS_MEASUREMENT_EXPLORES:
            raise ValueError("兽渊测速批次必须固定为100次")
        if self.maximum_explores is not None and self.maximum_explores < self.requested_explores:
            raise ValueError("兽渊自动探查目标超过当前资源上限")


@dataclass(frozen=True)
class BeastAbyssNativeAutoOptions:
    fairy_events: bool
    beast_events: bool
    player_events: bool
    auto_use_explore_items: bool
    stop_when_killed: bool
    fast_auto: bool
    skip_animation: bool
    use_find_demon_talisman: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "fairy_events": self.fairy_events,
            "beast_events": self.beast_events,
            "player_events": self.player_events,
            "auto_use_explore_items": self.auto_use_explore_items,
            "stop_when_killed": self.stop_when_killed,
            "fast_auto": self.fast_auto,
            "skip_animation": self.skip_animation,
            "use_find_demon_talisman": self.use_find_demon_talisman,
        }


BEAST_ABYSS_PRODUCTION_OPTIONS = BeastAbyssNativeAutoOptions(
    fairy_events=False,
    beast_events=True,
    player_events=True,
    auto_use_explore_items=True,
    stop_when_killed=False,
    fast_auto=True,
    skip_animation=True,
    use_find_demon_talisman=False,
)

BEAST_ABYSS_AUTO_RECOVERY_CLEAR_OPTIONS = BeastAbyssNativeAutoOptions(
    fairy_events=False,
    beast_events=True,
    player_events=True,
    auto_use_explore_items=False,
    stop_when_killed=False,
    fast_auto=True,
    skip_animation=True,
    use_find_demon_talisman=False,
)

DEFAULT_BEAST_ABYSS_NATIVE_AUTO_ASSETS = BeastAbyssNativeAutoAssets(
    explore_scene_id=657,
    help_view_scene_id=658,
    terminal_scene_ids=(382,),
)


@dataclass(frozen=True)
class BeastAbyssNativeAutoResult:
    terminal: BeastAbyssAutoTerminal
    scene_id: int | None
    ocr_text: str
    settings: BeastAbyssAutoSettings
    terminal_evidence: "BeastAbyssTerminalEvidence | None" = None


@dataclass(frozen=True)
class BeastAbyssTerminalEvidence:
    """Values shown by the terminal itself, without reinterpreting merit as currency."""

    terminal_total_score: int | None
    terminal_total_merit: int | None
    terminal_observed_explore_index: int | None


def _terminal_number(text: str, label: str) -> int | None:
    match = re.search(rf"{label}\s*[：:]?\s*([0-9][0-9,]*)", _compact(text))
    return int(match.group(1).replace(",", "")) if match else None


def parse_beast_abyss_terminal_evidence(text: str) -> BeastAbyssTerminalEvidence:
    """Parse only the two labels proven on #382; neither value is 兽元."""

    compact = _compact(text)
    observed_indices = [int(value) for value in re.findall(r"第(\d+)次探查", compact)]
    return BeastAbyssTerminalEvidence(
        terminal_total_score=_terminal_number(text, "总共获得积分"),
        terminal_total_merit=_terminal_number(text, "总共获得功勋"),
        terminal_observed_explore_index=max(observed_indices, default=None),
    )


TOGGLES: dict[str, BeastAbyssToggleAsset] = {
    "fairy_events": BeastAbyssToggleAsset("仙缘事件", "触发仙缘事件，跳过对话直接获得奖励", "仙侣事件"),
    "beast_events": BeastAbyssToggleAsset("妖兽事件", "发现妖兽事件，自动挑战并跳过战斗", "妖兽事件"),
    "player_events": BeastAbyssToggleAsset("玩家事件", "发现玩家事件，自动挑战并跳过战斗", "玩家事件"),
    "auto_use_explore_items": BeastAbyssToggleAsset("探查符", "体力不足的时候使用探查符", "自动使用探查符"),
    "stop_when_killed": BeastAbyssToggleAsset("击杀停止", "被其他玩家击杀停止自动取消", "被击杀停止"),
    "fast_auto": BeastAbyssToggleAsset("快速探查", "开启快速自动探查", "快速自动"),
    "skip_animation": BeastAbyssToggleAsset("跳过动画", "跳过动画", "跳过动画"),
    "use_find_demon_talisman": BeastAbyssToggleAsset("寻妖符", "使用寻妖符", "寻妖符"),
}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def classify_beast_abyss_auto_terminal(text: str) -> BeastAbyssAutoTerminal:
    value = _compact(text)
    if "探查体力和探查符不足" in value or "探查体力不足" in value:
        return BeastAbyssAutoTerminal.RESOURCE_EXHAUSTED
    if "有三个妖兽事件未完成击杀" in value:
        return BeastAbyssAutoTerminal.MONSTER_BLOCKED
    if "被其他玩家击杀" in value:
        return BeastAbyssAutoTerminal.KILLED
    if "已完成预设的自动探查次数" in value or (
        "探查结束" in value and "点击屏幕关闭" in value
    ):
        return BeastAbyssAutoTerminal.COMPLETED
    return BeastAbyssAutoTerminal.UNKNOWN


def _observe(context: Any, scene_ids: tuple[int, ...], anchors: tuple[str, ...]):
    _wait_scene_match = yield from context.wait_scene(list(scene_ids), wait=5.0, required=False)
    (scene_id, _score, frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    # The scene has already been identified. Re-entering generic OCR would
    # classify the whole scene graph again and may select a generic overlay.
    anchor_shapes = ([anchor for anchor in anchors
                      if context.view(scene_id).get_shape(anchor) is not None]
                     if scene_id in scene_ids else [])
    text = (context.ocr_text_in_shapes(scene_id, anchor_shapes, frame_data_url=frame)
            if anchor_shapes else "")
    if scene_id not in scene_ids or not any(_compact(anchor) in _compact(text) for anchor in anchors):
        raise RuntimeError(
            f"兽渊 Runtime-GUI 对齐失败：scene={scene_id!r}, expected={scene_ids}, ocr={text!r}"
        )
    return int(scene_id), text


def _shape_matches(context: Any, scene_id: int, title: str, *, frame: Any | None = None) -> bool:
    view_factory = getattr(context, "view", None)
    if callable(view_factory):
        view = view_factory(scene_id)
        get_shape = getattr(view, "get_shape", None)
        if callable(get_shape) and get_shape(title) is None:
            return False
    condition = context.shape_visible(scene_id, title)
    if frame is None:
        frame = context.cur_frame()
    result = condition.check(context, frame)
    return bool(result.matched)


def _read_runtime_options() -> dict[str, bool]:
    from backend.core.fanxiu.instrumentation.beast_abyss_runtime import (
        read_beast_abyss_auto_options_snapshot,
    )

    started = time.perf_counter()
    try:
        snapshot = read_beast_abyss_auto_options_snapshot()
    finally:
        _LOGGER.info("beast-auto phase=options_read elapsed=%.3fs", time.perf_counter() - started)
    raw = dict(snapshot.get("options") or {})
    expected = set(TOGGLES)
    if set(raw) != expected or any(type(raw[name]) is not bool for name in expected):
        raise RuntimeError(f"兽渊 Runtime 自动选项快照不完整：{raw!r}")
    return {name: raw[name] for name in TOGGLES}


def configure_beast_abyss_native_auto_options(
    context: Any,
    help_view_scene_id: int,
    options: BeastAbyssNativeAutoOptions,
) -> Iterator[Any]:
    """Read options once; verify again only after an actual option change.

    The already-correct path reuses this call's fresh complete observation,
    not a persisted settings cache. No action or yield intervenes. Real batch
    latency still needs measurement through the owning Kernel.
    """

    desired = options.as_dict()
    if options.use_find_demon_talisman:
        from backend.core.fanxiu.instrumentation.beast_abyss_runtime import (
            read_beast_abyss_auto_options_snapshot,
        )

        snapshot = read_beast_abyss_auto_options_snapshot()
        if not snapshot.get("evidence", {}).get("special_option_available"):
            raise RuntimeError("当前兽渊设置页没有寻妖符选项，不能启用")
    before = _read_runtime_options()
    if before == desired:
        return before
    for name, value in desired.items():
        if before[name] != value:
            context.click_shape_center(help_view_scene_id, TOGGLES[name].action)
            yield from context.wait_action_settle(0.35)
    after = _read_runtime_options()
    if after != desired:
        raise RuntimeError(f"兽渊自动探查配置终态不一致：{after!r}")
    return after


def _read_count(context: Any, assets: BeastAbyssNativeAutoAssets) -> int:
    values, text = context.ocr_numbers_in_shapes(assets.help_view_scene_id, [assets.count_region])
    unique = sorted({int(value) for value in values if int(value) > 0})
    if len(unique) != 1:
        raise RuntimeError(f"兽渊自动探查次数无法唯一读回：{text!r}")
    return unique[0]


def read_beast_abyss_native_auto_settings(
    context: Any,
    assets: BeastAbyssNativeAutoAssets,
    *,
    measurement: bool,
) -> BeastAbyssAutoSettings:
    """Read back the complete #658 contract without changing GUI state."""

    values = _read_runtime_options()
    settings = BeastAbyssAutoSettings(
        **values,
        requested_explores=_read_count(context, assets),
    )
    validate_beast_abyss_auto_settings(settings, measurement=measurement)
    return settings


def _set_count(
    context: Any,
    assets: BeastAbyssNativeAutoAssets,
    desired: int,
    *,
    maximum: int | None = None,
) -> Iterator[Any]:
    from backend.core.fanxiu.instrumentation.beast_abyss_runtime import (
        read_beast_abyss_auto_count_snapshot,
    )

    # ``maximum`` is the conservative resource capacity proved before entering
    # #658.  It is a safety ceiling, not the slider's coordinate range: the
    # native ``useMax`` may be much larger while the player is on a low-cost
    # Beast Abyss layer.  Mixing the two makes a 100-run target land hundreds
    # or thousands of runs away and then degrades into excessive +/- clicks.
    if maximum is not None and int(desired) > int(maximum):
        raise RuntimeError(
            f"兽渊自动探查目标超过资源安全容量："
            f"target={int(desired)}, capacity={int(maximum)}"
        )
    started = time.perf_counter()
    live_range = read_beast_abyss_auto_count_snapshot()
    _LOGGER.info("beast-auto phase=count_range_read elapsed=%.3fs", time.perf_counter() - started)
    live_maximum = int(live_range.get("maximum") or 0)
    if live_maximum < int(desired):
        raise RuntimeError(
            f"兽渊自动探查目标超过#658实时上限："
            f"target={int(desired)}, useMax={live_maximum}"
        )

    slider_assets = SimpleNamespace(
        settings_scene_id=assets.help_view_scene_id,
        count_region=assets.count_region,
        count_decrease=assets.count_decrease,
        count_increase=assets.count_increase,
        count_slider_thumb=assets.count_slider_thumb,
        count_slider_left_anchor=assets.count_slider_left_anchor,
        count_slider_right_anchor=assets.count_slider_right_anchor,
        count_slider_left_center_offset=assets.count_slider_left_center_offset,
        count_slider_right_center_offset=assets.count_slider_right_center_offset,
    )
    adjustment_started = time.perf_counter()
    adjustment = yield from set_verified_integer_slider_count(
        context,
        slider_assets,
        int(desired),
        max_adjustments=10,
        count_label="兽渊自动探查次数",
        maximum=live_maximum,
        initial_count=int(live_range["current"]),
    )
    _LOGGER.info(
        "beast-auto phase=count_adjust elapsed=%.3fs path=%s reads=%s",
        time.perf_counter() - adjustment_started,
        adjustment.get("phase"), adjustment.get("count_reads"),
    )
    if int(adjustment["after"]) != int(desired):
        raise RuntimeError(
            f"兽渊自动探查次数滑轨回读异常："
            f"expected={int(desired)}, actual={int(adjustment['after'])}"
        )


def dismiss_beast_abyss_defeat(context: Any) -> Iterator[Any]:
    """Dismiss only the verified PvP defeat overlay; never replay a batch."""
    frame = context.cur_frame(update=True)
    if not all(_shape_matches(context, 742, title, frame=frame) for title in (
        "本次失败损失", "即将返回",
    )):
        return False
    yield from context.wait_click_then_scene(
        742, "点击屏幕继续", 657, 658, 656, timeout=20.0,
        label="兽渊被击败：确认返回活动",
    )
    return True


def enter_beast_abyss_explore(
    context: Any,
    assets: BeastAbyssNativeAutoAssets,
) -> Iterator[Any]:
    """Reusable entry unit: reach #657 without coupling reward collection."""

    entry_scenes = (
        assets.home_scene_id,
        assets.explore_scene_id,
        assets.cutscene_scene_id,
        assets.skip_confirm_scene_id,
        assets.npc_entry_scene_id,
        assets.region_map_scene_id,
    )
    scene_id = None
    for _entry_probe in range(6):
        _wait_scene_match = yield from context.wait_scene(list(entry_scenes), wait=5.0, required=False)
        (scene_id, _score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id in entry_scenes:
            break
        if _entry_probe < 5:
            yield from context.wait_action_settle(0.5)
    if scene_id not in entry_scenes:
        for _navigation_attempt in range(3):
            yield from context.go_scene(assets.home_scene_id)
            try:
                yield from context.wait_scene(
                    [assets.home_scene_id],
                    wait=8.0,
                    label="兽渊：确认活动封面",
                )
            except TimeoutError:
                if _navigation_attempt >= 2:
                    raise
                yield from context.wait_action_settle(1.0)
                continue
            scene_id = assets.home_scene_id
            break
    if scene_id == assets.home_scene_id:
        yield from _observe(context, (assets.home_scene_id,), ("进入活动", "兽渊探秘"))
        context.click_shape_center(assets.home_scene_id, assets.enter_activity)
        yield from context.wait_action_settle(1.0)
    elif scene_id not in entry_scenes:
        raise RuntimeError(f"兽渊预检要求从活动页或探查页开始：scene={scene_id!r}")
    for _attempt in range(24):
        _wait_scene_match = yield from context.wait_scene(list(entry_scenes), wait=5.0, required=False)
        (scene_id, _score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id == assets.explore_scene_id:
            # During map transitions the scene classifier can briefly retain
            # the exploration identity. Require its visible controls as well.
            text = _compact(context.ocr_text_in_shapes(
                assets.explore_scene_id, [assets.open_auto, assets.open_quick],
                frame_data_url=_frame,
            ))
            if any(anchor in text for anchor in ("自动探查", "快捷处理")):
                # The first entry can still pass through the region map after
                # an exploration frame. Confirm readiness inside this same
                # transition loop, so a legal intermediate page is handled
                # rather than rejected by a separate final observation.
                yield from context.wait_action_settle(1.0)
                confirmed = yield from context.wait_scene(list(entry_scenes), wait=5.0, required=False)
                scene_id = confirmed.scene_id if confirmed is not None else None
                if scene_id == assets.explore_scene_id:
                    text = _compact(context.ocr_text_in_shapes(
                        assets.explore_scene_id, [assets.open_auto, assets.open_quick],
                        frame_data_url=confirmed.frame_data_url,
                    ))
                    if any(anchor in text for anchor in ("自动探查", "快捷处理")):
                        return assets.explore_scene_id
        action = {
            assets.cutscene_scene_id: assets.skip_cutscene,
            assets.skip_confirm_scene_id: assets.confirm_skip,
            assets.npc_entry_scene_id: assets.enter_beast_abyss,
            assets.region_map_scene_id: assets.enter_outer_region,
        }.get(scene_id)
        if action:
            context.click_shape_center(int(scene_id), action)
        yield from context.wait_action_settle(1.0)
    else:
        raise RuntimeError("兽渊首次进入动画在有界状态机内未到达探查页")


def enter_beast_abyss_explore_and_claim_rewards(
    context: Any,
    assets: BeastAbyssNativeAutoAssets,
) -> Iterator[Any]:
    """Compatibility wrapper for legacy callers that explicitly need both actions."""

    yield from enter_beast_abyss_explore(context, assets)
    yield from claim_beast_abyss_task_rewards(context)
    return assets.explore_scene_id


def prepare_beast_abyss_native_auto(
    context: Any,
    assets: BeastAbyssNativeAutoAssets,
    request: BeastAbyssNativeAutoRequest,
    *,
    explore_ready: bool = False,
) -> Iterator[Any]:
    """Navigate to native settings and read them back without starting exploration."""

    if not explore_ready:
        yield from enter_beast_abyss_explore(context, assets)
    auto_visible = _shape_matches(context, assets.explore_scene_id, assets.open_auto)
    quick_visible = _shape_matches(context, assets.explore_scene_id, assets.open_quick)
    if auto_visible == quick_visible:
        raise RuntimeError(
            "兽渊原生自动入口无法唯一读回：必须在「自动探查/快捷处理」中恰好命中一个"
        )
    entry_action = assets.open_quick if quick_visible else assets.open_auto
    yield from context.wait_click_then_scene(
        assets.explore_scene_id,
        entry_action,
        assets.help_view_scene_id,
        timeout=20.0,
        label=f"兽渊：点击「{entry_action}」后等待自动设置页",
    )
    yield from _observe(context, (assets.help_view_scene_id,), ("开启自动",))
    return (yield from configure_beast_abyss_native_auto_settings(context, assets, request))


def configure_beast_abyss_native_auto_settings(
    context: Any, assets: BeastAbyssNativeAutoAssets, request: BeastAbyssNativeAutoRequest,
) -> Iterator[Any]:
    """Configure the already-open settings page, without leaving the activity."""
    options = BeastAbyssNativeAutoOptions(
        fairy_events=request.fairy_events,
        beast_events=request.beast_events,
        player_events=request.player_events,
        auto_use_explore_items=request.auto_use_explore_items,
        stop_when_killed=request.stop_when_killed,
        fast_auto=request.fast_auto,
        skip_animation=request.skip_animation,
        use_find_demon_talisman=request.use_find_demon_talisman,
    )
    applied = yield from configure_beast_abyss_native_auto_options(
        context,
        assets.help_view_scene_id,
        options,
    )
    yield from _set_count(
        context,
        assets,
        request.requested_explores,
        maximum=request.maximum_explores,
    )
    # Count adjustment yields to the game for a while. Options observed before
    # it are not a final readback: the panel/server may have refreshed them.
    applied = yield from configure_beast_abyss_native_auto_options(
        context, assets.help_view_scene_id, options,
    )
    settings = BeastAbyssAutoSettings(
        **applied,
        requested_explores=_read_count(context, assets),
    )
    validate_beast_abyss_auto_settings(settings, measurement=request.measurement)
    return settings


def run_prepared_beast_abyss_native_auto(
    context: Any,
    assets: BeastAbyssNativeAutoAssets,
    request: BeastAbyssNativeAutoRequest,
    settings: BeastAbyssAutoSettings,
    *,
    terminal_polls: int = 3600,
    poll_seconds: float = 1.0,
) -> Iterator[Any]:
    """Start one already-read-back #658 configuration and await its terminal."""

    validate_beast_abyss_auto_settings(settings, measurement=request.measurement)
    if settings.requested_explores != request.requested_explores:
        raise RuntimeError("兽渊已配置次数与本批请求不一致")
    if not assets.terminal_scene_ids:
        raise RuntimeError("兽渊尚缺已验证的运行/终态场景资产，未点击「开启自动」")
    yield from context.wait_click(assets.help_view_scene_id, assets.start_auto)

    last_scene: int | None = None
    last_text = ""
    progress_count = None
    progress_changed_at = time.monotonic()
    for _poll in range(max(1, int(terminal_polls))):
        yield from context.wait_action_settle(poll_seconds)
        _wait_scene_match = yield from context.wait_scene([assets.running_scene_id, assets.explore_scene_id, assets.completed_notice_scene_id, *assets.terminal_scene_ids], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id in (assets.running_scene_id, assets.explore_scene_id):
            from backend.core.fanxiu.instrumentation.beast_abyss_runtime import read_beast_abyss_auto_progress_snapshot

            progress = read_beast_abyss_auto_progress_snapshot()["auto_fields"]
            count = (int(progress.get("_AutoExploreNum") or 0), int(progress.get("_exploreCount") or 0))
            if count != progress_count:
                progress_count, progress_changed_at = count, time.monotonic()
                _LOGGER.info("兽渊原生自动进度 %s/%s", count, settings.requested_explores)
            elif time.monotonic() - progress_changed_at >= 180:
                raise RuntimeError(f"兽渊原生自动连续180秒无进展，停在{count}/{settings.requested_explores}；保留现场与未结批次")
            continue
        if scene_id == assets.completed_notice_scene_id:
            notice_text = context.ocr_text_in_shapes(
                assets.completed_notice_scene_id,
                ["自动探查完成标识", "本次探查次数"],
                frame_data_url=frame,
            )
            notice_terminal = classify_beast_abyss_auto_terminal(notice_text)
            if notice_terminal is BeastAbyssAutoTerminal.COMPLETED:
                landed = yield from context.wait_click_then_scene(
                    assets.completed_notice_scene_id,
                    assets.completed_notice_confirm,
                    *assets.terminal_scene_ids,
                    assets.running_scene_id,
                    timeout=20.0,
                    label="兽渊自动探查完成：确认进入结果页",
                )
                scene_id = int(getattr(landed, "id", landed))
                if scene_id == assets.running_scene_id:
                    # The native completion notice can leave QuickAutoView's
                    # old title visible after its stop panel disappeared. Its
                    # upper Mask closes the result (verified on the real UI).
                    # Never dismiss a still-running batch on title alone.
                    from backend.core.fanxiu.instrumentation.beast_abyss_runtime import read_beast_abyss_auto_progress_snapshot
                    progress = read_beast_abyss_auto_progress_snapshot()
                    unit = 10 if settings.fast_auto else 1
                    expected = ((settings.requested_explores + unit - 1) // unit) * unit
                    if (progress.get("auto_requested")
                            or progress.get("requested_explores") != settings.requested_explores
                            or progress.get("dispatched_explores") != expected):
                        raise RuntimeError("兽渊完成提示后的原生计数不一致，保留结果层")
                    yield from context.wait_click_then_scene(
                        assets.running_scene_id, "上方背景关闭", assets.explore_scene_id,
                        timeout=20.0, label="兽渊完整批次：关闭残留快速探查结果层",
                    )
                    return BeastAbyssNativeAutoResult(
                        BeastAbyssAutoTerminal.COMPLETED, assets.explore_scene_id,
                        notice_text, settings, parse_beast_abyss_terminal_evidence(notice_text),
                    )
                _wait_scene_match = yield from context.wait_scene(list(assets.terminal_scene_ids), wait=5.0, required=False)
                (_confirmed, _score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                last_scene = (
                    int(_confirmed) if _confirmed in assets.terminal_scene_ids else None
                )
                last_text = context.ocr_text_in_shapes(382, ["关闭"], frame_data_url=frame)
                if last_scene is not None:
                    return BeastAbyssNativeAutoResult(
                        BeastAbyssAutoTerminal.COMPLETED,
                        last_scene,
                        last_text,
                        settings,
                        parse_beast_abyss_terminal_evidence(last_text),
                    )
        last_scene = int(scene_id) if scene_id in assets.terminal_scene_ids else None
        last_text = context.ocr_text(frame)
        terminal = classify_beast_abyss_auto_terminal(last_text)
        if last_scene is not None and terminal is not BeastAbyssAutoTerminal.UNKNOWN:
            return BeastAbyssNativeAutoResult(
                terminal,
                last_scene,
                last_text,
                settings,
                parse_beast_abyss_terminal_evidence(last_text),
            )
    return BeastAbyssNativeAutoResult(
        BeastAbyssAutoTerminal.UNKNOWN,
        last_scene,
        last_text,
        settings,
    )


def run_beast_abyss_native_auto(
    context: Any,
    assets: BeastAbyssNativeAutoAssets,
    request: BeastAbyssNativeAutoRequest,
    *,
    terminal_polls: int = 3600,
    poll_seconds: float = 1.0,
) -> Iterator[Any]:
    """Drive the native GUI; callers submit this generator through the normal Cell path."""

    settings = yield from prepare_beast_abyss_native_auto(context, assets, request)
    return (yield from run_prepared_beast_abyss_native_auto(
        context,
        assets,
        request,
        settings,
        terminal_polls=terminal_polls,
        poll_seconds=poll_seconds,
    ))
