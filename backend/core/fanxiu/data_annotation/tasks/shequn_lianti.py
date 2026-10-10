"""Occurrence-bound 社团炼体 navigation; resource use is a separate component.

The shared #754 introduction leads to the clan board #944, rather than the
individual/plane board #755. Scene recognition establishes layout; native
panel identity establishes the selected event. Opening the page grants no
purchase, training, or reward-click permission.
"""
from backend.core.fanxiu.activity.shequn_lianti import read_current_shequn_lianti_goal
from backend.core.fanxiu.activity.runtime_schedule import read_fanxiu_activity_runtime_schedule
from backend.core.fanxiu.instrumentation.activity_rank_page import (
    read_activity_rank_page_snapshot, read_activity_rank_task_page_snapshot,
    read_activity_rank_participation_snapshot,
)
from backend.core.fanxiu.data_annotation.schedule_navigation import select_schedule_activity


def open_shequn_lianti_rank_page(context):
    """Open the exact current 8-cross occurrence and verify its clan board.

    The target uses the currently materialized world-line ID, not a saved
    before-opening ID. A page already open for this event is reusable only
    after both positive scene recognition and native identity validation.
    Unknown layouts or changed periods stop before any resource action.
    """
    goal = read_current_shequn_lianti_goal()
    if not goal.get('available') or not goal.get('complete'):
        raise RuntimeError('社团炼体当前任务实例尚未完整加载')
    period = goal['period']
    page = read_activity_rank_page_snapshot()
    if (page.get('complete') and page.get('activity_id') == 8043401
            and page.get('selected_rank_activity_id') == goal['training_activity_id']
            and page.get('tab_index') == 0):
        yield from context.wait_scene_exact((944,), timeout=20)
    else:
        yield from context.go_scene(66, known_paths_only=True)
        yield from context.wait_scene_exact((66,), timeout=20)
        schedule = read_fanxiu_activity_runtime_schedule(allow_discovery=True)
        rows = [row for row in schedule.get('items') or []
                if row.get('id') == period['runtime_id']
                and row.get('activityId') == 8043401 and row.get('serverCount') == 8
                and row.get('startTime') == period['start_time_ms']
                and row.get('endTime') == period['end_time_ms']]
        if not schedule.get('complete') or len(rows) != 1:
            raise RuntimeError('社团炼体导航期间当前世界线实例发生变化')
        yield from select_schedule_activity(
            context, '社团炼体', enter=True, runtime_schedule=schedule,
            require_runtime_alignment=True, expected_activity_id=8043401,
            expected_runtime_id=str(period['runtime_id']), expected_cross_count=8)
        landing = yield from context.wait_scene_exact((754, 944), timeout=30)
        if landing.scene_id == 754:
            context.click_shape_center(754, '查看详情')
            yield from context.wait_scene_exact((944,), timeout=30)
    page = read_activity_rank_page_snapshot()
    if (not page.get('complete') or page.get('activity_id') != 8043401
            or page.get('selected_rank_activity_id') != goal['training_activity_id']
            or page.get('tab_index') != 0):
        raise RuntimeError('社团炼体当前社团榜页面身份不符')
    return {'scene_id': 944, 'goal': goal, 'page': page,
            'performed_resource_actions': False}


def open_shequn_lianti_task_page(context):
    """Open only the verified basic 14-tier task layout, never round rewards."""
    task = read_activity_rank_task_page_snapshot()
    if not (task.get('complete') and task.get('activity_id') == 8043401):
        yield from open_shequn_lianti_rank_page(context)
        eligibility = read_activity_rank_participation_snapshot(43403)
        if not eligibility.get('complete') or eligibility.get('is_join_rank') is not True:
            raise RuntimeError('社团炼体任务资格未完整确认')
        # The task tab appears after eligibility has loaded. Its position
        # differs from the three-tab first entry; match its text before click.
        yield from context.wait_click(944, '任务页签', timeout=15)
    yield from context.wait_scene_exact((945,), timeout=25)
    task = read_activity_rank_task_page_snapshot()
    if (not task.get('complete') or task.get('activity_id') != 8043401
            or task.get('tab_index') != 2 or task.get('sub_tab_index') != 7
            or task.get('current_round') != 0 or task.get('total_rounds') != 0):
        raise RuntimeError('社团炼体基础任务页身份或模式不符')
    return task


