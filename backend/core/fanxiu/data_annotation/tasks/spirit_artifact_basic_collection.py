"""普通洗炼凑齐四基础属性的连续研发入口；尚待真实游戏验收。

复用纯策略和正式 GUI/Runtime，不打开高级洗炼、不使用任何石头。
未知候选入口拒绝；运行内失败候选可被下一次普通洗炼覆盖，预算停止时
保留候选并显式返回 paused_pending，不把未保存结果算作本体事实。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .spirit_artifact_basic_attributes import WashAttribute, plan_basic_attributes
from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter
from ...instrumentation.backpack import read_backpack_item_counts
from ...instrumentation.spirit_artifact_ui_identity import read_spirit_artifact_ui_identity
from ...instrumentation.spirit_artifact_wash_observation import (
    SpiritArtifactWashTarget, read_spirit_artifact_wash_observation,
)


_BASIC_NAMES = {'MAXHP': '气血', 'MAXMP': '灵力', 'ATTACK': '攻击', 'DEFENSE': '守御'}
_MATERIAL_ID = 14000002


def run_basic_attribute_collection(
    context, execute, *, target: SpiritArtifactWashTarget, rules: dict,
    c_codes: set[str], evidence_path: Path, stop_at: float, max_rolls: int = 100,
    evaluate_existing_candidate: bool = False,
) -> dict:
    """已选定未突破本体上收集基础属性，颜色与数值不作采用门槛。

    c_codes 必须由正式属性配置确定。只解已锁 C，其他非基础已锁项不猜。
    每次消耗前核对 UI 目标/普通材料/实际费用并预留90秒；库存不足暂停。
    动作后观察失败记录不确定消耗并抛错，不自动重试、不伪造统计样本。
    evaluate_existing_candidate 显式授权按同一基础属性策略处理入口候选；
    不将该历史候选计入本次消耗样本，不复用前一 Cell 的步骤。
    """
    if max_rolls <= 0 or stop_at <= time.time() or c_codes & _BASIC_NAMES.keys():
        raise ValueError('基础培养需要有效期限、次数及互斥 C 类定义')
    gui = SpiritArtifactCleanseRuntimeGuiAdapter(context, execute)
    rules = dict(rules)
    path = Path(evidence_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    phase = 'entry'
    rolls = 0

    def record(event, **fields):
        with path.open('a', encoding='utf-8') as output:
            output.write(json.dumps(dict(event=event, at=time.time(), phase=phase,
                item_id=target.item_id, **fields), ensure_ascii=False, default=str) + '\n')

    def rows(effects):
        missing = sorted({e['cleanse_id'] for e in effects} - rules.keys())
        if missing:
            from ...instrumentation.spirit_artifact_affixes import read_spirit_artifact_affix_rules
            found = read_spirit_artifact_affix_rules(missing)
            if (found['pid'], found['process_start_ticks']) != target.process_identity:
                raise RuntimeError('补齐基础属性配置时进程改变')
            rules.update(found['rules'])
        result = []
        for e in effects:
            code = rules[e['cleanse_id']]['code']
            if not code:
                raise RuntimeError('词条缺少已知属性身份，不能猜测类别')
            result.append(WashAttribute(e['cleanse_id'], _BASIC_NAMES.get(code, code),
                e['value'], e['quality'], e['locked']))
        return result

    def effects_map(effects):
        return {e['cleanse_id']: (e['value'], e['quality'], e['locked']) for e in effects}

    def counts():
        values, state = read_backpack_item_counts([_MATERIAL_ID], manager_key='spirit-artifact-basic')
        if (state['pid'], state['process_start_ticks']) != target.process_identity:
            raise RuntimeError('普通洗炼资源观察进程改变')
        return values[_MATERIAL_ID]

    def identity():
        state = read_spirit_artifact_ui_identity()
        if ((state['pid'], state['process_start_ticks']) != target.process_identity
                or state['ware_id'] != target.ware_id or state['item_id'] != target.item_id):
            raise RuntimeError('基础洗炼当前选中目标改变')

    def finish(status, current):
        result = dict(status=status, rolls=rolls, snapshot=current,
                      elapsed_seconds=time.monotonic() - started)
        record('basic_collection_finished', **result)
        return result

    try:
        entry = execute(context.wait_scene([668, 714], wait=12))
        if entry.scene_id not in (668, 714):
            raise RuntimeError('基础洗炼入口不在洗炼页')
        current = read_spirit_artifact_wash_observation(target, verify_ui=True)
        if current.get('is_break') is not False or (current['pending_effects'] and not evaluate_existing_candidate):
            raise RuntimeError('入口要求未突破且没有未处理候选')
        record('entry_observed', existing_candidate_authorized=evaluate_existing_candidate, snapshot=current)
        while True:
            phase = 'plan'
            landed = execute(context.wait_scene([721, 714, 668], wait=12))
            if landed.scene_id == 721:
                gui.finish_effect_activation()
                current = read_spirit_artifact_wash_observation(target, verify_ui=True)
            current_rows = rows(current['effects'])
            # 解 C 是用户已明确的初始化动作。其余非基础锁仍交纯策略阻塞。
            locked_c = [e.cleanse_id for e in current_rows
                        if e.locked and rules[e.cleanse_id]['code'] in c_codes]
            if locked_c:
                if current['pending_effects']:
                    raise RuntimeError('候选未处理时不能切换 C 类锁')
                for uid in locked_c:
                    gui.set_lock(uid, False)
                current = read_spirit_artifact_wash_observation(target)
                continue
            plan = plan_basic_attributes(current_rows, rows(current['pending_effects']))
            if plan.action == 'blocked':
                raise RuntimeError(plan.reason)
            if plan.action == 'complete':
                return finish('complete', current)
            if plan.action == 'retain':
                phase = 'save'
                identity()
                pending = effects_map(current['pending_effects'])
                record('save_attempt', before=current)
                context.click_shape_center(714, '保留新属性')
                gui.finish_effect_activation()
                saved = read_spirit_artifact_wash_observation(target)
                if saved['pending_effects'] or effects_map(saved['effects']) != pending:
                    raise RuntimeError('保存新属性后实际六条未与候选一致')
                record('saved', before=current, after=saved)
                current = saved
                continue
            if time.time() >= stop_at - 90 or rolls >= max_rolls:
                return finish('paused_pending' if current['pending_effects'] else 'paused', current)
            if plan.action == 'lock':
                phase = 'lock'
                for uid in plan.lock_ids:
                    gui.set_lock(uid, True)
                current = read_spirit_artifact_wash_observation(target)
                record('locks_updated', after=current)
                continue
            phase = 'wash'
            # 费用随锁数变化；完整 UI 投影同时验证选中实例与当前实际成本。
            before = read_spirit_artifact_wash_observation(target, verify_ui=True)
            if (effects_map(before['effects']) != effects_map(current['effects'])
                    or effects_map(before['pending_effects']) != effects_map(current['pending_effects'])):
                raise RuntimeError('洗炼前属性改变，拒绝沿用旧决策')
            cost = before.get('material_cost')
            if before.get('material_id') != _MATERIAL_ID or type(cost) is not int or cost <= 0:
                raise RuntimeError('普通洗炼材料或费用未被明确核实')
            owned = counts()
            if owned < cost or time.time() >= stop_at - 90:
                return finish('paused_pending' if current['pending_effects'] else 'paused', current)
            scene = 714 if current['pending_effects'] else 668
            if execute(context.wait_scene([scene], wait=12)).scene_id != scene:
                raise RuntimeError('洗炼前页面与候选状态不一致')
            identity()
            after = None
            remaining = None
            record('wash_attempt', roll_index=rolls+1, before=before, cost=cost, inventory_before=owned)
            try:
                context.click_shape_center(scene, '执行洗炼' if scene == 714 else '执行洗炼（研发禁止）')
                # 非活动积分提示是本次已授权洗炼的业务确认，不让全局守护关闭。
                # 原页、确认页可能短暂滞留；确认仅点击一次，随后只观察。
                confirmed = set()
                candidate_deadline = time.monotonic() + 35
                while True:
                    landed = execute(context.wait_scene([727, 726, 714, 668], wait=12))
                    if landed.scene_id in (726, 727):
                        if landed.scene_id not in confirmed:
                            record('wash_business_confirm', scene=landed.scene_id, roll_index=rolls+1)
                            context.click_shape_center(landed.scene_id, '确认' if landed.scene_id == 726 else '继续洗炼')
                            confirmed.add(landed.scene_id)
                    elif landed.scene_id == 714:
                        observed = read_spirit_artifact_wash_observation(target)
                        if observed['refine_num'] == before['refine_num'] + 1:
                            after = observed
                            break
                    elif landed.scene_id != 668:
                        raise RuntimeError('普通洗炼出现未知业务页面')
                    if time.monotonic() >= candidate_deadline:
                        raise RuntimeError('等待本轮新候选超时；不重放洗炼消耗')
                    time.sleep(.5)
                remaining = counts()
                locked = {k: v for k, v in effects_map(before['effects']).items() if v[2]}
                pending = effects_map(after['pending_effects'])
                verified = (owned - remaining == cost and len(pending) == 6
                    and after['refine_num'] == before['refine_num'] + 1
                    and effects_map(after['effects']) == effects_map(before['effects'])
                    and all(pending.get(k) == v for k, v in locked.items()))
                record('wash_result', roll_index=rolls+1, before=before, after=after,
                    cost=cost, inventory_before=owned, inventory_after=remaining,
                    consumption_verified=verified)
                if not verified:
                    raise RuntimeError('普通洗炼消耗或候选后置验证不符')
            except Exception as exc:
                record('unverified_consumption', before=before, after=after,
                       inventory_before=owned, inventory_after=remaining, error=repr(exc))
                raise
            rolls += 1
            current = after
    except Exception as exc:
        record('basic_collection_stopped', error_type=type(exc).__name__, error=str(exc))
        raise
