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


def project_medicine_choose_rows(rows: list[dict[str, Any]], *, item_counts: dict[int, int],
                                 role_level: int, required_realm_level: int) -> list[dict[str, Any]]:
    """Mirror ArtificeChooseView.UpdateArtificeSc/SortList and the item realm mask.

    show==1 rows render only with a held count > 0; others always render.
    Held rows sort first, then config ``sort``. The mask is roleLevel < realm.
    This is the expected presentation, not a manifest or use permission.
    """
    visible = [row for row in rows if row['show'] != 1 or item_counts.get(row['item_id'], 0) > 0]
    visible.sort(key=lambda row: (0 if item_counts.get(row['item_id'], 0) > 0 else 1, row['sort']))
    masked = role_level < required_realm_level
    return [{**row, 'held_count': item_counts.get(row['item_id'], 0), 'realm_masked': masked}
            for row in visible]


def read_medicine_type_rows_snapshot(type_id: int) -> dict[str, Any]:
    """Project the choose-page rows for one medicine type from loaded config.

    Reads TakeMedicine/TakeMedicineType config, backpack counts and role level;
    never calls Lua or opens UI. Same-process identity is required across the
    three reads. Rows are the native expected list; a row is not consumable proof.
    """
    from .item_resources import read_item_available_counts
    from .role_progression import read_role_progression_snapshot

    type_id = _integer(type_id, 'type_id', minimum=1)
    c = None
    try:
        c = acquire_lua_global_runtime_context(('DBMgr',))
        r = c.reader
        db = c.loaded_manager_fields('DBMgr', frozenset({'DBMgr', 'GetConfigTable', 'Inst_get'}))
        configs = _storage(r, r.fields(db.get('inst')).get('ConfigDic'), '配置字典')
        table = r.fields(configs).get('TakeMedicine.TakeMedicine')
        type_table = r.fields(configs).get('TakeMedicine.TakeMedicineType')
        if not all(isinstance(t, LuaRef) and t.kind == 'table' for t in (table, type_table)):
            raise FanxiuRuntimeMemoryError('丹药配置尚未加载', code='data_not_loaded')
        native = r.table(table.address)
        raw = list(native['fields'].values()) + [v for v in native['array'] if v is not None]
        if not raw or len(raw) > 2048 or any(not isinstance(v, LuaRef) or v.kind != 'table' for v in raw):
            raise FanxiuRuntimeMemoryError('丹药配置行集合不完整', code='runtime_incomplete')
        cfg_rows = read_runtime_config_rows(r, raw, environment_address=c.binding.environment_address,
            state_address=c.binding.state_address, group_name='TakeMedicine', table_name='TakeMedicine',
            fields=('id', 'itemId', 'maxTimes', 'typeId', 'show', 'sort'))
        rows = [{'medicine_id': _integer(row['id'], '配置身份', minimum=1),
                 'item_id': _integer(row['itemId'], '道具身份', minimum=1),
                 'max_times': _integer(row['maxTimes'], '服用上限', minimum=-1),
                 'show': _integer(row['show'], '显示规则'), 'sort': _integer(row['sort'], '排序')}
                for row in cfg_rows if row.get('typeId') == type_id]
        type_row = r.numeric_fields(type_table.address, frozenset({type_id})).get(type_id)
        if not isinstance(type_row, LuaRef) or type_row.kind != 'table':
            raise FanxiuRuntimeMemoryError('丹药境界类型未加载', code='data_not_loaded')
        type_cfg = read_runtime_config_rows(r, [type_row], environment_address=c.binding.environment_address,
            state_address=c.binding.state_address, group_name='TakeMedicine', table_name='TakeMedicineType',
            fields=('id', 'realm'))[0]
        required = _integer(type_cfg['realm'], '需求境界')
        identity = (c.binding.pid, c.binding.process_start_ticks)
        counts, count_evidence = read_item_available_counts([row['item_id'] for row in rows] or [1],
                                                            manager_key='medicine-type-rows')
        role = read_role_progression_snapshot()
        role_identity = ((role.get('evidence') or {}).get('pid'), (role.get('evidence') or {}).get('process_start_ticks'))
        if (count_evidence.get('pid'), count_evidence.get('process_start_ticks')) != identity or role_identity != identity:
            raise FanxiuRuntimeMemoryError('丹药分类投影来源进程不一致', code='identity_mismatch')
        if role.get('complete') is not True or type(role.get('level')) is not int:
            raise FanxiuRuntimeMemoryError('角色境界未加载', code='data_not_loaded')
        return {'ok': True, 'complete': True, 'read_only': True, 'type_id': type_id,
                'required_realm_level': required, 'role_level': role['level'],
                'config_rows': len(rows),
                'expected_rows': project_medicine_choose_rows(rows, item_counts=counts, role_level=role['level'],
                                                               required_realm_level=required),
                'consumption_authorized': False,
                'source': 'DBMgr.ConfigDic[TakeMedicine.*] + ItemVoDic + RoleMgr; native UpdateArtificeSc rules',
                'evidence': {'pid': identity[0], 'process_start_ticks': identity[1]}}
    except Exception as exc:
        return {'ok': False, 'complete': False, 'read_only': True, 'type_id': type_id, 'reason': str(exc),
                'consumption_authorized': False,
                'evidence': {'pid': c.binding.pid if c else None,
                             'process_start_ticks': c.binding.process_start_ticks if c else None}}


