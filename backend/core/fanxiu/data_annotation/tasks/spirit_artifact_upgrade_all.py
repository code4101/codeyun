"""全量升级的单步执行：消费当前客户端选中的可升级部件，独立于兑换清单。

升阶/升品页首次进入会自动选中 GetUpgradeRed/GetUpColorRed 的首个部件；
调用方重进页签即可找下一个，不必按首屏位置猜第5/6部位。所有消费仍按
精确装备UID、配置费用、完整库存和前后差量验证。尚待全量真实验收。
"""
from dataclasses import asdict
import json
from pathlib import Path
import time

from ...catalog.spirit_artifact_progression import scan_spirit_artifact_upgrades
from ...instrumentation.spirit_artifact_grade import read_spirit_artifact_progression_owned_snapshot
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


def upgrade_current_spirit_artifact(context, execute, *, dimension, scene_id, action_shape,
                                  rules, evidence_dir: Path, stop_at: float):
    """从已加载升级页核验当前自动选中的部件，只升级一次；无材料返回不可执行。

    所有场景/Shape由调用方提供正式资产；本函数不创建标注或盲点位置。
    业务确认属于Layer0，每个确认页最多点一次。未决调用证据阻止重放。
    """
    if dimension not in ('grade', 'realm'):
        raise ValueError('只支持升阶/升境')
    if time.time() >= stop_at - 90:
        return {'status': 'paused'}
    if execute(context.wait_scene([scene_id], wait=8)).scene_id != scene_id:
        raise RuntimeError('当前不是升级页')
    observation = read_spirit_artifact_progression_owned_snapshot(dimension=dimension)
    ui, before = observation[dimension], observation['owned']
    cost_ids = {cost[0] for cost in rules[dimension].values() if cost}
    counts_before, evidence = read_backpack_item_counts(cost_ids, manager_key='spirit-all-upgrade')
    identity = (ui['pid'], ui['process_start_ticks'])
    if (evidence['pid'], evidence['process_start_ticks']) != identity:
        raise RuntimeError('材料数量与升级页面不属于同一进程')
    candidates = scan_spirit_artifact_upgrades(before, counts_before, rules=rules)
    chosen = [c for c in candidates if c.dimension == dimension and c.item_id == ui['item_id']]
    if not chosen:
        return {'status': 'unavailable', 'item_id': ui['item_id'],
                'other_candidates': [asdict(c) for c in candidates if c.dimension == dimension]}
    candidate = chosen[0]
    if ui['can_upgrade'] is not True or ui['material_id'] != candidate.cost_item_id:
        raise RuntimeError('客户端升级准入或材料与规划不一致')
    root = Path(evidence_dir)
    root.mkdir(parents=True, exist_ok=True)
    key = f'{candidate.item_id}-{dimension}-{candidate.current_level}'
    marker = root / f'{key}-intent.json'
    if marker.exists():
        raise RuntimeError(f'该级升级已有未决记录，禁止重放: {marker}')
    context.shape(scene_id, action_shape)
    marker.write_text(json.dumps(dict(candidate=asdict(candidate), before=before,
        counts_before=counts_before, count_evidence=evidence), ensure_ascii=False, default=str), encoding='utf-8')
    context.click_shape_center(scene_id, action_shape)
    confirmed, result_pages = set(), set()
    deadline = min(stop_at, time.time() + 100)
    # Raw-body upgrades have already been observed returning to the wash
    # page. Verify the same inventory delta before leaving that landing.
    stable_pages = {scene_id, *([668, 714] if dimension == 'grade' else [])}
    while time.time() < deadline:
        landed = execute(context.wait_scene([*sorted(stable_pages), 718, 719, 720, 721], wait=3)).scene_id
        if landed in (718, 719):
            if landed not in confirmed:
                confirmed.add(landed)
                context.click_shape_center(landed, '确认')
        elif landed in (720, 721):
            if landed not in result_pages:
                result_pages.add(landed)
                context.click_shape_center(landed, '点击屏幕继续')
        elif landed in stable_pages:
            after = read_spirit_artifact_owned_runtime([candidate.ware_id])
            current = next(r for r in after['inventory']['items'] if r['item_id'] == candidate.item_id)
            if current[dimension] == candidate.current_level:
                time.sleep(.5)
                continue
            counts_after, after_evidence = read_backpack_item_counts([candidate.cost_item_id], manager_key='spirit-all-upgrade')
            if (after_evidence['pid'], after_evidence['process_start_ticks']) != identity:
                raise RuntimeError('升级后材料数量进程改变')
            removed = verify_spirit_artifact_upgrade_delta(before, after, candidate=candidate,
                counts_before=counts_before, counts_after=counts_after)
            result = dict(status='complete', candidate=asdict(candidate), removed_item_ids=removed,
                          after=after, counts_after=counts_after, final_scene_id=landed)
            (root / f'{key}-receipt.json').write_text(json.dumps(result, ensure_ascii=False, default=str),encoding='utf-8')
            return result
        else:
            raise RuntimeError(f'升级落入未处理场景#{landed}，保留现场')
    raise RuntimeError('升级后未确认消费，禁止再次点击')


