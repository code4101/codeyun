"""装配已授权红raw集合中的首个候选；不采购、不消耗、不继承旧属性。

716首屏第二行与729不继承已获2-3真实现场；行位置仍以当前Index=0、
有序数据首项为当前装备及次项授权UID为门禁，不把池实例顺序当屏幕顺序。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter
from ...instrumentation.spirit_artifact_equipped import read_spirit_artifact_owned_runtime
from ...instrumentation.spirit_artifact_replacement import read_spirit_artifact_replacement_snapshot
from ...instrumentation.spirit_artifact_wash_observation import SpiritArtifactWashTarget


def equip_owned_spirit_artifact_raw(
    context, execute, *, target: SpiritArtifactWashTarget,
    authorized_raw_ids: set[str], expected_previous_item_id: str,
    artifact_name: str, evidence_path: Path, stop_at: float,
) -> dict:
    """从666/667装配一个授权raw；已装配集合内raw直接幂等返回。

    target.item_id是允许集合的一个代表，并非强制选择该UID。结果item_id为
    真实装备UID，供后续构造培养target。未决729入口不推断此前选择或重放。
    首屏必须当前装备在index0、授权raw在index1；不满足则无点击失败。
    """
    allowed = set(authorized_raw_ids)
    if (not allowed or target.item_id not in allowed or not target.base_id
            or not expected_previous_item_id or stop_at <= time.time()):
        raise ValueError('装配需要明确本体、旧装备、授权raw集合与有效窗口')
    path = Path(evidence_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def record(event, **fields):
        with path.open('a', encoding='utf-8') as out:
            out.write(json.dumps(dict(event=event, at=time.time(), **fields),
                                 ensure_ascii=False, default=str) + '\n')

    def observe():
        state = read_spirit_artifact_owned_runtime([target.ware_id])
        inventory, equipped = state['inventory'], state['equipped']
        if inventory.get('complete') is not True or any(
                (s['pid'], s['process_start_ticks']) != target.process_identity
                for s in (inventory, equipped)):
            raise RuntimeError('装配完整库存/进程身份不符')
        rows = {r['item_id']: r for r in inventory['items']}
        if len(rows) != len(inventory['items']):
            raise RuntimeError('库存UID重复')
        for uid in allowed:
            r = rows.get(uid)
            if (not r or (r['ware_id'], r['part'], r['base_id']) !=
                    (target.ware_id, target.part, target.base_id) or r['quality'] != 6
                    or r['quantity'] != 1 or r['grade'] != 1 or r['is_break'] is not False):
                raise RuntimeError('授权集合不是完整在库同部位红raw1')
        slots = [r for r in equipped['items'] if r['ware_id'] == target.ware_id and r['part'] == target.part]
        if len(slots) != 1 or slots[0]['item_id'] not in allowed | {expected_previous_item_id}:
            raise RuntimeError('当前装备不是明确旧件或授权raw')
        record('owned', snapshot=state)
        return rows, slots[0]['item_id']

    def wait(*scenes):
        match = execute(context.wait_scene(list(scenes), wait=12))
        if match.scene_id not in scenes:
            raise RuntimeError(f'装配要求{scenes}，实际#{match.scene_id}')
        return match.scene_id

    def click(scene, shape):
        if time.time() >= stop_at:
            raise RuntimeError('装配窗口已截止，保留现场')
        record('click_attempt', scene=scene, shape=shape)
        context.click_shape_center(scene, shape)

    try:
        before, equipped_id = observe()
        if equipped_id in allowed:
            return dict(status='already_equipped', item_id=equipped_id, item=before[equipped_id])
        scene = wait(666, 667)
        gui = SpiritArtifactCleanseRuntimeGuiAdapter(context, execute)
        if scene == 666:
            gui.select_artifact(target.ware_id, artifact_name)
        # 已在667也走公共identity校验，避免点到另一灵器。
        from ...instrumentation.spirit_artifact_ui_identity import read_spirit_artifact_ui_identity
        identity = read_spirit_artifact_ui_identity(window_kind='view')
        if ((identity['pid'], identity['process_start_ticks']) != target.process_identity
                or identity['ware_id'] != target.ware_id):
            raise RuntimeError('装配页灵器/进程不符')
        click(667, f'部位{target.part}')
        wait(715)
        click(715, '更换')
        wait(716)
        listing = read_spirit_artifact_replacement_snapshot(ware_id=target.ware_id, part=target.part)
        order = listing.get('ordered_items', [])
        if ((listing['pid'], listing['process_start_ticks']) != target.process_identity
                or listing.get('data_complete') is not True
                or listing.get('scroll_fields', {}).get('Index', {}).get('value') != 0
                or len(order) < 2 or order[0] != {'index': 0, 'item_id': expected_previous_item_id}
                or order[1].get('index') != 1 or order[1].get('item_id') not in allowed):
            raise RuntimeError('更换列表首屏有序身份不足，禁止猜选第二项')
        chosen = order[1]['item_id']
        fresh, old_uid = observe()
        if fresh != before or old_uid != expected_previous_item_id:
            raise RuntimeError('选择前库存或装备改变')
        record('candidate_verified', item_id=chosen)
        click(716, '第2个本体')
        if wait(729, 667) == 729:
            click(729, '不继承')
            wait(667)
        after, new_uid = observe()
        if after != before or new_uid != chosen:
            raise RuntimeError('装备后未得到选定raw或库存发生变化，禁止重复点击')
        result = dict(status='equipped', item_id=new_uid, item=after[new_uid], final_scene=667)
        record('complete', **result)
        return result
    except Exception as exc:
        record('stopped', error_type=type(exc).__name__, error=str(exc))
        raise
