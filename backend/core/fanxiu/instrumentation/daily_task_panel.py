"""当前日常页经客户端筛选后的完整列表；只读，不滚动或执行 Lua。"""

from .runtime_memory import FanxiuRuntimeMemoryError, LuaRef, as_int, table_ref, manager_index_fields
from .ui_runtime_context import (
    read_active_ui_window_panels, read_ui_object_field,
    read_ui_runtime_snapshot, read_ui_selected_tab_panel,
)


def read_daily_task_display_snapshot() -> dict:
    """读取 DailyMainView 当前日常页的 activeAllTaskList。

    服务器任务集合不等于当前显示集合。本接口沿实际选中页签读取
    ScreenTask 的结果，并与滚动视图全量输入逐项核对；不以当前三四
    个渲染行代替整表。标题取自当前加载的配置和语言表；作业业务
    完成判据仍由调用方定义，不能从页面状态直接推断。
    页面未打开/非日常页/列表不完整时失败关闭，不复用历史地址。
    """
    from .daily_task import read_daily_task_inventory_snapshot
    native = read_daily_task_inventory_snapshot()
    finished = set(native['finished_task_ids'])
    members = finished | {r['task_id'] for r in native['rows']}

    def read(c):
        if (native['evidence']['pid'], native['evidence']['process_start_ticks']) != (
                c.binding.pid, c.binding.process_start_ticks):
            raise FanxiuRuntimeMemoryError('日常显示与服务器集合不是同一进程', code='runtime_incomplete')
        def field(obj, name):
            ref = table_ref(obj)
            return read_ui_object_field(c, ref.address, name) if ref else None

        hosts = read_active_ui_window_panels(c, 'DailyMainView')
        if len(hosts) != 1:
            raise FanxiuRuntimeMemoryError('当前日常主页面未打开或不唯一', code='data_not_loaded')
        panel = hosts[0]
        for _ in range(4):
            if (as_int(field(panel, 'activeType')) == 1
                    and table_ref(field(panel, 'activeAllTaskList')) is not None):
                break
            selected = read_ui_selected_tab_panel(c, panel.address)
            if selected is None:
                raise FanxiuRuntimeMemoryError('当前选中页不是日常任务页', code='data_not_loaded')
            panel = LuaRef('table', selected[0])
        else:
            raise FanxiuRuntimeMemoryError('日常页签层级未核实', code='runtime_incomplete')
        values, count = c.reader.list_items(field(panel, 'activeAllTaskList'))
        scroll = field(panel, 'activeScrollView')
        rendered, rendered_count = c.reader.list_items(field(scroll, 'ItemInfoList'))
        if not 1 <= len(values) <= 128 or count != len(values) or rendered_count != count or rendered != values:
            raise FanxiuRuntimeMemoryError('日常显示列表与滚动视图输入不一致', code='runtime_incomplete')
        rows = []
        seen = set()
        for value in values:
            raw = [field(value, key) for key in ('taskId', 'status', 'turn', 'targetTurn')]
            parsed = [as_int(v) for v in raw]
            ident, status, turn, target = parsed
            # ScreenTask synthesizes claimed rows from finishTasks with only
            # taskId/status. Their native finished membership proves completion;
            # it does not supply invented turn/target numbers.
            claimed = ident in finished and status == 5 and turn is None and target is None
            if any(isinstance(v, bool) for v in raw) or any(v is None for v in parsed[:2]) or (
                    not claimed and (turn is None or target is None)):
                raise FanxiuRuntimeMemoryError('日常显示行身份/进度缺失', code='runtime_incomplete')
            if ident <= 0 or ident not in members or ident in seen or status < 0 or (
                    not claimed and (turn < 0 or target <= 0)):
                raise FanxiuRuntimeMemoryError('日常显示行身份重复或进度无效', code='runtime_incomplete')
            seen.add(ident)
            rows.append({'task_id': ident, 'status': status, 'turn': turn, 'target_turn': target,
                         'done': claimed or (status in (4, 5) and turn >= target),
                         'progress_source': 'finished_task_identity' if claimed else 'task_entry_progress'})
        root = table_ref(read_ui_object_field(c, c.binding.environment_address, 'ActivetaskMgr'))
        if root is None:
            raise FanxiuRuntimeMemoryError('日常管理器未自然加载', code='data_not_loaded')
        manager = manager_index_fields(c.reader, root.address, frozenset({'Inst_get'}))
        data = field(field(manager.get('inst'), 'Model'), 'ActivetaskData')
        config = table_ref(field(data, 'activeTaskCfg'))
        if config is None:
            raise FanxiuRuntimeMemoryError('日常标题配置未自然加载', code='data_not_loaded')
        table = c.reader.table(config.address)
        by_id = dict(table['fields'])
        for index, value in enumerate(table['array']):
            if value is not None:
                by_id.setdefault(index, value)
        from .resource_auto_use import read_runtime_config_rows
        configs = read_runtime_config_rows(c.reader, [by_id.get(r['task_id']) for r in rows],
            environment_address=c.binding.environment_address,
            group_name='ActiveTasks', table_name='ActiveTask', fields=('id', 'name', 'desc'))
        from .item_config import read_loaded_item_text
        text_ids = {as_int(cfg.get(key)) for cfg in configs for key in ('name', 'desc')
                    if not isinstance(cfg.get(key), (str, bool)) and as_int(cfg.get(key)) is not None
                    and as_int(cfg.get(key)) > 0}
        localized = read_loaded_item_text(text_ids, reader=c.reader,
            state_address=c.binding.state_address, environment_address=c.binding.environment_address)
        if not localized['complete']:
            raise FanxiuRuntimeMemoryError('日常语言文本未完整加载', code='data_not_loaded')
        for row, cfg in zip(rows, configs, strict=True):
            raw_title = cfg.get('name')
            title = raw_title if isinstance(raw_title, str) else localized['texts_by_id'].get(as_int(raw_title))
            if as_int(cfg.get('id')) != row['task_id'] or not isinstance(title, str) or not title.strip():
                raise FanxiuRuntimeMemoryError('日常标题配置身份/文本不完整', code='runtime_incomplete')
            raw_description = cfg.get('desc')
            description = raw_description if isinstance(raw_description, str) else localized['texts_by_id'].get(as_int(raw_description))
            row.update(title=title, description=description)
        return {'complete': True, 'read_only': True, 'source': 'active_DailyMainView.selected_TaskActivePanel',
                'row_count': count, 'rows': rows, 'title_mapping_verified': True,
                'pid': c.binding.pid, 'process_start_ticks': c.binding.process_start_ticks}
    return read_ui_runtime_snapshot(['ActivetaskMgr'], read, fast=True)
