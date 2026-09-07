"""已授权 A 类培养的连续研发入口；遇到未知状态停止，不重放消耗。

从已选定洗炼页开始，在一个 Cell 内复用滚动经验。规则由已核实的静态
配置注入；日志逐次记录实际消耗，与可调整的停止门槛分离。尚待真实验收。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter
from .spirit_artifact_yinxian import YinxianAttribute, analyze_yinxian_sample, plan_a_collection, plan_b_supplement
from ...instrumentation.spirit_artifact_wash_observation import (
    SpiritArtifactWashTarget, read_spirit_artifact_wash_observation,
)


def run_a_collection(
    context, execute, *, target: SpiritArtifactWashTarget, rules: dict,
    a_codes: set[str], b_codes: set[str], c_codes: set[str],
    evidence_path: Path, stop_at: float, target_ratio: float = .90,
    max_consumptions: int = 100,
    fast_observation: bool = False,
    supplement_b_code: str | None = None,
    evaluate_existing_candidate: bool = False,
    scroll_profile=None,
) -> dict:
    """调用即授权在指定目标上切锁、消耗引仙/精炼石、采用达标候选。

    stop_at 是生产窗口前的绝对截止秒；每次消耗前预留 90 秒收尾。
    max_consumptions 只限制本次运行，不修改概率样本或业务完成条件。
    不负责导航、突破或调度。默认拒绝入口候选；evaluate_existing_candidate=True
    仅授权用当前完整事实重新评价 A／补 B 筛选候选，不恢复历史步骤。
    当前策略不是引仙筛选（例如需要精炼或重新切锁）时仍阻塞，不猜候选来源。
    既存候选评价只记观察事件，不重复统计历史消耗或概率样本。
    supplement_b_code 指定 A 全满后的独立补 B 阶段：仅筛红色，不精炼。
    """
    from ...instrumentation.backpack import read_backpack_item_counts

    if max_consumptions <= 0 or stop_at <= time.time():
        raise ValueError('连续洗灵需要有效期限及消耗上限')
    gui = SpiritArtifactCleanseRuntimeGuiAdapter(context, execute)
    rules = dict(rules)
    evidence_path = Path(evidence_path)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    current = read_spirit_artifact_wash_observation(target, verify_ui=True)
    if current.get('is_break') is not False:
        raise RuntimeError('A 类培养入口须确认本体尚未突破；已突破或状态未知时停止')
    if current['pending_effects'] and not evaluate_existing_candidate:
        raise RuntimeError('入口存在未处理候选；需显式 evaluate_existing_candidate=True 才可重新评价')

    def attributes(effects):
        missing = sorted({e['cleanse_id'] for e in effects} - rules.keys())
        if missing:
            from ...instrumentation.spirit_artifact_affixes import read_spirit_artifact_affix_rules
            fetched = read_spirit_artifact_affix_rules(missing)
            if (fetched['pid'], fetched['process_start_ticks']) != target.process_identity:
                raise RuntimeError('补齐词条配置时游戏进程变化')
            rules.update(fetched['rules'])
        return tuple(YinxianAttribute(
            e['cleanse_id'], rules[e['cleanse_id']]['code'], e['value'],
            e['quality'], e['locked'], int(rules[e['cleanse_id']]['max']),
            150 if target.part in (5, 6) else 100) for e in effects)

    def effect_map(effects):
        return {e['cleanse_id']: (e['value'], e['quality'], e['locked']) for e in effects}

    def save_pending(snapshot):
        locked = {k: v for k, v in effect_map(snapshot['effects']).items() if v[2]}
        pending = effect_map(snapshot['pending_effects'])
        if any(pending.get(k) != v for k, v in locked.items()):
            raise RuntimeError('候选改变已锁属性，停止采用')
        context.click_shape_center(714, '保留新属性')
        gui.finish_effect_activation()
        saved = read_spirit_artifact_wash_observation(target)
        if saved['pending_effects'] or effect_map(saved['effects']) != pending:
            raise RuntimeError('采用候选后实际属性未一致，停止')
        record({'record_type': 'attributes_saved', 'probability_sample': False,
                'new_consumption': False, 'before': snapshot, 'after': saved})
        return saved

    def record(data):
        with evidence_path.open('a', encoding='utf-8') as output:
            output.write(json.dumps({'recorded_at': time.time(), 'item_id': target.item_id,
                                      'target_ratio': target_ratio,
                                      'unlocked_count': sum(not e['locked'] for e in current['effects']),
                                      'probability_sample': False,
                                      **data}, ensure_ascii=False, default=str) + '\n')

    if current['pending_effects']:
        execute(context.wait_scene([714], wait=12))
        original = attributes(current['effects'])
        pending = attributes(current['pending_effects'])
        entry_plan = (plan_b_supplement(original, a_codes=a_codes, target_code=supplement_b_code)
                      if supplement_b_code is not None else
                      plan_a_collection(original, a_codes=a_codes, b_codes=b_codes,
                                        c_codes=c_codes, target_ratio=target_ratio))
        try:
            accept_existing = evaluate_existing_yinxian_candidate(
                original, pending, plan_action=entry_plan.action,
                a_codes=a_codes, target_ratio=target_ratio, supplement_b_code=supplement_b_code)
        except ValueError as error:
            record({'record_type': 'existing_candidate_evaluation', 'sample_origin': 'entry_observation',
                    'probability_sample': False, 'new_consumption': False,
                    'decision': 'blocked', 'reason': str(error), 'snapshot': current})
            raise RuntimeError('既存候选无法按当前引仙策略安全评价；保留现场') from error
        record({'record_type': 'existing_candidate_evaluation', 'sample_origin': 'entry_observation',
                'probability_sample': False, 'new_consumption': False,
                'decision': 'accept' if accept_existing else 'continue_yinxian', 'snapshot': current})
        current = save_pending(current) if accept_existing else current

    consumed = 0
    with gui.advanced_scroll_session(f'a-collection-{time.time_ns()}', scroll_profile=scroll_profile):
        while True:
            # 完整业务候选优先于全局相似结果页，活动干扰走既有守护。
            # #713 仍由显式业务确认处理，不交给通用弹窗守护。
            landed = execute(context.wait_scene([721, 668, 714, 712], wait=12))
            if landed.scene_id == 721:
                gui.finish_effect_activation()
            if supplement_b_code is not None:
                plan = plan_b_supplement(attributes(current['effects']), a_codes=a_codes,
                                         target_code=supplement_b_code)
            else:
                plan = plan_a_collection(attributes(current['effects']), a_codes=a_codes,
                                         b_codes=b_codes, c_codes=c_codes, target_ratio=target_ratio)
            if plan.action == 'complete':
                return {'status': 'complete', 'consumed': consumed, 'snapshot': current,
                        'elapsed_seconds': time.monotonic() - started}
            if plan.action == 'blocked':
                raise RuntimeError(plan.reason)
            if time.time() >= stop_at - 90 or consumed >= max_consumptions:
                return {'status': 'paused', 'consumed': consumed, 'snapshot': current,
                        'elapsed_seconds': time.monotonic() - started}
            if plan.action == 'locks':
                if current['pending_effects']:
                    raise RuntimeError('切锁前仍有候选，不丢弃未知待采用结果')
                before_locks = current
                for effect in sorted(current['effects'], key=lambda e: e['cleanse_id'] in plan.desired_lock_ids):
                    desired = effect['cleanse_id'] in plan.desired_lock_ids
                    if effect['locked'] != desired:
                        gui.set_lock(effect['cleanse_id'], desired)
                current = read_spirit_artifact_wash_observation(target)
                if {e['cleanse_id'] for e in current['effects'] if e['locked']} != set(plan.desired_lock_ids):
                    raise RuntimeError('切锁后最终锁状态与计划不一致，停止')
                record({'record_type': 'locks_updated', 'probability_sample': False,
                        'new_consumption': False, 'before': before_locks, 'after': current,
                        'desired_lock_ids': sorted(plan.desired_lock_ids)})
                continue
            material = 14000006 if plan.action == 'yinxian' else 14000007
            iteration_started = time.monotonic()
            # preview 校验当前实例、库存、未锁项、道具确认文案；窗口路线在本程序内复用。
            preview = gui.preview_advanced_item(material, fast_observation=fast_observation)
            if preview['target_item_id'] != target.item_id:
                raise RuntimeError('使用道具确认目标与培养目标不一致')
            preview_seconds = time.monotonic() - iteration_started
            if time.time() >= stop_at - 30:
                gui.cancel()
                gui.cancel()
                return {'status': 'paused', 'consumed': consumed, 'snapshot': current,
                        'elapsed_seconds': time.monotonic() - started}
            after = None
            try:
                context.click_shape_center(713, '确认使用道具')
                execute(context.wait_scene([714], wait=12))
                after = read_spirit_artifact_wash_observation(target)
                counts, inventory = read_backpack_item_counts([material], manager_key='spirit-artifact-advanced')
            except Exception as error:
                # 确认已尝试，动作/读取失败不表示没有消耗，也不能算已确认抽样。
                record({'record_type': 'unverified_consumption', 'material_id': material,
                        'before': current, 'after': after,
                        'inventory_before': preview['count'], 'inventory_after': None,
                        'consumption_verified': False, 'verification_error': repr(error),
                        'preview_seconds': preview_seconds,
                        'elapsed_seconds': time.monotonic() - iteration_started})
                raise RuntimeError('道具确认后观察失败；已记录未核实消耗，禁止自动重试') from error
            consumed += 1
            consumption_verified = (
                (inventory['pid'], inventory['process_start_ticks']) == target.process_identity
                and preview['count'] - counts[material] == 1
                and effect_map(after['effects']) == effect_map(current['effects'])
                and len(after['pending_effects']) == 6)
            # 即使后置验证失败，也保留原始观察；不靠候选内容变化断言独立消耗。
            try:
                raw_sample = attributes(after['pending_effects'])
            except Exception as error:
                # 未知词条不能使已消耗的一次样本从统计证据中消失。
                record({'material_id': material, 'before': current, 'after': after,
                        'consumption_verified': consumption_verified,
                        'inventory_before': preview['count'], 'inventory_after': counts[material],
                        'normalization_error': str(error),
                        'preview_seconds': preview_seconds,
                        'elapsed_seconds': time.monotonic() - iteration_started})
                raise RuntimeError('已保留原始候选，归一化配置不完整；停止且不重放消耗') from error
            highest_red_ratio = max((e.ratio for e in raw_sample if not e.locked and e.quality >= 6),
                                    default=None)
            record({'material_id': material, 'before': current, 'after': after,
                    'record_type': 'consumption', 'sample_origin': 'current_invocation_consumption',
                    'probability_sample': consumption_verified and material == 14000006,
                    'consumption_verified': consumption_verified,
                    'inventory_before': preview['count'], 'inventory_after': counts[material],
                    'highest_red_ratio': highest_red_ratio,
                    'unlocked_candidates': [dict(cleanse_id=e.cleanse_id, code=e.code,
                        value=e.value, quality=e.quality, normal_max=e.normal_max)
                        for e in raw_sample if not e.locked],
                    'preview_seconds': preview_seconds,
                    'preview_timings': preview.get('timings', {}),
                    'inventory_diagnostics': preview.get('inventory_diagnostics', {}),
                    'elapsed_seconds': time.monotonic() - iteration_started})
            if not consumption_verified:
                raise RuntimeError('道具消耗或候选后置验证失败，禁止重试确认')
            if plan.action == 'yinxian':
                done = {e.code for e in attributes(current['effects']) if e.is_full and e.quality >= 6}
                sample = analyze_yinxian_sample(raw_sample,
                    roll_index=consumed, needed_a_codes=a_codes - done, target_ratio=target_ratio)
                accepted = (any(not e.locked and e.code == supplement_b_code and e.quality >= 6
                                for e in raw_sample) if supplement_b_code is not None else sample.stop_yinxian)
                current = save_pending(after) if accepted else after
            else:
                old = next(e for e in attributes(current['effects']) if e.cleanse_id == plan.target_cleanse_id)
                new = [e for e in attributes(after['pending_effects']) if not e.locked]
                if len(new) != 1 or new[0].code != old.code or new[0].value <= old.value:
                    raise RuntimeError('精炼未形成目标数值提升，停止检查')
                current = save_pending(after)


def evaluate_existing_yinxian_candidate(
    current, pending, *, plan_action: str, a_codes: set[str], target_ratio: float,
    supplement_b_code: str | None = None,
) -> bool:
    """纯评价既存候选，不为其创建抽样编号；仅接受当前已处于引仙筛选的状态。

    精炼、切锁、完成等状态没有足够来源证据，不借一个单属性改善猜测前一步。
    比较所有锁定项的身份和值、品质、锁标记，候选不得增锁或漏锁。
    """
    from decimal import Decimal
    if plan_action != 'yinxian':
        raise ValueError('当前策略不是引仙筛选，不能推断既存候选来自精炼或其他动作')
    if len(current) != 6 or len(pending) != 6:
        raise ValueError('既存候选及当前属性都须为完整六槽')
    if len({e.cleanse_id for e in current}) != 6 or len({e.cleanse_id for e in pending}) != 6:
        raise ValueError('既存候选或当前槽位身份重复')
    old_locks = {e.cleanse_id: e for e in current if e.locked}
    new_locks = {e.cleanse_id: e for e in pending if e.locked}
    if old_locks != new_locks:
        raise ValueError('既存候选改变了锁定项')
    if supplement_b_code is not None:
        return any(not e.locked and e.code == supplement_b_code and e.quality >= 6 for e in pending)
    ratio = Decimal(str(target_ratio))
    if not ratio.is_finite() or not 0 < ratio <= 1:
        raise ValueError('引仙目标比例无效')
    completed = {e.code for e in current if e.quality >= 6 and e.is_full}
    return any(not e.locked and e.code in a_codes - completed and e.quality >= 6 and e.ratio >= ratio
               for e in pending)
