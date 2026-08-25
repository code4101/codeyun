from __future__ import annotations

"""Fail-closed GUI driver for Beast Abyss' native auto-explore dialog.

The driver is deliberately an ordinary behavior-tree generator.  It only
clicks named asset shapes and treats OCR as corroborating evidence for a
Runtime-recognized scene; it does not provide a second command channel.
"""

from dataclasses import dataclass
from enum import StrEnum
import io
import re
from types import SimpleNamespace
from typing import Any, Iterator

from PIL import Image

from backend.core.fanxiu.activity.beast_abyss_challenge_planning import (
    BEAST_ABYSS_MEASUREMENT_EXPLORES,
    BeastAbyssAutoSettings,
    validate_beast_abyss_auto_settings,
)
from backend.core.fanxiu.data_annotation.tasks.yunmeng_native_auto import (
    set_verified_integer_slider_count,
)
from backend.core.fanxiu.data_annotation.tasks.beast_abyss_task_rewards import (
    claim_beast_abyss_cultivation_rewards,
)


class BeastAbyssAutoTerminal(StrEnum):
    COMPLETED = "completed"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    MONSTER_BLOCKED = "monster_blocked"
    KILLED = "killed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BeastAbyssToggleAsset:
    action: str
    selected: str
    unselected: str
    checkbox_box: tuple[float, float, float, float]


@dataclass(frozen=True)
class BeastAbyssNativeAutoAssets:
    """Asset names proven by screenshots; inner scene ids must never be guessed."""

    explore_scene_id: int
    help_view_scene_id: int
    terminal_scene_ids: tuple[int, ...]
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
    fairy_events: bool = True
    beast_events: bool = True
    player_events: bool = False
    stop_when_killed: bool = True
    fast_auto: bool = True
    skip_animation: bool = True

    def __post_init__(self) -> None:
        if self.measurement and self.requested_explores != BEAST_ABYSS_MEASUREMENT_EXPLORES:
            raise ValueError("兽渊测速批次必须固定为10次")


@dataclass(frozen=True)
class BeastAbyssNativeAutoOptions:
    fairy_events: bool
    beast_events: bool
    player_events: bool
    auto_use_explore_items: bool
    stop_when_killed: bool
    fast_auto: bool
    skip_animation: bool

    def as_dict(self) -> dict[str, bool]:
        return {
            "fairy_events": self.fairy_events,
            "beast_events": self.beast_events,
            "player_events": self.player_events,
            "auto_use_explore_items": self.auto_use_explore_items,
            "stop_when_killed": self.stop_when_killed,
            "fast_auto": self.fast_auto,
            "skip_animation": self.skip_animation,
        }


BEAST_ABYSS_PRODUCTION_OPTIONS = BeastAbyssNativeAutoOptions(
    fairy_events=False,
    beast_events=True,
    player_events=True,
    auto_use_explore_items=True,
    stop_when_killed=False,
    fast_auto=True,
    skip_animation=True,
)


@dataclass(frozen=True)
class BeastAbyssNativeAutoResult:
    terminal: BeastAbyssAutoTerminal
    scene_id: int | None
    ocr_text: str
    settings: BeastAbyssAutoSettings


TOGGLES: dict[str, BeastAbyssToggleAsset] = {
    "fairy_events": BeastAbyssToggleAsset("仙侣事件", "仙侣事件_已选", "仙侣事件_未选", (0.115, 0.455, 0.065, 0.025)),
    "beast_events": BeastAbyssToggleAsset("妖兽事件", "妖兽事件_已选", "妖兽事件_未选", (0.115, 0.495, 0.065, 0.025)),
    "player_events": BeastAbyssToggleAsset("玩家事件", "玩家事件_已选", "玩家事件_未选", (0.115, 0.535, 0.065, 0.025)),
    "auto_use_explore_items": BeastAbyssToggleAsset("自动使用探查符", "自动使用探查符_已选", "自动使用探查符_未选", (0.255, 0.585, 0.06, 0.025)),
    "stop_when_killed": BeastAbyssToggleAsset("被击杀停止", "被击杀停止_已选", "被击杀停止_未选", (0.255, 0.615, 0.06, 0.025)),
    "fast_auto": BeastAbyssToggleAsset("快速自动", "快速自动_已选", "快速自动_未选", (0.255, 0.65, 0.06, 0.025)),
    "skip_animation": BeastAbyssToggleAsset("跳过动画", "跳过动画_已选", "跳过动画_未选", (0.255, 0.685, 0.06, 0.025)),
}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def classify_beast_abyss_auto_terminal(text: str) -> BeastAbyssAutoTerminal:
    value = _compact(text)
    if "已完成预设的自动探查次数" in value:
        return BeastAbyssAutoTerminal.COMPLETED
    if "探查体力和探查符不足" in value or "探查体力不足" in value:
        return BeastAbyssAutoTerminal.RESOURCE_EXHAUSTED
    if "有三个妖兽事件未完成击杀" in value:
        return BeastAbyssAutoTerminal.MONSTER_BLOCKED
    if "被其他玩家击杀" in value:
        return BeastAbyssAutoTerminal.KILLED
    return BeastAbyssAutoTerminal.UNKNOWN


