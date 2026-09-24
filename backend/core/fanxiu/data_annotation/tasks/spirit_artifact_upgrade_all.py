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


def run_spirit_artifact_safe_upgrade_round(context, execute, *, stop_at: float,
                                           max_actions: int = 100) -> dict:
    return execute(spirit_artifact_safe_upgrade_steps(
        context, stop_at=stop_at, max_actions=max_actions))


def spirit_artifact_safe_upgrade_steps(context, *, stop_at: float,
                                       max_actions: int = 100):
    """从灵器总览按实时库存逐笔升阶/悟境，并核验每笔精确消耗。

    每次重新扫描全部已装备部件，只导航到有安全材料的候选；不依赖灵器编号、
    历史红点或上次 Cell 的位置。客户端自动选料可能包含已培养备件时，
    ``scan_spirit_artifact_upgrades`` 不会放行。当前只点击已证明位于首屏的
    目标，后屏定位缺失时保留现场交由研发补齐。
    """
    from ...catalog.spirit_artifact_identity import load_spirit_artifact_templates
    from ...catalog.spirit_artifact_progression import load_spirit_artifact_progression_rules
    from ...instrumentation.spirit_artifact_grade import (
        read_spirit_artifact_grade_snapshot, read_spirit_artifact_realm_snapshot,
    )
    from ...instrumentation.runtime_memory import FanxiuRuntimeMemoryError
    from .spirit_artifact_upgrade_count import (
        open_artifact_for_upgrade_steps, select_artifact_tab_steps,
    )

    if max_actions <= 0 or stop_at <= time.time():
        raise ValueError('灵器升级需要有效动作上限与截止时间')
    rules = load_spirit_artifact_progression_rules()
    templates = load_spirit_artifact_templates()
    material_ids = {cost[0] for dimension in ('grade', 'realm')
                    for cost in rules[dimension].values() if cost}
    results = []

    def observe():
        owned = read_spirit_artifact_owned_runtime()
        counts, _ = read_backpack_item_counts(material_ids, manager_key='spirit-safe-upgrade')
        candidates = scan_spirit_artifact_upgrades(owned, counts, rules=rules)
        return owned, counts, candidates

    def read_stable_page(reader):
        """Allow a just-opened panel's Runtime pointers to finish rebinding.

        A scene image can be stable before its backing VO is.  Retry only a
        transient memory-read failure; all identity and material checks below
        still use the fresh, fully validated snapshot.
        """
        last_error = None
        for attempt in range(3):
            try:
                return reader()
            except FanxiuRuntimeMemoryError as exc:
                last_error = exc
                if attempt < 2:
                    yield from context.wait_action_settle(0.75)
        raise last_error

    for _ in range(max_actions):
        if time.time() >= stop_at:
            return {'status': 'paused', 'reason': 'deadline', 'actions': results}
        before, counts_before, candidates = observe()
        if not candidates:
            return {'status': 'complete', 'actions': results, 'final_scene_id': 666}
        candidate = candidates[0]
        if candidate.ware_id not in templates:
            raise RuntimeError(f'灵器编号缺少正式名称：{candidate.ware_id}')
        scene = (yield from context.wait_scene([666], wait=5)).scene_id
        if scene != 666:
            raise RuntimeError(f'安全升级入口要求总览 #666，实际 #{scene}')
        yield from open_artifact_for_upgrade_steps(context, templates[candidate.ware_id][0])
        tab = '升阶' if candidate.dimension == 'grade' else '升品'
        page = 717 if candidate.dimension == 'grade' else 731
        read_page = (read_spirit_artifact_grade_snapshot if candidate.dimension == 'grade'
                     else read_spirit_artifact_realm_snapshot)
        yield from select_artifact_tab_steps(context, tab)
        if (yield from context.wait_scene([page], wait=8)).scene_id != page:
            raise RuntimeError(f'{tab}页未就绪')
        ui = yield from read_stable_page(read_page)
        matches = [row for row in ui['parts'] if (row['part'], row['item_id']) ==
                   (candidate.part, candidate.item_id)]
        if len(matches) != 1 or matches[0]['index'] not in range(4):
            raise RuntimeError(f'{tab}目标不在已验证首屏四格，保留现场：{candidate}')
        if ui['item_id'] != candidate.item_id:
            context.click_shape_center(page, f"首屏第{matches[0]['index'] + 1}格")
            yield from context.wait_action_settle(.8)
        ui = yield from read_stable_page(read_page)
        before, counts_before, fresh = observe()
        exact = [item for item in fresh if (item.ware_id, item.part, item.dimension,
                 item.item_id, item.current_level) == (candidate.ware_id, candidate.part,
                 candidate.dimension, candidate.item_id, candidate.current_level)]
        if (len(exact) != 1 or ui['ware_id'] != candidate.ware_id
                or ui['part'] != candidate.part or ui['item_id'] != candidate.item_id
                or ui[candidate.dimension] != candidate.current_level
                or ui['material_id'] != candidate.cost_item_id
                or ui['can_upgrade'] is not True):
            raise RuntimeError(f'{tab}客户端准入与新鲜材料候选不一致，保留现场')
        candidate = exact[0]
        action_shape = '执行升阶' if candidate.dimension == 'grade' else '执行悟境'
        context.click_shape_center(page, action_shape)
        seen_results = set()
        after = None
        for _attempt in range(16):
            landed = (yield from context.wait_scene([page, 718, 719, 720, 721], wait=8)).scene_id
            if landed in (718, 719, 720, 721):
                if landed not in seen_results:
                    seen_results.add(landed)
                    shape = '确认' if landed in (718, 719) else '点击屏幕继续'
                    context.click_shape_center(landed, shape)
                yield from context.wait_action_settle(.8)
                continue
            after = read_spirit_artifact_owned_runtime()
            if any(row['item_id'] == candidate.item_id and
                   row[candidate.dimension] == candidate.next_level
                   for row in after['inventory']['items']):
                break
            yield from context.wait_action_settle(.8)
        if after is None:
            raise RuntimeError(f'{tab}动作后未返回业务页，保留现场')
        counts_after, _ = read_backpack_item_counts(
            {candidate.cost_item_id}, manager_key='spirit-safe-upgrade-after')
        removed = verify_spirit_artifact_upgrade_delta(
            before, after, candidate=candidate,
            counts_before=counts_before, counts_after=counts_after)
        results.append({'ware_id': candidate.ware_id, 'part': candidate.part,
                        'dimension': candidate.dimension, 'from': candidate.current_level,
                        'to': candidate.next_level, 'consumed_item_ids': removed})
        yield from select_artifact_tab_steps(context, '装配')
        yield from context.wait_scene([667], wait=8)
        context.click_shape_center(667, '背景返回封面')
        yield from context.wait_scene([666], wait=10)
    return {'status': 'paused', 'reason': 'action_limit', 'actions': results}
