from __future__ import annotations

"""Read the native cultivation capacity without initializing or invoking Lua.

DiscipleData.GetDiscipleIsMaxNum compares GetDiscipleVoList():Count()
with #DBMgr.ConfigDic['Disciple.DiscipleLocation']. GetDiscipleVoList is
authoritative only after DiscipleSyn, and uses normalList, not fullLvList.
This provider takes fresh bytes each time and never caches capacity decisions.
"""

from datetime import datetime
from typing import Any

from .runtime_memory import FanxiuRuntimeMemoryError, LuaRef
from .ui_runtime_context import acquire_lua_global_runtime_context


def read_disciple_capacity_snapshot() -> dict[str, Any]:
    """Return complete=True only for synced disciples and contiguous slot rows."""
    source = "DiscipleMgr.Model.Data.normalList + DBMgr.ConfigDic[Disciple.DiscipleLocation]"
    context = None
    try:
        context = acquire_lua_global_runtime_context(('DiscipleMgr', 'DBMgr'))
        reader = context.reader
        manager = context.loaded_manager_fields(
            'DiscipleMgr', frozenset({'LuaDiscipleMgr', 'InitSingleton'}))
        instance = reader.fields(manager.get('inst'))
        model = reader.fields(instance.get('Model'))
        data = reader.fields(model.get('Data'))
        sync = data.get('DiscipleSyn')
        if not isinstance(sync, LuaRef) or sync.kind != 'table':
            raise FanxiuRuntimeMemoryError('弟子同步数据尚未加载', code='not_loaded')
        values, count = reader.list_items(data.get('normalList'))
        if count is None or count != len(values) or count < 0:
            raise FanxiuRuntimeMemoryError('培养弟子列表不完整')
        db = context.loaded_manager_fields('DBMgr', frozenset(
            {'DBMgr', 'GetConfigTable', 'GetConfigTableByIdWithLog', 'Inst_get'}))
        db_instance = reader.fields(db.get('inst'))
        wrapper = reader.fields(db_instance.get('ConfigDic'))
        configs = reader.fields(wrapper.get('_dt_'))
        slots = reader.fields(configs.get('Disciple.DiscipleLocation'))
        indices = sorted(int(k) for k in slots if isinstance(k, (int, float)) and int(k) == k and k > 0)
        if not indices or indices != list(range(1, len(indices) + 1)):
            raise FanxiuRuntimeMemoryError('培养位配置尚未完整加载', code='not_loaded')
        if any(not isinstance(slots[k], LuaRef) or slots[k].kind != 'table' for k in indices):
            raise FanxiuRuntimeMemoryError('培养位配置行类型异常')
        return {'ok': True, 'available': True, 'complete': True, 'source': source,
                'cultivating_count': count, 'capacity': len(indices),
                'full': count >= len(indices),
                'captured_at': datetime.now().astimezone().isoformat(),
                'evidence': {'pid': context.binding.pid,
                             'process_start_ticks': context.binding.process_start_ticks}}
    except Exception as exc:
        return {'ok': False, 'available': False, 'complete': False,
                'source': source, 'reason': str(exc),
                'evidence': {'pid': context.binding.pid if context else None,
                             'process_start_ticks': context.binding.process_start_ticks if context else None}}
