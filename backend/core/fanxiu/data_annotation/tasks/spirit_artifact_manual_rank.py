"""手动洗炼推进本期洗灵消耗任务；无双只是资源使用的培养方向。

研发入口：调用者持有 AI/工程独占权，传本期活动 ID 与截止时间。
不改动突破前算法，不启动游戏自动洗炼。每次消费都重新读任务，达标即停。
"""
import json
import time
from pathlib import Path
from datetime import datetime

from ...instrumentation.xiling_task_rewards import discover_xiling_task_spec, read_xiling_task_progress
from ...instrumentation.spirit_artifact_wash_observation import SpiritArtifactWashTarget, read_spirit_artifact_wash_observation
from ...instrumentation.spirit_artifact_affixes import read_spirit_artifact_affix_rules
from ...instrumentation.spirit_artifact_collector import collect_spirit_artifact_snapshot_once
from ...instrumentation.backpack import read_backpack_item_counts
from ...activity.daily_activity_sync import load_worldline_activity_schedule_snapshot
from .resource_rank_daily_gift import active_resource_rank_gift_adapters
from ...catalog.spirit_artifact_identity import load_spirit_artifact_templates
from ...catalog.spirit_artifact_wash_rules import load_spirit_artifact_wash_rules
from ...catalog.inventory_models import FanxiuSpiritArtifactHallSnapshot
from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter
from .spirit_artifact_preparation import spirit_artifact_priority
from .spirit_artifact_peerless_stone import use_peerless_stone_once
from .spirit_artifact_auto_route import try_spirit_artifact_auto_route


def effect_map(effects):
    return {e['cleanse_id']: (e['value'], e['quality'], e['locked']) for e in effects}


def manual_candidate_action(*, peerless: bool, score_increased: bool) -> str:
    """用户策略：无双必须采用；其它候选仅在评分提高时采用。"""
    return 'save' if peerless or score_increased else 'wash'


