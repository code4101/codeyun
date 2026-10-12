"""Read the exact active RevenueTask list, including native reward readiness.

The scroll view owns the displayed order and already filters task conditions.
This reader never reconstructs that order from exported task configuration,
opens a page, invokes Lua, or sends a reward request.
"""
from datetime import datetime

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    active_ui_component_objects, has_ui_object_fields, read_ui_object_field,
    read_ui_runtime_snapshot, read_ui_selected_tab_panel, acquire_lua_global_runtime_context,
)


def read_revenue_task_page_snapshot(activity_id: int, *, expected_task_id: int | None = None) -> dict:
    """Return one active activity/subtype and its native ordered task rows.

    ``first_row`` is a click candidate only while the native list is at its
    top. Reward readiness follows RankTaskItem's turn/progress rule. It does
    not authorize completing unfinished conditions or buying resources.
    """
    if type(activity_id) is not int or activity_id <= 0:
        raise ValueError('需要精确的正整数活动ID')
    if expected_task_id is not None and (type(expected_task_id) is not int or expected_task_id <= 0):
        raise ValueError('待核验任务ID必须是正整数')

    def observe(ctx):
        candidates = {}
        for obj in active_ui_component_objects(ctx):
            address = obj.address
            for _ in range(4):
                if (as_int(read_ui_object_field(ctx, address, 'V_ActivityId')) == activity_id
                        and has_ui_object_fields(ctx, address, {'RankTaskItem', 'scrollView', 'V_SubType'})):
                    candidates[address] = address
                selected = read_ui_selected_tab_panel(ctx, address)
                if selected is None:
                    break
                address = selected[0]
        if len(candidates) != 1:
            raise FanxiuRuntimeMemoryError('当期活动任务页未唯一打开', code='data_not_loaded')
        address = next(iter(candidates))
        scroll = table_ref(read_ui_object_field(ctx, address, 'scrollView'))
        subtype = as_int(read_ui_object_field(ctx, address, 'V_SubType'))
        if scroll is None or subtype is None:
            raise FanxiuRuntimeMemoryError('任务页列表或页签缺失', code='runtime_incomplete')
        items, count = ctx.reader.list_items(read_ui_object_field(ctx, scroll.address, 'ItemInfoList'))
        if count is None or not 0 <= count == len(items) <= 1000:
            raise FanxiuRuntimeMemoryError('活动任务列表读取不完整', code='runtime_incomplete')
        tasks = []
        for value in items:
            row = ctx.reader.fields(value)
            config = ctx.reader.fields(row.get('configData'))
            task_id = as_int(row.get('id'))
            if (not task_id or as_int(config.get('id')) != task_id
                    or as_int(config.get('activityId')) != activity_id
                    or type(row.get('isFinished')) is not bool):
                raise FanxiuRuntimeMemoryError('活动任务身份或完成状态不一致', code='runtime_incomplete')
            finished = row['isFinished']
            server = ctx.reader.fields(row.get('serverData'))
            progress = []
            turn, reward_time = as_int(server.get('turn')), as_int(server.get('rewardTime'))
            if not finished:
                values, declared = ctx.reader.list_items(server.get('progressList'))
                if (as_int(server.get('taskId')) != task_id or turn is None or reward_time is None
                        or declared is None or not 0 < declared == len(values) <= 100):
                    raise FanxiuRuntimeMemoryError('未完成任务进度不完整', code='runtime_incomplete')
                for entry in values:
                    fields = ctx.reader.fields(entry)
                    if type(fields.get('finish')) is not bool:
                        raise FanxiuRuntimeMemoryError('任务进度完成标记缺失', code='runtime_incomplete')
                    progress.append({key: fields.get(key) for key in ('finish', 'progress', 'target')})
            claimable = not finished and (turn > reward_time or (
                turn == reward_time and all(entry['finish'] for entry in progress)))
            reward = config.get('reward')
            if table_ref(reward) is not None:
                table = ctx.reader.table(reward.address)
                numeric = {k: v for k, v in enumerate(table['array']) if k > 0 and v is not None}
                numeric.update({as_int(k): v for k, v in table['fields'].items()
                                if as_int(k) is not None and as_int(k) > 0})
                if sorted(numeric) != list(range(1, len(numeric) + 1)):
                    raise FanxiuRuntimeMemoryError('任务奖励数组缺项', code='runtime_incomplete')
                reward = [v for k, v in sorted(numeric.items())]
            if not reward or not (isinstance(reward, str) or (
                    isinstance(reward, list) and all(isinstance(v, str) and v for v in reward))):
                raise FanxiuRuntimeMemoryError('任务奖励配置不完整', code='runtime_incomplete')
            tasks.append({'task_id': task_id, 'name': config.get('name'), 'description': config.get('desc'),
                          'reward': reward, 'claimed': finished, 'claimable': claimable,
                          'turn': turn, 'reward_time': reward_time, 'progress': progress})
        if len({row['task_id'] for row in tasks}) != count:
            raise FanxiuRuntimeMemoryError('活动任务ID重复', code='runtime_incomplete')
        at_top = as_int(read_ui_object_field(ctx, scroll.address, 'Index')) == 0
        first_row = None
        if tasks and at_top:
            controllers = ctx.reader.dictionary_fields(read_ui_object_field(ctx, scroll.address, 'ItemClassDic'))
            first = [ctx.reader.fields(value) for value in controllers.values()
                     if as_int(ctx.reader.fields(value).get('index')) == 0]
            if (len(first) == 1 and as_int(ctx.reader.fields(first[0].get('data')).get('id')) == tasks[0]['task_id']
                    and first[0].get('canGetReward') is tasks[0]['claimable']):
                first_row = tasks[0]
        quest = acquire_lua_global_runtime_context({'QuestMgr'})
        if (quest.binding.pid, quest.binding.process_start_ticks) != (ctx.binding.pid, ctx.binding.process_start_ticks):
            raise FanxiuRuntimeMemoryError('任务页面与领奖记录进程不一致', code='snapshot_incoherent')
        manager = quest.loaded_manager_fields('QuestMgr', frozenset({'Inst_get'}))
        reader = quest.reader
        model = reader.fields(reader.fields(manager.get('inst')).get('Model'))
        data = reader.fields(model.get('QuestData'))
        task_types = reader.dictionary_fields(data.get('taskInfoMap'))
        activity_tasks = reader.fields(task_types.get(3))
        finished_values, finished_count = reader.list_items(activity_tasks.get('finishTasks'))
        if finished_count is None or not 0 <= finished_count == len(finished_values) <= 100000:
            raise FanxiuRuntimeMemoryError('原生任务领奖记录不完整', code='runtime_incomplete')
        finished_ids = {as_int(value) for value in finished_values}
        if None in finished_ids or any(value <= 0 for value in finished_ids):
            raise FanxiuRuntimeMemoryError('原生已领奖任务ID异常', code='runtime_incomplete')
        authorized = [row['task_id'] for row in tasks if row['claimable']]
        if finished_ids.intersection(authorized):
            raise FanxiuRuntimeMemoryError('任务页面可领状态与原生已领奖记录冲突', code='snapshot_incoherent')
        if authorized and (first_row is None or first_row['task_id'] != authorized[0]):
            raise FanxiuRuntimeMemoryError('可领首行与原生列表或控制器未对齐', code='runtime_incomplete')
        relevant_ids = {row['task_id'] for row in tasks}
        if expected_task_id is not None:
            relevant_ids.add(expected_task_id)
        return {'activity_id': activity_id, 'subtype': subtype, 'complete': True, 'read_only': True,
                'ok': True, 'available': True, 'authorized_claim_task_ids': authorized,
                'claimed_task_ids': sorted(finished_ids & relevant_ids),
                'expected_task_claimed': expected_task_id in finished_ids if expected_task_id else None,
                'state': 'ready' if authorized else 'nothing_claimable',
                'captured_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                'tasks': tasks, 'at_top': at_top, 'first_row': first_row,
                'panel_address': hex(address),
                'evidence': {'pid': ctx.binding.pid, 'process_start_ticks': ctx.binding.process_start_ticks}}

    try:
        return read_ui_runtime_snapshot((), observe, fast=True)
    except (FanxiuRuntimeMemoryError, OSError, ValueError) as exc:
        return {'activity_id': activity_id, 'complete': False, 'read_only': True,
                'reason': str(exc), 'code': getattr(exc, 'code', 'runtime_unavailable')}
