"""升阶/升境配置与全量库存扫描；与兑换来源及洗炼阶段无关。"""
from dataclasses import dataclass
from pathlib import Path
import re

from .resources import resolve_fanxiu_export_root, FanxiuResourceError
from .lua_config import parse_fanxiu_generated_lua_config
from .spirit_artifact_wash_rules import load_spirit_artifact_wash_rules


@dataclass(frozen=True)
class SpiritArtifactUpgradeCandidate:
    item_id: str
    ware_id: int
    part: int
    base_id: int
    dimension: str
    current_level: int
    next_level: int
    cost_item_id: int
    cost_quantity: int
    available: int
    material_item_ids: tuple[str, ...] = ()


def load_spirit_artifact_progression_rules(*, export_root=None) -> dict:
    """读取正式导出的逐级消耗，不猜一件道具等于一境或最大阶数。"""
    root = Path(export_root or resolve_fanxiu_export_root())
    result = {}
    for dimension, name in [('grade', 'SpiritWareBase'), ('realm', 'SpiritWareUltra')]:
        paths = list((root / 'by_source/lscripts/generate/cfg').glob(f'spiritware_*/text_assets/{name}.lua'))
        if len(paths) != 1:
            raise FanxiuResourceError(f'{name} 导出不唯一')
        rows = parse_fanxiu_generated_lua_config(paths[0])['rows']
        indexed = {}
        for row in rows:
            key = (row['partsItem'], row['grade'])
            if key in indexed:
                raise FanxiuResourceError(f'{name} 等级配置重复')
            cost = row.get('consume')
            match = re.fullmatch(r'Item\|([1-9][0-9]*)_([1-9][0-9]*)', cost or '')
            if cost and match is None:
                raise FanxiuResourceError(f'{name} 不支持的消耗格式: {cost}')
            indexed[key] = (int(match[1]), int(match[2])) if match else None
        result[dimension] = indexed
    result['items'] = load_spirit_artifact_wash_rules(export_root=root)['items_by_base_id']
    return result


def scan_spirit_artifact_upgrades(owned, item_counts, *, rules) -> tuple[SpiritArtifactUpgradeCandidate, ...]:
    """扫描全部已装备部件的下一步可升级项，不接受兑换目标过滤器。

    owned 是 read_spirit_artifact_owned_runtime 的完整结果；item_counts 为同轮
    背包材料数量。结果只是候选，GUI 执行前必须重新校验当前目标与客户端准入。
    本体升阶仅将未装备、未突破、红色1阶原始本体作为材料；有培养的备件不能
    在全量升级中被默默吃掉。更换旧本体消费由明确的错升重置入口单独负责。
    """
    inventory, equipped = owned['inventory'], owned['equipped']
    identity = (inventory.get('pid'), inventory.get('process_start_ticks'))
    if (inventory.get('complete') is not True or equipped.get('complete') is not True or not all(identity)
            or (equipped.get('pid'), equipped.get('process_start_ticks')) != identity):
        raise ValueError('全量升级扫描需要同进程完整装备/库存')
    rows = {r['item_id']: r for r in inventory['items']}
    if len(rows) != len(inventory['items']):
        raise ValueError('库存实例重复')
    equipped_ids = {r['item_id'] for r in equipped['items'] if r.get('item_id')}
    result = []
    for slot in equipped['items']:
        uid = slot.get('item_id')
        if not uid:
            continue
        current = rows.get(uid)
        if current is None:
            raise ValueError('装备引用不存在于库存')
        base = current['base_id']
        for dimension in ('grade', 'realm'):
            level = current.get(dimension)
            if type(level) is not int or level < 0:
                raise ValueError('当前阶/境未识别')
            if dimension == 'realm' and rules['items'][base].get('canUltra') != 1:
                continue
            cost = rules[dimension].get((base, level + 1))
            if cost is None:
                continue
            cost_id, quantity = cost
            materials = ()
            if cost_id in rules['items']:
                possible = [r for r in inventory['items'] if r['base_id'] == cost_id
                            and r['item_id'] not in equipped_ids]
                # Client auto-selection among same-base items must not choose a trained spare.
                safe = [r for r in possible if r['quality'] == 6 and r['grade'] == 1
                        and r['is_break'] is False and r.get('realm') == 0 and r['quantity'] == 1]
                if possible and (not safe or any(r['grade'] == 1 and r not in safe for r in possible)):
                    continue
                materials = tuple(sorted(r['item_id'] for r in safe))
                available = len(materials)
            else:
                available = item_counts.get(cost_id, 0)
                if type(available) is not int or available < 0:
                    raise ValueError('材料数量未核实')
            if available >= quantity:
                result.append(SpiritArtifactUpgradeCandidate(uid, current['ware_id'], current['part'],
                    base, dimension, level, level + 1, cost_id, quantity, available, materials))
    return tuple(sorted(result, key=lambda c: (c.ware_id, c.part, c.dimension)))
