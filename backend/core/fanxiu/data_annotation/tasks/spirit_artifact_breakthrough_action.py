"""洗灵突破的单次业务动作；流程已手工验证，本封装尚待真实验收。

只从已就绪的 #668 发起，#722 只确认一次。中断恢复仅支持明确选择
finish_result_only=True 并且当前确为 #723：核验指定本体已突破后继续收尾。
不会从 #722 自动恢复/重复确认，也不会将 #668 已突破当作本次动作成功。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter
from ...instrumentation.spirit_artifact import read_spirit_artifact_item_runtime
from ...instrumentation.spirit_artifact_ui_identity import read_spirit_artifact_ui_identity
from ...instrumentation.spirit_artifact_wash_observation import (
    SpiritArtifactWashTarget, validate_spirit_artifact_wash_snapshot,
)


def breakthrough_spirit_artifact(
    context, execute, *, target: SpiritArtifactWashTarget, evidence_path: Path,
    finish_result_only: bool = False,
) -> dict[str, Any]:
    """在明确目标上执行一次突破，或仅收尾既存结果页；不导航、不培养属性。

    调用方先完成该灵器的 A 类策略目标。本函数只检查客户端就绪状态，
    不以固定 A 条数、B 满值或阶数替代客户端准入。target 必须含 base_id。
    返回突破后重新读取的 effects/locks；调用方须废弃突破前的锁账本。
    确认后任一观察失败即记录并抛错，保留当前现场，绝不重新发送确认。
    """
    if not target.base_id:
        raise ValueError('突破要求明确本体 base_id')
    path = Path(evidence_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    gui = SpiritArtifactCleanseRuntimeGuiAdapter(context, execute)
    assets = gui.assets
    started = time.monotonic()
    phase = 'entry'
    confirmed = False

    def wait_for(expected, allowed, seconds):
        """确认页/结果动画可能滞留；只观察，不重放已发送的动作。"""
        deadline = time.monotonic() + seconds
        while True:
            landed = execute(context.wait_scene(list(allowed), wait=12))
            if landed.scene_id == expected:
                return landed
            if landed.scene_id not in allowed or time.monotonic() >= deadline:
                raise RuntimeError(f'突破过渡未到达 #{expected}，实际 #{landed.scene_id}')
            time.sleep(.5)

    def record(event, **fields):
        with path.open('a', encoding='utf-8') as output:
            output.write(json.dumps(dict(recorded_at=time.time(), event=event,
                item_id=target.item_id, phase=phase, **fields), ensure_ascii=False, default=str) + '\n')

    def item(expected_break):
        snapshot = read_spirit_artifact_item_runtime(target.item_id)
        validate_spirit_artifact_wash_snapshot(snapshot, target, verify_ui=False)
        record('item_observed', snapshot=snapshot)
        if snapshot.get('is_break') is not expected_break or snapshot['pending_effects']:
            raise RuntimeError('突破本体状态或待采用属性与预期不一致')
        if len(snapshot['effects']) != 6:
            raise RuntimeError('突破本体属性槽位不完整')
        return snapshot

    def identity(*, readiness=False):
        current = read_spirit_artifact_ui_identity(include_readiness=readiness)
        if ((current['pid'], current['process_start_ticks']) != target.process_identity
                or current['item_id'] != target.item_id or current['ware_id'] != target.ware_id):
            raise RuntimeError('突破面板选中实例或进程已改变')
        if readiness and (current['_IsCanUpgrade'] is not True or current['_CurItemBreak'] is not False):
            raise RuntimeError('客户端未确认该本体可突破')
        return current

    try:
        entry = execute(context.wait_scene(
            [assets.breakthrough_result_scene_id, assets.breakthrough_confirm_scene_id,
             assets.effect_activation_scene_id, *assets.wash_scene_ids], wait=12))
        before = None
        if finish_result_only:
            if entry.scene_id != assets.breakthrough_result_scene_id:
                raise RuntimeError('仅收尾模式要求当前明确为突破结果 #723')
            phase = 'existing_result_verification'
            identity()
            item(True)
        else:
            if entry.scene_id != assets.wash_scene_id:
                raise RuntimeError('突破入口要求无候选洗炼页 #668；不自动恢复确认窗口')
            identity(readiness=True)
            before = item(False)
            identity(readiness=True)
            frame = context.cur_frame(update=True)
            match = context.find_ocr_text(assets.wash_scene_id, '突破上限',
                frame_data_url=frame, match_mode='exact')
            if match is None:
                raise RuntimeError('突破上限按钮未明确识别')
            phase = 'open_confirmation'
            context.click_frame_point(assets.wash_scene_id, *match.point())
            wait_for(assets.breakthrough_confirm_scene_id,
                     (assets.breakthrough_confirm_scene_id, assets.wash_scene_id), 20)
            identity()
            checked = item(False)
            if checked['effects'] != before['effects']:
                raise RuntimeError('突破确认前本体属性发生变化')
            phase = 'confirm_once'
            record('confirmation_attempt', before=before)
            # 点击发生异常时同样视为结果不确定；从此禁止任何自动重发。
            confirmed = True
            context.click_shape_center(assets.breakthrough_confirm_scene_id, '确认突破')
            phase = 'verify_result'
            wait_for(assets.breakthrough_result_scene_id,
                     (assets.breakthrough_result_scene_id, assets.breakthrough_confirm_scene_id,
                      assets.wash_scene_id), 25)
            item(True)  # 先核实成功，再关闭结果；读取失败保留 #723。
        phase = 'finish_result'
        context.click_shape_center(assets.breakthrough_result_scene_id, '点击屏幕继续')
        landed = gui.finish_effect_activation()
        if landed.scene_id != assets.wash_scene_id:
            raise RuntimeError('突破结果收尾未回到无候选洗炼页')
        phase = 'verify_completed'
        after = item(True)
        identity()
        if any(effect['locked'] for effect in after['effects']):
            raise RuntimeError('突破后仍存在锁定，不能沿用预期的全部解锁状态')
        result = dict(status='finished_existing_result' if finish_result_only else 'complete',
                      before=before, snapshot=after, lock_state_reset=True,
                      elapsed_seconds=time.monotonic() - started)
        record('breakthrough_finished', **result)
        return result
    except Exception as exc:
        record('breakthrough_stopped', confirmation_may_have_been_sent=confirmed,
               error_type=type(exc).__name__, error=str(exc))
        raise
