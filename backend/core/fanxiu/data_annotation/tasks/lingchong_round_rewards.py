"""本期积分轮次的独立任务/礼包领取入口；每次动作回读后才继续。"""
from datetime import datetime

from backend.core.fanxiu.activity.lingchong_round_rewards import read_lingchong_round_rewards
from backend.core.fanxiu.instrumentation.activity_rank_page import read_activity_rank_task_page_snapshot

SCORE_TASK_SCENE = 940


def require_round_task_page(activity_id, *, number=None):
    page = read_activity_rank_task_page_snapshot()
    if not (page.get('complete') and page.get('activity_id') == activity_id
            and page.get('tab_index') == 2 and page.get('sub_tab_index') == 999):
        raise RuntimeError(f'灵宠积分子页身份不符：{page}')
    if number is not None and page.get('current_round') != number:
        raise RuntimeError(f'灵宠积分页轮次不符：{page}')
    return page


def open_round_task_page(context, *, activity_id):
    from .resource_rank_daily_gift import open_resource_rank_activity_page
    from .resource_rank_page import LINGCHONG_CROSS_RANK_ADAPTER
    page = read_activity_rank_task_page_snapshot()
    if page.get('complete') and page.get('activity_id') == activity_id and page.get('sub_tab_index') == 999:
        yield from context.wait_scene([SCORE_TASK_SCENE], wait=15)
        return require_round_task_page(activity_id)
    if not (page.get('complete') and page.get('activity_id') == activity_id and page.get('tab_index') == 2):
        scene = yield from open_resource_rank_activity_page(context, LINGCHONG_CROSS_RANK_ADAPTER,
            activity_id=activity_id, now=datetime.now().astimezone())
        yield from context.wait_click(int(scene), '任务页签')
    yield from context.wait_scene([747], wait=15)
    yield from context.wait_click(747, '积分页签')
    yield from context.wait_scene([SCORE_TASK_SCENE], wait=15)
    return require_round_task_page(activity_id)


def claim_loaded_round_rewards(context, *, activity_id):
    """Claim only a Runtime-authorized task with its title visible in the safe row."""
    claimed_tasks, claimed_boxes = [], []
    for _ in range(44):
        facts = read_lingchong_round_rewards(activity_id)
        if facts['all_claimed']:
            return {'facts': facts, 'claimed_task_ids': claimed_tasks, 'claimed_box_ids': claimed_boxes}
        number = facts['current_round']
        scene = yield from context.wait_scene([SCORE_TASK_SCENE], wait=15)
        page = require_round_task_page(activity_id, number=number)
        tasks = [r for r in facts['current_tasks'] if r['claimable']]
        if tasks or facts['rounds'][number - 1]['box_claimable']:
            from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_scheduler_research_batch_admission
            admission = read_scheduler_research_batch_admission(batch_seconds=30, release_seconds=60)
            if not admission.get('admitted'):
                return {'facts': facts, 'claimed_task_ids': claimed_tasks,
                        'claimed_box_ids': claimed_boxes, 'yield_for_due_job': admission}
        if tasks:
            task = tasks[0]
            title = context.ocr_text_in_shapes(SCORE_TASK_SCENE, ['首行任务标题'],
                frame_data_url=scene.frame_data_url, crop=True, padding=10)
            if str(task['name']).replace(' ', '') not in ''.join(title.split()):
                raise RuntimeError(f'积分领取首行与任务{task["task_id"]}不符：{title}')
            yield from context.wait_click(SCORE_TASK_SCENE, '首行领取区')
            yield from context.wait_action_settle(2)
            after = read_lingchong_round_rewards(activity_id)
            if not any(r['task_id'] == task['task_id'] and r['claimed'] for r in after['current_tasks']):
                # After the last task, the round box still prevents advancement;
                # a round can leave current_tasks only after both receipts exist.
                if not after['rounds'][number - 1]['tasks_claimed']:
                    raise RuntimeError('积分任务点击后未形成领取凭证，禁止重复点击')
            claimed_tasks.append(task['task_id'])
            continue
        box = facts['rounds'][number - 1]
        if box['box_claimable']:
            if not box['tasks_claimed'] or page['total_rounds'] != len(facts['rounds']):
                raise RuntimeError('轮次礼包与任务完成事实不一致')
            # ActiveBgClick's native gate is getType==CanGet(2) and
            # curProcessId. Small stylized status text proved unreliable on
            # the third round; bind the visible panel to the exact box instead.
            # Re-read after wait_click's internal waits, immediately before
            # clicking; the durable receiveBoxList remains the success proof.
            def require_box_before_click():
                live = require_round_task_page(activity_id, number=number)
                if (live.get('box_config_id') != box['box_id']
                        or live.get('box_state_type') != 2):
                    raise RuntimeError('轮次礼包当前页面目标或可领取状态变化，停止点击')
            require_box_before_click()
            yield from context.wait_click(SCORE_TASK_SCENE, '轮次礼包领取区',
                before_click=require_box_before_click)
            yield from context.wait_action_settle(2)
            after = read_lingchong_round_rewards(activity_id)
            if box['box_id'] not in after['box_state']['claimed_config_ids']:
                raise RuntimeError('轮次礼包点击后receiveBoxList未确认，禁止重复点击')
            claimed_boxes.append(box['box_id'])
            if after['all_claimed']:
                return {'facts': after, 'claimed_task_ids': claimed_tasks, 'claimed_box_ids': claimed_boxes}
            continue
        return {'facts': facts, 'claimed_task_ids': claimed_tasks, 'claimed_box_ids': claimed_boxes}
    raise RuntimeError('灵宠积分轮次领取超过有界动作数')