def _observe(runtime: Any, scene_ids: tuple[int, ...], anchors: tuple[str, ...]) -> tuple[int, str]:
    scene_id, _score, frame = runtime.current_scene(list(scene_ids), update=True)
    text = runtime.ocr_text(frame)
    if scene_id not in scene_ids or not any(_compact(anchor) in _compact(text) for anchor in anchors):
        raise RuntimeError(
            f"兽渊 Runtime-GUI 对齐失败：scene={scene_id!r}, expected={scene_ids}, ocr={text!r}"
        )
    return int(scene_id), text


def _shape_matches(runtime: Any, scene_id: int, title: str, *, frame: Any | None = None) -> bool:
    view_factory = getattr(runtime, "view", None)
    if callable(view_factory):
        view = view_factory(scene_id)
        get_shape = getattr(view, "get_shape", None)
        if callable(get_shape) and get_shape(title) is None:
            return False
    condition = runtime.shape_visible(scene_id, title)
    if frame is None:
        frame = runtime.cur_frame()
    result = condition.check(runtime, frame)
    return bool(result.matched)


def _read_toggle(runtime: Any, scene_id: int, asset: BeastAbyssToggleAsset) -> bool:
    # Read both visual states from one explicitly refreshed frame.  This avoids
    # mixing pre/post-click observations when the UI is still repainting.
    frame = runtime.cur_frame(update=True)
    runner = getattr(runtime, "runner", None)
    decode = getattr(runner, "_decode_frame_data_url", None)
    if callable(decode):
        raw = decode(frame)
        with Image.open(io.BytesIO(raw)) as source:
            rgb = source.convert("RGB")
            width, height = rgb.size
            x, y, w, h = asset.checkbox_box
            crop = rgb.crop((round(x * width), round(y * height), round((x + w) * width), round((y + h) * height)))
            pixels = list(crop.get_flattened_data())
        if not pixels:
            raise RuntimeError(f"兽渊开关「{asset.action}」复选框区域为空")
        green = sum(1 for red, channel_green, blue in pixels if channel_green >= 105 and channel_green >= red * 1.25 and channel_green >= blue * 1.08)
        return green / len(pixels) >= 0.08
    selected = _shape_matches(runtime, scene_id, asset.selected, frame=frame)
    unselected = _shape_matches(runtime, scene_id, asset.unselected, frame=frame)
    if selected == unselected:
        raise RuntimeError(f"兽渊开关「{asset.action}」状态无法唯一读回")
    return selected


def _set_toggle(runtime: Any, scene_id: int, asset: BeastAbyssToggleAsset, desired: bool) -> Iterator[Any]:
    current = _read_toggle(runtime, scene_id, asset)
    if current == desired:
        return
    runtime.click_shape_center(scene_id, asset.action)
    for _attempt in range(10):
        yield from runtime.wait_action_settle(0.5)
        if _read_toggle(runtime, scene_id, asset) == desired:
            return
    raise RuntimeError(f"兽渊开关「{asset.action}」设置后在5秒内未收敛")


