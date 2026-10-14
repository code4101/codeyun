"""Read loaded medicine limits and cumulative records without invoking game Lua.

MedicineData.hasTakeMedicineVoDic is cumulative; takeMedicineDic is the active
queue. An absent cumulative record does not prove a synchronized zero. These
facts never authorize consuming a medicine or stopping an existing queue.
"""

from datetime import datetime
import math
from typing import Any

from .runtime_memory import FanxiuRuntimeMemoryError, LuaRef
from .ui_runtime_context import acquire_lua_global_runtime_context
from .resource_auto_use import read_runtime_config_rows
from .seat_runtime import runtime_number
from .ui_runtime_context import read_active_ui_window_panels, read_ui_object_field, read_ui_runtime_snapshot


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value or value < minimum:
        raise FanxiuRuntimeMemoryError(f"丹药 {label} 无效", code="runtime_incomplete")
    return int(value)


def _storage(reader, value, label):
    wrapper = reader.fields(value)
    data = wrapper.get('_dt_')
    if not isinstance(data, LuaRef) or data.kind != 'table':
        raise FanxiuRuntimeMemoryError(f"丹药 {label} 未加载", code="data_not_loaded")
    return data


def read_medicine_item_snapshot(item_id: int, *, refresh_process_maps: bool = False) -> dict[str, Any]:
    """Observe one loaded TakeMedicine item and its native cumulative/queue IDs.

    A complete snapshot proves the config and dictionary identities, not that
    an absent history row means zero. `taken_count` stays None in that case.
    `configured_max_times=-1` is reported literally; no inferred use permission,
    realm eligibility, current bag quantity, or permanent effect is supplied.
    Explicit map refresh and one unmapped-address retry each discard the entire
    earlier observation; no item counts or identities survive between readers.
    """
    snapshot = _read_medicine_item_snapshot(item_id, refresh_process_maps=refresh_process_maps)
    if not refresh_process_maps and snapshot.get('error_code') == 'memory_address_unmapped':
        # A growing Lua heap can outlive the process-map cache. Discard the
        # entire failed observation, rediscover mappings once, and resolve all
        # manager/config/history identities again. Never merge old/new values.
        retry = _read_medicine_item_snapshot(item_id, refresh_process_maps=True)
        return {**retry, 'process_maps_refreshed': True,
                'previous_observation_error': snapshot['reason']}
    return snapshot


