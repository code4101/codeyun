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
    def read_count():
        snapshot = read_physical_training_dialog_snapshot(max_cost_nodes=nodes)
        if (snapshot.get('base_id') != expected_book_id
                or snapshot.get('current_node') != before['current_node']
                or snapshot.get('stage') != before['stage']):
            raise RuntimeError('社团炼体配置中功法或节点发生变化')
        return snapshot['selected_nodes']
    adjustment = yield from set_verified_integer_slider_count(
        context, assets, nodes, max_adjustments=1,
        maximum=before['maximum_nodes'], initial_count=before['selected_nodes'],
        runtime_count_reader=read_count, count_label='社团炼体淬炼节点')
    after = read_physical_training_dialog_snapshot(max_cost_nodes=nodes)
    if (not after.get('complete') or after.get('selected_nodes') != nodes
            or after.get('base_id') != expected_book_id or after.get('activity_id') != 43403
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
           'leave_shequn_lianti_training_settings', 'claim_shequn_lianti_goal_rewards']
