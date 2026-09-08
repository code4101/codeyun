"""已有红色原始本体的单部件错升重置；首屏第3部位真实闭环通过。

只支持已验证的旧本体 + 唯一原始本体两件路径，不采购、不运行标准作业。
任一点击后观察失败即留场退出，不自动重放更换或升阶。结果页是业务 Layer 0。
2026-09-08：3-3 单红 raw+旧5阶→新6阶未突破、旧UID消失、回666通过。
境数仅记录；5/6部位、多raw及储物袋实际到账未在此案例验收。
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


def classify_owned_raw_reset(rows, *, target: SpiritArtifactWashTarget,
                             previous_item_id: str, expected_previous_grade: int,
                             equipped_item_id: str) -> str:
    """纯事实门禁：replace / upgrade / finish；调用方先核验进程及精确装备。

    rows 必须为完整库存中该 base 的全部实例；不能截成两件绕过多材料歧义。
    境数仅留证据，不作为准入条件；未决确认不由此函数授权重放。
    """
    by_id = {r['item_id']: r for r in rows}
    if (not previous_item_id or previous_item_id == target.item_id
            or expected_previous_grade < 1 or len(by_id) != len(rows)
            or any((r['base_id'], r['ware_id'], r['part']) !=
                   (target.base_id, target.ware_id, target.part)
                   or r['quantity'] != 1 or r['quality'] != 6 for r in rows)):
        raise ValueError('重置库存身份、品质或数量不符合明确目标')
    new = by_id.get(target.item_id)
    if (len(rows) == 1 and new is not None and equipped_item_id == target.item_id
            and new['grade'] == expected_previous_grade + 1 and new['is_break'] is False):
        return 'finish'
    old = by_id.get(previous_item_id)
    if (set(by_id) != {previous_item_id, target.item_id} or old is None or new is None
            or old['grade'] != expected_previous_grade or old['is_break'] is not True
            or new['grade'] != 1 or new['is_break'] is not False):
        raise ValueError('只接受已授权旧本体+唯一红raw，或已完成的唯一新本体')
    if equipped_item_id == previous_item_id:
        return 'replace'
    if equipped_item_id == target.item_id:
        return 'upgrade'
    raise ValueError('当前装备既不是授权旧本体也不是新本体')


def reset_spirit_artifact_from_owned_raw(
    context, execute, *, target: SpiritArtifactWashTarget, previous_item_id: str,
    expected_previous_grade: int, artifact_name: str, selected_part_text: str,
    evidence_path: Path, stop_at: float,
) -> dict[str, Any]:
    """从当前装备/库存事实重入，更换或继续吃旧本体升阶，最终返回 #666。

    target 绑定新本体、进程、灵器、部位、base；previous_item_id 是明确授权
    消耗的旧本体。只接受同 base 两件各 quantity=1、旧已突破、新未突破1阶。
    新 raw 已装备时只续升；旧 UID 消失且唯一新件阶数准确时只收尾。
    未决确认、额外同 base 库存或不明消费均阻塞，不重购、不重放旧 generator。
    境数不是本次重置准入或终态条件；库存日志及结果保留服务端原始境数，
    本流程不操作境数，也不承诺旧本体境数继承或最终境数取值。
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

    def finish(rows):
        nonlocal phase
        phase = 'finish'
        final_equipped = equipped(target.item_id)
        scene = wait(721, 720, 717, 667, 666).scene_id
        # 多个灵器效果可连续激活；它们是业务结果，不交弹窗守护。
        for _ in range(12):
            if scene not in (720, 721):
                break
            click(scene, '点击屏幕继续')
            scene = wait(721, 720, 717, 667, 666).scene_id
        else:
            if scene in (720, 721):
                raise RuntimeError('重置结果连续页超过收尾上限，保留现场')
        if scene == 717:
            identity()
            click(717, '装配')
            scene = wait(667).scene_id
        if scene == 667:
            identity()
            click(667, '返回')
            wait(666)
        result = dict(status='complete', item=rows[0], equipped=final_equipped,
                      expected_grade=expected_previous_grade + 1, final_scene_id=666)
        record('reset_complete', **result)
        return result

    try:
        entry = wait(666, 667, 717, 721, 720, 718, 719)
        if entry.scene_id in (718, 719):
            raise RuntimeError('入口存在未决升阶确认，不能重放或推断消费状态')
        rows = stock()
        state = read_spirit_artifact_equipped_runtime([target.ware_id])
        slot = [r for r in state['items'] if r['part'] == target.part]
        if ((state['pid'], state['process_start_ticks']) != target.process_identity
                or len(slot) != 1 or slot[0]['base_id'] != target.base_id):
            raise RuntimeError('重入时精确装备身份不完整或冲突')
        record('equipped', snapshot=state)
        action = classify_owned_raw_reset(rows, target=target,
            previous_item_id=previous_item_id, expected_previous_grade=expected_previous_grade,
            equipped_item_id=slot[0]['item_id'])
        record('entry_classified', action=action)
        if action == 'finish':
            return finish(rows)
        if entry.scene_id in (720, 721):
            raise RuntimeError('结果页与未完成库存冲突，保留现场')
        if time.time() >= stop_at - 120:
            raise RuntimeError('运行权窗口不足以完成重置，尚未操作')
        if entry.scene_id == 666:
            gui.select_artifact(target.ware_id, artifact_name)
        identity()
        if action == 'replace':
            if entry.scene_id == 717:
                click(717, '装配')
                wait(667)
            phase = 'replace'
            click(667, f'部位{target.part}')
            wait(715)
            click(715, '更换')
            wait(716)
            click(716, '第2个本体')
            replacement_scene = wait(729, 667).scene_id
            if replacement_scene == 729:
                # 低品质换高品质时的业务选择；取消意味着继续装备但不继承。
                click(729, '不继承')
                wait(667)
            equipped(target.item_id)
            identity()
        elif entry.scene_id == 717:
            click(717, '装配')
            wait(667)
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
        # 导航耗时不能沿用入口库存；消费前再次证明自动选料只有授权旧件。
        fresh_rows = stock()
        equipped(target.item_id)
        if classify_owned_raw_reset(fresh_rows, target=target,
                previous_item_id=previous_item_id, expected_previous_grade=expected_previous_grade,
                equipped_item_id=target.item_id) != 'upgrade':
            raise RuntimeError('消费前库存已不再处于明确待升阶状态')
        if time.time() >= stop_at - 120:
            raise RuntimeError('剩余窗口不足，已换本体但未消耗旧件；保留现场')
        click(717, '执行升阶')
        confirmed = set()
        terminal_since = None
        final_rows = None
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            scene = wait(721, 720, 719, 718, 717).scene_id
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
            elif scene in (720, 721):
                click(scene, '点击屏幕继续')
                wait(721, 720, 717)
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
        return finish(final_rows)
    except Exception as exc:
        record('reset_stopped', mutation_may_have_been_sent=mutation_may_have_been_sent,
               error_type=type(exc).__name__, error=str(exc))
        raise