def open_shequn_lianti_training_settings(context, *, expected_book_id=910515):
    """Open training configuration without learning, upgrading or spending.

    Book ordering is dynamic. The first book is only a navigation choice;
    native identity must match before its training button is touched. The
    configuration's default node count is never permission to start it.
    """
    from backend.core.fanxiu.instrumentation.physical_comprehension import read_physical_comprehension_state
    from backend.core.fanxiu.instrumentation.physical_training import read_physical_training_dialog_snapshot

    goal = read_current_shequn_lianti_goal()
    if not goal.get('complete') or not goal.get('training_binding_verified'):
        raise RuntimeError('社团炼体当前淬炼活动绑定不完整')
    if goal.get('activity_progress', 0) >= goal['target_score']:
        raise RuntimeError('社团炼体任务目标已达到，停止打开淬炼配置')
    yield from context.go_scene(34, known_paths_only=True)
    yield from context.wait_click_then_scene(34, '角色', 770, timeout=20, max_clicks=1)
    yield from context.wait_click_then_scene(770, '炼体', 867, timeout=30, max_clicks=1)
    yield from context.wait_click(867, '第一本', timeout=10)
    detail = yield from context.wait_scene_exact((899, 868, 869), timeout=30)
    if detail.scene_id == 899:
        raise RuntimeError('社团炼体选择了未领悟功法，保留现场且不领悟')
    if detail.scene_id == 868:
        yield from context.wait_click_then_scene(868, '淬体', 869, timeout=20, max_clicks=1)
    state = read_physical_comprehension_state()
    if state.get('base_id') != expected_book_id:
        raise RuntimeError('社团炼体功法排序变化，禁止按旧选择开启淬炼')
    yield from context.wait_click(869, '淬炼', timeout=10)
    yield from context.wait_scene_exact((943,), timeout=20)
    dialog = read_physical_training_dialog_snapshot()
    if (dialog.get('base_id') != expected_book_id
            or dialog.get('activity_id') != goal['training_activity_id']
            or dialog.get('auto_buy_enabled') is not False
            or dialog.get('multiplicity_enabled') is not False):
        raise RuntimeError('社团炼体配置身份、自动购买或倍率门禁不符')
    return {'goal': goal, 'dialog': dialog, 'performed_resource_actions': False}