def configure_beast_abyss_native_auto_options(
    runtime: Any,
    help_view_scene_id: int,
    options: BeastAbyssNativeAutoOptions,
) -> Iterator[Any]:
    """Idempotently apply and verify the seven native auto-explore options."""

    desired = options.as_dict()
    before = {
        name: _read_toggle(runtime, help_view_scene_id, TOGGLES[name])
        for name in desired
    }
    for name, value in desired.items():
        if before[name] != value:
            yield from _set_toggle(runtime, help_view_scene_id, TOGGLES[name], value)
    after = {
        name: _read_toggle(runtime, help_view_scene_id, TOGGLES[name])
        for name in desired
    }
    if after != desired:
        raise RuntimeError(f"兽渊7项自动探查配置终态不一致：{after!r}")
    return after


def _read_count(runtime: Any, assets: BeastAbyssNativeAutoAssets) -> int:
    values, text = runtime.ocr_numbers_in_shapes(assets.help_view_scene_id, [assets.count_region])
    unique = sorted({int(value) for value in values if int(value) > 0})
    if len(unique) != 1:
        raise RuntimeError(f"兽渊自动探查次数无法唯一读回：{text!r}")
    return unique[0]


def _read_stable_count(runtime: Any, assets: BeastAbyssNativeAutoAssets) -> Iterator[Any]:
    previous: int | None = None
    for _poll in range(6):
        current = _read_count(runtime, assets)
        if current == previous:
            return current
        previous = current
        yield from runtime.wait_action_settle(0.4)
    raise RuntimeError("兽渊自动探查次数在稳定读回窗口内仍持续变化")


def _set_count(runtime: Any, assets: BeastAbyssNativeAutoAssets, desired: int) -> Iterator[Any]:
    current = yield from _read_stable_count(runtime, assets)
    if abs(int(desired) - current) > 100:
        slider_assets = SimpleNamespace(
            settings_scene_id=assets.help_view_scene_id,
            count_region=assets.count_region,
            count_decrease=assets.count_decrease,
            count_increase=assets.count_increase,
            count_slider_thumb=assets.count_slider_thumb,
            count_slider_left_anchor=assets.count_slider_left_anchor,
            count_slider_right_anchor=assets.count_slider_right_anchor,
        )
        adjustment = yield from set_verified_integer_slider_count(
            runtime,
            slider_assets,
            int(desired),
            max_adjustments=100,
            force_bound_probe=True,
            count_label="兽渊自动探查次数",
        )
        if int(adjustment["after"]) != int(desired):
            raise RuntimeError(
                f"兽渊自动探查次数滑轨回读异常："
                f"expected={int(desired)}, actual={int(adjustment['after'])}"
            )
        return
    for _attempt in range(5):
        if current == desired:
            return
        action = assets.count_increase if current < desired else assets.count_decrease
        delta = abs(desired - current)
        if delta > 100:
            raise RuntimeError(f"兽渊自动探查次数差值异常：current={current}, desired={desired}")
        # The +/- control is deterministic.  Batch the known delta and perform
        # one OCR readback afterwards; if the UI drops a click, the next bounded
        # pass corrects only the residual instead of paying for OCR per click.
        for _click in range(delta):
            runtime.click_shape_center(assets.help_view_scene_id, action)
            yield from runtime.wait_action_settle(0.08)
        current = yield from _read_stable_count(runtime, assets)
    raise RuntimeError("兽渊自动探查次数在5轮批量校正内未收敛")


