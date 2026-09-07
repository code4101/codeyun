"""已有红色原始本体的单部件错升重置；1～4 部位，尚待封装真实验收。

只支持已验证的旧本体 + 唯一原始本体两件路径，不采购、不运行标准作业。
任一点击后观察失败即留场退出，不自动重放更换或升阶。结果页是业务 Layer 0。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter
from ...instrumentation.spirit_artifact_equipped import read_spirit_artifact_equipped_runtime
from ...instrumentation.spirit_artifact import read_spirit_artifact_inventory_runtime
from ...instrumentation.spirit_artifact_ui_identity import read_spirit_artifact_ui_identity
from ...instrumentation.spirit_artifact_wash_observation import SpiritArtifactWashTarget


def reset_spirit_artifact_from_owned_raw(
    context, execute, *, target: SpiritArtifactWashTarget, previous_item_id: str,
    expected_previous_grade: int, artifact_name: str, selected_part_text: str,
    evidence_path: Path, stop_at: float,
) -> dict[str, Any]:
    """从 #666/#667 更换唯一红 raw、吃旧本体升阶并返回 #666。

    target 绑定新本体、进程、灵器、部位、base；previous_item_id 是明确授权
    消耗的旧本体。只接受同 base 两件各 quantity=1、旧已突破、新未突破1阶。
    selected_part_text 必须是下方“当前部件名称”的完整可识别名称；该资产
    若不能读出目标则停止，不能依上方固定格子猜测升阶对象。
    stop_at 为绝对秒，消耗前预留120秒。已进入确认链不因截止丢下弹窗。
    """
    if (target.part not in range(1, 5) or not target.base_id or not previous_item_id
            or previous_item_id == target.item_id or expected_previous_grade < 1
            or not artifact_name or not selected_part_text or stop_at <= time.time()):
        raise ValueError('重置参数无效；当前仅支持明确的1～4部位及旧/新本体')
    path = Path(evidence_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    gui = SpiritArtifactCleanseRuntimeGuiAdapter(context, execute)
    phase = 'entry'
    mutation_may_have_been_sent = False

    def record(event, **fields):
        with path.open('a', encoding='utf-8') as output:
            output.write(json.dumps(dict(event=event, at=time.time(), phase=phase,
                new_item_id=target.item_id, previous_item_id=previous_item_id,
                **fields), ensure_ascii=False, default=str) + '\n')

    def identity():
        state = read_spirit_artifact_ui_identity(window_kind='view')
        if ((state['pid'], state['process_start_ticks']) != target.process_identity
                or state['ware_id'] != target.ware_id):
            raise RuntimeError('当前灵器或进程与重置目标不符')

    def stock():
        state = read_spirit_artifact_inventory_runtime()
        if (state.get('complete') is not True or
                (state['pid'], state['process_start_ticks']) != target.process_identity):
            raise RuntimeError('重置库存观察不完整或进程改变')
        record('inventory', snapshot=state)
        return [r for r in state['items'] if r['base_id'] == target.base_id]

    def equipped(uid):
        state = read_spirit_artifact_equipped_runtime([target.ware_id])
        rows = [r for r in state['items'] if r['part'] == target.part]
        if ((state['pid'], state['process_start_ticks']) != target.process_identity
                or len(rows) != 1 or rows[0]['item_id'] != uid
                or rows[0]['base_id'] != target.base_id):
            raise RuntimeError('精确装配引用与重置目标不符')
        record('equipped', snapshot=state)
        return state

    def wait(*scenes):
        match = execute(context.wait_scene(list(scenes), wait=12))
        if match.scene_id not in scenes:
            raise RuntimeError(f'重置流程要求场景{scenes}，实际为#{match.scene_id}')
        return match

    def click(scene, shape):
        nonlocal mutation_may_have_been_sent
        record('click_attempt', scene=scene, shape=shape)
        mutation_may_have_been_sent = True
        context.click_shape_center(scene, shape)

    try:
        entry = wait(666, 667)
        rows = stock()
        by_id = {r['item_id']: r for r in rows}
        if (set(by_id) != {previous_item_id, target.item_id} or len(rows) != 2
                or any(r['quantity'] != 1 or r['quality'] != 6 for r in rows)
                or by_id[previous_item_id]['grade'] != expected_previous_grade
                or by_id[previous_item_id]['is_break'] is not True
                or by_id[target.item_id]['grade'] != 1
                or by_id[target.item_id]['is_break'] is not False):
            raise RuntimeError('只接受已授权旧本体与唯一红色1阶原始本体的库存组合')
        equipped(previous_item_id)
        if time.time() >= stop_at - 120:
            raise RuntimeError('运行权窗口不足以完成重置，尚未操作')
        if entry.scene_id == 666:
            gui.select_artifact(target.ware_id, artifact_name)
        identity()
        phase = 'replace'
        click(667, f'部位{target.part}')
        wait(715)
        click(715, '更换')
        wait(716)
        click(716, '第2个本体')
        wait(667)
        equipped(target.item_id)
        identity()
        phase = 'select_grade_target'
        click(667, '升阶页签')
        wait(717)
        click(717, f'首屏第{target.part}格')
        # 首帧可能还是旧目标；正式 OCR 等待只观察，不再点击，不滚动列表。
        execute(context.wait_ocr_text(717, selected_part_text,
            in_shapes=['当前部件名称'], timeout_seconds=12,
            max_scrolls_per_direction=0, match_mode='exact', crop_fallback=True))
        identity()
        phase = 'consume_old'
        if time.time() >= stop_at - 120:
            raise RuntimeError('剩余窗口不足，已换本体但未消耗旧件；保留现场')
        click(717, '执行升阶')
        confirmed = set()
        terminal_since = None
        final_rows = None
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            scene = wait(720, 719, 718, 717).scene_id
            if scene in (718, 719):
                if scene in confirmed:
                    # A submitted confirmation can remain on screen during
                    # transition. Observe within the existing deadline; never
                    # click the same confirmation twice or reset its budget.
                    time.sleep(.5)
                    continue
                confirmed.add(scene)
                click(scene, '确认')
                terminal_since = None
            elif scene == 720:
                click(720, '点击屏幕继续')
                wait(717)
                terminal_since = None
            else:
                # #717 可能先于延迟 #720 到达，必须先证明库存终态，继续观察。
                if terminal_since is None:
                    final_rows = stock()
                    if (len(final_rows) == 1 and final_rows[0]['item_id'] == target.item_id
                            and final_rows[0]['grade'] == expected_previous_grade + 1
                            and final_rows[0]['quantity'] == 1
                            and final_rows[0]['is_break'] is False):
                        terminal_since = time.monotonic()
                elif time.monotonic() - terminal_since >= 8:
                    break
            time.sleep(.5)
        else:
            raise RuntimeError('升阶后未在有界时间内核实库存与结果页收尾终态')
        phase = 'finish'
        final_equipped = equipped(target.item_id)
        # 耗时 Runtime 读取期间可能出现延迟结果，离场前再按 Layer 0 收尾。
        scene = wait(720, 717).scene_id
        if scene == 720:
            click(720, '点击屏幕继续')
            wait(717)
        click(717, '装配')
        wait(667)
        click(667, '返回')
        wait(666)
        result = dict(status='complete', item=final_rows[0], equipped=final_equipped,
                      expected_grade=expected_previous_grade + 1, final_scene_id=666)
        record('reset_complete', **result)
        return result
    except Exception as exc:
        record('reset_stopped', mutation_may_have_been_sent=mutation_may_have_been_sent,
               error_type=type(exc).__name__, error=str(exc))
        raise