def configure_shequn_lianti_training_nodes(context, *, nodes, expected_book_id=910515):
    """Configure an exact bounded batch; return a fresh cost proof, never start.

    The native slider maximum describes its range, not our spending budget.
    Batch cost must fit a freshly observed prefix, available normal stock and
    zero breakthrough use before the caller may consider its start action.
    """
    from backend.core.fanxiu.instrumentation.physical_training import read_physical_training_dialog_snapshot
    from backend.core.fanxiu.runtime_gui.integer_count_control import (
        IntegerSliderAssets, set_verified_integer_slider_count,
    )
    if type(nodes) is not int or not 1 <= nodes <= 512:
        raise ValueError('社团炼体单批配置范围为1至512节点')
    yield from context.wait_scene_exact((943,), timeout=15)
    before = read_physical_training_dialog_snapshot(max_cost_nodes=nodes)
    if (before.get('base_id') != expected_book_id or before.get('activity_id') != 43403
            or before.get('auto_buy_enabled') is not False
            or before.get('multiplicity_enabled') is not False):
        raise RuntimeError('社团炼体数量配置前身份或消费门禁不符')
    assets = IntegerSliderAssets(
        settings_scene_id=943, count_region='预计淬炼次数',
        count_decrease='减少数量', count_increase='增加数量',
        count_slider_thumb='数量滑块', count_slider_track='数量滑条',
        count_slider_left_anchor='减少数量', count_slider_right_anchor='增加数量',
        count_slider_left_center_offset=34, count_slider_right_center_offset=-30)
    adjustment = yield from set_verified_integer_slider_count(
        # At the left endpoint the thumb overlaps the decrement button.
        # A proportional drag can land within a few nodes but its tiny thumb
        # then cannot be uniquely matched. Use the helper's bounded 30-tap
        # exact +/- phase there; fresh Runtime feedback still proves nodes.
        context, assets, nodes, max_adjustments=30, max_button_actions=30,
        maximum=before['maximum_nodes'], initial_count=before['selected_nodes'],
        # Feedback changes only the slider. Local OCR avoids repeatedly
        # projecting the full native cost path between +/- taps. The fresh
        # native observation below, and the independent pre-start guard,
        # remain the authority for identity, exact count and spending.
        count_label='社团炼体淬炼节点')
    after = read_physical_training_dialog_snapshot(max_cost_nodes=nodes)
    if (not after.get('complete') or after.get('selected_nodes') != nodes
            or after.get('base_id') != expected_book_id or after.get('activity_id') != 43403
            or after.get('current_node') != before['current_node']
            or after.get('stage') != before['stage']
            or after.get('auto_buy_enabled') is not False
            or after.get('multiplicity_enabled') is not False
            or after.get('breakthrough_material_cost') != 0
            or after['normal_material_cost'] > after['available_normal_material']):
        raise RuntimeError('社团炼体单批精确成本或消费门禁尚未验证')
    return {'dialog': after, 'adjustment': adjustment, 'performed_resource_actions': False}


def leave_shequn_lianti_training_settings(context):
    """Cancel an unstarted configuration and return through named controls."""
    yield from context.wait_click_then_scene(943, '关闭', 869, timeout=20, max_clicks=1)
    yield from context.wait_click_then_scene(869, '返回', 867, timeout=20, max_clicks=1)
    yield from context.wait_click_then_scene(867, '属性', 770, timeout=20, max_clicks=1)
    yield from context.go_scene(34, known_paths_only=True)
    yield from context.wait_scene_exact((34,), timeout=15)
    return {'final_scene': 34, 'performed_resource_actions': False}