def _read_medicine_item_snapshot(item_id: int, *, refresh_process_maps: bool = False) -> dict[str, Any]:
    item_id = _integer(item_id, 'item_id', minimum=1)
    c = None
    try:
        c = acquire_lua_global_runtime_context(('MedicineMgr', 'DBMgr'),
                                             refresh_process_maps=refresh_process_maps)
        r = c.reader
        manager = c.loaded_manager_fields('MedicineMgr', frozenset({'LuaMedicineMgr', 'Inst_get'}))
        model = r.fields(r.fields(manager.get('inst')).get('Model'))
        data = r.fields(model.get('MedicineData'))
        history = _storage(r, data.get('hasTakeMedicineVoDic'), '累计记录')
        db = c.loaded_manager_fields('DBMgr', frozenset({'DBMgr', 'GetConfigTable', 'Inst_get'}))
        configs = _storage(r, r.fields(db.get('inst')).get('ConfigDic'), '配置字典')
        table = r.fields(configs).get('TakeMedicine.TakeMedicine')
        if not isinstance(table, LuaRef) or table.kind != 'table':
            raise FanxiuRuntimeMemoryError('丹药配置尚未加载', code='data_not_loaded')
        # TipsNewView resolves by the itemId column, not the medicine config ID.
        # Preserve that relation instead of guessing that the two IDs coincide.
        native_table = r.table(table.address)
        raw_rows = list(native_table['fields'].values()) + [v for v in native_table['array'] if v is not None]
        if not raw_rows or len(raw_rows) > 2048 or any(
                not isinstance(v, LuaRef) or v.kind != 'table' for v in raw_rows):
            raise FanxiuRuntimeMemoryError('丹药配置行集合不完整', code='runtime_incomplete')
        rows = read_runtime_config_rows(r, raw_rows, environment_address=c.binding.environment_address,
            state_address=c.binding.state_address, group_name='TakeMedicine', table_name='TakeMedicine',
            fields=('id', 'itemId', 'maxTimes', 'typeId'))
        matches = [row for row in rows if row.get('itemId') == item_id]
        if len(matches) != 1:
            raise FanxiuRuntimeMemoryError('目标丹药 itemId 配置不唯一或未加载', code='data_not_loaded')
        cfg = matches[0]
        medicine_id = _integer(cfg['id'], '配置身份', minimum=1)
        if _integer(cfg['itemId'], '道具身份', minimum=1) != item_id:
            raise FanxiuRuntimeMemoryError('丹药配置与请求身份不一致', code='identity_mismatch')
        maximum = _integer(cfg['maxTimes'], '服用上限', minimum=-1)
        type_id = _integer(cfg['typeId'], '类型', minimum=1)
        type_table = r.fields(configs).get('TakeMedicine.TakeMedicineType')
        if not isinstance(type_table, LuaRef) or type_table.kind != 'table':
            raise FanxiuRuntimeMemoryError('丹药境界类型配置尚未加载', code='data_not_loaded')
        type_row = r.numeric_fields(type_table.address, frozenset({type_id})).get(type_id)
        if not isinstance(type_row, LuaRef) or type_row.kind != 'table':
            raise FanxiuRuntimeMemoryError('目标丹药境界类型未加载', code='data_not_loaded')
        type_cfg = read_runtime_config_rows(r, [type_row], environment_address=c.binding.environment_address,
            state_address=c.binding.state_address, group_name='TakeMedicine', table_name='TakeMedicineType',
            fields=('id', 'realm'))[0]
        if _integer(type_cfg['id'], '境界类型身份', minimum=1) != type_id:
            raise FanxiuRuntimeMemoryError('丹药境界类型身份不符', code='identity_mismatch')
        required_realm = _integer(type_cfg['realm'], '需求境界')
        vo = r.numeric_fields(history.address, frozenset({medicine_id})).get(medicine_id)
        taken = None
        if vo is not None:
            fields = r.fields(vo)
            if _integer(fields.get('medicineId'), '累计记录身份', minimum=1) != medicine_id:
                raise FanxiuRuntimeMemoryError('丹药累计记录身份不符', code='identity_mismatch')
            taken = _integer(fields.get('num'), '已服数量')
        queue = None
        queue_loaded = data.get('takeMedicineDic') is not None
        if queue_loaded:
            storage = _storage(r, data['takeMedicineDic'], '服用队列')
            queued = r.numeric_fields(storage.address, frozenset({medicine_id})).get(medicine_id)
            if queued is not None:
                fields = r.fields(queued)
                if _integer(fields.get('medicineId'), '队列身份', minimum=1) != medicine_id:
                    raise FanxiuRuntimeMemoryError('丹药队列身份不符', code='identity_mismatch')
                queue = {key: _integer(fields.get(key), key) for key in
                         ('state', 'remainTakeNum', 'tookNum', 'beyondNum', 'allNum')}
        return {'ok': True, 'complete': True, 'read_only': True, 'item_id': item_id,
                'medicine_id': medicine_id, 'configured_max_times': maximum,
                # ArtificeChooseView.AutoTakeOneType skips its quantity cap
                # specifically for -1. This is not a permission to consume.
                'configured_limit_kind': 'unbounded' if maximum == -1 else 'finite',
                'type_id': type_id, 'required_realm_level': required_realm,
                'realm_eligibility_verified': False,
                'history_row_present': vo is not None, 'taken_count': taken,
                'queue_loaded': queue_loaded, 'queue': queue,
                'consumption_authorized': False,
                'source': 'MedicineMgr.Model.MedicineData + DBMgr.ConfigDic[TakeMedicine.TakeMedicine]',
                'captured_at': datetime.now().astimezone().isoformat(),
                'evidence': {'pid': c.binding.pid, 'process_start_ticks': c.binding.process_start_ticks}}
    except Exception as exc:
        return {'ok': False, 'complete': False, 'read_only': True, 'item_id': item_id,
                'reason': str(exc), 'error_code': getattr(exc, 'code', None), 'consumption_authorized': False,
                'evidence': {'pid': c.binding.pid if c else None,
                             'process_start_ticks': c.binding.process_start_ticks if c else None}}


