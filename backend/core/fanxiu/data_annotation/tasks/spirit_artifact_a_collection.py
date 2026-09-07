"""已授权 A 类培养的连续研发入口；遇到未知状态停止，不重放消耗。

从已选定洗炼页开始，在一个 Cell 内复用滚动经验。规则由已核实的静态
配置注入；日志逐次记录实际消耗，与可调整的停止门槛分离。尚待真实验收。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter
from .spirit_artifact_yinxian import YinxianAttribute, analyze_yinxian_sample, plan_a_collection
from ...instrumentation.spirit_artifact_wash_observation import (
    SpiritArtifactWashTarget, read_spirit_artifact_wash_observation,
)


def run_a_collection(
    context, execute, *, target: SpiritArtifactWashTarget, rules: dict,
    a_codes: set[str], b_codes: set[str], c_codes: set[str],
    evidence_path: Path, stop_at: float, target_ratio: float = .90,
    max_consumptions: int = 100,
) -> dict:
    """调用即授权在指定目标上切锁、消耗引仙/精炼石、采用达标候选。

    stop_at 是生产窗口前的绝对截止秒；每次消耗前预留 90 秒收尾。
    max_consumptions 只限制本次运行，不修改概率样本或业务完成条件。
    不负责导航、突破、调度或恢复中断的候选；入口已有候选时停止。
    """
    from ...instrumentation.backpack import read_backpack_item_counts

    if max_consumptions <= 0 or stop_at <= time.time():
        raise ValueError('连续洗灵需要有效期限及消耗上限')
    gui = SpiritArtifactCleanseRuntimeGuiAdapter(context, execute)
    evidence_path = Path(evidence_path)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    current = read_spirit_artifact_wash_observation(target, verify_ui=True)
    if current['pending_effects']:
        raise RuntimeError('入口存在未处理候选，需从真实现场明确处理')

    def attributes(effects):
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
        return saved

    def record(data):
        with evidence_path.open('a', encoding='utf-8') as output:
            output.write(json.dumps({'recorded_at': time.time(), 'item_id': target.item_id,
                                      **data}, ensure_ascii=False, default=str) + '\n')

    consumed = 0
    with gui.advanced_scroll_session(f'a-collection-{time.time_ns()}'):
        while True:
            # 完整业务候选优先于全局相似结果页，活动干扰走既有守护。
            # #713 仍由显式业务确认处理，不交给通用弹窗守护。
            landed = execute(context.wait_scene([721, 668, 714, 712], wait=12))
            if landed.scene_id == 721:
                gui.finish_effect_activation()
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
                for effect in current['effects']:
                    desired = effect['cleanse_id'] in plan.desired_lock_ids
                    if effect['locked'] != desired:
                        gui.set_lock(effect['cleanse_id'], desired)
                current = read_spirit_artifact_wash_observation(target)
                continue
            material = 14000006 if plan.action == 'yinxian' else 14000007
            iteration_started = time.monotonic()
            # preview 校验当前实例、库存、未锁项、道具确认文案；窗口路线在本程序内复用。
            preview = gui.preview_advanced_item(material)
            if preview['target_item_id'] != target.item_id:
                raise RuntimeError('使用道具确认目标与培养目标不一致')
            preview_seconds = time.monotonic() - iteration_started
            if time.time() >= stop_at - 30:
                gui.cancel()
                gui.cancel()
                return {'status': 'paused', 'consumed': consumed, 'snapshot': current,
                        'elapsed_seconds': time.monotonic() - started}
            context.click_shape_center(713, '确认使用道具')
            execute(context.wait_scene([714], wait=12))
            after = read_spirit_artifact_wash_observation(target)
            counts, inventory = read_backpack_item_counts([material], manager_key='spirit-artifact-advanced')
            consumed += 1
            # 即使后置验证失败，也保留原始观察；不靠候选内容变化断言独立消耗。
            raw_sample = attributes(after['pending_effects'])
            highest_red_ratio = max((e.ratio for e in raw_sample if not e.locked and e.quality >= 6),
                                    default=None)
            record({'material_id': material, 'before': current, 'after': after,
                    'inventory_before': preview['count'], 'inventory_after': counts[material],
                    'highest_red_ratio': highest_red_ratio,
                    'unlocked_candidates': [dict(cleanse_id=e.cleanse_id, code=e.code,
                        value=e.value, quality=e.quality, normal_max=e.normal_max)
                        for e in raw_sample if not e.locked],
                    'preview_seconds': preview_seconds,
                    'elapsed_seconds': time.monotonic() - iteration_started})
            if ((inventory['pid'], inventory['process_start_ticks']) != target.process_identity
                    or preview['count'] - counts[material] != 1
                    or effect_map(after['effects']) != effect_map(current['effects'])
                    or len(after['pending_effects']) != 6):
                raise RuntimeError('道具消耗或候选后置验证失败，禁止重试确认')
            if plan.action == 'yinxian':
                done = {e.code for e in attributes(current['effects']) if e.is_full and e.quality >= 6}
                sample = analyze_yinxian_sample(raw_sample,
                    roll_index=consumed, needed_a_codes=a_codes - done, target_ratio=target_ratio)
                current = save_pending(after) if sample.stop_yinxian else after
            else:
                old = next(e for e in attributes(current['effects']) if e.cleanse_id == plan.target_cleanse_id)
                new = [e for e in attributes(after['pending_effects']) if not e.locked]
                if len(new) != 1 or new[0].code != old.code or new[0].value <= old.value:
                    raise RuntimeError('精炼未形成目标数值提升，停止检查')
                current = save_pending(after)