def run_shequn_lianti_training_batch(context, *, nodes=1, expected_book_id=910515,
                                    goal_feedback=False):
    """Submit one exact batch once; reconcile pending effects before navigation.

    One-node start/effect acceptance is recorded in the occurrence receipt.
    Fixed-rate sizing requires a verified score rule and target-bound plan.
    The whole-goal feedback mode instead permits at most one native send
    quantum (10 nodes, PhysicalAutoStartView.SendToServerUser), with exact
    normal-material costs and actual task progress after every quantum.
    It makes no predicted score/completion claim. A timeout
    leaves the original action pending; a fresh invocation reads it without
    resending the start click or replaying an old generator.
    """
    from backend.core.fanxiu.activity.shequn_lianti_receipts import (
        begin_shequn_lianti_training_action, reconcile_shequn_lianti_training_action,
        read_shequn_lianti_training_receipts, read_shequn_lianti_training_score_rule,
    )
    from backend.core.fanxiu.instrumentation.physical_training import (
        read_physical_training_books_snapshot, read_physical_training_dialog_snapshot,
    )
    from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts

    if type(goal_feedback) is not bool:
        raise ValueError('社团炼体任务反馈模式必须为布尔值')
    if goal_feedback and (type(nodes) is not int or not 1 <= nodes <= 10):
        raise ValueError('社团炼体任务反馈单批不能超过原生10节点发送边界')

    goal = read_current_shequn_lianti_goal()
    if not goal.get('complete') or not goal.get('training_binding_verified'):
        raise RuntimeError('社团炼体单批执行缺少当前实例')
    key = goal['occurrence_key']

    def reconcile(action_id, dialog):
        books = read_physical_training_books_snapshot()
        candidates = [book for book in books.get('books', [])
                      if book.get('base_id') == dialog['base_id']]
        if not books.get('complete') or len(candidates) != 1:
            raise RuntimeError('社团炼体效果功法身份不完整，禁止重放')
        counts, _ = read_backpack_item_counts(
            [dialog['normal_item_id']], manager_key='shequn-lianti-training', force_refresh=True)
        after_goal = read_current_shequn_lianti_goal()
        return reconcile_shequn_lianti_training_action(
            occurrence_key=key, action_id=action_id, book=candidates[0],
            inventory_count=counts[dialog['normal_item_id']], goal=after_goal)

    pending = [row for row in read_shequn_lianti_training_receipts(key)
               if row.get('status') == 'pending']
    if pending:
        if len(pending) != 1:
            raise RuntimeError('社团炼体待核验操作不唯一')
        receipt = reconcile(pending[0]['action_id'], pending[0]['dialog_before'])
        if receipt['status'] != 'verified':
            raise RuntimeError('社团炼体原action_id效果未确定，禁止再次启动')
        # A recovered invocation ends at this effect boundary. The caller
        # chooses further work from fresh progress, never the previous intent.
        yield from context.go_scene(34, known_paths_only=True)
        return {'receipt': receipt, 'recovered': True, 'started_now': False}
    yield from open_shequn_lianti_training_settings(context, expected_book_id=expected_book_id)
    configured = yield from configure_shequn_lianti_training_nodes(
        context, nodes=nodes, expected_book_id=expected_book_id)
    dialog = configured['dialog']
    if nodes > 1 and not goal_feedback:
        from backend.core.fanxiu.instrumentation.physical_training import read_physical_training_scoring_snapshot
        from backend.core.fanxiu.activity.shequn_lianti import plan_shequn_lianti_training_batch
        native = read_physical_training_scoring_snapshot(
            base_id=expected_book_id, item_id=dialog['normal_item_id'])
        rule = read_shequn_lianti_training_score_rule(
            occurrence_key=key, item_id=dialog['normal_item_id'],
            native_rate=native['configured_score_per_item'])
        if not rule['verified']:
            raise RuntimeError('社团炼体计分尚无真实消费样本，禁止扩批')
        planned = plan_shequn_lianti_training_batch(
            goal=read_current_shequn_lianti_goal(), dialog=dialog,
            inventory={dialog['normal_item_id']: dialog['available_normal_material']},
            score_per_item={dialog['normal_item_id']: rule['score_per_item']},
            max_batch_nodes=nodes)
        if planned['kind'] != 'train' or planned['selected_nodes'] != nodes:
            raise RuntimeError('社团炼体单批数量不等于当前目标的有界成本前缀，禁止启动')
    action_id = None
    def reserve_before_click():
        nonlocal action_id
        current = read_physical_training_dialog_snapshot(max_cost_nodes=nodes)
        fresh_goal = read_current_shequn_lianti_goal()
        fields = ('base_id','activity_id','current_node','stage','selected_nodes',
                  'normal_item_id','normal_material_cost','available_normal_material')
        if (fresh_goal.get('occurrence_key') != key
                or any(current.get(k) != dialog[k] for k in fields)):
            raise RuntimeError('社团炼体提交前实例或精确配置变化')
        if nodes > 1 and not goal_feedback:
            final_plan = plan_shequn_lianti_training_batch(
                goal=fresh_goal, dialog=current,
                inventory={current['normal_item_id']: current['available_normal_material']},
                score_per_item={current['normal_item_id']: rule['score_per_item']},
                max_batch_nodes=nodes)
            if final_plan['kind'] != 'train' or final_plan['selected_nodes'] != nodes:
                raise RuntimeError('社团炼体提交前社团积分变化，需按新任务进度重新规划')
        action_id = begin_shequn_lianti_training_action(goal=fresh_goal, dialog=current)
    yield from context.wait_click_then_scene(
        943, '开启自动', 869, timeout=180, max_clicks=1,
        retry_if_source_remains=False, before_click=reserve_before_click)
    receipt = reconcile(action_id, dialog)
    if receipt['status'] != 'verified':
        raise RuntimeError('社团炼体实际节点或库存尚未核验，原action_id保留')
    yield from context.wait_click_then_scene(869, '返回', 867, timeout=20, max_clicks=1)
    yield from context.wait_click_then_scene(867, '属性', 770, timeout=20, max_clicks=1)
    yield from context.go_scene(34, known_paths_only=True)
    return {'receipt': receipt, 'recovered': False, 'started_now': True}