def prepare_beast_abyss_native_auto(
    runtime: Any,
    assets: BeastAbyssNativeAutoAssets,
    request: BeastAbyssNativeAutoRequest,
) -> Iterator[Any]:
    """Navigate to native settings and read them back without starting exploration."""

    entry_scenes = (
        assets.home_scene_id,
        assets.explore_scene_id,
        assets.cutscene_scene_id,
        assets.skip_confirm_scene_id,
        assets.npc_entry_scene_id,
        assets.region_map_scene_id,
    )
    scene_id, _score, _frame = runtime.current_scene(list(entry_scenes), update=True)
    if scene_id == assets.home_scene_id:
        _observe(runtime, (assets.home_scene_id,), ("进入活动", "兽渊探秘"))
        runtime.click_shape_center(assets.home_scene_id, assets.enter_activity)
        yield from runtime.wait_action_settle(1.0)
    elif scene_id != assets.explore_scene_id:
        raise RuntimeError(f"兽渊预检要求从活动页或探查页开始：scene={scene_id!r}")
    for _attempt in range(24):
        scene_id, _score, _frame = runtime.current_scene(list(entry_scenes), update=True)
        if scene_id == assets.explore_scene_id:
            break
        action = {
            assets.cutscene_scene_id: assets.skip_cutscene,
            assets.skip_confirm_scene_id: assets.confirm_skip,
            assets.npc_entry_scene_id: assets.enter_beast_abyss,
            assets.region_map_scene_id: assets.enter_outer_region,
        }.get(scene_id)
        if action:
            runtime.click_shape_center(int(scene_id), action)
        yield from runtime.wait_action_settle(1.0)
    else:
        raise RuntimeError("兽渊首次进入动画在有界状态机内未到达探查页")
    # This maintenance check belongs to every Beast Abyss invocation.  A
    # target-tier idempotent short-circuit may skip exploration, but must not
    # skip an independent, currently claimable cultivation reward.
    yield from claim_beast_abyss_cultivation_rewards(runtime)
    _observe(runtime, (assets.explore_scene_id,), ("自动探查", "快捷处理"))
    auto_visible = _shape_matches(runtime, assets.explore_scene_id, assets.open_auto)
    quick_visible = _shape_matches(runtime, assets.explore_scene_id, assets.open_quick)
    if auto_visible == quick_visible:
        raise RuntimeError(
            "兽渊原生自动入口无法唯一读回：必须在「自动探查/快捷处理」中恰好命中一个"
        )
    entry_action = assets.open_quick if quick_visible else assets.open_auto
    yield from runtime.wait_click_then_view(
        assets.explore_scene_id,
        entry_action,
        assets.help_view_scene_id,
        timeout=20.0,
        label=f"兽渊：点击「{entry_action}」后等待自动设置页",
    )
    _observe(runtime, (assets.help_view_scene_id,), ("开启自动",))

    options = BeastAbyssNativeAutoOptions(
        fairy_events=request.fairy_events,
        beast_events=request.beast_events,
        player_events=request.player_events,
        auto_use_explore_items=request.auto_use_explore_items,
        stop_when_killed=request.stop_when_killed,
        fast_auto=request.fast_auto,
        skip_animation=request.skip_animation,
    )
    applied = yield from configure_beast_abyss_native_auto_options(
        runtime,
        assets.help_view_scene_id,
        options,
    )
    yield from _set_count(runtime, assets, request.requested_explores)
    settings = BeastAbyssAutoSettings(
        **applied,
        requested_explores=_read_count(runtime, assets),
    )
    validate_beast_abyss_auto_settings(settings, measurement=request.measurement)
    return settings


def run_beast_abyss_native_auto(
    runtime: Any,
    assets: BeastAbyssNativeAutoAssets,
    request: BeastAbyssNativeAutoRequest,
    *,
    terminal_polls: int = 300,
    poll_seconds: float = 1.0,
) -> Iterator[Any]:
    """Drive the native GUI; callers submit this generator through the normal Cell path."""

    settings = yield from prepare_beast_abyss_native_auto(runtime, assets, request)
    if not assets.terminal_scene_ids:
        raise RuntimeError("兽渊尚缺已验证的运行/终态场景资产，已停在设置页且未点击「开启自动」")
    runtime.click_shape_center(assets.help_view_scene_id, assets.start_auto)

    last_scene: int | None = None
    last_text = ""
    for _poll in range(max(1, int(terminal_polls))):
        yield from runtime.wait_action_settle(poll_seconds)
        scene_id, _score, frame = runtime.current_scene(list(assets.terminal_scene_ids), update=True)
        last_scene = int(scene_id) if scene_id in assets.terminal_scene_ids else None
        last_text = runtime.ocr_text(frame)
        terminal = classify_beast_abyss_auto_terminal(last_text)
        if last_scene is not None and terminal is not BeastAbyssAutoTerminal.UNKNOWN:
            return BeastAbyssNativeAutoResult(terminal, last_scene, last_text, settings)
    return BeastAbyssNativeAutoResult(BeastAbyssAutoTerminal.UNKNOWN, last_scene, last_text, settings)
