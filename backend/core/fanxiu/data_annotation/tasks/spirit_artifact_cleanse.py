from __future__ import annotations

"""Fail-closed contracts for the spirit-artifact cleanse UI.

The game Runtime is authoritative for target identity and committed effects.
GUI 通过正式资产导航，当前页 Runtime 校验目标和锁状态。
高级洗炼道具预览、确认取消与六词条锁切换已有真实验收；材料消耗和
候选替换仍须在有界预算下单独验收后开放。
"""

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
import secrets
import time
from typing import Any, Protocol

from .spirit_artifact_lock_state import (
    SpiritArtifactLockChange, SpiritArtifactLockRow, SpiritArtifactLockState,
)


SPIRIT_ARTIFACT_CLEANSE_MATERIAL_ID = 14_000_002


@dataclass(frozen=True)
class SpiritArtifactCleanseGuiAssets:
    """Formal scene/shape contract from the first reversible UI survey."""

    world_scene_id: int = 34
    world_menu_scene_id: int = 35
    overview_scene_id: int = 666
    detail_scene_id: int = 667
    wash_scene_id: int = 668
    auto_unlocked_warning_scene_id: int = 669
    attribute_preview_scene_id: int = 670
    auto_settings_scene_id: int = 671
    advanced_items_scene_id: int = 712
    advanced_confirm_scene_id: int = 713
    pending_wash_scene_id: int = 714
    part_detail_scene_id: int = 715
    effect_activation_scene_id: int = 721
    breakthrough_confirm_scene_id: int = 722
    breakthrough_result_scene_id: int = 723
    off_event_confirm_scene_id: int = 726
    discard_score_confirm_scene_id: int = 727
    open_menu_shape: str = "打开下方菜单"
    open_spiritware_shape: str = "灵器"
    first_artifact_shape: str = "首个灵器"
    wash_tab_shape: str = "洗炼"
    equip_tab_shape: str = "装配"
    return_shape: str = "返回"
    auto_settings_shape: str = "自动洗炼设置"
    cancel_warning_shape: str = "取消"
    confirm_auto_settings_shape: str = "确定进入自动设置"
    attribute_preview_shape: str = "词条预览"
    close_overlay_shape: str = "点击空白关闭"

    @property
    def business_scene_ids(self) -> tuple[int, ...]:
        return (self.overview_scene_id, self.detail_scene_id, self.part_detail_scene_id, *self.wash_scene_ids)

    @property
    def wash_scene_ids(self) -> tuple[int, ...]:
        return (self.wash_scene_id, self.pending_wash_scene_id)

    @property
    def layer0_candidate_ids(self) -> tuple[int, ...]:
        return (
            self.off_event_confirm_scene_id,
            self.discard_score_confirm_scene_id,
            self.breakthrough_result_scene_id,
            self.breakthrough_confirm_scene_id,
            self.effect_activation_scene_id,
            self.auto_unlocked_warning_scene_id,
            self.attribute_preview_scene_id,
            self.auto_settings_scene_id,
            self.advanced_items_scene_id,
            self.advanced_confirm_scene_id,
        )

    @property
    def observation_scene_ids(self) -> tuple[int, ...]:
        return (
            self.world_scene_id,
            self.world_menu_scene_id,
            *self.business_scene_ids,
            *self.layer0_candidate_ids,
        )


class SpiritArtifactCleanseErrorCode(str, Enum):
    SNAPSHOT_STALE = "SNAPSHOT_STALE"
    PROCESS_CHANGED = "PROCESS_CHANGED"
    GENERATION_CHANGED = "GENERATION_CHANGED"
    ATTEMPT_CHANGED = "ATTEMPT_CHANGED"
    TARGET_UNIVERSE_INCOMPLETE = "TARGET_UNIVERSE_INCOMPLETE"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    PENDING_EXISTS = "PENDING_EXISTS"
    PENDING_FROM_PRIOR_ATTEMPT = "PENDING_FROM_PRIOR_ATTEMPT"
    AUTH_MISSING = "AUTH_MISSING"
    AUTH_STALE = "AUTH_STALE"
    AUTH_REUSED = "AUTH_REUSED"
    PHASE_TOKEN_MISMATCH = "PHASE_TOKEN_MISMATCH"
    ASSET_MISSING = "ASSET_MISSING"
    CONTROL_UNAVAILABLE = "CONTROL_UNAVAILABLE"
    SCENE_MISMATCH = "SCENE_MISMATCH"
    POSTCONDITION_MISMATCH = "POSTCONDITION_MISMATCH"


