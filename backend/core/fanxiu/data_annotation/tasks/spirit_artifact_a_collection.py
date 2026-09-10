"""已授权 A 类培养的连续研发入口；遇到未知状态停止，不重放消耗。

从已选定洗炼页开始，在一个 Cell 内复用滚动经验。规则由已核实的静态
配置注入；日志逐次记录实际消耗，与可调整的停止门槛分离。
1-3 四 A 培养、批量切锁及合并读取路径已真实完成；不代表全部部件已处理。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter
from .spirit_artifact_yinxian import (
    DEFAULT_PREPARED_TARGET_RATIO, YinxianAttribute, analyze_yinxian_sample,
    plan_a_collection, plan_b_supplement,
)
from ...instrumentation.spirit_artifact_wash_observation import (
    SpiritArtifactWashTarget, read_spirit_artifact_wash_observation,
)


def run_a_collection(
    context, execute, *, target: SpiritArtifactWashTarget, rules: dict,
    a_codes: set[str] | None = None, b_codes: set[str] | None = None, c_codes: set[str],
    evidence_path: Path, stop_at: float, target_ratio: float = DEFAULT_PREPARED_TARGET_RATIO,
    max_consumptions: int = 100,
    fast_observation: bool = False,
    supplement_b_code: str | None = None,
    evaluate_existing_candidate: bool = False,
    scroll_profile=None,
    initial_snapshot: dict | None = None,
) -> dict:
    """调用即授权在指定目标上切锁、消耗引仙/精炼石、采用达标候选。

    stop_at 是生产窗口前的绝对截止秒；每次消耗前预留 90 秒收尾。
    max_consumptions 只限制本次运行，不修改概率样本或业务完成条件。
    返回 status 保持 complete/paused；stop_reason 区分 all_a_full、
    b_supplement_complete、budget_paused、deadline_paused。target_hit 仅是
    候选决策事件，表示停止本轮引仙筛选并采用，不表示整个培养完成。
    不负责导航、突破或调度。默认拒绝入口候选；evaluate_existing_candidate=True
    仅授权用当前完整事实重新评价 A／补 B 筛选或严格改善的精炼目标候选，
    不恢复历史步骤、不猜材料来源。需要重新切锁等未定义状态仍阻塞。
    既存候选评价只记观察事件，不重复统计历史消耗或概率样本。
    supplement_b_code 指定 A 全满后的独立补 B 阶段：仅筛红色，不精炼。
    """
    from ...instrumentation.spirit_artifact_memory import spirit_artifact_memory as memory_model

    if max_consumptions <= 0 or stop_at <= time.time():
        raise ValueError('连续洗灵需要有效期限及消耗上限')
    from ...catalog.spirit_artifact_wash_rules import load_spirit_artifact_wash_rules
    configured = load_spirit_artifact_wash_rules()['wares'].get(target.ware_id)
    if configured is None:
        raise ValueError('灵器策略配置未知')
    configured_a, configured_b = set(configured['a_codes']), set(configured['b_codes'])
    if ((a_codes is not None and set(a_codes) != configured_a)
            or (b_codes is not None and set(b_codes) != configured_b)):
        raise ValueError('调用方 A/B 集合与当前四 A 策略不一致；须重新读取配置')
    a_codes, b_codes = configured_a, configured_b
    if supplement_b_code is not None and supplement_b_code not in b_codes:
        raise ValueError('补 B 目标不是本灵器当前 B 属性，不能将 A 按仅红色补位')
    gui = SpiritArtifactCleanseRuntimeGuiAdapter(context, execute)
    rules = dict(rules)
    evidence_path = Path(evidence_path)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    entry = execute(context.wait_scene([713, 721, 668, 714, 712], wait=12))
    if entry.scene_id == 713:
        raise RuntimeError('入口存在未决道具确认，保留现场且禁止重发确认')
    if entry.scene_id == 721:
        gui.finish_effect_activation()
    if initial_snapshot is None:
        current = read_spirit_artifact_wash_observation(target, verify_ui=True)
    else:
        from ...instrumentation.spirit_artifact_wash_observation import validate_spirit_artifact_wash_snapshot
        validate_spirit_artifact_wash_snapshot(initial_snapshot, target, verify_ui=True)
        current = initial_snapshot
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

    def finish(status, stop_reason, plan):
        full_a = {e.code for e in attributes(current['effects'])
                  if e.code in a_codes and e.quality >= 6 and e.is_full}
        result = {'status': status, 'stop_reason': stop_reason,
                  'consumed': consumed, 'snapshot': current,
                  'pending_candidate': bool(current['pending_effects']),
                  'all_a_full': a_codes <= full_a,
                  'remaining_a_codes': sorted(a_codes - full_a),
                  'next_action': plan.action,
                  'elapsed_seconds': time.monotonic() - started}
        record({'record_type': 'run_finished', 'new_consumption': False, **result})
        return result

    if current['pending_effects']:
        execute(context.wait_scene([714], wait=12))
        original = attributes(current['effects'])
        pending = attributes(current['pending_effects'])
        entry_plan = (plan_b_supplement(original, a_codes=a_codes, target_code=supplement_b_code)
                      if supplement_b_code is not None else
                      plan_a_collection(original, a_codes=a_codes, b_codes=b_codes,
                                        c_codes=c_codes, target_ratio=target_ratio))
        try:
            if entry_plan.action == 'refine' and supplement_b_code is None:
                accept_existing = evaluate_existing_refinement_candidate(
                    original, pending, plan_action=entry_plan.action,
                    target_cleanse_id=entry_plan.target_cleanse_id, a_codes=a_codes)
            else:
                accept_existing = evaluate_existing_yinxian_candidate(
                    original, pending, plan_action=entry_plan.action,
                    a_codes=a_codes, target_ratio=target_ratio, supplement_b_code=supplement_b_code)
        except ValueError as error:
            record({'record_type': 'existing_candidate_evaluation', 'sample_origin': 'entry_observation',
                    'probability_sample': False, 'new_consumption': False,
                    'decision': 'blocked', 'reason': str(error), 'snapshot': current})
            raise RuntimeError('既存候选无法按当前培养策略安全评价；保留现场') from error
        record({'record_type': 'existing_candidate_evaluation', 'sample_origin': 'entry_observation',
                'probability_sample': False, 'new_consumption': False,
                'decision': ('accept_improvement' if entry_plan.action == 'refine' else 'accept')
                            if accept_existing else 'continue_yinxian',
                'plan_action': entry_plan.action, 'snapshot': current})
        current = save_pending(current) if accept_existing else current

    consumed = 0
    with gui.advanced_scroll_session(f'a-collection-{time.time_ns()}', scroll_profile=scroll_profile):
        while True:
            # 动作提供方负责页面就绪；纯属性规划不再先做一次重复场景识别。
            if supplement_b_code is not None:
                plan = plan_b_supplement(attributes(current['effects']), a_codes=a_codes,
                                         target_code=supplement_b_code)
            else:
                plan = plan_a_collection(attributes(current['effects']), a_codes=a_codes,
                                         b_codes=b_codes, c_codes=c_codes, target_ratio=target_ratio)
            if plan.action == 'complete':
                return finish('complete', 'b_supplement_complete' if supplement_b_code
                              is not None else 'all_a_full', plan)
            if plan.action == 'blocked':
                raise RuntimeError(plan.reason)
            if time.time() >= stop_at - 90 or consumed >= max_consumptions:
                return finish('paused', 'budget_paused' if consumed >= max_consumptions
                              else 'deadline_paused', plan)
            if plan.action == 'locks':
                if current['pending_effects']:
                    raise RuntimeError('切锁前仍有候选，不丢弃未知待采用结果')
                before_locks = current
                record({'record_type': 'locks_planned', 'probability_sample': False,
                        'new_consumption': False, 'before': current,
                        'target_cleanse_id': plan.target_cleanse_id,
                        'reason': plan.reason,
                        'desired_lock_ids': sorted(plan.desired_lock_ids)})
                current = gui.set_locks(plan.desired_lock_ids, target_item_id=target.item_id)
                from ...instrumentation.spirit_artifact_wash_observation import validate_spirit_artifact_wash_snapshot
                validate_spirit_artifact_wash_snapshot(current, target, verify_ui=True)
                memory_model.remember_snapshot(current)
                if {e['cleanse_id'] for e in current['effects'] if e['locked']} != set(plan.desired_lock_ids):
                    raise RuntimeError('切锁后最终锁状态与计划不一致，停止')
                record({'record_type': 'locks_updated', 'probability_sample': False,
                        'new_consumption': False, 'before': before_locks, 'after': current,
                        'desired_lock_ids': sorted(plan.desired_lock_ids)})
                continue
            material = 14000006 if plan.action == 'yinxian' else 14000007
            iteration_started = time.monotonic()
            # preview 校验当前实例、库存、未锁项、道具确认文案；窗口路线在本程序内复用。
            preview = gui.preview_advanced_item(material, fast_observation=fast_observation,
                expected_snapshot=current)
            if preview['target_item_id'] != target.item_id:
                raise RuntimeError('使用道具确认目标与培养目标不一致')
            # 对照已知计划；正常独占连续操作不为未改变的事实追加 Runtime。
            verify_a_collection_preview(current, preview['observation'])
            current = {**current, 'pending_revision': preview['pending_revision']}
            preview_seconds = time.monotonic() - iteration_started
            if time.time() >= stop_at - 30:
                gui.cancel()
                gui.cancel()
                return finish('paused', 'deadline_paused', plan)
            after = None
            result_timings = {}
            try:
                measured = time.monotonic()
                context.click_shape_center(713, '确认使用道具')
                execute(context.wait_scene([714], wait=12))
                result_timings['confirm_and_wait'] = time.monotonic() - measured
                measured = time.monotonic()
                after = read_spirit_artifact_wash_observation(target)
                result_timings['candidate_read'] = time.monotonic() - measured
                measured = time.monotonic()
                inventory_after = memory_model.confirm_use(material, current, after)
                result_timings['validate_and_deduct'] = time.monotonic() - measured
            except BaseException as error:
                memory_model.invalidate()
                # 确认已尝试，动作/读取失败不表示没有消耗，也不能算已确认抽样。
                record({'record_type': 'unverified_consumption', 'material_id': material,
                        'before': current, 'after': after,
                        'inventory_before': preview['count'], 'inventory_after': None,
                        'consumption_verified': False, 'verification_error': repr(error),
                        'preview_seconds': preview_seconds,
                        'elapsed_seconds': time.monotonic() - iteration_started})
                raise RuntimeError('道具确认后观察失败；已记录未核实消耗，禁止自动重试') from error
            consumed += 1
            consumption_verified = True
            # 即使后置验证失败，也保留原始观察；不靠候选内容变化断言独立消耗。
            try:
                raw_sample = attributes(after['pending_effects'])
            except Exception as error:
                # 未知词条不能使已消耗的一次样本从统计证据中消失。
                record({'material_id': material, 'before': current, 'after': after,
                        'consumption_verified': consumption_verified,
                        'inventory_before': preview['count'], 'inventory_after': inventory_after,
                        'inventory_source': 'kernel_model',
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
                    'inventory_before': preview['count'], 'inventory_after': inventory_after,
                    'inventory_source': 'kernel_model',
                    'highest_red_ratio': highest_red_ratio,
                    'unlocked_candidates': [dict(cleanse_id=e.cleanse_id, code=e.code,
                        value=e.value, quality=e.quality, normal_max=e.normal_max)
                        for e in raw_sample if not e.locked],
                    'preview_seconds': preview_seconds,
                    'preview_timings': preview.get('timings', {}),
                    'result_timings': result_timings,
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
                # 独立于概率样本：满 B 仍可记 highest_red_ratio=1，但不是 A 命中。
                record({'record_type': 'candidate_evaluation', 'new_consumption': False,
                        'consumption_index': consumed, 'plan_action': plan.action,
                        'decision': ('b_supplement_hit' if supplement_b_code is not None
                                     else 'target_hit') if accepted else 'continue_yinxian',
                        'accepted': accepted, 'needed_a_codes': sorted(a_codes - done),
                        'hit_a_codes': sorted({e.code for e in sample.hits}),
                        'supplement_b_code': supplement_b_code})
                current = save_pending(after) if accepted else after
            else:
                old = next(e for e in attributes(current['effects']) if e.cleanse_id == plan.target_cleanse_id)
                new = [e for e in attributes(after['pending_effects']) if not e.locked]
                if len(new) != 1 or new[0].code != old.code or new[0].value <= old.value:
                    raise RuntimeError('精炼未形成目标数值提升，停止检查')
                current = save_pending(after)


def verify_a_collection_preview(current: dict, observed: dict) -> None:
    """确认消耗前核对计划事实；忽略 UI 行号与 Runtime map 的遍历顺序。"""
    for key in ('pid', 'process_start_ticks', 'item_id', 'refine_num'):
        if current[key] != observed[key]:
            raise RuntimeError(f'道具确认前培养计划过期：{key} 改变；禁止消耗')
    for key in ('effects', 'pending_effects'):
        def canonical(rows):
            return sorted((e['cleanse_id'], e['value'], e['quality'], e['locked']) for e in rows)
        if canonical(current[key]) != canonical(observed[key]):
            raise RuntimeError(f'道具确认前培养计划过期：{key} 改变；禁止消耗')


def evaluate_existing_refinement_candidate(
    current, pending, *, plan_action: str, target_cleanse_id: int | None,
    a_codes: set[str],
) -> bool:
    """只采用当前精炼目标的严格改善，不推断候选来源或补记消耗。

    需要当前纯策略明确 refine 和目标：N 槽中恰好 N−1 锁完全保持，唯一
    未锁项是未满红色 A，候选同属性/同上限、品质不降且值严格提高。
    不满足时阻塞，不将其当失败抽样后继续消耗。GUI 重入尚待真实验收。
    """
    if plan_action != 'refine' or not target_cleanse_id:
        raise ValueError('现有改善候选需要明确的当前精炼目标')
    if len(pending) != len(current):
        raise ValueError('候选槽位数与当前属性不一致')
    for rows in (current, pending):
        if (len(rows) not in (5, 6) or len({e.cleanse_id for e in rows}) != len(rows)
                or len({e.code for e in rows}) != len(rows)):
            raise ValueError('现有改善候选及当前属性需要完整唯一五／六槽')
    old_locks = {e.cleanse_id: e for e in current if e.locked}
    new_locks = {e.cleanse_id: e for e in pending if e.locked}
    if len(old_locks) != len(current) - 1 or old_locks != new_locks:
        raise ValueError('现有改善候选必须完整保持其余锁定项')
    old = next(e for e in current if not e.locked)
    new = next(e for e in pending if not e.locked)
    if (old.cleanse_id != target_cleanse_id or old.code not in a_codes
            or old.quality < 6 or old.is_full or new.code != old.code
            or new.normal_max != old.normal_max or new.basic_full_score != old.basic_full_score
            or new.quality < old.quality or new.value <= old.value):
        raise ValueError('现有候选不是当前所需 A 目标的同属性严格改善')
    return True


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
    if len(current) not in (5, 6) or len(pending) != len(current):
        raise ValueError('既存候选及当前属性都须为完整五／六槽')
    if len({e.cleanse_id for e in current}) != len(current) or len({e.cleanse_id for e in pending}) != len(pending):
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
    return any(not e.locked and e.code in a_codes - completed and e.ratio >= ratio
               for e in pending)
