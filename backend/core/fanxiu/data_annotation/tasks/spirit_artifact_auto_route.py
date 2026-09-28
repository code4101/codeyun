"""原生自动洗灵的完整批次：限额、等待、关弹窗、核账、保存候选。"""
import time
from ...instrumentation.backpack import read_backpack_item_counts
from ...instrumentation.spirit_artifact_auto import read_spirit_artifact_auto_snapshot
from ...instrumentation.spirit_artifact_ui import read_spirit_artifact_ui_snapshot
from ...instrumentation.spirit_artifact_affixes import enrich_spirit_artifact_effects
from ...instrumentation.xiling_task_rewards import read_xiling_task_progress
from .spirit_artifact_auto_batch import configure_spirit_artifact_auto_budget, start_spirit_artifact_auto_batch
from .spirit_artifact_cleanse import SpiritArtifactCleanseRuntimeGuiAdapter, SpiritArtifactCleanseBlocked, SpiritArtifactCleanseErrorCode


def plan_xiling_batch(*, remaining: int, cost: int, sample: dict | None = None) -> dict:
    """用本期任务增量/实耗估计，榜单积分不参与任务预算。

    首批至多20次，后续留5%余量；小于10次的尾差手动逐次验收。
    区间适配滑轨颗粒度；倍率仅用于估算，每批重新读取任务事实。
    """
    if type(remaining) is not int or type(cost) is not int or remaining < 0 or cost <= 0:
        raise ValueError('洗灵进度/单次费用无效')
    if remaining == 0:
        return {'mode': 'complete'}
    ratio = 1.0
    if sample:
        if sample['consumed'] <= 0 or sample['progress_delta'] <= 0:
            raise ValueError('测速必须具有正消耗与进度')
        ratio = sample['progress_delta'] / sample['consumed']
    estimate = remaining / ratio
    if estimate <= cost * 10:
        return {'mode': 'manual', 'ratio': ratio, 'estimated_cost': estimate}
    ceiling = min(20 * cost, remaining) if not sample else min(remaining, int(estimate * .95))
    ceiling = max(cost, ceiling // cost * cost)
    return {'mode': 'auto', 'ratio': ratio, 'estimated_cost': estimate,
            'maximum_budget': ceiling + 1, 'minimum_budget': max(cost + 1, int(ceiling * .8))}


def finish_spirit_artifact_auto_candidate(context, execute, *, item_id: str) -> dict:
    """保存无双或评分提升的精确候选，验证所有已锁词条完整保留。"""
    gui = SpiritArtifactCleanseRuntimeGuiAdapter(context, execute)
    before = read_spirit_artifact_ui_snapshot()
    if before.get('item_id') != item_id:
        raise RuntimeError('自动洗灵结果部件身份改变')
    enrich_spirit_artifact_effects(before['effects'] + before['pending_effects'],
        pid=before['pid'], process_start_ticks=before['process_start_ticks'])
    def fingerprint(rows):
        return {e['cleanse_id']: (e['value'], e['quality'], e['locked']) for e in rows}
    pending = fingerprint(before['pending_effects'])
    if pending and any(pending.get(e['cleanse_id']) != fingerprint([e])[e['cleanse_id']]
                       for e in before['effects'] if e['locked']):
        raise RuntimeError('自动候选修改了已有锁定属性')
    peerless = any(e['name'] == '灵器无双' for e in before['pending_effects'])
    save = bool(pending) and (peerless or before['manual_wash_state']['candidate_score_increased'])
    after = before
    if save:
        if execute(context.wait_scene([714], wait=12)).scene_id != 714:
            raise RuntimeError('自动洗灵候选页未就绪')
        context.click_shape_center(714, '保留新属性')
        gui.finish_effect_activation()
        after = read_spirit_artifact_ui_snapshot()
        if (after.get('item_id') != item_id or after['pending_effects']
                or fingerprint(after['effects']) != pending
                or (after['pid'], after['process_start_ticks']) != (before['pid'], before['process_start_ticks'])):
            raise RuntimeError('自动洗灵候选保存未形成完整终态')
    return {'saved': save, 'peerless': peerless, 'snapshot': after}


def try_spirit_artifact_auto_route(context, execute, *, target, spec,
                                  plan: dict, stop_at: float, record) -> dict:
    """完整单批，只有已证明入口不可用/尾差过小才 pass。

    故障不降级为继续消费。启动后只观察；关闭原生完成弹窗后核对任务与
    实耗，处理候选再交还上层。预算配置不修改任何其它自动选项。
    """
    if plan['mode'] != 'auto':
        return {'status': 'pass', 'reason': 'manual_tail', 'consumed': 0}
    if time.time() >= stop_at - 180:
        return {'status': 'paused', 'reason': 'deadline', 'consumed': 0}
    gui = SpiritArtifactCleanseRuntimeGuiAdapter(context, execute)
    try:
        gui.open_auto_settings()
    except SpiritArtifactCleanseBlocked as exc:
        if exc.code == SpiritArtifactCleanseErrorCode.CONTROL_UNAVAILABLE:
            return {'status': 'pass', 'reason': 'auto_unavailable', 'consumed': 0}
        raise
    configured = configure_spirit_artifact_auto_budget(
        context, execute, item_id=target.item_id, budget=plan['maximum_budget'],
        minimum_budget=plan['minimum_budget'])
    progress_before = read_xiling_task_progress(spec)
    if progress_before['goal_reached']:
        gui.close_current_overlay()
        return {'status': 'goal_reached', 'consumed': 0, 'task_progress': progress_before}
    counts_before, identity = read_backpack_item_counts([14000002], manager_key='xiling-auto')
    if (identity['pid'], identity['process_start_ticks']) != target.process_identity:
        raise RuntimeError('自动洗灵消耗前进程改变')
    record('auto_configured', plan=plan, configured=configured, progress=progress_before, counts=counts_before)
    started = start_spirit_artifact_auto_batch(context, execute, item_id=target.item_id,
        max_material_cost=plan['maximum_budget'], discard_non_target_candidate=True)
    if started['status'] != 'start_click_sent':
        raise RuntimeError('自动洗灵未形成启动授权')
    record('auto_started', evidence=started)
    deadline = min(stop_at, time.time() + 180)
    previous_consumed, advanced_at = -1, time.time()
    while True:
        execute(context.wait_action_settle(3))
        running = read_spirit_artifact_auto_snapshot()
        if not running.get('available'):
            break
        if running.get('mode') != 'running' or running.get('item_id') != target.item_id:
            raise RuntimeError('自动洗灵没有进入本批运行窗口，不重发启动')
        if running['consumed'] != previous_consumed:
            previous_consumed, advanced_at = running['consumed'], time.time()
        if time.time() > deadline or time.time() - advanced_at > 40:
            raise RuntimeError('自动洗灵超时/无进展，保留运行现场，停止后续消费')
    terminal = execute(context.wait_scene([734], wait=12))
    if terminal.scene_id != 734:
        raise RuntimeError(f'自动洗灵停止但完成弹窗未确认：#{terminal.scene_id}')
    terminal_text = context.ocr_text_in_shapes(734, ('消耗与积分',), crop=True)
    context.click_shape_center(734, '确定')
    if execute(context.wait_scene([714, 668], wait=12)).scene_id not in (714, 668):
        raise RuntimeError('自动完成弹窗未关闭到洗灵页')
    progress = read_xiling_task_progress(spec)
    counts_after, identity_after = read_backpack_item_counts([14000002], manager_key='xiling-auto')
    if (identity_after['pid'], identity_after['process_start_ticks']) != target.process_identity:
        raise RuntimeError('自动洗灵结束进程改变')
    consumed = counts_before[14000002] - counts_after[14000002]
    delta = progress['activity_progress'] - progress_before['activity_progress']
    if not 0 < consumed <= plan['maximum_budget'] or delta <= 0:
        raise RuntimeError(f'自动洗灵实耗/任务进度异常：{consumed}, {delta}')
    candidate = finish_spirit_artifact_auto_candidate(context, execute, item_id=target.item_id)
    result = {'status': 'complete', 'consumed': consumed, 'progress_delta': delta,
              'task_progress': progress, 'candidate': candidate, 'terminal_text': terminal_text,
              'elapsed_seconds': time.time() - started['started_at'],
              'rolls': candidate['snapshot']['refine_num'] - started['selected']['refine_num']}
    record('auto_completed', **result)
    return result