MEDICINE_GAIN_PATTERN = r'([一-龥A-Za-z·]+)\+([0-9.]+)(%?)'


def parse_medicine_gain_text(text: str) -> list[dict[str, Any]]:
    """Parse item effect text such as '气血+6000' / '气血加成+0.02%'.

    Percent gains are converted to the native Ratio unit (display value*0.01%).
    Unity rich-text tags in client text (``<color=#..>天资</color>+<color=..>2</color>``)
    are stripped first; they carry no gain semantics.
    """
    import re
    plain = re.sub(r'<[^<>]+>', '', str(text or ''))
    return [{'name': name, 'value': float(value) * (100 if pct else 1), 'percent': bool(pct)}
            for name, value, pct in re.findall(MEDICINE_GAIN_PATTERN, plain)]


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def reconcile_medicine_aggregate(medicines: list[dict[str, Any]], *, observed: dict[int, float],
                                 attribute_codes: dict[str, list[int]],
                                 over_cap_weight: float) -> dict[str, Any]:
    """Test one arithmetic model against the server aggregate; never infers missing values.

    Model: aggregate[code] = sum(gain x (min(n,cap) + w*max(0,n-cap))), cap<0 unbounded,
    with each over-cap weighted product allowed one unit of server rounding. An
    expected code absent from ``observed`` is ``observed_missing`` (never zero);
    a non-zero observed code with no modelled contributor is unexplained; an
    explicit observed 0 without contributors is consistent. Unparsed gains for a
    taken medicine, unmapped gain names, or empty inputs block overall consistency.
    Consistency supports the model only; it is not a measured per-item effect.
    """
    if not isinstance(observed, dict) or any(
            type(code) is not int or code <= 0 or not _finite_number(value) for code, value in observed.items()):
        raise ValueError('汇总属性必须为正整数身份和有限数值')
    if not _finite_number(over_cap_weight) or not 0 <= over_cap_weight <= 1:
        raise ValueError('超限权重必须在0至1之间')
    seen = set()
    for med in medicines:
        if (type(med.get('taken')) is not int or med['taken'] < 0 or type(med.get('max_times')) is not int
                or med['max_times'] < -1 or med.get('medicine_id') in seen):
            raise ValueError('丹药历史次数、上限或身份无效')
        seen.add(med['medicine_id'])
    expected: dict[int, float] = {}
    contributors: dict[int, list[dict[str, Any]]] = {}
    unmapped: dict[str, int] = {}
    unparsed = [med['item_id'] for med in medicines if med['taken'] > 0 and not med.get('gains')]
    for med in medicines:
        n, cap = med['taken'], med['max_times']
        if not n:
            continue
        effective = n if cap < 0 or n <= cap else cap + (n - cap) * over_cap_weight
        for gain in med.get('gains') or []:
            codes = attribute_codes.get(gain['name']) or []
            if len(codes) > 1:
                codes = [code for code in codes if code in observed]
            if len(codes) != 1:
                unmapped[gain['name']] = unmapped.get(gain['name'], 0) + n
                continue
            code = codes[0]
            expected[code] = expected.get(code, 0.0) + gain['value'] * effective
            contributors.setdefault(code, []).append(
                {'medicine_id': med['medicine_id'], 'item_id': med['item_id'], 'taken': n,
                 'effective_count': effective, 'gain': gain['value'],
                 'cap_class': 'unbounded' if cap < 0 else 'over_cap' if n > cap else 'within_cap'})
    attributes = []
    for code in sorted(set(expected) | set(observed)):
        rows = contributors.get(code, [])
        tolerance = sum(1 for row in rows if row['cap_class'] == 'over_cap')
        if code not in observed:
            state, residual = 'observed_missing', None
        elif code not in expected:
            residual = observed[code]
            state = 'explicit_zero' if observed[code] == 0 else 'observed_unexplained'
        else:
            residual = observed[code] - expected[code]
            state = 'model_consistent' if abs(residual) <= tolerance else 'model_inconsistent'
        attributes.append({'attribute_code': code, 'observed': observed.get(code), 'expected': expected.get(code),
                           'residual': residual, 'rounding_tolerance': tolerance, 'state': state,
                           'model_consistent': state in ('model_consistent', 'explicit_zero'),
                           'contributors': rows})
    blocked = []
    if not observed:
        blocked.append('observed_empty')
    if not any(med['taken'] > 0 for med in medicines):
        blocked.append('no_taken_history')
    if unparsed:
        blocked.append('unparsed_gains')
    if unmapped:
        blocked.append('unmapped_gain_names')
    if any(not a['model_consistent'] for a in attributes):
        blocked.append('attribute_not_consistent')
    return {'attributes': attributes, 'unparsed_item_ids': unparsed, 'unmapped_gain_names': unmapped,
            'blocked_by': blocked, 'all_model_consistent': not blocked,
            'over_cap_weight': over_cap_weight, 'evidence_kind': 'model_consistency_not_individual_measurement'}