def read_medicine_attribute_snapshot() -> dict[str, Any]:
    """Read the loaded server aggregate used by the medicine attribute panel.

    SM_TakeMedicineSync.allAttrs supplies this dictionary. It covers all taken
    medicines and cannot identify one item's contribution or prove persistence
    across logout. Missing data remains unknown; this reader does not sync it.
    """
    c = None
    try:
        c = acquire_lua_global_runtime_context(('MedicineMgr',))
        r = c.reader
        manager = c.loaded_manager_fields('MedicineMgr', frozenset({'LuaMedicineMgr', 'Inst_get'}))
        instance = r.fields(manager.get('inst'))
        storage = _storage(r, instance.get('allAttrs'), '服务器汇总属性')
        native = r.table(storage.address)
        values = dict(native['fields'])
        for key, value in enumerate(native['array']):
            if value is not None:
                values.setdefault(key, value)
        values = {key: runtime_number(r, value) if not isinstance(value, bool) else None
                  for key, value in values.items()}
        if not values or len(values) > 2048:
            raise FanxiuRuntimeMemoryError('丹药汇总属性集合未完整加载', code='data_not_loaded')
        invalid = [(repr(key), type(value).__name__) for key, value in values.items()
                   if isinstance(key, bool) or not isinstance(key, (int, float))
                   or not math.isfinite(key) or int(key) != key or key <= 0 or isinstance(value, bool)
                   or not isinstance(value, (int, float)) or not math.isfinite(value)]
        if invalid:
            raise FanxiuRuntimeMemoryError(f'丹药汇总属性身份或数值异常：{invalid[:4]}', code='runtime_incomplete')
        attributes = [{'attribute_id': int(key), 'value': value}
                      for key, value in sorted(values.items())]
        return {'ok': True, 'complete': True, 'read_only': True, 'attributes': attributes,
                'attribute_count': len(values), 'scope': 'all_medicines_server_aggregate',
                'individual_item_contribution_verified': False, 'persistence_verified': False,
                'source': 'MedicineMgr.inst.allAttrs <- SM_TakeMedicineSync.allAttrs',
                'captured_at': datetime.now().astimezone().isoformat(),
                'evidence': {'pid': c.binding.pid, 'process_start_ticks': c.binding.process_start_ticks}}
    except Exception as exc:
        return {'ok': False, 'complete': False, 'read_only': True, 'reason': str(exc),
                'evidence': {'pid': c.binding.pid if c else None,
                             'process_start_ticks': c.binding.process_start_ticks if c else None}}