def run_spirit_artifact_manual_rank(context, execute, *, activity_id: int,
                                   stop_at: float, evidence_path: Path,
                                   max_rolls: int = 100) -> dict:
    """完成本期实际发放的全部消耗档位；预算/截止只表示暂停。

    本期成员由 QuestMgr 与配置关联，单次动作前后完整核对这些任务。
    命中无双采用后同步全馆并重新排序；候选保存也须保护全部四 A。
    不确定消耗立即抛错并保留现场，绝不重发；领取状态独立返回给领取入口。
    """
    if max_rolls <= 0 or stop_at <= time.time():
        raise ValueError('需要有效的实验次数与截止时间')
    path = Path(evidence_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    def record(event, **data):
        with path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(dict(event=event, at=time.time(), **data), ensure_ascii=False, default=str) + '\n')
    gui = SpiritArtifactCleanseRuntimeGuiAdapter(context, execute)
    schedule = load_worldline_activity_schedule_snapshot()
    active = [(adapter, aid) for adapter, aid in active_resource_rank_gift_adapters(
        schedule, now=datetime.now().astimezone()) if adapter.key == 'xiling-zhengwu']
    if len(active) != 1 or active[0][1] != activity_id:
        raise RuntimeError('指定洗灵活动不是当前唯一开放实例')
    occurrence = [r for r in schedule['occurrences'] if r.get('activity_id') == activity_id
                  and r.get('identity_complete')]
    if len(occurrence) != 1:
        raise RuntimeError('本期洗灵日程身份不唯一')
    stop_at = min(stop_at, datetime.fromisoformat(occurrence[0]['end_at']).timestamp())
    spec = discover_xiling_task_spec(activity_id)
    rolls, consumed, completed = 0, 0, []
    stones_used = 0
    stone_checked = set()
    target = None
    rules = {}
    progress = read_xiling_task_progress(spec)
    record('task_goal', progress=progress, spec=spec)

    def finish(reason):
        result = dict(status='complete' if reason == 'task_goal_reached' else 'paused',
                      stop_reason=reason, rolls=rolls, consumed=consumed,
                      peerless_stones_used=stones_used,
                      completed_parts=completed, task_progress=progress)
        record('finished', **result)
        return result

    def observe():
        value = read_spirit_artifact_wash_observation(target, verify_ui=True)
        missing = {e['cleanse_id'] for e in value['effects'] + value['pending_effects']} - rules.keys()
        if missing:
            found = read_spirit_artifact_affix_rules(sorted(missing))
            if (found['pid'], found['process_start_ticks']) != target.process_identity:
                raise RuntimeError('洗炼词条配置进程改变')
            rules.update(found['rules'])
        return value

    while True:
        if progress['goal_reached']:
            return finish('task_goal_reached')
        if time.time() >= stop_at - 90 or rolls >= max_rolls:
            return finish('deadline' if time.time() >= stop_at - 90 else 'experiment_budget')
        if target is None:
            hall_raw = collect_spirit_artifact_snapshot_once()
            if not hall_raw.get('runtime_complete'):
                raise RuntimeError('全馆观察不完整，不能选部件')
            hall = FanxiuSpiritArtifactHallSnapshot.model_validate(hall_raw)
            candidates = [(w.order, r) for w in hall.artifacts for r in w.rows if r.stage == '突破']
            if not candidates:
                return finish('no_breakthrough_parts')
            ware, row = min(candidates, key=lambda x: spirit_artifact_priority('突破', x[0], x[1].order))
            identity = hall_raw['runtime_debug']
            target = SpiritArtifactWashTarget(row.runtime_item_id, ware, row.order,
                       (identity['pid'], identity['process_start_ticks']), row.runtime_base_id)
            name, parts = load_spirit_artifact_templates()[ware]
            gui.select_item(target.item_id, ware, name, parts[target.part - 1])
            record('selected', target=target)
        current = observe()
        if current.get('is_break') is not True:
            raise RuntimeError('当前部件不是已突破本体')
        a_codes = set(load_spirit_artifact_wash_rules()['wares'][target.ware_id]['a_codes'])
        a_ids = {e['cleanse_id'] for e in current['effects'] if rules[e['cleanse_id']]['code'] in a_codes}
        if len(a_ids) != 4:
            raise RuntimeError('四 A 不完整，不能执行突破后洗炼')
        if any(not e['locked'] for e in current['effects'] if e['cleanse_id'] in a_ids):
            if current['pending_effects']:
                raise RuntimeError('存在候选且 A 未全锁，保留现场')
            gui.set_locks(sorted(a_ids), target_item_id=target.item_id)
            current = observe()
        locked = {k: v for k, v in effect_map(current['effects']).items() if v[2]}
        pending = effect_map(current['pending_effects'])
        hit = any(rules[e['cleanse_id']]['name'] == '灵器无双' for e in current['pending_effects'])
        if pending:
            if any(pending.get(k) != v for k, v in locked.items()):
                raise RuntimeError('候选改变锁定属性')
            increased = current.get('manual_wash_state', {}).get('candidate_score_increased')
            if type(increased) is not bool:
                raise RuntimeError('候选评分比较不可用')
            if manual_candidate_action(peerless=hit, score_increased=increased) == 'save':
                execute(context.wait_scene([714], wait=12))
                context.click_shape_center(714, '保留新属性')
                gui.finish_effect_activation()
                saved = observe()
                if saved['pending_effects'] or effect_map(saved['effects']) != pending:
                    raise RuntimeError('保留新属性未验证成功')
                record('saved', target=target, peerless=hit, snapshot=saved)
                if hit:
                    completed.append(f'{target.ware_id}-{target.part}')
                    target = None
                continue
        progress = read_xiling_task_progress(spec)
        if progress['goal_reached']:
            return finish('task_goal_reached')
        # 同一部件按无双石→自动→手动选路线。自动暂为无副作用 pass。
        # 仅在本次连续运行中记检查结果；新的调用重新读取库存与部件条件。
        if target.item_id not in stone_checked:
            if time.time() >= stop_at - 90:
                return finish('deadline')
            stone = use_peerless_stone_once(context, execute, target=target,
                                            evidence_path=path.with_suffix('.stone.jsonl'))
            stone_checked.add(target.item_id)
            if stone['status'] == 'complete':
                stones_used += stone['consumed']
                completed.append(f'{target.ware_id}-{target.part}')
                target = None
                progress = read_xiling_task_progress(spec)
                continue
            automatic = try_spirit_artifact_auto_route()
            record('auto_route', target=target, result=automatic)
            if automatic['status'] != 'pass':
                raise RuntimeError('自动路线尚未接入结果处理，不能继续手动消耗')
        cost = current.get('material_cost')
        if current.get('material_id') != 14000002 or type(cost) is not int or cost <= 0:
            raise RuntimeError('洗炼费用未核实')
        counts, state = read_backpack_item_counts([14000002], manager_key='xiling-manual')
        if (state['pid'], state['process_start_ticks']) != target.process_identity:
            raise RuntimeError('资源进程身份变化')
        if counts[14000002] < cost:
            return finish('material_exhausted')
        if time.time() >= stop_at - 90:
            return finish('deadline')
        scene = 714 if pending else 668
        execute(context.wait_scene([scene], wait=12))
        record('wash_attempt', target=target, cost=cost, before=current, task_progress=progress)
        context.click_shape_center(scene, '执行洗炼' if pending else '执行洗炼（研发禁止）')
        deadline, confirmed = time.monotonic() + 45, set()
        while True:
            landed = execute(context.wait_scene([727, 714, 668], wait=12))
            if landed.scene_id == 727 and 727 not in confirmed:
                context.click_shape_center(727, '继续洗炼')
                confirmed.add(727)
            elif landed.scene_id == 714:
                after = read_spirit_artifact_wash_observation(target)
                if after['refine_num'] == current['refine_num'] + 1:
                    break
            if time.monotonic() >= deadline:
                raise RuntimeError('本次洗炼无新候选；不重试消耗')
            time.sleep(.5)
        if (effect_map(after['effects']) != effect_map(current['effects'])
                or len(after['pending_effects']) != len(current['effects'])
                or any(effect_map(after['pending_effects']).get(k) != v for k, v in locked.items())):
            raise RuntimeError('洗炼候选或锁定属性验证失败')
        fresh_progress = read_xiling_task_progress(spec)
        expected = min(progress['activity_progress'] + cost, progress['task_target'])
        if fresh_progress['activity_progress'] != expected:
            raise RuntimeError('洗炼后资源榜任务进度与本次费用不一致')
        rolls += 1
        consumed += cost
        progress = fresh_progress
        record('wash_verified', rolls=rolls, consumed=consumed, after=after, task_progress=progress)
        # 先处理本次候选，再按主目标停止；命中无双不能在终点丢失。
        if progress['goal_reached'] or rolls >= max_rolls or time.time() >= stop_at - 90:
            current = observe()
            hit = any(rules[e['cleanse_id']]['name'] == '灵器无双' for e in current['pending_effects'])
            if hit or current['manual_wash_state']['candidate_score_increased']:
                pending = effect_map(current['pending_effects'])
                context.click_shape_center(714, '保留新属性')
                gui.finish_effect_activation()
                saved = observe()
                if saved['pending_effects'] or effect_map(saved['effects']) != pending:
                    raise RuntimeError('收尾保存未验证')
                record('saved', target=target, peerless=hit, snapshot=saved)
                if hit:
                    completed.append(f'{target.ware_id}-{target.part}')
            return finish('task_goal_reached' if progress['goal_reached'] else
                          'experiment_budget' if rolls >= max_rolls else 'deadline')