def run_lingchong_round_rewards_flow(context, *, occurrence, options):
    if occurrence.instance_key not in options.get('instance_keys', []):
        raise ValueError('灵宠轮次未授权当前精确实例')
    if occurrence.activity_id != 16042901:
        raise ValueError('灵宠积分轮次尚未实机绑定该跨服页面')
    now = datetime.now().astimezone()
    if not occurrence.start_at <= now < occurrence.end_at:
        raise RuntimeError('灵宠积分轮次不在真实计分窗口')
    from backend.core.fanxiu.activity.lingchong_round_resource_planning import (
        read_round_resource_history, round_resource_samples, plan_round_resource_batch,
        begin_round_resource_observation, settle_round_resource_observation,
        assess_round_resource_reachability,
    )
    from backend.core.fanxiu.activity.lingchong_jingwu import collect_lingchong_jingwu_resource_snapshot
    from backend.core.fanxiu.instrumentation.pet_aptitude import read_pet_aptitude_runtime
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_scheduler_research_batch_admission
    from .pet_resource_use import use_pet_resource_batch
    from .pet_aptitude_navigation import prepare_pet_resource_page, enter_first_growing_pet_aptitude
    limit = int(options.get('max_resource_batches', 80))
    if not 0 <= limit <= 300:
        raise ValueError('轮次资源批数须为0到300')
    pet_id, pet_name = options.get('pet_id'), options.get('pet_name')
    if limit and (pet_id != 7101 or pet_name != '玄霜冰凤'):
        raise ValueError('轮次资源目标须绑定已验收的玄霜冰凤')
    receipts, observations, task_ids, box_ids = [], [], [], []
    assessment = None
    facts = read_lingchong_round_rewards(occurrence.activity_id)
    history = read_round_resource_history(occurrence)
    pending = [r for r in history if r.get('status') == 'round_observation_pending']
    if len(pending) > 1:
        raise RuntimeError('存在多个未结轮次积分观察')
    if pending:
        observations.append(settle_round_resource_observation(occurrence,
            observation=pending[0], history=history, facts=facts))
        if observations[-1]['score_after'] == observations[-1]['score_before']:
            yield from context.go_scene(34)
            return {'status': 'unavailable', 'terminal_reason': 'zero_score_gain', 'facts': facts,
                    'message': '上一批资源已结算但积分未增长，禁止重复消费，须诊断计分条件'}
    zero_samples = [r for r in history if r.get('status') == 'round_observation_verified'
        and r.get('instance_key') == occurrence.instance_key and r.get('score_after') == r.get('score_before')]
    if zero_samples:
        recovered = settle_round_resource_observation(occurrence,
            observation=zero_samples[-1], history=history, facts=facts)
        if recovered['score_after'] == recovered['score_before']:
            yield from context.go_scene(34)
            return {'status': 'unavailable', 'terminal_reason': 'zero_score_gain', 'facts': facts,
                    'message': '真实资源使用已结算，但积分未增长；停止消费，须诊断计分条件'}
    for batch in range(limit + 1):
        if facts['all_claimed']:
            status, reason = 'completed', 'all_rounds_claimed'
            break
        current = facts['rounds'][facts['current_round'] - 1]
        if any(r['claimable'] for r in facts['current_tasks']) or current['box_claimable']:
            yield from context.go_scene(34)
            yield from open_round_task_page(context, activity_id=occurrence.activity_id)
            result = yield from claim_loaded_round_rewards(context, activity_id=occurrence.activity_id)
            facts = result['facts']
            task_ids.extend(result['claimed_task_ids']); box_ids.extend(result['claimed_box_ids'])
            if result.get('yield_for_due_job'):
                status, reason = 'pending', 'higher_priority_trigger'
                admission = result['yield_for_due_job']
                break
            if facts['all_claimed']:
                status, reason = 'completed', 'all_rounds_claimed'
                break
        if batch == limit:
            status, reason = 'pending', 'bounded_batch_limit'
            break
        admission = read_scheduler_research_batch_admission(batch_seconds=600, release_seconds=120)
        if not admission['admitted']:
            status, reason = 'pending', 'higher_priority_trigger'
            break
        if (occurrence.end_at - datetime.now().astimezone()).total_seconds() < 720:
            status, reason = 'pending', 'scoring_window_budget'
            break
        # Swallow preparation may add score. Establish the baseline only after
        # it settles, and use the first growing pet's actual visible identity.
        yield from context.go_scene(483)
        yield from prepare_pet_resource_page(context)
        frame = context.cur_frame(update=True)
        rows = context.ocr_fragments_in_shapes(483, ['第一个成长灵兽'], frame_data_url=frame)
        if not any(pet_name in r.get('text', '') for r in rows):
            raise RuntimeError('轮次资源目标与首个成长灵兽不符')
        yield from enter_first_growing_pet_aptitude(context)
        facts = read_lingchong_round_rewards(occurrence.activity_id)
        resources = collect_lingchong_jingwu_resource_snapshot(activity_id=str(occurrence.activity_id))
        pet = read_pet_aptitude_runtime(expected_pet_id=pet_id)
        if pet['pending_swallow_count']:
            raise RuntimeError('灵兽吞噬尚未稳定，禁止积分基线消费')
        history = read_round_resource_history(occurrence)
        samples = round_resource_samples(history, instance_key=occurrence.instance_key, pet_id=pet_id)
        assessment = assess_round_resource_reachability(facts, resources.items, pet['target'], history,
            instance_key=occurrence.instance_key, pet_id=pet_id)
        from backend.core.fanxiu.data_annotation.kernel_scheduler_control import record_world_discovery
        record_world_discovery('monitor:lingchong-round-resource-budget',
            {'instance_key': occurrence.instance_key, 'captured_at': datetime.now().astimezone().isoformat(),
             'assessment': assessment})
        plan = plan_round_resource_batch(facts, resources.items, pet['target'], samples)
        if not plan['quantity']:
            if plan['mode'] == 'rewards_required':
                continue
            status, reason = 'unavailable', plan['mode']
            break
        admission = read_scheduler_research_batch_admission(batch_seconds=600, release_seconds=120)
        if not admission['admitted']:
            status, reason = 'pending', 'higher_priority_trigger'
            break
        observation = begin_round_resource_observation(occurrence, pet_id=pet_id, facts=facts, plan=plan)
        receipt = yield from use_pet_resource_batch(context, activity_id=occurrence.activity_id,
            pet_id=pet_id, item_id=plan['item_id'], item_name=plan['item_name'],
            quantity=plan['quantity'], base_gain=plan['base_gain'], verify_task_progress=False,
            consumption_window=(occurrence.start_at, occurrence.end_at))
        receipts.append(receipt)
        facts = read_lingchong_round_rewards(occurrence.activity_id)
        for _ in range(3):
            if facts['score'] > observation['score_before']:
                break
            yield from context.wait_action_settle(2)
            facts = read_lingchong_round_rewards(occurrence.activity_id)
        settled = settle_round_resource_observation(occurrence, observation=observation,
            history=read_round_resource_history(occurrence), facts=facts)
        observations.append(settled)
        if settled['score_after'] == settled['score_before']:
            status, reason = 'unavailable', 'zero_score_gain'
            break
    yield from context.go_scene(34)
    return {'status': status, 'terminal_reason': reason, 'facts': facts,
            'round_budget_assessment': assessment,
            'receipts': receipts, 'score_observations': observations,
            'claimed_task_ids': task_ids, 'claimed_box_ids': box_ids,
            'performed_actions': bool(receipts or task_ids or box_ids),
            'yield_reason': 'higher_priority_trigger' if reason == 'higher_priority_trigger' else '',
            'retry_at': admission['retry_at'] if reason == 'higher_priority_trigger' else datetime.now().astimezone().isoformat(),
            'message': f'积分轮次：{reason}；本次资源批次{len(receipts)}，任务领取{len(task_ids)}，轮次礼包{len(box_ids)}'}


def execute_lingchong_round_rewards_cell(runner, ctx, payload, stop_event):
    from backend.core.fanxiu.activity.ranking_lifecycle import discover_ranking_occurrences
    from backend.core.fanxiu.activity.runtime_schedule import read_fanxiu_activity_runtime_schedule
    schedule = read_fanxiu_activity_runtime_schedule()
    if not (schedule.get('available') and schedule.get('complete')):
        raise RuntimeError('灵宠轮次Runtime日程不可读')
    candidates = [r for r in discover_ranking_occurrences(schedule)
                  if r.instance_key in payload.get('instance_keys', [])]
    if len(candidates) != 1:
        raise ValueError('灵宠轮次Task须绑定一个精确活动实例')
    context = runner._behavior_tree_context(ctx, ctx.get('asset_tree_path'), stop_event=stop_event)
    return (yield from run_lingchong_round_rewards_flow(context, occurrence=candidates[0], options=payload))