def run_shequn_lianti_goal(context, *, stop_event, max_batches=1):
    """Advance the highest live task target, then claim all its reward tiers.

    One native send quantum is a bounded action toward the owner's full
    target. It never divides aggregate progress by material use or
    extrapolates that observation into a large batch. Each
    settled action returns to the world and supplies its original receipt.
    Ordinary due Jobs take priority at those stable boundaries. A bounded
    invocation returns partial, retaining the same occurrence and highest
    target; only actual fourteen-tier reward receipts mean completion.
    """
    from datetime import datetime
    from time import monotonic
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_scheduler_tasks
    from backend.core.fanxiu.data_annotation.state import parse_data_annotation_task_time
    from backend.core.fanxiu.activity.shequn_lianti_receipts import read_shequn_lianti_training_receipts

    if type(max_batches) is not int or not 1 <= max_batches <= 64:
        raise ValueError('社团炼体单次节点批数必须为1到64')
    initial = read_current_shequn_lianti_goal()
    if not initial.get('available'):
        return {'result': 'not_applicable', 'goal_completed': False,
                'message': '当前没有开放的社团炼体实例', 'started_batches': 0}
    key = initial['occurrence_key']
    started, recovered, actions = 0, 0, []
    batch_seconds = []
    goal = initial

    def partial(goal, reason, **extra):
        return {'result': 'partial', 'goal_completed': False, 'reason': reason,
                'message': f"社团炼体任务进度 {goal['activity_progress']}/{goal['target_score']}，整单未完成",
                'occurrence_key': key, 'target_score': goal['target_score'],
                'activity_progress': goal['activity_progress'], 'started_batches': started,
                'recovered_batches': recovered, 'action_ids': actions,
                'batch_seconds': batch_seconds,
                'goal_evidence': goal, **extra}

    while True:
        if stop_event.is_set():
            raise InterruptedError()
        # No action occurs between initial capture and the first iteration.
        # Subsequent iterations refresh at the completed batch boundary.
        if (not goal.get('available') or not goal.get('complete')
                or goal.get('occurrence_key') != key or goal['target_score'] != initial['target_score']):
            raise RuntimeError('社团炼体整单期间实例或最高任务目标变化，停止消费')
        receipts = read_shequn_lianti_training_receipts(key)
        pending = any(row['status'] == 'pending' for row in receipts)
        if not pending:
            if goal['activity_progress'] >= goal['target_score']:
                claimed = yield from claim_shequn_lianti_goal_rewards(context)
                after = read_current_shequn_lianti_goal()
                if after.get('occurrence_key') != key or not after.get('goal_completed'):
                    raise RuntimeError('社团炼体整单最高目标及14档领奖凭证未完整')
                return {'result': 'success', 'goal_completed': True, 'occurrence_key': key,
                        'target_score': goal['target_score'], 'claimed': claimed,
                        'started_batches': started, 'recovered_batches': recovered,
                        'action_ids': actions, 'batch_seconds': batch_seconds,
                        'goal_evidence': after}
            # A verified material effect does not prove that its score packet
            # has arrived. Never spend another node to compensate for latency.
            if receipts and goal['activity_progress'] <= receipts[-1]['goal_before']['activity_progress']:
                return partial(goal, 'await_original_score_effect', action_id=receipts[-1]['action_id'])
            now = datetime.now().timestamp()
            scheduled = [(task['id'], trigger) for task in read_scheduler_tasks()
                         if task.get('id') != 'resource-ranking' and task.get('enabled', True)
                         and (trigger := parse_data_annotation_task_time(task.get('next_time'))) is not None]
            due = [identity for identity, trigger in scheduled if trigger <= now]
            if due:
                return partial(goal, 'due_jobs_take_priority', due_job_ids=due)
            if started + recovered >= max_batches:
                return partial(goal, 'bounded_batch_boundary')
            # Use this invocation's actual completed batch cost, never a
            # guessed node/score rate, to avoid starting a new long GUI batch
            # just before an ordinary Job is due. The fresh goal read and
            # dispatch handoff receive an additional 30-second allowance.
            if batch_seconds:
                reserve_seconds = max(batch_seconds) + 30
                upcoming = [identity for identity, trigger in scheduled
                            if now < trigger <= now + reserve_seconds]
                if upcoming:
                    return partial(goal, 'upcoming_due_jobs_take_priority',
                                   due_job_ids=upcoming, reserve_seconds=reserve_seconds)
            eligibility = read_activity_rank_participation_snapshot(goal['training_activity_id'])
            if not eligibility.get('complete'):
                yield from open_shequn_lianti_rank_page(context)
                eligibility = read_activity_rank_participation_snapshot(goal['training_activity_id'])
                yield from context.go_scene(34, known_paths_only=True)
            if not eligibility.get('complete') or eligibility.get('is_join_rank') is not True:
                raise RuntimeError('社团炼体当前参榜资格未确认，停止消费')
        batch_started = monotonic()
        batch = yield from run_shequn_lianti_training_batch(
            context, nodes=10, goal_feedback=True)
        batch_seconds.append(monotonic() - batch_started)
        receipt = batch['receipt']
        actions.append(receipt['action_id'])
        if batch['started_now']:
            started += 1
        else:
            recovered += 1
        goal = read_current_shequn_lianti_goal()