class SpiritArtifactCleanseBlocked(RuntimeError):
    """Typed fail-closed error; callers must not parse the Chinese message."""

    def __init__(
        self,
        message: str,
        *,
        code: SpiritArtifactCleanseErrorCode = SpiritArtifactCleanseErrorCode.POSTCONDITION_MISMATCH,
        phase: str = "unknown",
        retryable: bool = False,
        evidence: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase
        self.retryable = retryable
        self.evidence = dict(evidence or {})


@dataclass(frozen=True)
class SpiritArtifactAttemptContext:
    attempt_id: str
    kernel_generation: int
    process_identity: tuple[int, int]
    origin_scene_id: int = 34

    def __post_init__(self) -> None:
        if not self.attempt_id or self.kernel_generation <= 0:
            raise ValueError("attempt_id 与 kernel_generation 必须有效")
        if min(self.process_identity) <= 0:
            raise ValueError("process_identity 必须有效")
        if self.origin_scene_id != 34:
            raise SpiritArtifactCleanseBlocked(
                "洗灵 attempt 必须从稳定世界 #34 开始",
                code=SpiritArtifactCleanseErrorCode.SCENE_MISMATCH,
                phase="begin_attempt",
            )


@dataclass(frozen=True)
class SpiritArtifactTarget:
    item_id: str
    ware_id: int
    part: int
    base_id: int = 0


@dataclass(frozen=True)
class SpiritArtifactEffect:
    cleanse_id: int
    value: int
    quality: int
    locked: bool


@dataclass(frozen=True)
class SpiritArtifactObservation:
    target: SpiritArtifactTarget
    artifact_name: str
    part_name: str
    refine_num: int
    effects: tuple[SpiritArtifactEffect, ...]
    pending_effects: tuple[SpiritArtifactEffect, ...]
    process_identity: tuple[int, int]
    captured_at: float
    fingerprint: str


@dataclass(frozen=True)
class SpiritArtifactCleanseBudget:
    max_rolls: int
    max_material_cost: int
    material_id: int = SPIRIT_ARTIFACT_CLEANSE_MATERIAL_ID

    def __post_init__(self) -> None:
        if self.max_rolls <= 0 or self.max_material_cost <= 0 or self.material_id <= 0:
            raise ValueError("洗灵预算必须是正数")


@dataclass(frozen=True)
class SpiritArtifactCleanseRequest:
    target: SpiritArtifactTarget
    expected_fingerprint: str
    required_cleanse_ids: tuple[int, ...]
    preserve_cleanse_ids: tuple[int, ...]
    desired_locked_ids: tuple[int, ...]
    budget: SpiritArtifactCleanseBudget
    allow_replace: bool = False


@dataclass(frozen=True)
class PreparedSpiritArtifactCleanse:
    status: str
    reason: str
    request: SpiritArtifactCleanseRequest
    observation: SpiritArtifactObservation
    plan_token: str
    attempt_id: str = ""
    kernel_generation: int = 0

    @property
    def ready(self) -> bool:
        return self.status == "ready"


@dataclass(frozen=True)
class SpiritArtifactIrreversibleAuthorization:
    plan_token: str
    phase: str = ""
    nonce: str = ""
    allow_material_consumption: bool = False
    allow_lock_change: bool = False
    allow_replace: bool = False


@dataclass(frozen=True)
class FreshSpiritArtifactSnapshot:
    attempt: SpiritArtifactAttemptContext
    observation: SpiritArtifactObservation
    snapshot_token: str


@dataclass(frozen=True)
class SpiritArtifactPendingCandidate:
    attempt_id: str
    kernel_generation: int
    target: SpiritArtifactTarget
    effects: tuple[SpiritArtifactEffect, ...]
    candidate_token: str
    observed_fingerprint: str


@dataclass(frozen=True)
class SpiritArtifactCommitVerification:
    before_fingerprint: str
    after_fingerprint: str
    material_before: int
    material_after: int
    material_cost: int
    page_scene: str
    page_frame_sha256: str


@dataclass(frozen=True)
class SpiritArtifactPageEvidence:
    """Independent current-page proof produced by the future formal adapter."""

    scene: str
    target_item_id: str
    observed_effect_fingerprint: str
    frame_sha256: str


SnapshotReader = Callable[[], Mapping[str, Any]]

from ...catalog.spirit_artifact_wash_rules import spirit_artifact_ware_ids
_EXPECTED_PARTS = frozenset(range(1, 7))


class SpiritArtifactCleanseGui(Protocol):
    """Future formal-asset adapter; methods are business actions, not points."""

    def select(self, fresh: FreshSpiritArtifactSnapshot) -> Any: ...

    def set_lock(self, cleanse_id: int, locked: bool) -> Any: ...

    def open_attribute_preview(self, prepared: PreparedSpiritArtifactCleanse) -> Any: ...

    def start_auto_cleanse(self, prepared: PreparedSpiritArtifactCleanse) -> Any: ...

    def start_advanced_cleanse(self, prepared: PreparedSpiritArtifactCleanse) -> Any: ...

    def accept_pending(self, candidate: SpiritArtifactPendingCandidate) -> Any: ...

    def cancel(self) -> Any: ...

    def return_to_world(self) -> Any: ...

    def current_scene_id(self) -> int | None: ...


class SpiritArtifactCleanseRuntimeGuiAdapter:
    """正式资产导航与 Runtime 动作校验；消耗入口尚不开放。"""

    def __init__(
        self,
        context: Any,
        execute: Callable[[Any], Any],
        *,
        assets: SpiritArtifactCleanseGuiAssets | None = None,
    ) -> None:
        self.context = context
        self.execute = execute
        self.assets = assets or SpiritArtifactCleanseGuiAssets()
        self._advanced_scroll_memory = None

    def current_scene_id(self) -> int | None:
        scene_id, _score, _frame = self.execute(
            self.context.current_scene(
                self.assets.observation_scene_ids,
                update=True,
                label="洗灵：识别当前场景",
            )
        )
        return int(scene_id) if scene_id is not None else None

    def _require_scene(self, expected: int, *, phase: str) -> None:
        current = self.current_scene_id()
        if current != int(expected):
            raise SpiritArtifactCleanseBlocked(
                f"洗灵 {phase} 要求 #{expected}，当前={current}",
                code=SpiritArtifactCleanseErrorCode.SCENE_MISMATCH,
                phase=phase,
                evidence={"expected": int(expected), "current": current},
            )

    def _require_wash_scene(self, *, phase: str) -> int:
        current = self.current_scene_id()
        if current not in self.assets.wash_scene_ids:
            raise SpiritArtifactCleanseBlocked("当前不是洗炼页", phase=phase)
        return current

    def _transition(
        self,
        source_scene_id: int,
        shape: str,
        *target_scene_ids: int,
        phase: str,
    ) -> Any:
        self._require_scene(source_scene_id, phase=phase)
        result = self.execute(
            self.context.click_shape_center_then_scene(
                source_scene_id,
                shape,
                *target_scene_ids,
                timeout=25,
                label=f"洗灵 {phase}",
            )
        )
        landed = getattr(result, "id", None)
        if landed is not None and int(landed) not in {
            int(scene_id) for scene_id in target_scene_ids
        }:
            raise SpiritArtifactCleanseBlocked(
                f"洗灵 {phase} 落点异常：#{landed}",
                code=SpiritArtifactCleanseErrorCode.POSTCONDITION_MISMATCH,
                phase=phase,
                evidence={"targets": target_scene_ids, "landed": landed},
            )
        return result

    def open_overview(self) -> Any:
        assets = self.assets
        current = self.current_scene_id()
        if current == assets.overview_scene_id:
            return current
        if current != assets.world_scene_id:
            raise SpiritArtifactCleanseBlocked(
                "灵器总览只允许从稳定世界 #34 启动",
                code=SpiritArtifactCleanseErrorCode.SCENE_MISMATCH,
                phase="open_overview",
                evidence={"current": current},
            )
        self._transition(
            assets.world_scene_id,
            assets.open_menu_shape,
            assets.world_menu_scene_id,
            phase="open_world_menu",
        )
        return self._transition(
            assets.world_menu_scene_id,
            assets.open_spiritware_shape,
            assets.overview_scene_id,
            phase="open_overview",
        )

    def select(self, fresh: FreshSpiritArtifactSnapshot) -> Any:
        """导航到任意灵器的指定实例；每层都用 Runtime 校验身份。"""
        from backend.core.fanxiu.instrumentation.spirit_artifact_ui_identity import read_spirit_artifact_ui_identity

        observation, assets = fresh.observation, self.assets
        current = self.current_scene_id()
        if current in (*assets.wash_scene_ids, assets.detail_scene_id):
            selected = read_spirit_artifact_ui_identity(window_kind='view')
            if selected['ware_id'] != observation.target.ware_id:
                self.return_to_world()
                current = assets.world_scene_id
        elif current not in (assets.world_scene_id, assets.overview_scene_id):
            self.return_to_world()
            current = assets.world_scene_id
        if current == assets.world_scene_id:
            self.open_overview()
            current = assets.overview_scene_id
        if current == assets.overview_scene_id:
            self.select_artifact(observation.target.ware_id, observation.artifact_name)
            current = assets.detail_scene_id
        if current == assets.detail_scene_id:
            self._transition(current, assets.wash_tab_shape, *assets.wash_scene_ids, phase='open_wash')
        return self.select_wash_part(observation.target.item_id, observation.part_name)

    def select_artifact(self, ware_id: int, artifact_name: str) -> Any:
        """有界横向搜索竖排名称，进入后核对实际灵器 ID。"""
        from backend.core.fanxiu.instrumentation.spirit_artifact_ui import (
            locate_spirit_artifact_name,
        )
        from backend.core.fanxiu.instrumentation.spirit_artifact_ui_identity import read_spirit_artifact_ui_identity

        self._require_scene(self.assets.overview_scene_id, phase='select_artifact')
        if ware_id not in spirit_artifact_ware_ids() or not artifact_name:
            raise SpiritArtifactCleanseBlocked('无效灵器身份', phase='select_artifact')
        names = tuple(dict.fromkeys((artifact_name, artifact_name.replace('摩诃', '摩河'),
                                     artifact_name.replace('干天', '千天'))))
        scene = self.assets.overview_scene_id
        for direction in ('right', 'left'):
            for step in range(6):
                frame = self.context.cur_frame(update=True)
                tokens = self.context.ocr_tokens_in_shapes(scene, ['灵器列表'], frame_data_url=frame, padding=0)
                point = locate_spirit_artifact_name(tokens, names)
                if point is None:
                    tokens = self.context.ocr_tokens_in_shapes(scene, ['灵器列表'], frame_data_url=frame, padding=0, crop=True)
                    point = locate_spirit_artifact_name(tokens, names)
                if point is not None:
                    self.context.click_frame_point(scene, *point)
                    self.execute(self.context.wait_scene([self.assets.detail_scene_id], wait=12))
                    selected = read_spirit_artifact_ui_identity(window_kind='view')
                    if selected['ware_id'] != ware_id:
                        raise SpiritArtifactCleanseBlocked('灵器名称点击后 Runtime ID 不一致', phase='select_artifact')
                    return selected
                if step == 5:
                    break
                changed = self.execute(self.context.scroll_shape_content(
                    self.context.shape(scene, '灵器列表'), direction=direction))
                if not changed:
                    break
        raise SpiritArtifactCleanseBlocked(f'未找到灵器 {artifact_name}',
                                          code=SpiritArtifactCleanseErrorCode.ASSET_MISSING,
                                          phase='select_artifact')

    def select_wash_part(self, item_id: str, part_name: str) -> Any:
        """当前灵器顶栏归左后按固定格选部位，再核验精确实例。

        part_name 保留调用兼容，不参与美术字 OCR。部位来自 Runtime
        V_PartList.partId，不能用数组下标代替。六卡/首屏四卡的布局下，
        一次0.9窗口宽度归左覆盖最大偏移；不缓存上次滚动位置。
        1～4格依正式资产，5/6末屏定位尚未验证时停止，不退世界或猜格。
        此固定格封装仍待主线真实验收；当前归左假设失效应修定位提供方。
        """
        from backend.core.fanxiu.instrumentation.spirit_artifact_ui import read_spirit_artifact_ui_snapshot

        scene = self._require_wash_scene(phase="select_part")
        before = read_spirit_artifact_ui_snapshot()
        matches = [p for p in before.get("parts", []) if p["itemUid"] == str(item_id)]
        if len(matches) != 1:
            raise SpiritArtifactCleanseBlocked("目标实例不属于当前灵器的部件列表", phase="select_part")
        if before["item_id"] == str(item_id):
            return before
        part_ids = [p.get('partId') for p in before['parts']]
        if len(part_ids) != 6 or any(type(p) is not int for p in part_ids) or set(part_ids) != set(range(1, 7)):
            raise SpiritArtifactCleanseBlocked('部件列表缺少完整唯一的 partId，不能按数组位置猜测', phase='select_part')
        part_id = matches[0]['partId']
        if part_id > 4:
            raise SpiritArtifactCleanseBlocked('第5/6部位末屏固定格尚未验证',
                code=SpiritArtifactCleanseErrorCode.ASSET_MISSING, phase='select_part')
        shape_name = '首屏第一部件' if part_id == 1 else f'首屏第{part_id}格'
        # 动作前解析资产，缺格时留在原页，不先滚动再发现配置缺失。
        self.context.shape(scene, shape_name)
        self.context.drag_shape_content(self.context.shape(scene, '部件列表'), direction='left', ratio=.9)
        self.execute(self.context.wait_action_settle(.7))
        self.execute(self.context.click_shape_center(scene, shape_name))
        self.execute(self.context.wait_action_settle(.7))
        after = read_spirit_artifact_ui_snapshot()
        if after.get("item_id") != str(item_id) or after.get('part') != part_id or any(
            before[key] != after[key] for key in ("pid", "process_start_ticks", "ware_id")
        ):
            raise SpiritArtifactCleanseBlocked("部位选择后 Runtime 实例不匹配", phase="select_part")
        return after

    def probe_auto_settings_warning(self) -> Any:
        """Open the verified #669 guard only; never confirm it."""
        from backend.core.fanxiu.instrumentation.spirit_artifact_ui import read_spirit_artifact_ui_snapshot
        self._require_wash_scene(phase='probe_auto_settings_warning')
        if not read_spirit_artifact_ui_snapshot()['needs_auto_warning']:
            raise SpiritArtifactCleanseBlocked('当前部件无需自动洗炼警告', phase='probe_auto_settings_warning')
        return self._open_auto_entry()

    def _open_auto_entry(self) -> Any:
        """按钮当前可见才点击；允许警告或直达设置，不能用高级洗炼代替。"""
        scene = self._require_wash_scene(phase='open_auto_settings')
        frame = self.context.cur_frame(update=True)
        match = self.context.find_ocr_text(scene, '自动洗炼',
            in_shapes=[self.assets.auto_settings_shape], frame_data_url=frame, crop=True)
        if match is None:
            from backend.core.fanxiu.instrumentation.spirit_artifact_ui import read_spirit_artifact_auto_open_rule
            rule = read_spirit_artifact_auto_open_rule()
            raise SpiritArtifactCleanseBlocked('自动洗炼入口当前不可见；受活动开放条件及部件品质限制',
                code=SpiritArtifactCleanseErrorCode.CONTROL_UNAVAILABLE, phase='open_auto_settings', evidence=rule)
        self.context.click_frame_point(scene, *match.point())
        result = self.execute(self.context.wait_scene(
            [self.assets.auto_unlocked_warning_scene_id, self.assets.auto_settings_scene_id], wait=10))
        return result

    def open_auto_settings(self) -> Any:
        """Open #671 without changing controls or activating KeepBtn."""

        assets = self.assets
        current = self.current_scene_id()
        if current in assets.wash_scene_ids:
            self._open_auto_entry()
            current = self.current_scene_id()
        if current == assets.auto_settings_scene_id:
            return current
        if current != assets.auto_unlocked_warning_scene_id:
            raise SpiritArtifactCleanseBlocked(
                "自动洗炼设置要求当前洗炼页或自动洗炼警告页",
                code=SpiritArtifactCleanseErrorCode.SCENE_MISMATCH,
                phase="open_auto_settings",
                evidence={"current": current},
            )
        return self._transition(
            assets.auto_unlocked_warning_scene_id,
            assets.confirm_auto_settings_shape,
            assets.auto_settings_scene_id,
            phase="open_auto_settings",
        )

    def close_current_overlay(self) -> Any:
        """Close only the two Lua-proved empty-mask overlays."""

        assets = self.assets
        current = self.current_scene_id()
        if current not in {
            assets.attribute_preview_scene_id,
            assets.auto_settings_scene_id,
            assets.advanced_items_scene_id,
        }:
            raise SpiritArtifactCleanseBlocked(
                "当前不是已证明可由 emptyMask 关闭的洗灵浮层",
                code=SpiritArtifactCleanseErrorCode.SCENE_MISMATCH,
                phase="close_overlay",
                evidence={"current": current},
            )
        return self._transition(
            current,
            assets.close_overlay_shape,
            *assets.wash_scene_ids,
            phase="close_overlay",
        )

    def cancel(self) -> Any:
        assets = self.assets
        current = self.current_scene_id()
        if current == assets.effect_activation_scene_id:
            return self.finish_effect_activation()
        if current == assets.advanced_confirm_scene_id:
            return self._transition(current, "取消", assets.advanced_items_scene_id,
                                    phase="cancel_advanced_confirmation")
        if current in {
            assets.attribute_preview_scene_id,
            assets.auto_settings_scene_id,
            assets.advanced_items_scene_id,
        }:
            return self.close_current_overlay()
        return self._transition(
            assets.auto_unlocked_warning_scene_id,
            assets.cancel_warning_shape,
            *assets.wash_scene_ids,
            phase="cancel_auto_warning",
        )

    def finish_effect_activation(self) -> Any:
        """新增灵器组合效果触发的业务 Layer0；可连续出现。

        客户端 UpdateSuitInfo 仅为新增激活组合弹出结果，并非每次保存
        都出现。普通洗炼页可直接返回，但不能据此宣称结果页分支已验收。
        """
        candidates = [self.assets.effect_activation_scene_id, *self.assets.wash_scene_ids]
        for _ in range(6):
            result = self.execute(self.context.wait_scene(candidates, wait=12))
            if result.scene_id in self.assets.wash_scene_ids:
                return result
            if result.scene_id != self.assets.effect_activation_scene_id:
                raise SpiritArtifactCleanseBlocked('灵器效果激活收尾落点不明', phase='effect_activation')
            self.context.click_shape_center(self.assets.effect_activation_scene_id, '点击屏幕继续')
            self.execute(self.context.wait_action_settle(0.8))
        raise SpiritArtifactCleanseBlocked('灵器效果激活连续结果超出已知边界', phase='effect_activation')

    def return_to_world(self) -> Any:
        assets = self.assets
        current = self.current_scene_id()
        if current == assets.advanced_confirm_scene_id:
            self.cancel()
            current = assets.advanced_items_scene_id
        if current in assets.layer0_candidate_ids:
            self.cancel()
            current = self.current_scene_id()
        if current in assets.wash_scene_ids:
            self._transition(
                current,
                assets.equip_tab_shape,
                assets.detail_scene_id,
                phase="return_to_detail",
            )
            current = assets.detail_scene_id
        if current == assets.detail_scene_id:
            self._transition(
                assets.detail_scene_id,
                assets.return_shape,
                assets.overview_scene_id,
                assets.part_detail_scene_id,
                phase="return_to_overview",
            )
            current = self.current_scene_id()
        if current == assets.part_detail_scene_id:
            self._transition(current, "右侧暗幕关闭", assets.overview_scene_id,
                             phase="close_part_detail")
            current = assets.overview_scene_id
        if current == assets.overview_scene_id:
            result = self._transition(
                assets.overview_scene_id,
                assets.return_shape,
                assets.world_scene_id,
                phase="return_to_world",
            )
            current = assets.world_scene_id
        else:
            result = current
        if current != assets.world_scene_id or self.current_scene_id() != assets.world_scene_id:
            raise SpiritArtifactCleanseBlocked(
                "洗灵正式返回闭环未落到 #34",
                code=SpiritArtifactCleanseErrorCode.POSTCONDITION_MISMATCH,
                phase="return_to_world",
                evidence={"current": current},
            )
        return result

    @contextmanager
    def lock_session(
        self,
        attempt: SpiritArtifactAttemptContext,
        target: SpiritArtifactTarget,
        *,
        confirm_change: Callable[[SpiritArtifactLockChange, tuple[SpiritArtifactLockRow, ...]], bool | None],
    ):
        """研发接口：一个连续切锁块只读一次 Runtime，yield set_locked(id, bool)。

        confirm_change 必须用动作后的新鲜 GUI 证据确认同一部件、词条身份、
        其他锁未变，并返回实际 locked；证据不全返回 None 或抛错，不能返回
        点击回执或直接回显期望值。暂无已验收的 GUI 确认器，故参数无默认值。
        本块仅可切锁；不能跨 Cell/attempt、换部件、洗炼或采用候选复用。
        外部操作发生时退出本块，下次进入重新读 Runtime。锁变化后的材料成本
        不在本账本内，后续消耗动作仍须独立核对成本。
        """
        from backend.core.fanxiu.instrumentation.spirit_artifact_ui import read_spirit_artifact_ui_snapshot

        self.execute(self.context.wait_scene(list(self.assets.wash_scene_ids), wait=10))
        snapshot = read_spirit_artifact_ui_snapshot()
        if ((snapshot['pid'], snapshot['process_start_ticks']) != attempt.process_identity
                or snapshot.get('item_id') != target.item_id or snapshot['ware_id'] != target.ware_id
                or snapshot.get('part') != target.part
                or (target.base_id and snapshot.get('base_id') != target.base_id)):
            raise SpiritArtifactCleanseBlocked('切锁会话与当前 Runtime 目标不一致', phase='lock')
        state = SpiritArtifactLockState(snapshot, attempt_id=attempt.attempt_id,
                                        kernel_generation=attempt.kernel_generation)
        if len(state.rows) != 6:
            raise SpiritArtifactCleanseBlocked('锁操作仅支持已标注的六词条布局', phase='lock')

        def set_locked(cleanse_id: int, locked: bool) -> tuple[SpiritArtifactLockRow, ...]:
            before = state.rows
            change = state.begin_change(cleanse_id, locked)
            if change is None:
                return before
            try:
                scene = self.execute(self.context.wait_scene(list(self.assets.wash_scene_ids), wait=10)).scene_id
                self.execute(self.context.click_shape_center(scene, f'属性锁{change.effect.row + 1}'))
                state.confirm_change(change, observed_locked=confirm_change(change, before))
                return state.rows
            except BaseException:
                state.invalidate()
                raise

        try:
            yield set_locked
        finally:
            state.invalidate()

    def set_lock(self, cleanse_id: int, locked: bool) -> Any:
        """按当前 UI 行绑定词条，并验证唯一锁 delta；重复设置零动作。

        六词条布局已标注；其他布局不能套用这些坐标。锁变化影响下次成本，
        因此调用者必须在洗炼前重新读取成本，不沿用切锁前的预算。
        """
        from backend.core.fanxiu.instrumentation.spirit_artifact_ui import read_spirit_artifact_ui_snapshot

        scene = self._require_wash_scene(phase="lock")
        before = read_spirit_artifact_ui_snapshot()
        effects = before.get("effects", [])
        matches = [effect for effect in effects if effect["cleanse_id"] == cleanse_id]
        if not before["is_wash"] or len(matches) != 1 or len(effects) != 6:
            raise SpiritArtifactCleanseBlocked("锁操作要求已标注的六词条页面与唯一词条", phase="lock")
        if matches[0]["locked"] is locked:
            return before
        if locked and sum(e['locked'] for e in effects) >= len(effects) - 1:
            raise SpiritArtifactCleanseBlocked('客户端至少保留一条未锁，请先解除下一目标的锁', phase='lock')
        self.execute(self.context.click_shape_center(
            scene, f"属性锁{matches[0]['row'] + 1}"))
        expected = {e["cleanse_id"]: {k: v for k, v in e.items() if k != "row"} for e in effects}
        expected[cleanse_id]["locked"] = locked
        deadline = time.monotonic() + 10
        while True:
            after = read_spirit_artifact_ui_snapshot()
            if any(before[k] != after[k] for k in ("pid", "process_start_ticks", "item_id", "refine_num")):
                raise SpiritArtifactCleanseBlocked("锁操作期间页面目标改变", phase="lock")
            actual = {e["cleanse_id"]: {k: v for k, v in e.items() if k != "row"}
                      for e in after["effects"]}
            if actual == expected and before["pending_effects"] == after["pending_effects"]:
                return after
            if time.monotonic() >= deadline:
                raise SpiritArtifactCleanseBlocked("锁操作没有形成预期唯一 delta", phase="lock")
            time.sleep(0.2)

    def open_advanced_items(self) -> Any:
        """一次新鲜场景判断后打开高级列表；已在列表时不点击、不消耗。

        不经过 _require_wash_scene/_transition 的重复无动作观察。仍使用
        正式点击保护及落点等待，场景只在本次动作前消费，不跨动作缓存。
        业务确认/结果显式保留在 Layer 0；意外落到这些页面时停止。
        此入口缩减尚待真实连续调用与已打开列表两条路径验收。
        """
        assets = self.assets
        candidates = list(dict.fromkeys((assets.advanced_items_scene_id,
            *assets.wash_scene_ids, *assets.layer0_candidate_ids)))
        observed = self.execute(self.context.wait_scene(candidates, wait=12,
            label='洗灵：高级列表入口'))
        if observed.scene_id == assets.advanced_items_scene_id:
            return observed
        if observed.scene_id not in assets.wash_scene_ids:
            raise SpiritArtifactCleanseBlocked('当前不是洗炼页或高级列表',
                code=SpiritArtifactCleanseErrorCode.SCENE_MISMATCH, phase='open_advanced_items',
                evidence={'current': observed.scene_id})
        result = self.execute(self.context.click_shape_center_then_scene(
            observed.scene_id, '高级洗炼', assets.advanced_items_scene_id,
            timeout=25, label='洗灵：打开高级列表'))
        if result.scene_id != assets.advanced_items_scene_id:
            from .spirit_artifact_advanced_scroll import AdvancedItemLocationError
            raise AdvancedItemLocationError(self.context, expected=assets.advanced_items_scene_id,
                observed=result.scene_id,
                frame=result.frame_data_url or self.context.cur_frame(update=True),
                phase='open_advanced_items', problem_code='advanced_list.scene_mismatch')
        return result

    def inspect_advanced_items(self, *, fast: bool = False) -> Any:
        """从洗炼页打开高级列表并读取全部道具；已打开时只读，不使用道具。"""
        from backend.core.fanxiu.instrumentation.spirit_artifact_advanced import read_spirit_artifact_advanced_items

        self.open_advanced_items()
        return read_spirit_artifact_advanced_items(fast=fast)

    def preview_peak_stone(self) -> Any:
        """显示巅峰石使用确认；仍需取消或独立的消耗授权。"""
        return self.preview_advanced_item(14000052)

    @contextmanager
    def advanced_scroll_session(self, program_id: str, *, scroll_profile=None):
        """一次程序内复用高级列表滚动经验，离开上下文即清空。

        每次定位仍识别实际起始视口，不假定重开窗口回顶部。中断后须退出
        本块，下一 Cell/attempt 重新开始；不能把这个上下文存成续跑状态。
        scroll_profile 可显式传 AdvancedScrollProfile；整个程序的冷发现与
        重放使用同一参数，参数参与经验键。省略时保留原已验证手势。
        """
        from .spirit_artifact_advanced_scroll import AdvancedScrollMemory

        if not program_id or self._advanced_scroll_memory is not None:
            raise ValueError('高级列表需要独立、非嵌套的程序作用域')
        memory = self._advanced_scroll_memory = AdvancedScrollMemory(profile=scroll_profile)
        try:
            yield self
        finally:
            memory.close()
            self._advanced_scroll_memory = None

    def preview_advanced_item(self, item_id: int, *, fast_observation: bool = False) -> Any:
        """按 Runtime 道具 ID 定位并显示使用确认，不点击确认。

        OnClickItem 在库存为零时会打开获取途径并发出洗炼请求，因此必须
        在点击前拒绝零库存。其它品质/突破/词条条件由客户端检查；若只出现
        条件不足提示则等待确认失败，保留现场，不继续任何消耗动作。
        """
        from backend.core.fanxiu.instrumentation.spirit_artifact_ui import read_spirit_artifact_ui_snapshot
        from backend.core.fanxiu.instrumentation.spirit_artifact_advanced import advanced_item_confirmation_names

        def observe():
            if not fast_observation:
                return read_spirit_artifact_ui_snapshot()
            from backend.core.fanxiu.instrumentation.spirit_artifact_ui_identity import read_spirit_artifact_ui_identity
            from backend.core.fanxiu.instrumentation.spirit_artifact import read_spirit_artifact_item_runtime
            identity = read_spirit_artifact_ui_identity()
            snapshot = read_spirit_artifact_item_runtime(identity['item_id'])
            if any(identity[key] != snapshot[key] for key in ('pid', 'process_start_ticks', 'ware_id', 'item_id')):
                raise SpiritArtifactCleanseBlocked('轻量窗口身份与本体读取不一致', phase='preview_advanced')
            return snapshot

        started = time.monotonic()
        timings = {}
        catalog = self.inspect_advanced_items(fast=fast_observation)
        timings['catalog'] = time.monotonic() - started
        timings['catalog_detail'] = catalog.get('timings', {})
        matches = [row for row in catalog['items'] if row['item'] == item_id]
        if len(matches) != 1 or matches[0]['count'] <= 0:
            raise SpiritArtifactCleanseBlocked('当前灵器没有该道具或库存为零', phase='preview_advanced')
        item = matches[0]
        name = str(item['name']).replace('·', '').replace(' ', '')
        if not name.startswith('洗灵') or len(name) <= 2:
            raise SpiritArtifactCleanseBlocked('高级洗炼道具名称未解析', phase='preview_advanced')
        ui_started = time.monotonic()
        before = observe()
        timings['ui_before'] = time.monotonic() - ui_started
        if before.get('item_id') != catalog['item_id'] or any(
            before[key] != catalog[key] for key in ('pid', 'process_start_ticks', 'ware_id')
        ):
            raise SpiritArtifactCleanseBlocked('高级洗炼窗口与当前洗炼部件不一致', phase='preview_advanced')
        if not any(not effect['locked'] for effect in before['effects']):
            raise SpiritArtifactCleanseBlocked('没有未锁词条', phase='preview_advanced')
        scroll_key, scroll_route = None, ()
        memory = self._advanced_scroll_memory
        locate_started = time.monotonic()
        if memory is None:
            observed = self.execute(self.context.wait_scene([self.assets.advanced_items_scene_id], wait=10))
            if observed.scene_id != self.assets.advanced_items_scene_id:
                from .spirit_artifact_advanced_scroll import AdvancedItemLocationError
                raise AdvancedItemLocationError(self.context, expected=self.assets.advanced_items_scene_id,
                    observed=observed.scene_id,
                    frame=observed.frame_data_url or self.context.cur_frame(update=True),
                    phase='preview_before_locate', problem_code='advanced_list.scene_mismatch')
            self.execute(self.context.wait_click_ocr_text(
                self.assets.advanced_items_scene_id, name[2:], in_shapes=['道具列表'],
                max_scrolls_per_direction=10, timeout_seconds=60, crop_fallback=True))
        else:
            from .spirit_artifact_advanced_scroll import locate_advanced_item_with_experience
            match, scroll_key, scroll_route = locate_advanced_item_with_experience(
                self.context, self.execute, scene_id=self.assets.advanced_items_scene_id,
                catalog=catalog, item_id=item_id, memory=memory)
            if scroll_key is not None:
                memory.forget(scroll_key)  # 确认失败或中断时，不留成功经验。
            self.context.click_frame_point(self.assets.advanced_items_scene_id, *match.point())
        self.execute(self.context.wait_scene([self.assets.advanced_confirm_scene_id], wait=10))
        self._require_scene(self.assets.advanced_confirm_scene_id, phase='preview_advanced')
        frame = self.context.cur_frame(update=True)
        tokens = self.context.ocr_tokens_in_shapes(self.assets.advanced_confirm_scene_id,
                                                  ['使用道具说明'], frame_data_url=frame)
        text = ''.join(token['text'] for token in tokens).replace('·', '').replace(' ', '')
        if not any(candidate in text for candidate in advanced_item_confirmation_names(item)):
            raise SpiritArtifactCleanseBlocked('使用确认未包含所选道具名称', phase='preview_advanced')
        timings['locate_confirm'] = time.monotonic() - locate_started
        ui_started = time.monotonic()
        after = observe()
        timings['ui_after'] = time.monotonic() - ui_started
        if any(before[key] != after[key] for key in (
            'pid', 'process_start_ticks', 'item_id', 'effects', 'pending_effects', 'refine_num'
        )):
            raise SpiritArtifactCleanseBlocked('查看确认期间部件状态改变', phase='preview_advanced')
        if memory is not None and scroll_key is not None and len(scroll_route) <= 30:
            memory.remember(scroll_key, scroll_route)
        return {'scene': self.assets.advanced_confirm_scene_id, 'target_item_id': catalog['item_id'],
                'item_id': item_id, 'name': item['name'], 'count': item['count'],
                'timings': {**timings, 'total': time.monotonic() - started},
                'inventory_diagnostics': catalog.get('inventory_diagnostics', {}),
                'unlocked_effects': [effect for effect in after['effects'] if not effect['locked']]}

    def open_attribute_preview(self, prepared: PreparedSpiritArtifactCleanse) -> Any:
        """Open the read-only WashAttrPreview; this never starts a cleanse."""

        del prepared
        assets = self.assets
        return self._transition(
            self._require_wash_scene(phase="open_attribute_preview"),
            assets.attribute_preview_shape,
            assets.attribute_preview_scene_id,
            phase="open_attribute_preview",
        )

    def start_auto_cleanse(self, prepared: PreparedSpiritArtifactCleanse) -> Any:
        raise SpiritArtifactCleanseBlocked(
            "AutoSet KeepBtn 会立即开始 200ms 洗炼循环，正式 adapter 默认禁用",
            code=SpiritArtifactCleanseErrorCode.AUTH_MISSING,
            phase="consume",
        )

    def _observe_selected(self, target: SpiritArtifactTarget) -> SpiritArtifactObservation:
        from backend.core.fanxiu.instrumentation.spirit_artifact_ui import read_spirit_artifact_ui_snapshot
        ui = read_spirit_artifact_ui_snapshot()
        if (not ui.get('is_wash') or ui.get('item_id') != target.item_id or ui['ware_id'] != target.ware_id
                or ui['part'] != target.part or (target.base_id and ui['base_id'] != target.base_id)):
            raise SpiritArtifactCleanseBlocked('当前洗炼实例与指定目标不一致', phase='observe_selected')
        def effects(key):
            return tuple(sorted((SpiritArtifactEffect(**{k: row[k] for k in (
                'cleanse_id', 'value', 'quality', 'locked')}) for row in ui[key]), key=lambda e: e.cleanse_id))
        current, pending = effects('effects'), effects('pending_effects')
        return SpiritArtifactObservation(target, '', '', ui['refine_num'], current, pending,
            (ui['pid'], ui['process_start_ticks']), time.time(),
            _fingerprint(_effect_fingerprint_payload(target, ui['refine_num'], current, pending)))

    def start_advanced_cleanse(self, prepared: PreparedSpiritArtifactCleanse) -> Any:
        """执行一次指定高级道具洗炼；不自动采用，不重试有副作用的确认。"""
        from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
        budget = prepared.request.budget
        if not prepared.ready or budget.max_rolls != 1 or budget.max_material_cost != 1:
            raise SpiritArtifactCleanseBlocked('高级洗炼单次接口要求一次、一个道具的预算', phase='consume')
        before = self._observe_selected(prepared.observation.target)
        if before.fingerprint != prepared.observation.fingerprint or before.process_identity != prepared.observation.process_identity:
            raise SpiritArtifactCleanseBlocked('高级洗炼计划已过期', phase='consume')
        if before.pending_effects or {e.cleanse_id for e in before.effects if e.locked} != set(prepared.request.desired_locked_ids):
            raise SpiritArtifactCleanseBlocked('高级洗炼存在旧候选或锁状态与计划不一致', phase='consume')
        preview = self.preview_advanced_item(budget.material_id)
        fresh = self._observe_selected(before.target)
        if fresh.fingerprint != before.fingerprint or fresh.process_identity != before.process_identity:
            raise SpiritArtifactCleanseBlocked('确认前部件状态改变', phase='consume')
        self._transition(self.assets.advanced_confirm_scene_id, '确认使用道具',
                         *self.assets.wash_scene_ids, phase='consume_advanced')
        after = self._observe_selected(before.target)
        counts, inventory = read_backpack_item_counts([budget.material_id], manager_key='spirit-artifact-advanced')
        if (inventory['pid'], inventory['process_start_ticks']) != before.process_identity or after.process_identity != before.process_identity:
            raise SpiritArtifactCleanseBlocked('高级洗炼前后进程改变', phase='consume')
        if preview['count'] - counts[budget.material_id] != 1:
            raise SpiritArtifactCleanseBlocked('高级洗炼没有形成精确一个道具的消耗', phase='consume')
        if after.effects != before.effects or not after.pending_effects:
            raise SpiritArtifactCleanseBlocked('高级洗炼未生成独立候选，保留现场检查', phase='consume')
        return {'before': before, 'after': after, 'material_id': budget.material_id,
                'material_before': preview['count'], 'material_after': counts[budget.material_id]}

    def accept_pending(self, candidate: SpiritArtifactPendingCandidate) -> Any:
        """采用已授权的精确候选；可能丢失锁定词条的二次确认另行处理。"""
        self._require_scene(self.assets.pending_wash_scene_id, phase='replace')
        before = self._observe_selected(candidate.target)
        if before.fingerprint != candidate.observed_fingerprint or before.pending_effects != candidate.effects:
            raise SpiritArtifactCleanseBlocked('候选已变化，拒绝采用', phase='replace')
        pending = {e.cleanse_id: e for e in candidate.effects}
        if any(e.locked and pending.get(e.cleanse_id) != e for e in before.effects):
            raise SpiritArtifactCleanseBlocked('候选会改变锁定词条，需要单独的替换决策', phase='replace')
        self._transition(self.assets.pending_wash_scene_id, '保留新属性',
                         self.assets.wash_scene_id, self.assets.effect_activation_scene_id, phase='replace')
        self.finish_effect_activation()
        after = self._observe_selected(candidate.target)
        verify_spirit_artifact_candidate_saved(candidate, before, after)
        frame = self.context.cur_frame(update=True)
        return SpiritArtifactPageEvidence(str(self.assets.wash_scene_id), candidate.target.item_id,
            spirit_artifact_effect_fingerprint(after.effects), hashlib.sha256(frame.encode()).hexdigest())


def _int(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SpiritArtifactCleanseBlocked(f"灵器 Runtime {label} 无效") from exc


def _effect_fingerprint_payload(
    target: SpiritArtifactTarget,
    refine_num: int,
    effects: Sequence[SpiritArtifactEffect],
    pending_effects: Sequence[SpiritArtifactEffect] = (),
) -> dict[str, Any]:
    return {
        "target": {
            "item_id": target.item_id,
            "ware_id": target.ware_id,
            "part": target.part,
            "base_id": target.base_id,
        },
        "refine_num": refine_num,
        "effects": [
            {
                "cleanse_id": effect.cleanse_id,
                "value": effect.value,
                "quality": effect.quality,
                "locked": effect.locked,
            }
            for effect in sorted(effects, key=lambda item: item.cleanse_id)
        ],
        "pending_effects": [
            {
                "cleanse_id": effect.cleanse_id,
                "value": effect.value,
                "quality": effect.quality,
                "locked": effect.locked,
            }
            for effect in sorted(pending_effects, key=lambda item: item.cleanse_id)
        ],
    }


def _fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def spirit_artifact_effect_fingerprint(
    effects: Sequence[SpiritArtifactEffect],
) -> str:
    return _fingerprint(
        {
            "effects": [
                {
                    "cleanse_id": effect.cleanse_id,
                    "value": effect.value,
                    "quality": effect.quality,
                    "locked": effect.locked,
                }
                for effect in sorted(effects, key=lambda item: item.cleanse_id)
            ]
        }
    )


def validate_spirit_artifact_target_universe(snapshot: Mapping[str, Any]) -> None:
    """核验服务器声明完整的已加载器集合；正式配置仅界定合法编号，不要求全已解锁。"""
    supported = spirit_artifact_ware_ids()
    artifacts = snapshot.get("artifacts") or []
    ware_ids = [artifact.get("order") for artifact in artifacts if isinstance(artifact, Mapping)]
    if (snapshot.get("runtime_complete") is not True or not ware_ids
            or len(ware_ids) != len(artifacts) or len(set(ware_ids)) != len(ware_ids)
            or not set(ware_ids) <= supported):
        raise SpiritArtifactCleanseBlocked("灵器已加载集合不完整或编号未知", code=SpiritArtifactCleanseErrorCode.TARGET_UNIVERSE_INCOMPLETE, phase="observe")
    positions: set[tuple[int, int]] = set()
    item_ids: set[str] = set()
    for artifact in artifacts:
        for row in artifact.get("rows") or []:
            ware_id = _int(row.get("runtime_ware_id"), "ware_id")
            part = _int(row.get("runtime_part"), "part")
            item_id = str(row.get("runtime_item_id") or "").strip()
            empty = row.get("runtime_empty_slot") is True
            position = (ware_id, part)
            if (ware_id != artifact['order'] or part not in _EXPECTED_PARTS
                    or (not item_id and not empty)
                    or (empty and (item_id or row.get('runtime_base_id') or row.get('runtime_effects')))
                    or position in positions or (item_id and item_id in item_ids)):
                raise SpiritArtifactCleanseBlocked("灵器部位身份、空槽或装配引用不一致", code=SpiritArtifactCleanseErrorCode.TARGET_AMBIGUOUS, phase="observe")
            positions.add(position)
            if item_id:
                item_ids.add(item_id)
    expected = {(ware_id, part) for ware_id in ware_ids for part in _EXPECTED_PARTS}
    if positions != expected or len(item_ids) != snapshot.get('runtime_equipped_count'):
        raise SpiritArtifactCleanseBlocked("灵器已加载集合的六部位或装配数量不完整", code=SpiritArtifactCleanseErrorCode.TARGET_UNIVERSE_INCOMPLETE, phase="observe")


def observe_spirit_artifact(
    snapshot: Mapping[str, Any],
    target: SpiritArtifactTarget,
    *,
    now: float | None = None,
    max_age_seconds: float = 90.0,
) -> SpiritArtifactObservation:
    """Project one exact equipped part from a fresh, complete Runtime snapshot."""

    if snapshot.get("runtime_complete") is not True:
        raise SpiritArtifactCleanseBlocked("灵器 Runtime 快照不完整")
    debug = snapshot.get("runtime_debug")
    debug = debug if isinstance(debug, Mapping) else {}
    pid = _int(debug.get("pid"), "pid")
    process_start_ticks = _int(
        debug.get("process_start_ticks"), "process_start_ticks"
    )
    captured_at = float(snapshot.get("runtime_updated_at") or 0)
    current = time.time() if now is None else float(now)
    age = current - captured_at
    if pid <= 0 or process_start_ticks <= 0 or captured_at <= 0:
        raise SpiritArtifactCleanseBlocked("灵器 Runtime 缺少进程身份或采集时间")
    if age < -2.0 or age > max(1.0, float(max_age_seconds)):
        raise SpiritArtifactCleanseBlocked(
            f"灵器 Runtime 快照不新鲜（age={age:.1f}s）"
        )

    matches: list[tuple[str, str, Mapping[str, Any]]] = []
    for raw_artifact in snapshot.get("artifacts") or []:
        if not isinstance(raw_artifact, Mapping):
            continue
        artifact_name = str(raw_artifact.get("name") or "").strip()
        for raw_row in raw_artifact.get("rows") or []:
            if not isinstance(raw_row, Mapping):
                continue
            if (
                str(raw_row.get("runtime_item_id") or "") == target.item_id
                and _int(raw_row.get("runtime_ware_id"), "ware_id")
                == target.ware_id
                and _int(raw_row.get("runtime_part"), "part") == target.part
            ):
                matches.append(
                    (
                        artifact_name,
                        str(raw_row.get("part_name") or "").strip(),
                        raw_row,
                    )
                )
    if len(matches) != 1:
        raise SpiritArtifactCleanseBlocked(
            f"灵器 Runtime 目标不是唯一匹配（count={len(matches)}）"
        )
    artifact_name, part_name, row = matches[0]
    base_id = _int(row.get("runtime_base_id"), "base_id")
    if target.base_id and base_id != target.base_id:
        raise SpiritArtifactCleanseBlocked("灵器 Runtime 目标 base_id 已漂移")
    actual_target = SpiritArtifactTarget(
        item_id=target.item_id,
        ware_id=target.ware_id,
        part=target.part,
        base_id=base_id,
    )
    def parse_effects(raw_effects: Any, label: str) -> list[SpiritArtifactEffect]:
        parsed: list[SpiritArtifactEffect] = []
        seen: set[int] = set()
        for raw_effect in raw_effects or []:
            if not isinstance(raw_effect, Mapping):
                raise SpiritArtifactCleanseBlocked(f"灵器 Runtime {label}词条结构无效")
            cleanse_id = _int(raw_effect.get("cleanse_id"), "cleanse_id")
            if cleanse_id <= 0 or cleanse_id in seen:
                raise SpiritArtifactCleanseBlocked(
                    f"灵器 Runtime {label}词条身份无效或重复"
                )
            seen.add(cleanse_id)
            parsed.append(
                SpiritArtifactEffect(
                    cleanse_id=cleanse_id,
                    value=_int(raw_effect.get("value"), "effect.value"),
                    quality=_int(raw_effect.get("quality"), "effect.quality"),
                    locked=bool(raw_effect.get("locked")),
                )
            )
        return parsed

    effects = parse_effects(row.get("runtime_effects"), "已采用")
    pending_effects = parse_effects(row.get("runtime_pending_effects"), "未保存候选")
    if not effects:
        raise SpiritArtifactCleanseBlocked("灵器 Runtime 目标没有可验证词条")
    refine_num = _int(row.get("runtime_refine_num"), "refine_num")
    fingerprint = _fingerprint(
        _effect_fingerprint_payload(
            actual_target, refine_num, effects, pending_effects
        )
    )
    return SpiritArtifactObservation(
        target=actual_target,
        artifact_name=artifact_name,
        part_name=part_name,
        refine_num=refine_num,
        effects=tuple(sorted(effects, key=lambda item: item.cleanse_id)),
        pending_effects=tuple(
            sorted(pending_effects, key=lambda item: item.cleanse_id)
        ),
        process_identity=(pid, process_start_ticks),
        captured_at=captured_at,
        fingerprint=fingerprint,
    )


def prepare_spirit_artifact_cleanse(
    observation: SpiritArtifactObservation,
    request: SpiritArtifactCleanseRequest,
) -> PreparedSpiritArtifactCleanse:
    """Build a pure plan.  A plan token is an identity guard, not permission."""

    if observation.target != request.target and (
        observation.target.item_id,
        observation.target.ware_id,
        observation.target.part,
    ) != (request.target.item_id, request.target.ware_id, request.target.part):
        raise SpiritArtifactCleanseBlocked("洗灵请求目标与 Runtime 观测不一致")
    if observation.fingerprint != request.expected_fingerprint:
        raise SpiritArtifactCleanseBlocked("洗灵请求的 Runtime 指纹已经失效")
    if observation.pending_effects:
        raise SpiritArtifactCleanseBlocked(
            "目标存在未保存洗灵候选；缺少采用/放弃策略，拒绝开始新一轮"
        )
    current = {effect.cleanse_id: effect for effect in observation.effects}
    required = set(request.required_cleanse_ids)
    preserve = set(request.preserve_cleanse_ids)
    desired_locked = set(request.desired_locked_ids)
    if any(value <= 0 for value in required | preserve | desired_locked):
        raise SpiritArtifactCleanseBlocked("洗灵请求包含无效 cleanse_id")
    if desired_locked - current.keys():
        raise SpiritArtifactCleanseBlocked("锁定请求包含当前目标不存在的词条")
    lock_mismatch = sorted(
        cleanse_id
        for cleanse_id, effect in current.items()
        if effect.locked != (cleanse_id in desired_locked)
    )
    status = "noop" if required and required.issubset(current) else "ready"
    reason = (
        "目标词条已满足，零动作"
        if status == "noop"
        else "计划就绪；执行须通过对应动作入口、一次性授权及预算校验"
    )
    token_payload = {
        "observation": observation.fingerprint,
        "required": sorted(required),
        "preserve": sorted(preserve),
        "desired_locked": sorted(desired_locked),
        "lock_mismatch": lock_mismatch,
        "budget": {
            "max_rolls": request.budget.max_rolls,
            "max_material_cost": request.budget.max_material_cost,
            "material_id": request.budget.material_id,
        },
        "allow_replace": request.allow_replace,
    }
    return PreparedSpiritArtifactCleanse(
        status=status,
        reason=reason,
        request=request,
        observation=observation,
        plan_token=_fingerprint(token_payload),
    )


def require_irreversible_authorization(
    prepared: PreparedSpiritArtifactCleanse,
    authorization: SpiritArtifactIrreversibleAuthorization | None,
    *,
    material: bool = False,
    lock: bool = False,
    replace: bool = False,
) -> None:
    if authorization is None or authorization.plan_token != prepared.plan_token:
        raise SpiritArtifactCleanseBlocked("缺少与当前 Runtime 指纹绑定的授权 token")
    if material and not authorization.allow_material_consumption:
        raise SpiritArtifactCleanseBlocked("未授权消耗洗灵材料")
    if lock and not authorization.allow_lock_change:
        raise SpiritArtifactCleanseBlocked("未授权改变词条锁定状态")
    if replace and (
        not authorization.allow_replace or not prepared.request.allow_replace
    ):
        raise SpiritArtifactCleanseBlocked("未授权采用新词条覆盖旧结果")


def verify_spirit_artifact_lock_delta(
    before: SpiritArtifactObservation,
    after: SpiritArtifactObservation,
    *,
    cleanse_id: int,
    locked: bool,
) -> None:
    if before.process_identity != after.process_identity or before.target != after.target:
        raise SpiritArtifactCleanseBlocked("锁定动作前后不是同一进程与部件")
    if before.refine_num != after.refine_num:
        raise SpiritArtifactCleanseBlocked("锁定动作意外改变了 refine_num")
    before_map = {effect.cleanse_id: effect for effect in before.effects}
    after_map = {effect.cleanse_id: effect for effect in after.effects}
    if before_map.keys() != after_map.keys() or cleanse_id not in before_map:
        raise SpiritArtifactCleanseBlocked("锁定动作前后词条集合发生变化")
    changed = []
    for key in before_map:
        left, right = before_map[key], after_map[key]
        if (left.value, left.quality) != (right.value, right.quality):
            raise SpiritArtifactCleanseBlocked("锁定动作意外改变了词条数值或品质")
        if left.locked != right.locked:
            changed.append(key)
    if changed != [cleanse_id] or after_map[cleanse_id].locked is not locked:
        raise SpiritArtifactCleanseBlocked("锁定动作没有形成唯一精确 delta")


def verify_spirit_artifact_commit_delta(
    before: SpiritArtifactObservation,
    after: SpiritArtifactObservation,
    *,
    material_before: int,
    material_after: int,
    expected_material_cost: int,
    page_evidence: SpiritArtifactPageEvidence,
) -> SpiritArtifactCommitVerification:
    if before.process_identity != after.process_identity or before.target != after.target:
        raise SpiritArtifactCleanseBlocked("采用动作前后不是同一进程与部件")
    if expected_material_cost <= 0:
        raise SpiritArtifactCleanseBlocked("采用验证缺少正数材料成本")
    if material_before - material_after != expected_material_cost:
        raise SpiritArtifactCleanseBlocked("洗灵材料没有形成精确消耗 delta")
    if before.fingerprint == after.fingerprint:
        raise SpiritArtifactCleanseBlocked("采用后灵器词条指纹没有变化")
    if (
        not page_evidence.scene
        or page_evidence.target_item_id != after.target.item_id
        or len(page_evidence.frame_sha256) != 64
        or page_evidence.observed_effect_fingerprint
        != spirit_artifact_effect_fingerprint(after.effects)
    ):
        raise SpiritArtifactCleanseBlocked(
            "采用后的当前页面证据与 Runtime 目标/词条不一致"
        )
    return SpiritArtifactCommitVerification(
        before_fingerprint=before.fingerprint,
        after_fingerprint=after.fingerprint,
        material_before=material_before,
        material_after=material_after,
        material_cost=expected_material_cost,
        page_scene=page_evidence.scene,
        page_frame_sha256=page_evidence.frame_sha256,
    )


def verify_spirit_artifact_candidate_saved(
    candidate: SpiritArtifactPendingCandidate,
    before: SpiritArtifactObservation,
    after: SpiritArtifactObservation,
) -> None:
    """采用只搬运指定候选：不洗新词条、不丢失锁定词条、清空候选。

    before 必须紧邻采用动作；材料总账由 commit_delta 独立验证，不能把
    整轮洗炼前的观测当作保存前观测，也不能仅凭指纹变化判断采用成功。
    """
    if before.process_identity != after.process_identity or before.target != after.target or before.target != candidate.target:
        raise SpiritArtifactCleanseBlocked('采用前后进程或目标不一致', phase='verify_replace')
    if candidate.observed_fingerprint != before.fingerprint or not candidate.effects or (
        spirit_artifact_effect_fingerprint(candidate.effects)
        != spirit_artifact_effect_fingerprint(before.pending_effects)
    ):
        raise SpiritArtifactCleanseBlocked('采用前不是指定的新鲜候选', phase='verify_replace')
    if after.pending_effects:
        raise SpiritArtifactCleanseBlocked('采用后仍存在未保存候选', phase='verify_replace')
    if spirit_artifact_effect_fingerprint(after.effects) != spirit_artifact_effect_fingerprint(candidate.effects):
        raise SpiritArtifactCleanseBlocked('已采用属性不等于指定候选', phase='verify_replace')
    saved = {effect.cleanse_id: effect for effect in after.effects}
    if any(effect.locked and saved.get(effect.cleanse_id) != effect for effect in before.effects):
        raise SpiritArtifactCleanseBlocked('采用损失或修改了原有锁定词条', phase='verify_replace')


class SpiritArtifactCleanseInterface:
    """Attempt-scoped façade with fresh reads and one-shot phase authorization."""

    def __init__(
        self,
        snapshot_reader: SnapshotReader,
        *,
        gui: SpiritArtifactCleanseGui | None = None,
    ) -> None:
        self.snapshot_reader = snapshot_reader
        self.gui = gui
        self._attempt: SpiritArtifactAttemptContext | None = None
        self._target: SpiritArtifactTarget | None = None
        self._baseline_pending_fingerprint = ""
        self._used_authorization_nonces: set[str] = set()

    def observe(self) -> Mapping[str, Any]:
        return dict(self.snapshot_reader())

    def read(self, target: SpiritArtifactTarget) -> SpiritArtifactObservation:
        return observe_spirit_artifact(self.observe(), target)

    def begin_attempt(
        self,
        context: SpiritArtifactAttemptContext,
        target: SpiritArtifactTarget,
    ) -> FreshSpiritArtifactSnapshot:
        snapshot = self.observe()
        validate_spirit_artifact_target_universe(snapshot)
        observation = observe_spirit_artifact(snapshot, target)
        if observation.process_identity != context.process_identity:
            raise SpiritArtifactCleanseBlocked(
                "洗灵 attempt 与当前游戏进程不一致",
                code=SpiritArtifactCleanseErrorCode.PROCESS_CHANGED,
                phase="begin_attempt",
            )
        self._attempt = context
        self._target = observation.target
        self._used_authorization_nonces.clear()
        self._baseline_pending_fingerprint = (
            spirit_artifact_effect_fingerprint(observation.pending_effects)
            if observation.pending_effects
            else ""
        )
        return self._fresh(observation)

    def _fresh(
        self, observation: SpiritArtifactObservation
    ) -> FreshSpiritArtifactSnapshot:
        if self._attempt is None:
            raise SpiritArtifactCleanseBlocked(
                "洗灵 attempt 尚未开始",
                code=SpiritArtifactCleanseErrorCode.ATTEMPT_CHANGED,
                phase="observe",
            )
        token = _fingerprint(
            {
                "attempt": self._attempt.attempt_id,
                "generation": self._attempt.kernel_generation,
                "process": self._attempt.process_identity,
                "observation": observation.fingerprint,
                "nonce": secrets.token_hex(16),
            }
        )
        return FreshSpiritArtifactSnapshot(self._attempt, observation, token)

    def observe_current(self) -> FreshSpiritArtifactSnapshot:
        if self._attempt is None or self._target is None:
            raise SpiritArtifactCleanseBlocked(
                "洗灵 attempt 尚未开始",
                code=SpiritArtifactCleanseErrorCode.ATTEMPT_CHANGED,
                phase="observe",
            )
        observation = self.read(self._target)
        if observation.process_identity != self._attempt.process_identity:
            raise SpiritArtifactCleanseBlocked(
                "洗灵期间游戏进程已变化",
                code=SpiritArtifactCleanseErrorCode.PROCESS_CHANGED,
                phase="observe",
            )
        return self._fresh(observation)

    def _assert_fresh(self, fresh: FreshSpiritArtifactSnapshot) -> SpiritArtifactObservation:
        if self._attempt is None or fresh.attempt.attempt_id != self._attempt.attempt_id:
            raise SpiritArtifactCleanseBlocked(
                "洗灵快照来自其他 attempt",
                code=SpiritArtifactCleanseErrorCode.ATTEMPT_CHANGED,
                phase="pre_action",
            )
        if fresh.attempt.kernel_generation != self._attempt.kernel_generation:
            raise SpiritArtifactCleanseBlocked(
                "洗灵快照的 Kernel generation 已变化",
                code=SpiritArtifactCleanseErrorCode.GENERATION_CHANGED,
                phase="pre_action",
            )
        current = self.observe_current().observation
        if current.fingerprint != fresh.observation.fingerprint:
            raise SpiritArtifactCleanseBlocked(
                "洗灵动作前快照已失效",
                code=SpiritArtifactCleanseErrorCode.SNAPSHOT_STALE,
                phase="pre_action",
                retryable=True,
            )
        return current

    def prepare(
        self, request: SpiritArtifactCleanseRequest
    ) -> PreparedSpiritArtifactCleanse:
        if self._attempt is None:
            raise SpiritArtifactCleanseBlocked(
                "必须先 begin_attempt",
                code=SpiritArtifactCleanseErrorCode.ATTEMPT_CHANGED,
                phase="prepare",
            )
        prepared = prepare_spirit_artifact_cleanse(self.read(request.target), request)
        return replace(
            prepared,
            attempt_id=self._attempt.attempt_id,
            kernel_generation=self._attempt.kernel_generation,
        )

    def _gui(self) -> SpiritArtifactCleanseGui:
        if self.gui is None:
            raise SpiritArtifactCleanseBlocked(
                "洗灵正式 scene/shape 与 GUI adapter 尚未完成，拒绝动作"
            )
        return self.gui

    def select(self, prepared: PreparedSpiritArtifactCleanse) -> Any:
        raise SpiritArtifactCleanseBlocked(
            "select(prepared) 不具备 fresh token；请使用 select_target",
            code=SpiritArtifactCleanseErrorCode.SNAPSHOT_STALE,
            phase="select",
        )

    def select_target(self, fresh: FreshSpiritArtifactSnapshot) -> Any:
        self._assert_fresh(fresh)
        return self._gui().select(fresh)

    def _consume_authorization(
        self,
        prepared_token: str,
        authorization: SpiritArtifactIrreversibleAuthorization | None,
        *,
        phase: str,
        material: bool = False,
        lock: bool = False,
        replace_result: bool = False,
    ) -> None:
        if authorization is None or not authorization.nonce:
            raise SpiritArtifactCleanseBlocked(
                "缺少一次性授权",
                code=SpiritArtifactCleanseErrorCode.AUTH_MISSING,
                phase=phase,
            )
        if authorization.nonce in self._used_authorization_nonces:
            raise SpiritArtifactCleanseBlocked(
                "一次性授权已使用",
                code=SpiritArtifactCleanseErrorCode.AUTH_REUSED,
                phase=phase,
            )
        if authorization.phase != phase or authorization.plan_token != prepared_token:
            raise SpiritArtifactCleanseBlocked(
                "授权 phase/token 与当前动作不匹配",
                code=SpiritArtifactCleanseErrorCode.PHASE_TOKEN_MISMATCH,
                phase=phase,
            )
        if material and not authorization.allow_material_consumption:
            raise SpiritArtifactCleanseBlocked("未授权消耗洗灵材料", phase=phase)
        if lock and not authorization.allow_lock_change:
            raise SpiritArtifactCleanseBlocked("未授权改变词条锁定状态", phase=phase)
        if replace_result and not authorization.allow_replace:
            raise SpiritArtifactCleanseBlocked("未授权采用新词条", phase=phase)
        self._used_authorization_nonces.add(authorization.nonce)

    def set_lock(
        self,
        prepared: PreparedSpiritArtifactCleanse,
        cleanse_id: int,
        locked: bool,
        authorization: SpiritArtifactIrreversibleAuthorization | None = None,
    ) -> Any:
        if self._attempt is None or prepared.attempt_id != self._attempt.attempt_id:
            raise SpiritArtifactCleanseBlocked(
                "锁定计划来自其他 attempt",
                code=SpiritArtifactCleanseErrorCode.ATTEMPT_CHANGED,
                phase="lock",
            )
        current = self.read(prepared.observation.target)
        if current.fingerprint != prepared.observation.fingerprint:
            raise SpiritArtifactCleanseBlocked(
                "切锁前 Runtime 已变化",
                code=SpiritArtifactCleanseErrorCode.SNAPSHOT_STALE,
                phase="lock",
                retryable=True,
            )
        self._consume_authorization(
            prepared.plan_token, authorization, phase="lock", lock=True
        )
        return self._gui().set_lock(cleanse_id, locked)

    def preview_attributes(
        self,
        prepared: PreparedSpiritArtifactCleanse,
    ) -> Any:
        """Open WashAttrPreview only; this path must never start a cleanse."""

        return self._gui().open_attribute_preview(prepared)

    def start_auto_cleanse(
        self,
        prepared: PreparedSpiritArtifactCleanse,
        authorization: SpiritArtifactIrreversibleAuthorization | None = None,
    ) -> Any:
        if prepared.request.budget.material_id != SPIRIT_ARTIFACT_CLEANSE_MATERIAL_ID:
            raise SpiritArtifactCleanseBlocked('原生自动洗炼不能使用高级道具预算', phase='consume')
        if self._attempt is None or prepared.attempt_id != self._attempt.attempt_id:
            raise SpiritArtifactCleanseBlocked(
                "自动洗灵计划来自其他 attempt",
                code=SpiritArtifactCleanseErrorCode.ATTEMPT_CHANGED,
                phase="consume",
            )
        current = self.read(prepared.observation.target)
        if current.fingerprint != prepared.observation.fingerprint:
            raise SpiritArtifactCleanseBlocked(
                "自动洗灵前 Runtime 已变化",
                code=SpiritArtifactCleanseErrorCode.SNAPSHOT_STALE,
                phase="consume",
                retryable=True,
            )
        self._consume_authorization(
            prepared.plan_token, authorization, phase="consume", material=True
        )
        return self._gui().start_auto_cleanse(prepared)

    def start_advanced_cleanse(
        self, prepared: PreparedSpiritArtifactCleanse,
        authorization: SpiritArtifactIrreversibleAuthorization | None = None,
    ) -> Any:
        if self._attempt is None or prepared.attempt_id != self._attempt.attempt_id:
            raise SpiritArtifactCleanseBlocked('高级洗炼计划来自其他 attempt', phase='consume')
        if prepared.kernel_generation != self._attempt.kernel_generation:
            raise SpiritArtifactCleanseBlocked('高级洗炼计划的内核代次已失效',
                code=SpiritArtifactCleanseErrorCode.GENERATION_CHANGED, phase='consume')
        current = self.read(prepared.observation.target)
        if current.process_identity != self._attempt.process_identity:
            raise SpiritArtifactCleanseBlocked('高级洗炼计划的游戏进程已改变',
                code=SpiritArtifactCleanseErrorCode.PROCESS_CHANGED, phase='consume')
        if current.fingerprint != prepared.observation.fingerprint:
            raise SpiritArtifactCleanseBlocked('高级洗炼前 Runtime 已变化', phase='consume')
        self._consume_authorization(prepared.plan_token, authorization, phase='consume', material=True)
        return self._gui().start_advanced_cleanse(prepared)

    def observe_pending(self) -> SpiritArtifactPendingCandidate:
        fresh = self.observe_current()
        pending = fresh.observation.pending_effects
        if not pending:
            raise SpiritArtifactCleanseBlocked(
                "当前没有待采用候选", phase="observe_pending", retryable=True
            )
        effect_token = spirit_artifact_effect_fingerprint(pending)
        if effect_token == self._baseline_pending_fingerprint:
            raise SpiritArtifactCleanseBlocked(
                "待采用候选来自 attempt 开始前",
                code=SpiritArtifactCleanseErrorCode.PENDING_FROM_PRIOR_ATTEMPT,
                phase="observe_pending",
            )
        assert self._attempt is not None
        return SpiritArtifactPendingCandidate(
            attempt_id=self._attempt.attempt_id,
            kernel_generation=self._attempt.kernel_generation,
            target=fresh.observation.target,
            effects=pending,
            candidate_token=_fingerprint(
                {
                    "attempt": self._attempt.attempt_id,
                    "generation": self._attempt.kernel_generation,
                    "target": fresh.observation.target.item_id,
                    "pending": effect_token,
                }
            ),
            observed_fingerprint=fresh.observation.fingerprint,
        )

    def accept_pending(
        self,
        candidate: SpiritArtifactPendingCandidate,
        authorization: SpiritArtifactIrreversibleAuthorization | None = None,
    ) -> Any:
        if self._attempt is None or candidate.attempt_id != self._attempt.attempt_id:
            raise SpiritArtifactCleanseBlocked(
                "候选来自其他 attempt",
                code=SpiritArtifactCleanseErrorCode.PENDING_FROM_PRIOR_ATTEMPT,
                phase="replace",
            )
        if candidate.kernel_generation != self._attempt.kernel_generation:
            raise SpiritArtifactCleanseBlocked('候选的内核代次已失效',
                code=SpiritArtifactCleanseErrorCode.GENERATION_CHANGED, phase='replace')
        current = self.read(candidate.target)
        if current.process_identity != self._attempt.process_identity:
            raise SpiritArtifactCleanseBlocked('采用前游戏进程已改变',
                code=SpiritArtifactCleanseErrorCode.PROCESS_CHANGED, phase='replace')
        if (
            current.fingerprint != candidate.observed_fingerprint
            or tuple(current.pending_effects) != candidate.effects
        ):
            raise SpiritArtifactCleanseBlocked(
                "待采用候选已经变化",
                code=SpiritArtifactCleanseErrorCode.SNAPSHOT_STALE,
                phase="replace",
            )
        self._consume_authorization(
            candidate.candidate_token,
            authorization,
            phase="replace",
            replace_result=True,
        )
        result = self._gui().accept_pending(candidate)
        verify_spirit_artifact_candidate_saved(candidate, current, self.read(candidate.target))
        return result

    def verify(
        self,
        before: SpiritArtifactObservation,
        *,
        material_before: int,
        material_after: int,
        expected_material_cost: int,
        page_evidence: SpiritArtifactPageEvidence,
    ) -> SpiritArtifactCommitVerification:
        after = self.read(before.target)
        return verify_spirit_artifact_commit_delta(
            before,
            after,
            material_before=material_before,
            material_after=material_after,
            expected_material_cost=expected_material_cost,
            page_evidence=page_evidence,
        )

    def cancel(self) -> Any:
        return self._gui().cancel()

    def return_to_world(self) -> Any:
        result = self._gui().return_to_world()
        if self._gui().current_scene_id() != 34:
            raise SpiritArtifactCleanseBlocked(
                "洗灵返回后未验证到世界 #34",
                code=SpiritArtifactCleanseErrorCode.SCENE_MISMATCH,
                phase="return_to_world",
            )
        self._attempt = None
        self._target = None
        return result


__all__ = [
    "FreshSpiritArtifactSnapshot",
    "PreparedSpiritArtifactCleanse",
    "SPIRIT_ARTIFACT_CLEANSE_MATERIAL_ID",
    "SpiritArtifactAttemptContext",
    "SpiritArtifactCleanseBlocked",
    "SpiritArtifactCleanseErrorCode",
    "SpiritArtifactCleanseBudget",
    "SpiritArtifactCleanseGuiAssets",
    "SpiritArtifactCleanseInterface",
    "SpiritArtifactCleanseRequest",
    "SpiritArtifactCleanseRuntimeGuiAdapter",
    "SpiritArtifactCommitVerification",
    "SpiritArtifactEffect",
    "SpiritArtifactIrreversibleAuthorization",
    "SpiritArtifactObservation",
    "SpiritArtifactPendingCandidate",
    "SpiritArtifactPageEvidence",
    "SpiritArtifactTarget",
    "observe_spirit_artifact",
    "prepare_spirit_artifact_cleanse",
    "require_irreversible_authorization",
    "spirit_artifact_effect_fingerprint",
    "verify_spirit_artifact_commit_delta",
    "verify_spirit_artifact_candidate_saved",
    "verify_spirit_artifact_lock_delta",
    "validate_spirit_artifact_target_universe",
]
