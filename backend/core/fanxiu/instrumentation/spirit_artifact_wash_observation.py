"""洗炼只读观察：本体快读 → 强制重新定位 → 完整 UI 校验。

只缓存底层的位置，不缓存属性。调用方应已通过 scene 证明洗炼页就绪；
进入部件时使用 verify_ui=True，后续同一连续操作块可走本体快读。
快读不声称验证了当前 UI 选择；换页、中断、外部动作后必须重新校验 UI。
所有层均绑定同一进程和实例，不寻找同部位替代品，不导航，不写游戏。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import time
from typing import Any

from .runtime_memory import FanxiuRuntimeMemoryError


@dataclass(frozen=True)
class SpiritArtifactWashTarget:
    item_id: str
    ware_id: int
    part: int
    process_identity: tuple[int, int]
    base_id: int = 0

    def __post_init__(self):
        if (not self.item_id or self.ware_id <= 0 or not 1 <= self.part <= 6
                or len(self.process_identity) != 2 or min(self.process_identity) <= 0):
            raise ValueError('洗炼观察要求明确的实例、部位和进程身份')


class SpiritArtifactWashTargetChanged(FanxiuRuntimeMemoryError):
    """目标或进程已变化：禁止通过回退悄悄改绑另一个目标。"""


def validate_spirit_artifact_wash_snapshot(
    snapshot: Mapping[str, Any], target: SpiritArtifactWashTarget, *, verify_ui: bool,
) -> None:
    """校验观察契约；实例/进程错配是终止信号，不是重新定位许可。"""
    if ((snapshot.get('pid'), snapshot.get('process_start_ticks')) != target.process_identity
            or snapshot.get('item_id') != target.item_id
            or snapshot.get('ware_id') != target.ware_id or snapshot.get('part') != target.part
            or (target.base_id and snapshot.get('base_id') != target.base_id)):
        raise SpiritArtifactWashTargetChanged('洗炼观察的实例、部位或进程与指定目标不一致', code='wash_target_changed')
    if verify_ui and snapshot.get('is_wash') is not True:
        raise SpiritArtifactWashTargetChanged('当前 UI 已不是指定洗炼页', code='wash_target_changed')
    if type(snapshot.get('refine_num')) is not int or snapshot['refine_num'] < 0:
        raise FanxiuRuntimeMemoryError('洗炼观察缺少有效 refine_num', code='wash_snapshot_incomplete')
    for key in ('effects', 'pending_effects'):
        effects = snapshot.get(key)
        if not isinstance(effects, list) or len(effects) > 6 or (key == 'effects' and not effects):
            raise FanxiuRuntimeMemoryError(f'洗炼观察 {key} 不完整', code='wash_snapshot_incomplete')
        ids = []
        for effect in effects:
            if (not isinstance(effect, Mapping) or type(effect.get('cleanse_id')) is not int
                    or effect['cleanse_id'] <= 0 or type(effect.get('locked')) is not bool
                    or type(effect.get('value')) is not int or type(effect.get('quality')) is not int):
                raise FanxiuRuntimeMemoryError(f'洗炼观察 {key} 词条结构无效', code='wash_snapshot_incomplete')
            ids.append(effect['cleanse_id'])
        if len(set(ids)) != len(ids):
            raise FanxiuRuntimeMemoryError(f'洗炼观察 {key} 词条身份重复', code='wash_snapshot_incomplete')


def read_spirit_artifact_wash_observation(
    target: SpiritArtifactWashTarget, *, verify_ui: bool = False,
) -> dict[str, Any]:
    """读取指定目标的新鲜属性、候选与锁；回退最多各层一次。

    正常快读成功即返回，ui_verified=False，不附带 UI 行序/费用的承诺。
    快读的 Runtime 错误才触发强制重定位；无论重定位是否恢复，都以完整
    UI 校验收尾。UI 仍失败则抛错，错误携带 fallback_errors 供诊断。
    指定实例不存在、身份错配、非 Runtime 程序错误直接停止，不重试。
    verify_ui=True 用于初始化/重新校准，直接完整 UI 读取。
    下层原有有界解析恢复仍由下层负责；这里不循环重试或扩展扫描。
    """
    from .spirit_artifact import read_spirit_artifact_item_runtime
    from .spirit_artifact_ui import read_spirit_artifact_ui_snapshot

    errors: list[dict[str, str]] = []

    def record(layer: str, exc: FanxiuRuntimeMemoryError) -> None:
        errors.append({'layer': layer, 'code': exc.code, 'error': str(exc)})

    def result(snapshot: dict[str, Any], path: str, ui_verified: bool) -> dict[str, Any]:
        value = {**snapshot, 'observation_path': path, 'ui_verified': ui_verified,
                'observed_at': time.time(), 'fallback_errors': errors}
        from .spirit_artifact_memory import spirit_artifact_memory
        spirit_artifact_memory.remember_snapshot(value)
        return value

    if not verify_ui:
        try:
            fast = read_spirit_artifact_item_runtime(target.item_id)
            validate_spirit_artifact_wash_snapshot(fast, target, verify_ui=False)
            return result(fast, 'item', False)
        except SpiritArtifactWashTargetChanged:
            raise
        except FanxiuRuntimeMemoryError as exc:
            record('item', exc)

        try:
            relocated = read_spirit_artifact_item_runtime(target.item_id, force_relocate=True)
            validate_spirit_artifact_wash_snapshot(relocated, target, verify_ui=False)
        except SpiritArtifactWashTargetChanged:
            raise
        except FanxiuRuntimeMemoryError as exc:
            record('relocate', exc)

    try:
        ui = read_spirit_artifact_ui_snapshot()
        validate_spirit_artifact_wash_snapshot(ui, target, verify_ui=True)
        return result(ui, 'ui' if verify_ui else 'relocate_ui', True)
    except FanxiuRuntimeMemoryError as exc:
        record('ui', exc)
        exc.fallback_errors = tuple(errors)
        raise
