from __future__ import annotations

"""Fail-closed contracts for the spirit-artifact cleanse UI.

The game Runtime is authoritative for target identity and committed effects.
GUI assets will eventually implement navigation and the game's native
``AutoSet -> AutoStart`` flow.  Until those assets and the pending
``refineMap`` Runtime projection exist, this module intentionally exposes only
read/plan/verification primitives and refuses every irreversible action.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
import secrets
import time
from typing import Any, Protocol


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
        return (self.overview_scene_id, self.detail_scene_id, self.wash_scene_id)

    @property
    def layer0_candidate_ids(self) -> tuple[int, ...]:
        return (
            self.auto_unlocked_warning_scene_id,
            self.attribute_preview_scene_id,
            self.auto_settings_scene_id,
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

    def __post_init__(self) -> None:
        if self.max_rolls <= 0 or self.max_material_cost <= 0:
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

_EXPECTED_WARE_IDS = frozenset(range(1, 9))
_EXPECTED_PARTS = frozenset(range(1, 7))


class SpiritArtifactCleanseGui(Protocol):
    """Future formal-asset adapter; methods are business actions, not points."""

    def select(self, fresh: FreshSpiritArtifactSnapshot) -> Any: ...

    def set_lock(self, cleanse_id: int, locked: bool) -> Any: ...

    def open_attribute_preview(self, prepared: PreparedSpiritArtifactCleanse) -> Any: ...

    def start_auto_cleanse(self, prepared: PreparedSpiritArtifactCleanse) -> Any: ...

    def accept_pending(self, candidate: SpiritArtifactPendingCandidate) -> Any: ...

    def cancel(self) -> Any: ...

    def return_to_world(self) -> Any: ...

    def current_scene_id(self) -> int | None: ...


class SpiritArtifactCleanseRuntimeGuiAdapter:
    """Narrow formal-Runtime adapter for the verified #34→#666→#668 path.

    Only reversible navigation and the Lua-proved read-only overlays are
    implemented.  Lock changes, material consumption and candidate replacement
    remain fail-closed even when the higher-level interface has authorization.
    """

    def __init__(
        self,
        runtime: Any,
        execute: Callable[[Any], Any],
        *,
        assets: SpiritArtifactCleanseGuiAssets | None = None,
    ) -> None:
        self.runtime = runtime
        self.execute = execute
        self.assets = assets or SpiritArtifactCleanseGuiAssets()

    def current_scene_id(self) -> int | None:
        scene_id, _score, _frame = self.runtime.current_scene(
            self.assets.observation_scene_ids,
            update=True,
            handle_interruptions=False,
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

    def _transition(
        self,
        source_scene_id: int,
        shape: str,
        *target_scene_ids: int,
        phase: str,
    ) -> Any:
        self._require_scene(source_scene_id, phase=phase)
        result = self.execute(
            self.runtime.click_shape_center_then_view(
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
        """Select only the one target proved by the current formal asset path."""

        observation = fresh.observation
        known_artifact_names = {"血晶摩诃剑", "血晶摩河剑"}
        if (
            observation.artifact_name not in known_artifact_names
            or observation.part_name != "柄"
            or observation.target.ware_id != 1
            or observation.target.part != 1
        ):
            raise SpiritArtifactCleanseBlocked(
                "当前正式 GUI 资产只证明血晶摩诃剑·柄，其他目标拒绝选择",
                code=SpiritArtifactCleanseErrorCode.ASSET_MISSING,
                phase="select",
                evidence={
                    "artifact": observation.artifact_name,
                    "part": observation.part_name,
                    "ware_id": observation.target.ware_id,
                    "runtime_part": observation.target.part,
                },
            )
        assets = self.assets
        current = self.current_scene_id()
        if current == assets.world_scene_id:
            self.open_overview()
            current = assets.overview_scene_id
        if current == assets.overview_scene_id:
            self._transition(
                assets.overview_scene_id,
                assets.first_artifact_shape,
                assets.detail_scene_id,
                phase="open_artifact_detail",
            )
            current = assets.detail_scene_id
        if current == assets.detail_scene_id:
            return self._transition(
                assets.detail_scene_id,
                assets.wash_tab_shape,
                assets.wash_scene_id,
                phase="open_wash",
            )
        if current == assets.wash_scene_id:
            return current
        raise SpiritArtifactCleanseBlocked(
            "洗灵选择路径不在已证明的正式场景闭环内",
            code=SpiritArtifactCleanseErrorCode.SCENE_MISMATCH,
            phase="select",
            evidence={"current": current},
        )

    def probe_auto_settings_warning(self) -> Any:
        """Open the verified #669 guard only; never confirm it."""

        assets = self.assets
        return self._transition(
            assets.wash_scene_id,
            assets.auto_settings_shape,
            assets.auto_unlocked_warning_scene_id,
            phase="probe_auto_settings_warning",
        )

    def open_auto_settings(self) -> Any:
        """Open #671 without changing controls or activating KeepBtn."""

        assets = self.assets
        current = self.current_scene_id()
        if current == assets.wash_scene_id:
            self.probe_auto_settings_warning()
            current = assets.auto_unlocked_warning_scene_id
        if current != assets.auto_unlocked_warning_scene_id:
            raise SpiritArtifactCleanseBlocked(
                "自动洗炼设置只允许从 #668/#669 的已证明路径进入",
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
            assets.wash_scene_id,
            phase="close_overlay",
        )

    def cancel(self) -> Any:
        assets = self.assets
        current = self.current_scene_id()
        if current in {
            assets.attribute_preview_scene_id,
            assets.auto_settings_scene_id,
        }:
            return self.close_current_overlay()
        return self._transition(
            assets.auto_unlocked_warning_scene_id,
            assets.cancel_warning_shape,
            assets.wash_scene_id,
            phase="cancel_auto_warning",
        )

    def return_to_world(self) -> Any:
        assets = self.assets
        current = self.current_scene_id()
        if current in assets.layer0_candidate_ids:
            self.cancel()
            current = assets.wash_scene_id
        if current == assets.wash_scene_id:
            self._transition(
                assets.wash_scene_id,
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
                phase="return_to_overview",
            )
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

    def set_lock(self, cleanse_id: int, locked: bool) -> Any:
        raise SpiritArtifactCleanseBlocked(
            "词条锁会发网络请求并改变成本，正式 adapter 默认禁用",
            code=SpiritArtifactCleanseErrorCode.AUTH_MISSING,
            phase="lock",
        )

    def open_attribute_preview(self, prepared: PreparedSpiritArtifactCleanse) -> Any:
        """Open the read-only WashAttrPreview; this never starts a cleanse."""

        del prepared
        assets = self.assets
        return self._transition(
            assets.wash_scene_id,
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

    def accept_pending(self, candidate: SpiritArtifactPendingCandidate) -> Any:
        raise SpiritArtifactCleanseBlocked(
            "Save 会覆盖已采用属性，正式 adapter 默认禁用",
            code=SpiritArtifactCleanseErrorCode.AUTH_MISSING,
            phase="replace",
        )


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
    """Require the current 8 artifacts x 6 equipped parts without ambiguity."""

    positions: dict[tuple[int, int], str] = {}
    item_ids: set[str] = set()
    for artifact in snapshot.get("artifacts") or []:
        if not isinstance(artifact, Mapping):
            continue
        for row in artifact.get("rows") or []:
            if not isinstance(row, Mapping):
                continue
            ware_id = _int(row.get("runtime_ware_id"), "ware_id")
            part = _int(row.get("runtime_part"), "part")
            item_id = str(row.get("runtime_item_id") or "").strip()
            position = (ware_id, part)
            if (
                ware_id not in _EXPECTED_WARE_IDS
                or part not in _EXPECTED_PARTS
                or not item_id
            ):
                raise SpiritArtifactCleanseBlocked(
                    "灵器目标全集包含未知或无效部件",
                    code=SpiritArtifactCleanseErrorCode.TARGET_AMBIGUOUS,
                    phase="observe",
                    evidence={"ware_id": ware_id, "part": part, "item_id": item_id},
                )
            if position in positions or item_id in item_ids:
                raise SpiritArtifactCleanseBlocked(
                    "灵器目标全集存在重复部位或重复实例",
                    code=SpiritArtifactCleanseErrorCode.TARGET_AMBIGUOUS,
                    phase="observe",
                    evidence={"position": position, "item_id": item_id},
                )
            positions[position] = item_id
            item_ids.add(item_id)
    expected = {(ware_id, part) for ware_id in _EXPECTED_WARE_IDS for part in _EXPECTED_PARTS}
    if positions.keys() != expected:
        raise SpiritArtifactCleanseBlocked(
            "灵器目标全集不是严格 8×6",
            code=SpiritArtifactCleanseErrorCode.TARGET_UNIVERSE_INCOMPLETE,
            phase="observe",
            retryable=True,
            evidence={"observed": len(positions), "missing": sorted(expected - positions.keys())},
        )


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
        else "纯计划就绪；当前仅允许 dry-run，原生自动洗灵入口尚未资产化"
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
        current = self.read(candidate.target)
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
        return self._gui().accept_pending(candidate)

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
    "verify_spirit_artifact_lock_delta",
    "validate_spirit_artifact_target_universe",
]
