"""已有装备红色本体逐颗升到6阶；不采购、不换件、不洗炼或突破。

复用正式灵器导航、升阶Runtime与718/719/720/721资产。仅1–4部位且升阶
实际列表目标位于首屏四格；5/6或后屏定位未验收，明确拒绝。完整入口待实测。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter
from ...instrumentation.spirit_artifact_equipped import read_spirit_artifact_owned_runtime
from ...instrumentation.spirit_artifact_grade import read_spirit_artifact_grade_snapshot
from ...instrumentation.spirit_artifact_wash_observation import SpiritArtifactWashTarget


def authorized_raw_upgrade_candidates(items, *, target: SpiritArtifactWashTarget,
                                      authorized_raw_ids: set[str]) -> tuple[str, ...]:
    """返回客户端最低阶等价候选集合，不冒充已选中的单个消耗UID。

    客户端仅按grade升序，等阶次序未知。所有可能最低阶材料必须都为明确
    授权同base红raw1；更高阶备件不当raw折算，raw用尽不得继续吃它。
    items必须完整全库存；允许授权集合包含此前已消耗UID，重入只看当前事实。
    """
    rows = {r['item_id']: r for r in items}
    if len(rows) != len(items) or target.item_id in authorized_raw_ids:
        raise ValueError('库存UID重复或授权材料包含培养目标')
    current = rows.get(target.item_id)
    if (current is None or (current['base_id'], current['ware_id'], current['part']) !=
            (target.base_id, target.ware_id, target.part) or current['quality'] != 6
            or current['quantity'] != 1 or current['is_break'] is not False
            or not 1 <= current['grade'] <= 6):
        raise ValueError('初始培养仅接受明确已装备红色未突破1至6阶本体')
    if current['grade'] == 6:
        return ()
    others = [r for r in items if r['base_id'] == target.base_id and r['item_id'] != target.item_id]
    if not others:
        raise ValueError('没有可供升阶的同本体材料')
    lowest = min(r['grade'] for r in others)
    possible = [r for r in others if r['grade'] == lowest]
    if any(r['item_id'] not in authorized_raw_ids or r['grade'] != 1
           or r['quality'] != 6 or r['quantity'] != 1 or r['is_break'] is not False
           or (r['ware_id'], r['part']) != (target.ware_id, target.part) for r in possible):
        raise ValueError('自动选料的最低阶集合含未授权或非raw1材料，禁止升阶')
    return tuple(sorted(r['item_id'] for r in possible))


def verify_raw_upgrade_delta(before, after, *, target: SpiritArtifactWashTarget,
                             candidates: tuple[str, ...]) -> str:
    """单次消耗：唯一授权UID消失、目标恰好+1、其他实例完整不变。"""
    old, new = ({r['item_id']: r for r in rows} for rows in (before, after))
    removed = set(old) - set(new)
    if len(removed) != 1 or not removed <= set(candidates) or set(new) - set(old):
        raise ValueError('单次升阶没有形成唯一已授权材料消耗')
    if target.item_id not in new or new[target.item_id]['grade'] != old[target.item_id]['grade'] + 1:
        raise ValueError('目标未恰好升1阶')
    for field in ('base_id', 'ware_id', 'part', 'quality', 'quantity', 'is_break'):
        if new[target.item_id][field] != old[target.item_id][field]:
            raise ValueError('升阶改变目标品质/身份/突破状态')
    if any(row != new.get(uid) for uid, row in old.items()
           if uid != target.item_id and uid not in removed):
        raise ValueError('升阶期间其他本体发生变化，停止归因')
    return next(iter(removed))


def prepare_owned_spirit_artifact_to_six(
    context, execute, *, target: SpiritArtifactWashTarget, authorized_raw_ids: set[str],
    artifact_name: str, evidence_path: Path, stop_at: float,
) -> dict:
    """从666/667/717培养已装备红本体到6；允许安全结果页重入，未决确认拒绝。

    每颗fresh联合库存/装备+升阶UI身份，允许自动选料取授权最低阶集合任一UID。
    首屏选择用Runtime的实际index，绝不以part直接代替index；不到首屏则阻塞。
    每次确认最多发送一次；消耗后验证差量才继续下一颗，预留120秒收尾。
    不为收集材料导航，不承诺境数继承；原始境数随库存证据保留。
    """
    if target.part not in range(1, 5) or not target.base_id or stop_at <= time.time():
        raise ValueError('当前只开放1–4部位首屏升阶研发')
    path = Path(evidence_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    gui = SpiritArtifactCleanseRuntimeGuiAdapter(context, execute)

    def record(event, **fields):
        with path.open('a', encoding='utf-8') as output:
            output.write(json.dumps({'event': event, 'at': time.time(),
                'item_id': target.item_id, **fields}, ensure_ascii=False, default=str) + '\n')

    def wait(*scenes):
        result = execute(context.wait_scene(list(scenes), wait=12))
        if result.scene_id not in scenes:
            raise RuntimeError(f'初始升阶预期{scenes}，实际#{result.scene_id}')
        return result.scene_id

    def observe():
        state = read_spirit_artifact_owned_runtime([target.ware_id])
        inventory, equipped = state['inventory'], state['equipped']
        slot = [r for r in equipped['items'] if r['part'] == target.part]
        if (inventory.get('complete') is not True
                or (inventory['pid'], inventory['process_start_ticks']) != target.process_identity
                or (equipped['pid'], equipped['process_start_ticks']) != target.process_identity
                or len(slot) != 1 or slot[0]['item_id'] != target.item_id):
            raise RuntimeError('完整库存/精确装备与指定培养目标不符')
        record('owned_observation', snapshot=state)
        return inventory['items']

    def grade_identity():
        grade = read_spirit_artifact_grade_snapshot()
        if (grade['pid'], grade['process_start_ticks']) != target.process_identity or grade['ware_id'] != target.ware_id:
            raise RuntimeError('升阶页灵器/进程错配')
        return grade

    scene = wait(666, 667, 717, 720, 721, 718, 719)
    if scene in (718, 719):
        raise RuntimeError('入口有未决升阶确认，不重放')
    current = observe()
    authorized_raw_upgrade_candidates(current, target=target, authorized_raw_ids=authorized_raw_ids)
    for _ in range(12):
        if scene not in (720, 721):
            break
        context.click_shape_center(scene, '点击屏幕继续')
        scene = wait(720, 721, 717, 667, 666)
    else:
        raise RuntimeError('连续业务结果页超过上限')
    if scene == 666:
        gui.select_artifact(target.ware_id, artifact_name)
        scene = wait(667)
    if scene == 667:
        context.click_shape_center(667, '升阶页签')
        wait(717)
    ui = grade_identity()
    matches = [r for r in ui['parts'] if r['part'] == target.part and r['item_id'] == target.item_id]
    if len(matches) != 1 or matches[0]['index'] not in range(4):
        raise RuntimeError('目标不在已验证首屏4格，需要独立滚动定位验收')
    context.click_shape_center(717, f"首屏第{matches[0]['index'] + 1}格")
    deadline = time.monotonic() + 15
    while True:
        ui = grade_identity()
        if (ui['part'], ui['item_id']) == (target.part, target.item_id):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('点击后升阶目标UID未对齐')
        time.sleep(.3)
    consumed = []
    while True:
        current = observe()
        candidates = authorized_raw_upgrade_candidates(current, target=target, authorized_raw_ids=authorized_raw_ids)
        if not candidates:
            break
        if time.time() >= stop_at - 120:
            return {'status': 'paused', 'reason': 'deadline', 'consumed_item_ids': consumed}
        ui = grade_identity()
        item = next(r for r in current if r['item_id'] == target.item_id)
        if (ui['item_id'], ui['part'], ui['grade']) != (target.item_id, target.part, item['grade']):
            raise RuntimeError('升阶消费前目标身份/阶数不一致')
        record('upgrade_attempt', authorized_candidates=candidates, grade=item['grade'])
        context.click_shape_center(717, '执行升阶')
        deadline, confirmed, stable_since = time.monotonic() + 100, set(), None
        while time.monotonic() < deadline:
            scene = wait(721, 720, 719, 718, 717)
            if scene in (718, 719):
                if scene not in confirmed:
                    confirmed.add(scene)
                    context.click_shape_center(scene, '确认')
                stable_since = None
            elif scene in (720, 721):
                context.click_shape_center(scene, '点击屏幕继续')
                stable_since = None
            elif stable_since is None:
                after = observe()
                if after != current:
                    removed = verify_raw_upgrade_delta(current, after, target=target, candidates=candidates)
                    stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= 8:
                consumed.append(removed)
                record('upgrade_verified', consumed_item_id=removed)
                break
            time.sleep(.5)
        else:
            raise RuntimeError('单次升阶结果未核实，禁止再次点击升阶')
    context.click_shape_center(717, '装配')
    wait(667)
    context.click_shape_center(667, '返回')
    wait(666)
    result = {'status': 'complete', 'item_id': target.item_id, 'grade': 6,
              'consumed_item_ids': consumed, 'final_scene_id': 666}
    record('prepare_complete', **result)
    return result