def read_medicine_history_snapshot() -> dict[str, Any]:
    """Read the loaded cumulative history (hasTakeMedicineVoDic) with identity gates.

    Absent/empty storage is incomplete, never a synchronized zero history. Keys
    must equal medicineId, be unique, and counts must be non-negative integers.
    """
    c = None
    try:
        c = acquire_lua_global_runtime_context(('MedicineMgr',))
        r = c.reader
        manager = c.loaded_manager_fields('MedicineMgr', frozenset({'LuaMedicineMgr', 'Inst_get'}))
        data = r.fields(r.fields(r.fields(manager.get('inst')).get('Model')).get('MedicineData'))
        table = r.table(_storage(r, data.get('hasTakeMedicineVoDic'), '累计记录').address)
        entries = dict(table['fields'])
        for key, value in enumerate(table['array']):
            if value is not None:
                if key in entries:
                    raise FanxiuRuntimeMemoryError('累计记录身份重复', code='identity_mismatch')
                entries[key] = value
        if not entries or len(entries) > 2048:
            raise FanxiuRuntimeMemoryError('累计记录未完整加载或越界', code='data_not_loaded')
        history = {}
        for key, value in entries.items():
            fields = r.fields(value)
            medicine_id = _integer(fields.get('medicineId'), '累计记录身份', minimum=1)
            if _integer(key, '累计字典身份', minimum=1) != medicine_id or medicine_id in history:
                raise FanxiuRuntimeMemoryError('累计记录身份不符或重复', code='identity_mismatch')
            history[medicine_id] = _integer(fields.get('num'), '已服数量')
        return {'ok': True, 'complete': True, 'read_only': True, 'history': history, 'row_count': len(history),
                'source': 'loaded_MedicineMgr.Model.MedicineData.hasTakeMedicineVoDic',
                'evidence': {'pid': c.binding.pid, 'process_start_ticks': c.binding.process_start_ticks}}
    except Exception as exc:
        return {'ok': False, 'complete': False, 'read_only': True, 'reason': str(exc),
                'evidence': {'pid': c.binding.pid if c else None,
                             'process_start_ticks': c.binding.process_start_ticks if c else None}}