def run_spirit_artifact_all_upgrades(context, execute, *, artifacts, rules,
                                    evidence_dir: Path, stop_at: float, realm_assets=None):
    """逐灵器全量扫描升阶、升境，来源无关；每次升级后重新进入页签让游戏选目标。

    artifacts 必须覆盖全部配置灵器，元素为 (ware_id, name)。realm_assets 为
    已验收的 (scene_id, 装配页升品页签Shape, 执行升境Shape, 返回装配Shape)。
    缺升境资产时明确返回 blocked，不能把仅升阶称为全量完成。不会开启洗炼或突破。
    """
    from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter
    from ...catalog.spirit_artifact_wash_rules import spirit_artifact_ware_ids
    from ...instrumentation.spirit_artifact_collector import collect_spirit_artifact_snapshot_once
    from ...instrumentation.spirit_artifact_ui_identity import read_spirit_artifact_ui_identity
    artifacts = tuple(artifacts)
    if len(artifacts) != len({w for w, _ in artifacts}) or {w for w, _ in artifacts} != set(spirit_artifact_ware_ids()):
        raise ValueError('全量升级必须包含全部灵器，不接受兑换目标子集')
    gui = SpiritArtifactCleanseRuntimeGuiAdapter(context, execute)
    results = []
    all_owned = read_spirit_artifact_owned_runtime()
    all_cost_ids = {cost[0] for dimension in ('grade', 'realm')
                    for cost in rules[dimension].values() if cost}
    all_counts, count_identity = read_backpack_item_counts(all_cost_ids, manager_key='spirit-all-upgrade')
    if (count_identity['pid'], count_identity['process_start_ticks']) != (
            all_owned['inventory']['pid'], all_owned['inventory']['process_start_ticks']):
        raise RuntimeError('全量材料与装备进程不一致')
    initial_candidates = scan_spirit_artifact_upgrades(all_owned, all_counts, rules=rules)
    actionable = {(c.ware_id, c.dimension) for c in initial_candidates}
    for ware, name in sorted(artifacts):
        if not any(w == ware for w, _ in actionable):
            continue
        if time.time() >= stop_at - 120:
            return dict(status='paused', upgrades=results)
        match = execute(context.wait_scene([666], wait=8))
        if match.scene_id != 666:
            raise RuntimeError('全量升级从洗灵封面交接')
        gui.select_artifact(ware, name)
        for dimension, assets in [('grade', (717, '升阶页签', '执行升阶', '装配')),
                                  ('realm', realm_assets)]:
            if assets is None or (ware, dimension) not in actionable:
                continue
            page, tab_shape, action, return_shape = assets
            while time.time() < stop_at - 120:
                if execute(context.wait_scene([667], wait=8)).scene_id != 667:
                    raise RuntimeError('升级页签切换要求装配页')
                identity = read_spirit_artifact_ui_identity(window_kind='view')
                if identity['ware_id'] != ware:
                    raise RuntimeError('当前灵器改变')
                execute(context.wait_click(667, tab_shape))
                if execute(context.wait_scene([page], wait=8)).scene_id != page:
                    raise RuntimeError('升级页未就绪')
                result = upgrade_current_spirit_artifact(context, execute, dimension=dimension,
                    scene_id=page, action_shape=action, rules=rules, evidence_dir=evidence_dir,
                    stop_at=stop_at)
                execute(context.wait_click(result.get('final_scene_id', page), return_shape))
                if result['status'] == 'complete':
                    results.append({k:v for k,v in result.items() if k != 'after'})
                    collect_spirit_artifact_snapshot_once()
                    continue
                if result['status'] == 'unavailable' and result.get('other_candidates'):
                    raise RuntimeError('客户端默认目标与全量可升级集合不符，需修定位')
                break
        execute(context.wait_click(667, '返回'))
        if execute(context.wait_scene([666], wait=8)).scene_id != 666:
            raise RuntimeError('升级后未返回洗灵封面')
    hall = collect_spirit_artifact_snapshot_once()
    final_owned = read_spirit_artifact_owned_runtime()
    final_counts, final_count_identity = read_backpack_item_counts(all_cost_ids, manager_key='spirit-all-upgrade')
    if (final_count_identity['pid'], final_count_identity['process_start_ticks']) != (
            final_owned['inventory']['pid'], final_owned['inventory']['process_start_ticks']):
        raise RuntimeError('收尾全量材料与装备进程不一致')
    remaining = scan_spirit_artifact_upgrades(final_owned, final_counts, rules=rules)
    missing_realm_assets = realm_assets is None and any(c.dimension == 'realm' for c in remaining)
    return dict(status='blocked' if missing_realm_assets else 'paused' if remaining else 'complete',
                reason='升境资产尚待实际验收' if missing_realm_assets else '', upgrades=results,
                remaining=[asdict(c) for c in remaining],
                hall_updated_at=hall.get('updated_at'), final_scene_id=666)