def read_medicine_selection_page_snapshot() -> dict[str, Any]:
    """Read the active choose page's available types and selected type tab.

    No AutoTakeHandler, batch confirmation or Lua method is invoked. Available
    tabs are not consumption permission or a selected medicine manifest.
    Inventory counts and unsubmitted batch choices are not projected here.
    """
    def read(c):
        panels = read_active_ui_window_panels(c, 'ArtificeChooseView')
        if not panels:
            return {'complete': False, 'read_only': True, 'reason': 'medicine_selection_page_not_open'}
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('选药页身份不唯一', code='identity_mismatch')
        p = panels[0]
        tabs = read_ui_object_field(c, p.address, 'V_TabList')
        rows, count = c.reader.list_items(tabs)
        if count is None or not 0 < count <= 64 or len(rows) != count:
            raise FanxiuRuntimeMemoryError('选药页分类列表不完整', code='data_not_loaded')
        configs = read_runtime_config_rows(c.reader, rows, environment_address=c.binding.environment_address,
            state_address=c.binding.state_address, group_name='TakeMedicine', table_name='TakeMedicineType',
            fields=('id', 'realm'))
        types = [{'type_id': _integer(row['id'], '分类身份', minimum=1),
                  'required_realm_level': _integer(row['realm'], '分类需求境界')} for row in configs]
        if len({row['type_id'] for row in types}) != count:
            raise FanxiuRuntimeMemoryError('选药页分类身份重复', code='identity_mismatch')
        selected_type = _integer(read_ui_object_field(c, p.address, 'typeId'), '当前分类', minimum=1)
        return {'complete': True, 'read_only': True, 'types': types, 'type_count': count,
                'selected_type_id': selected_type, 'consumption_authorized': False,
                'source': 'active_ArtificeChooseView.V_TabList',
                'evidence': {'pid': c.binding.pid, 'process_start_ticks': c.binding.process_start_ticks}}
    return read_ui_runtime_snapshot([], read, fast=True)


def read_medicine_queue_snapshot() -> dict[str, Any]:
    """Read the loaded server queue, preserving native timestamps and counts.

    This does not synchronize, stop, append, or consume medicines. A missing
    dictionary is unknown; an initialized empty dictionary is an observed empty
    queue. Native medicine IDs are not assumed to equal backpack item IDs.
    """
    c = None
    try:
        c = acquire_lua_global_runtime_context(('MedicineMgr',))
        r = c.reader
        manager = c.loaded_manager_fields('MedicineMgr', frozenset({'LuaMedicineMgr', 'Inst_get'}))
        model = r.fields(r.fields(manager.get('inst')).get('Model'))
        data = r.fields(model.get('MedicineData'))
        storage = _storage(r, data.get('takeMedicineDic'), '服用队列')
        table = r.table(storage.address)
        entries = dict(table['fields'])
        for key, value in enumerate(table['array']):
            if value is not None:
                entries.setdefault(key, value)
        if len(entries) > 2048:
            raise FanxiuRuntimeMemoryError('服药队列数量越界', code='runtime_incomplete')
        rows = []
        for key, value in entries.items():
            fields = r.fields(value)
            medicine_id = _integer(fields.get('medicineId'), '队列药品身份', minimum=1)
            if _integer(key, '队列字典身份', minimum=1) != medicine_id:
                raise FanxiuRuntimeMemoryError('服药队列成员身份不符', code='identity_mismatch')
            row = {'medicine_id': medicine_id}
            for name in ('state', 'remainTakeNum', 'tookNum', 'beyondNum', 'allNum'):
                row[name] = _integer(fields.get(name), name)
            for name in ('startTime', 'endTime'):
                row[name] = _integer(runtime_number(r, fields.get(name)), name)
            rows.append(row)
        if len({row['medicine_id'] for row in rows}) != len(rows):
            raise FanxiuRuntimeMemoryError('服药队列身份重复', code='identity_mismatch')
        return {'ok': True, 'complete': True, 'read_only': True, 'queue_count': len(rows),
                'entries': sorted(rows, key=lambda row: (row['startTime'], row['medicine_id'])),
                'source': 'loaded_MedicineMgr.Model.MedicineData.takeMedicineDic',
                'timestamps': 'native_server_long_values',
                'captured_at': datetime.now().astimezone().isoformat(),
                'evidence': {'pid': c.binding.pid, 'process_start_ticks': c.binding.process_start_ticks}}
    except Exception as exc:
        return {'ok': False, 'complete': False, 'read_only': True, 'reason': str(exc),
                'evidence': {'pid': c.binding.pid if c else None,
                             'process_start_ticks': c.binding.process_start_ticks if c else None}}