def _read_take_medicine_config() -> dict[str, Any]:
    """Loaded TakeMedicine rows and ConfigValue with identity/type gates."""
    c = acquire_lua_global_runtime_context(('DBMgr',))
    r = c.reader
    db = c.loaded_manager_fields('DBMgr', frozenset({'DBMgr', 'GetConfigTable', 'Inst_get'}))
    configs = _storage(r, r.fields(db.get('inst')).get('ConfigDic'), '配置字典')

    def table_rows(name, fields):
        table = r.fields(configs).get(name)
        if not isinstance(table, LuaRef) or table.kind != 'table':
            raise FanxiuRuntimeMemoryError(f'{name} 尚未加载', code='data_not_loaded')
        native = r.table(table.address)
        raw = list(native['fields'].values()) + [v for v in native['array'] if v is not None]
        if not raw or len(raw) > 2048 or any(not isinstance(v, LuaRef) or v.kind != 'table' for v in raw):
            raise FanxiuRuntimeMemoryError(f'{name} 行集合不完整', code='runtime_incomplete')
        group, tname = name.split('.')
        return read_runtime_config_rows(r, raw, environment_address=c.binding.environment_address,
                                        state_address=c.binding.state_address, group_name=group,
                                        table_name=tname, fields=fields)
    rows = []
    for row in table_rows('TakeMedicine.TakeMedicine', ('id', 'itemId', 'maxTimes', 'time', 'typeId')):
        rows.append({'medicine_id': _integer(row['id'], '配置身份', minimum=1),
                     'item_id': _integer(row['itemId'], '道具身份', minimum=1),
                     'max_times': _integer(row['maxTimes'], '服用上限', minimum=-1),
                     'refine_ms_per_pill': _integer(row['time'], '炼化耗时'),
                     'type_id': _integer(row['typeId'], '类型', minimum=1)})
    if len({row['medicine_id'] for row in rows}) != len(rows) or len({row['item_id'] for row in rows}) != len(rows):
        raise FanxiuRuntimeMemoryError('丹药配置身份重复', code='identity_mismatch')
    values = {}
    for row in table_rows('TakeMedicine.ConfigValue', ('id', 'value')):
        if row['id'] in values:
            raise FanxiuRuntimeMemoryError('丹药常量身份重复', code='identity_mismatch')
        values[row['id']] = row['value']
    weakening = str(values.get('EFFECT_WEAKENING', ''))
    if not weakening.isdecimal() or not 0 <= int(weakening) <= 10000:
        raise FanxiuRuntimeMemoryError('EFFECT_WEAKENING 缺失或越界', code='runtime_incomplete')
    return {'rows': rows, 'effect_weakening_bp': int(weakening), 'exceed_if_time': values.get('EXCEED_IF_TIME'),
            'evidence': {'pid': c.binding.pid, 'process_start_ticks': c.binding.process_start_ticks}}


