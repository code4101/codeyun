"""从当前祈愿任务面板读取整轮任务数据；只读，不导航或执行游戏 Lua。"""
import time
import re

from .runtime_memory import FanxiuRuntimeMemoryError, LuaRef, as_int, table_ref
from .resource_auto_use import read_runtime_config_rows
from .ui_runtime_context import (
    active_ui_component_objects, read_ui_object_field,
    read_ui_runtime_snapshot, read_ui_selected_tab_panel,
)


def read_prayer_task_panel(*, expected_activity_id: int, diagnostic: bool = False) -> dict:
    """读取实际活动的当前轮次、完整列表及服务器进度与轮次偏移。

    调用前须打开祈愿任务页。本接口沿当前选中页签读取 ItemInfoList，
    不是仅可见的渲染行；ThemeWeekActivityTaskItem 显示值为服务器
    progress/target 分别减 configData.sub。保留每行原始量供调用者
    核验整轮最高档，禁止将此取证当作领奖或资源消耗授权。
    窗口未打开、活动不符、列表/服务器数据缺失或多义时失败关闭。
    已在炼丹第5轮实机核验完整20行及连续稳定读取。
    """
    if type(expected_activity_id) is not int or expected_activity_id <= 0:
        raise ValueError('祈愿面板取证需要实际活动正整数 ID')

    def read(c):
        def fail(message):
            raise FanxiuRuntimeMemoryError('祈愿任务面板：' + message,
                                         code='prayer_panel_incomplete')

        def field(obj, key):
            ref = table_ref(obj)
            if ref is None:
                fail(f'{key} 父对象未加载')
            return read_ui_object_field(c, ref.address, key)

        panels = {}
        seen = set()
        for component in active_ui_component_objects(c):
            panel = table_ref(read_ui_object_field(c, component.address, 'm_panel')) or component
            for _ in range(4):
                if panel.address in seen:
                    break
                seen.add(panel.address)
                if (as_int(field(panel, 'V_ActivityId')) == expected_activity_id
                        and as_int(field(panel, 'curTimes')) is not None
                        and table_ref(field(panel, 'scrollView')) is not None):
                    panels[panel.address] = panel
                child = read_ui_selected_tab_panel(c, panel.address)
                if child is None:
                    break
                panel = LuaRef('table', child[0])
        if len(panels) != 1:
            fail('当前活动任务页不唯一或未打开')
        panel = next(iter(panels.values()))
        current_round = as_int(field(panel, 'curTimes'))
        rows, count = c.reader.list_items(field(field(panel, 'scrollView'), 'ItemInfoList'))
        if current_round is None or current_round <= 0 or count != len(rows) or not 1 <= len(rows) <= 128:
            fail('当前轮次或完整任务列表缺失')
        if diagnostic:
            # 只记录已验证面板成员的结构，用于解释配置身份错误；
            # 诊断结果没有 complete 授权，不可用于消耗或领奖。
            def layout(obj):
                ref = table_ref(obj)
                if ref is None:
                    return {'kind': type(obj).__name__}
                data = c.reader.table(ref.address)
                def simple(value):
                    return {'kind': value.kind} if isinstance(value, LuaRef) else value
                return {'fields': {str(k): simple(v) for k, v in data['fields'].items()},
                        'array': [simple(v) for v in data['array'][:40]]}
            return dict(complete=False, diagnostic_only=True,
                activity_id=expected_activity_id, current_round=current_round,
                row_count=count, row_members=[{'id': field(row, 'id'),
                    'fields': list(c.reader.fields(row)),
                    'is_finished': field(row, 'isFinished'),
                    'has_config': table_ref(field(row, 'configData')) is not None,
                    'has_server': table_ref(field(row, 'serverData')) is not None}
                    for row in rows], layouts=[{'row': layout(row),
                    'config': layout(field(row, 'configData')),
                    'server': layout(field(row, 'serverData'))} for row in rows[:2]])
        configs = read_runtime_config_rows(c.reader,
            [field(row, 'configData') for row in rows],
            environment_address=c.binding.environment_address,
            group_name='Activity', table_name='ActiveTask',
            fields=('id', 'sub', 'activityId', 'times', 'finishCondition'))
        result = []
        for index, (row, config) in enumerate(zip(rows, configs, strict=True)):
            server = field(row, 'serverData')
            task_id = as_int(config['id'])
            if task_id is None or task_id <= 0 or as_int(field(row, 'id')) != task_id:
                fail('任务配置与服务器身份不一致')
            if (as_int(config['activityId']) != expected_activity_id
                    or as_int(config['times']) != current_round):
                fail('任务配置与面板当前活动或轮次不一致')
            claimed = field(row, 'isFinished')
            if type(claimed) is not bool:
                fail('任务领奖状态缺失')
            if claimed:
                # 原生已领奖行可以没有 serverData；客户端从实际
                # finishCondition 展示封顶档位。档位不是当前真实积分。
                conditions = c.reader.fields(config['finishCondition'])
                ref = table_ref(config['finishCondition'])
                if ref is None:
                    fail('已领奖档位条件未加载')
                conditions = {**conditions, **{i: v for i, v in enumerate(
                    c.reader.table(ref.address)['array']) if v is not None}}
                thresholds = [re.fullmatch(r'ActivityItemScore\|(\d+)_(\d+)', v)
                    for v in conditions.values() if isinstance(v, str)]
                thresholds = [m for m in thresholds if m is not None]
                if len(thresholds) != 1:
                    fail('已领奖行并非唯一积分条件')
                target = int(thresholds[0][2])
                offset = as_int(config['sub'])
                if offset is None or not 0 <= offset < target:
                    fail('已领奖档位或轮次偏移不完整')
                result.append(dict(ui_index=index + 1, task_id=task_id,
                    config_activity_id=as_int(config['activityId']),
                    config_round=as_int(config['times']), raw_progress=None,
                    raw_target=target, round_offset=offset, current_score=None,
                    target_score=target - offset, finished=True, is_claimed=True))
                continue
            if table_ref(server) is None or as_int(field(server, 'taskId')) != task_id:
                fail(f'未领奖任务 {task_id} 缺少匹配的服务器数据')
            progress, declared = c.reader.list_items(field(server, 'progressList'))
            if declared != len(progress) or len(progress) != 1:
                fail('任务进度条件不完整或并非单积分条件')
            offset = as_int(config['sub'])
            score = as_int(field(progress[0], 'progress'))
            target = as_int(field(progress[0], 'target'))
            if offset is None or score is None or target is None or not 0 <= offset < target or score < offset:
                fail('任务积分或轮次偏移不完整')
            finished = field(progress[0], 'finish')
            if type(finished) is not bool or type(claimed) is not bool:
                fail('任务完成与领奖状态缺失')
            result.append(dict(ui_index=index + 1, task_id=task_id,
                config_activity_id=as_int(config['activityId']), config_round=as_int(config['times']),
                raw_progress=score, raw_target=target, round_offset=offset,
                current_score=score - offset, target_score=target - offset,
                finished=finished, is_claimed=claimed))
        if len({r['task_id'] for r in result}) != len(result):
            fail('任务身份重复')
        return dict(complete=True, source='active_prayer_task_panel_complete_list',
            activity_id=expected_activity_id, current_round=current_round, rows=result,
            observed_at=time.time(), evidence=dict(pid=c.binding.pid,
                process_start_ticks=c.binding.process_start_ticks))

    return read_ui_runtime_snapshot([], read, fast=True)