def claim_shequn_lianti_goal_rewards(context):
    """Claim the verified goal tiers only after the highest target is reached.

    QuestMgr proves completion, including retries after earlier claims. The
    row's left body excludes item-detail icons. GUI claims remain pending live
    acceptance until real 600000 progress and all 14 receipts are observed.
    """
    from .task_reward_rows import claim_task_rows_by_ocr

    goal = read_current_shequn_lianti_goal()
    if not goal.get('complete') or len(goal.get('task_ids') or []) != 14:
        raise RuntimeError('社团炼体任务档次事实不完整')
    if goal.get('goal_completed'):
        yield from context.go_scene(34, known_paths_only=True)
        return {'completed': True, 'already_claimed': True, 'claimed_now': 0}
    if goal.get('activity_progress', -1) < goal.get('target_score', 600000):
        raise RuntimeError('社团炼体最高任务档次尚未达到，禁止领奖行点击')
    yield from open_shequn_lianti_task_page(context)
    claims = yield from claim_task_rows_by_ocr(
        context, scene_id=945, first_row_shape='首行领取区',
        observer_shape='第三行任务标题', progress_shape='首行进度',
        require_ratio_separator=True, label='社团炼体任务档次奖励',
        max_clicks=len(goal.get('claimable_task_ids') or []))
    after = read_current_shequn_lianti_goal()
    if not after.get('goal_completed') or after.get('claimable_task_ids'):
        raise RuntimeError('社团炼体14档实际领取凭证尚未完整')
    yield from context.go_scene(34, known_paths_only=True)
    return {'completed': True, 'total_claimed': 14, 'claims': claims}


__all__ = ['open_shequn_lianti_rank_page', 'open_shequn_lianti_task_page',
           'open_shequn_lianti_training_settings', 'configure_shequn_lianti_training_nodes',
           'leave_shequn_lianti_training_settings', 'run_shequn_lianti_training_batch',
           'claim_shequn_lianti_goal_rewards', 'run_shequn_lianti_goal']