def read_medicine_effect_snapshot(item_id: int) -> dict[str, Any]:
    """Configured / observed / derived / unknown effect facts for one medicine item.

    configured: TakeMedicine row (maxTimes, per-pill refine ``time`` ms = queue
    duration, typeId), ConfigValue EFFECT_WEAKENING / EXCEED_IF_TIME and the
    current client effect text; the client has no per-medicine attribute or
    effect-duration column. observed: validated cumulative history and server
    aggregate from public readers in one process. derived: a conditional model
    consistency check only — never a measured per-item contribution or
    persistence. Absent history rows are model assumptions, not synced zeros.
    """
    import json as _json
    from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
    from .item_config import read_item_metadata_runtime, read_item_text_runtime

    item_id = _integer(item_id, 'item_id', minimum=1)
    base = {'read_only': True, 'item_id': item_id, 'consumption_authorized': False,
            'individual_item_contribution_verified': False, 'persistence_verified': False}
    try:
        aggregate = read_medicine_attribute_snapshot()
        history = read_medicine_history_snapshot()
        config = _read_take_medicine_config()
        incomplete = [name for name, snap in (('aggregate', aggregate), ('history', history)) if snap.get('complete') is not True]
        if incomplete:
            return {**base, 'ok': False, 'complete': False, 'reason': f'incomplete public readers: {incomplete}',
                    'aggregate_reason': aggregate.get('reason'), 'history_reason': history.get('reason')}
        mine = [row for row in config['rows'] if row['item_id'] == item_id]
        if len(mine) != 1:
            raise FanxiuRuntimeMemoryError('目标丹药 itemId 配置不唯一或未加载', code='data_not_loaded')
        taken_rows = [row for row in config['rows'] if history['history'].get(row['medicine_id'], 0) > 0]
        unknown_history_ids = sorted(set(history['history']) - {row['medicine_id'] for row in config['rows']})
        item_ids = sorted({row['item_id'] for row in taken_rows} | {item_id})
        metadata = read_item_metadata_runtime(item_ids)
        text_ids = sorted({meta.get('runtime_effect_description_id') for meta in (metadata.get('items_by_id') or {}).values()
                           if type(meta.get('runtime_effect_description_id')) is int and meta['runtime_effect_description_id'] > 0})
        texts = read_item_text_runtime(text_ids) if text_ids else {'texts_by_id': {}, 'complete': False}
        identities = {(snap['evidence']['pid'], snap['evidence']['process_start_ticks'])
                      for snap in (aggregate, history, config)}
        identities |= {(metadata.get('pid'), metadata.get('process_start_ticks')),
                       (texts.get('pid'), texts.get('process_start_ticks'))}
        if len(identities) != 1:
            return {**base, 'ok': False, 'complete': False, 'reason': 'readers observed different processes',
                    'identities': sorted(map(str, identities))}
        client_text = {}
        for iid in item_ids:
            meta = (metadata.get('items_by_id') or {}).get(iid) or {}
            text = texts.get('texts_by_id', {}).get(meta.get('runtime_effect_description_id'))
            if isinstance(text, str) and text:
                client_text[iid] = text
        medicines = [{'medicine_id': row['medicine_id'], 'item_id': row['item_id'], 'max_times': row['max_times'],
                      'taken': history['history'][row['medicine_id']],
                      'gains': parse_medicine_gain_text(client_text.get(row['item_id']))} for row in taken_rows]
        attribute_rows = _json.loads((resolve_fanxiu_export_root() / 'parsed_configs' / 'Attribute' / 'rows.json')
                                     .read_text(encoding='utf-8'))
        codes: dict[str, list[int]] = {}
        for row in attribute_rows:
            codes.setdefault(row.get('name_plain') or row.get('name'), []).append(int(row['code']))
        observed = {row['attribute_id']: row['value'] for row in aggregate['attributes']}
        model = reconcile_medicine_aggregate(medicines, observed=observed, attribute_codes=codes,
                                             over_cap_weight=config['effect_weakening_bp'] / 10000)
        if unknown_history_ids:
            model['blocked_by'].append('history_ids_without_config')
            model['all_model_consistent'] = False
        target = mine[0]
        own = {a['attribute_code']: a for a in model['attributes']
               if any(x['item_id'] == item_id for x in a['contributors'])}
        target_gains = parse_medicine_gain_text(client_text.get(item_id))
        consistent = (model['all_model_consistent'] and bool(target_gains) and len(own) == len(target_gains)
                      and all(a['model_consistent'] for a in own.values()))
        return {**base, 'ok': True, 'complete': True,
                'configured': {'medicine_id': target['medicine_id'], 'type_id': target['type_id'],
                               'max_times': target['max_times'], 'refine_ms_per_pill': target['refine_ms_per_pill'],
                               'effect_weakening_bp': config['effect_weakening_bp'],
                               'exceed_if_time': config['exceed_if_time'],
                               'client_effect_text': client_text.get(item_id), 'client_text_gains': target_gains,
                               'effect_duration_field': None,
                               'semantics': 'refine time is queue duration; client config has no effect-duration column'},
                'observed': {'taken_count': history['history'].get(target['medicine_id']),
                             'history_rows': history['row_count'], 'aggregate_attribute_count': aggregate['attribute_count'],
                             'medicines_with_history_and_config': len(taken_rows),
                             'history_ids_without_config': unknown_history_ids,
                             'client_text_missing_item_ids': [i for i in item_ids if i not in client_text]},
                'derived': {'evidence_kind': 'model_estimated_conditional',
                            'aggregate_model_consistent': model['all_model_consistent'],
                            'blocked_by': model['blocked_by'], 'unparsed_item_ids': model['unparsed_item_ids'],
                            'unmapped_gain_names': model['unmapped_gain_names'],
                            'item_attributes': [{k: v for k, v in a.items() if k != 'contributors'} for a in own.values()],
                            'item_model_estimated_contribution': (
                                [{'attribute_code': code, 'value': next(x['gain'] * x['effective_count']
                                                                        for x in a['contributors'] if x['item_id'] == item_id)}
                                 for code, a in own.items()] if consistent else None),
                            'assumptions': [
                                'gains taken from the CURRENT client effect text; text at the time each pill was taken is unverified',
                                'medicines without a history row contribute nothing (absent row is not proven synced zero)',
                                'over-cap pills weighted by EFFECT_WEAKENING/10000 with <=1 server rounding per over-cap contributor',
                                'attribute names map to codes via exported Attribute display names'],
                            'supporting_evidence': [
                                'server aggregate and history read from the same loaded process',
                                'every aggregate attribute compared against the stated model; '
                                'result is aggregate_model_consistent / blocked_by']},
                'unknown': ['individual item contribution not measured (no per-item server field)',
                            'cross-relogin persistence not directly observed',
                            'application of the aggregate to role panel/combat totals'],
                'source': 'public readers: read_medicine_attribute_snapshot + read_medicine_history_snapshot + '
                          'TakeMedicine config + read_item_metadata_runtime/read_item_text_runtime',
                'evidence': {'pid': next(iter(identities))[0], 'process_start_ticks': next(iter(identities))[1]}}
    except Exception as exc:
        return {**base, 'ok': False, 'complete': False, 'reason': str(exc)}
