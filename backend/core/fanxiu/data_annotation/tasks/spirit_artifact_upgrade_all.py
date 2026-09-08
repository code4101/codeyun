"""一次筛选待处理灵器，GUI 批量升阶与悟境；独立于兑换来源。"""
import json
from pathlib import Path
import time

from ...catalog.spirit_artifact_progression import scan_spirit_artifact_upgrades
from ...instrumentation.spirit_artifact_equipped import read_spirit_artifact_owned_runtime
from ...instrumentation.backpack import read_backpack_item_counts


def verify_spirit_artifact_upgrade_delta(before, after, *, candidate, counts_before, counts_after):
    """一次升级恰好+1，仅扣明确材料，其余本体完整不变。"""
    identity = (before['inventory']['pid'], before['inventory']['process_start_ticks'])
    if (after['inventory'].get('complete') is not True or
            (after['inventory']['pid'], after['inventory']['process_start_ticks']) != identity or
            (after['equipped']['pid'], after['equipped']['process_start_ticks']) != identity):
        raise ValueError('升级后库存/装备不完整或进程改变')
    old, new = ({r['item_id']: r for r in state['inventory']['items']} for state in (before, after))
    uid = candidate.item_id
    if uid not in new or new[uid][candidate.dimension] != candidate.next_level:
        raise ValueError('目标未恰好升级一级')
    expected = dict(old[uid], **{candidate.dimension: candidate.next_level})
    if new[uid] != expected:
        raise ValueError('升级改变了其他目标属性')
    slots = [r for r in after['equipped']['items']
             if (r['ware_id'], r['part']) == (candidate.ware_id, candidate.part)]
    if len(slots) != 1 or slots[0]['item_id'] != uid:
        raise ValueError('升级后装备本体改变')
    removed = set(old) - set(new)
    if set(new) - set(old):
        raise ValueError('升级期间出现额外本体，无法唯一归因')
    if candidate.material_item_ids:
        if len(removed) != candidate.cost_quantity or not removed <= set(candidate.material_item_ids):
            raise ValueError('升阶消耗了非目标原始本体')
    elif removed or counts_before[candidate.cost_item_id] - counts_after[candidate.cost_item_id] != candidate.cost_quantity:
        raise ValueError('升境材料扣除与费用不符')
    if any(row != new.get(key) for key, row in old.items() if key != uid and key not in removed):
        raise ValueError('升级期间其他本体变化')
    return tuple(sorted(removed))


def run_spirit_artifact_all_upgrades(context, execute, *, artifacts, rules,
                                    evidence_dir: Path, stop_at: float,
                                    candidate_ware_ids=None, refresh=True):
    """封面入口：一次筛选，逐灵器升阶→悟境，最后统一更新总览。

    candidate_ware_ids 可由红点或既有 Runtime 观察提供；None 时只扫描一轮
    Runtime。过滤只减少导航，是否有材料由新切入页签后的 OCR 分子决定。
    不核对逐次扣料，不保存内部续跑位置；下次从当前事实重新筛选。
    """
    from ...catalog.spirit_artifact_wash_rules import spirit_artifact_ware_ids
    from ...instrumentation.spirit_artifact_collector import collect_spirit_artifact_snapshot_once
    from .spirit_artifact_upgrade_count import (
        open_artifact_for_upgrade, finish_visible_artifact,
    )
    artifacts = tuple(artifacts)
    ids = [ware for ware, _ in artifacts]
    if len(ids) != len(set(ids)) or set(ids) != set(spirit_artifact_ware_ids()):
        raise ValueError('全量升级入口必须提供全部配置灵器')
    if candidate_ware_ids is None:
        owned = read_spirit_artifact_owned_runtime()
        costs = {cost[0] for dimension in ('grade', 'realm')
                 for cost in rules[dimension].values() if cost}
        counts, _ = read_backpack_item_counts(costs, manager_key='spirit-all-upgrade')
        candidate_ware_ids = {c.ware_id for c in scan_spirit_artifact_upgrades(
            owned, counts, rules=rules)}
    candidates = set(candidate_ware_ids)
    if not candidates <= set(ids):
        raise ValueError('候选包含未知灵器')
    results = []
    pending = sorted(candidates)
    for ware, name in sorted(artifacts):
        if ware not in candidates:
            continue
        if time.time() >= stop_at:
            break
        open_artifact_for_upgrade(context, execute, name)
        result = finish_visible_artifact(context, execute)
        results.append({'ware_id': ware, 'result': result})
        pending.remove(ware)
        context.click_shape_center(667, '背景返回封面')
        execute(context.wait_scene([666], wait=5))
    report = {'status': 'paused' if pending else 'complete', 'upgrades': results,
              'pending_ware_ids': pending, 'final_scene_id': 666}
    if refresh:
        hall = collect_spirit_artifact_snapshot_once()
        report['hall_updated_at'] = hall.get('updated_at')
    root = Path(evidence_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / 'last-round.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
    return report
