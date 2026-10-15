"""Current Xutian template rank identity; no requests, Lua or consumption."""
from datetime import datetime

from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    read_active_ui_window_panels, read_ui_object_field,
    read_ui_runtime_snapshot, read_ui_selected_tab_panel,
)


def read_xutian_rank_page_snapshot(activity_id: int) -> dict:
    """Follow only the live template window's selected tabs to its rank leaf.

    The exact parent activity must agree with V_OpenParam. Missing server
    rank data does not mean an empty population; this API reports identity
    only, even when the visible board consists of placeholder rows.
    """
    if type(activity_id) is not int or activity_id <= 0:
        raise ValueError('虚天榜页需要正整数父活动ID')

    def read(ctx):
        panels = read_active_ui_window_panels(ctx, 'ActivityTemplateMainView')
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('活动模板主窗口未唯一加载', code='data_not_loaded')
        root = panels[0].address
        parent = as_int(read_ui_object_field(ctx, root, 'V_ActivityId'))
        params = table_ref(read_ui_object_field(ctx, root, 'V_OpenParam'))
        param_id = as_int(read_ui_object_field(ctx, params.address, 'activityId')) if params else None
        if parent != activity_id or param_id != activity_id:
            raise FanxiuRuntimeMemoryError('活动模板父实例身份不一致', code='identity_mismatch')
        node, tab_indexes = root, []
        for _ in range(5):
            selected = read_ui_selected_tab_panel(ctx, node)
            if selected is None:
                break
            node, index = selected
            tab_indexes.append(index)
        rank_id = as_int(read_ui_object_field(ctx, node, 'V_RankActivityId'))
        leaf_params = table_ref(read_ui_object_field(ctx, node, 'V_OpenParam'))
        leaf_parent = as_int(read_ui_object_field(ctx, leaf_params.address, 'activityId')) if leaf_params else None
        if not tab_indexes or rank_id is None or rank_id <= 0 or leaf_parent != activity_id:
            raise FanxiuRuntimeMemoryError('虚天当前榜域子页身份不完整', code='data_not_loaded')
        return {'ok': True, 'complete': True, 'read_only': True,
                'activity_id': parent, 'selected_rank_activity_id': rank_id,
                'selected_tab_indexes': tab_indexes, 'population_verified': False,
                'captured_at': datetime.now().astimezone().isoformat(),
                'source': 'active ActivityTemplateMainView selected rank leaf',
                'evidence': {'pid': ctx.binding.pid, 'process_start_ticks': ctx.binding.process_start_ticks}}
    try:
        return read_ui_runtime_snapshot((), read, fast=True)
    except FanxiuRuntimeMemoryError as exc:
        return {'ok': False, 'complete': False, 'read_only': True,
                'activity_id': activity_id, 'selected_rank_activity_id': None,
                'population_verified': False, 'reason': str(exc), 'error_code': exc.code}
