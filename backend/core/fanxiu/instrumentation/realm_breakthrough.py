"""当前境界突破条件窗的只读判断；不打开窗口、不开始突破。"""

import math
from typing import Any

from .runtime_memory import FanxiuRuntimeMemoryError, as_int
from .ui_runtime_context import (
    UiRuntimeContext, read_active_ui_window_panels, read_ui_object_field,
    read_ui_runtime_snapshot,
)


def _read_conditions(context: UiRuntimeContext) -> dict[str, Any]:
    panels = read_active_ui_window_panels(context, "LeiJieView")
    if not panels:
        return {"complete": False, "conditions_met": None,
                "reason": "realm_conditions_window_not_open", "read_only": True}
    if len(panels) != 1:
        raise FanxiuRuntimeMemoryError("境界突破条件窗实例不唯一", code="runtime_incomplete")
    ready = read_ui_object_field(context, panels[0].address, "canUp")
    if not isinstance(ready, bool):
        raise FanxiuRuntimeMemoryError("境界突破条件判断尚未加载", code="data_not_loaded")
    # The live panel uses V_CurDungeonId; the exported Lua's _CurDungeonId
    # spelling is not its current runtime field. Zero is observed while the
    # current realm conditions are unmet and must not become a guessed ID.
    raw_dungeon = read_ui_object_field(context, panels[0].address, "V_CurDungeonId")
    dungeon_id = as_int(raw_dungeon)
    challenge_identity_complete = (
        not isinstance(raw_dungeon, bool) and dungeon_id is not None and dungeon_id > 0
    )
    # Live class binding: only LeijieViewNew defines F_IntoLeijieFun (its start
    # handler with the BREAKING_LIMITATIONS check and the dujie 0 Upgrade path).
    field_names = {key for key in context.reader.fields(panels[0]) if isinstance(key, str)}
    implementation = ("LeijieViewNew" if "F_IntoLeijieFun" in field_names else
                      "LeiJieView_legacy" if "_CurDungeonId" in field_names else "unknown")
    return {"complete": True, "conditions_met": ready, "read_only": True, "implementation": implementation,
            "dungeon_id": dungeon_id if challenge_identity_complete else None,
            "challenge_identity_complete": challenge_identity_complete,
            "challenge_identity_reason": "" if challenge_identity_complete else "dungeon_identity_unavailable",
            "dungeon_id_observed": raw_dungeon if isinstance(raw_dungeon, (str, int, float, bool)) else None,
            "source": "active_LeiJieView.canUp", "pid": context.binding.pid,
            "process_start_ticks": context.binding.process_start_ticks}


def read_realm_breakthrough_conditions_snapshot() -> dict[str, Any]:
    """读取当前打开的原生条件窗，关闭时返回未知而非复用旧判断。

    canUp 是原生窗口检查条件的合取结果，不能替代修为、资源预算、
    挑战胜负或实际等级提升的验收。challenge_identity_complete 独立
    表示是否读到有效副本身份；未知身份不使已加载的条件判断失效。
    只有身份完整时 dungeon_id 才可供进入后的副本核对，不授权挑战。
    此接口不调用任何游戏 Lua。
    """
    return read_ui_runtime_snapshot([], _read_conditions, fast=True)


def parse_breaking_limitation(value: Any) -> dict[str, Any]:
    """Parse Realm.ConfigValue BREAKING_LIMITATIONS ``"level,condition"`` (RoleMgr.GetLeiJieCondition).

    Absent value is the native no-limit case. A present value must parse to a
    positive level and a non-empty condition; anything else raises (unknown).
    """
    if value is None:
        return {'level': None, 'condition': None, 'raw': None}
    level, _, condition = str(value).partition(',')
    if not level.strip().isdecimal() or int(level) <= 0 or not condition.strip():
        raise FanxiuRuntimeMemoryError(f'BREAKING_LIMITATIONS 无法解析：{value!r}', code='runtime_incomplete')
    return {'level': int(level), 'condition': condition.strip(), 'raw': str(value)}


def evaluate_realm_breakthrough_conditions(conditions: list[str], *, attributes: dict[int, Any],
                                           core_bases: dict[int, dict[str, Any]],
                                           highest_open_part_by_type: dict[int, int]) -> dict[str, Any]:
    """Evaluate Realm.RealmResource[level+1].condition with native LeijieItem rules.

    ATTR|code_n: Entity:GetProperty(code) >= n. CoreBaseMaxLevel|id: highest
    unlocked part of Core.CoreBase[id].type >= CoreBase[id].parts. Any other
    kind, malformed entry or missing fact is unknown (met None), never met.
    """
    rows = []
    for raw in conditions:
        kind, _, arg = str(raw).partition('|')
        row = {'condition': raw, 'kind': kind, 'value': None, 'threshold': None, 'met': None}
        if kind == 'ATTR':
            code, _, threshold = arg.partition('_')
            if code.isdecimal() and threshold.isdecimal():
                value = attributes.get(int(code))
                row.update(attribute_code=int(code), threshold=int(threshold), value=value)
                if (isinstance(value, (int, float)) and not isinstance(value, bool)
                        and math.isfinite(value) and value >= 0):
                    row['met'] = value >= int(threshold)
        elif kind == 'CoreBaseMaxLevel' and arg.isdecimal():
            base = core_bases.get(int(arg)) or {}
            if type(base.get('type')) is int and type(base.get('parts')) is int:
                value = highest_open_part_by_type.get(base['type'])
                row.update(core_base_id=int(arg), core_type=base['type'], threshold=base['parts'], value=value)
                if type(value) is int:
                    row['met'] = value >= base['parts']
        rows.append(row)
    states = [row['met'] for row in rows]
    all_met = None if not rows or None in states else all(states)
    return {'conditions': rows, 'all_conditions_met': all_met}


def read_realm_breakthrough_requirements_snapshot() -> dict[str, Any]:
    """Read the next realm's native conditions and current facts without any window.

    Combines RoleMgr level/EXP, Realm.RealmResource[level+1] (condition,
    conditionDescript, dujie), Core.CoreBase rows, PropertyDic attributes and
    ImmHoleData opening; all from one process. ``state`` is ready only when
    EXP and every condition are met; unknown facts keep it unknown. Ready is
    not breakthrough permission: the start flow is accepted separately.
    """
    from .resource_auto_use import read_runtime_config_rows
    from .role_progression import read_role_progression_snapshot, read_role_property_snapshot
    from .runtime_memory import LuaRef
    from .ui_runtime_context import acquire_lua_global_runtime_context
    from .xianqiao import read_xianqiao_opening_snapshot

    try:
        role = read_role_progression_snapshot()
        if role.get('complete') is not True:
            return {'complete': False, 'state': 'unknown', 'reason': role.get('reason'), 'read_only': True}
        c = acquire_lua_global_runtime_context(('DBMgr',))
        r = c.reader
        db = c.loaded_manager_fields('DBMgr', frozenset({'DBMgr', 'GetConfigTable', 'Inst_get'}))
        configs = r.fields(r.fields(db.get('inst')).get('ConfigDic')).get('_dt_')

        def config_row(name, key, fields):
            table = r.fields(configs).get(name)
            if not isinstance(table, LuaRef) or table.kind != 'table':
                raise FanxiuRuntimeMemoryError(f'{name} 尚未加载', code='data_not_loaded')
            row = r.numeric_fields(table.address, frozenset({key})).get(key)
            if not isinstance(row, LuaRef) or row.kind != 'table':
                raise FanxiuRuntimeMemoryError(f'{name}[{key}] 尚未加载', code='data_not_loaded')
            group, tname = name.split('.')
            return read_runtime_config_rows(r, [row], environment_address=c.binding.environment_address,
                                            state_address=c.binding.state_address, group_name=group,
                                            table_name=tname, fields=fields)[0]

        def lua_list(value):
            # A config list must be a contiguous 1..n array; holes or hash parts
            # mean a truncated/unknown read, never a shorter list.
            if not isinstance(value, LuaRef) or value.kind != 'table':
                return None
            table = r.table(value.address)
            items = list(table['array'][1:])
            while items and items[-1] is None:
                items.pop()
            if table['fields'] or any(v is None for v in items):
                return None
            return items

        target = role['level'] + 1

        def read_breaking_limitation(reader, config_dic, binding):
            table = reader.fields(config_dic).get('Realm.ConfigValue')
            if not isinstance(table, LuaRef) or table.kind != 'table':
                raise FanxiuRuntimeMemoryError('Realm.ConfigValue 尚未加载', code='data_not_loaded')
            native = reader.table(table.address)
            raw = list(native['fields'].values()) + [v for v in native['array'] if v is not None]
            rows = read_runtime_config_rows(reader, [v for v in raw if isinstance(v, LuaRef) and v.kind == 'table'],
                                            environment_address=binding.environment_address,
                                            state_address=binding.state_address, group_name='Realm',
                                            table_name='ConfigValue', fields=('id', 'value'))
            values = [row['value'] for row in rows if row['id'] == 'BREAKING_LIMITATIONS']
            if not rows or len(values) > 1:
                raise FanxiuRuntimeMemoryError('Realm.ConfigValue 不完整或重复', code='runtime_incomplete')
            parsed = parse_breaking_limitation(values[0] if values else None)
            return {**parsed, 'applies': parsed['level'] == target}

        realm = config_row('Realm.RealmResource', target, ('id', 'condition', 'conditionDescript', 'dujie'))
        conditions = lua_list(realm['condition'])
        descriptions = lua_list(realm['conditionDescript']) or []
        if conditions is None or any(not isinstance(v, str) for v in conditions):
            raise FanxiuRuntimeMemoryError('下一境界条件配置不完整', code='runtime_incomplete')
        # LeijieViewNew start also checks RoleMgr.GetLeiJieCondition():
        # Realm.ConfigValue BREAKING_LIMITATIONS = "level,condition"; when the
        # target equals that level the condition must pass. A loaded table
        # without the row is the native "no limit" (cfg nil).
        limitation = read_breaking_limitation(r, configs, c.binding)
        if limitation['applies']:
            conditions = conditions + [limitation['condition']]
            descriptions = descriptions + [f"天道桎梏限制（{limitation['level']}级）"]
        core_bases = {}
        attribute_codes = []
        for raw in conditions:
            kind, _, arg = raw.partition('|')
            if kind == 'CoreBaseMaxLevel' and arg.isdecimal():
                base = config_row('Core.CoreBase', int(arg), ('id', 'type', 'parts'))
                core_bases[int(arg)] = {'type': as_int(base['type']), 'parts': as_int(base['parts'])}
            elif kind == 'ATTR' and arg.partition('_')[0].isdecimal():
                attribute_codes.append(int(arg.partition('_')[0]))
        props = read_role_property_snapshot(attribute_codes) if attribute_codes else {
            'complete': True, 'values': {}, 'evidence': role['evidence']}
        holes = read_xianqiao_opening_snapshot() if core_bases else None
        incomplete = [name for name, snap in (('role_property', props), ('xianqiao_opening', holes))
                      if snap is not None and snap.get('complete') is not True]
        if incomplete or any(b['type'] is None or b['parts'] is None for b in core_bases.values()):
            return {'complete': False, 'state': 'unknown', 'read_only': True,
                    'reason': f'incomplete readers or config: {incomplete or "Core.CoreBase"}'}
        identities = {(role['evidence']['pid'], role['evidence']['process_start_ticks']),
                      (c.binding.pid, c.binding.process_start_ticks),
                      (props['evidence']['pid'], props['evidence']['process_start_ticks'])}
        if holes is not None:
            identities.add((holes['pid'], holes['process_start_ticks']))
        if len(identities) != 1:
            return {'complete': False, 'state': 'unknown', 'reason': 'readers observed different processes',
                    'read_only': True}
        evaluated = evaluate_realm_breakthrough_conditions(
            conditions, attributes=props.get('values') or {}, core_bases=core_bases,
            highest_open_part_by_type={s['type']: s['highest_open_part_id'] for s in (holes or {}).get('systems', [])})
        for row, desc in zip(evaluated['conditions'], descriptions):
            row['description'] = desc
        all_met = evaluated['all_conditions_met']
        raw_dujie = realm['dujie']
        # dujie selects the tribulation dungeon; only an explicit integer is a
        # fact (0 = none configured). Missing/non-integer stays unknown.
        dujie = int(raw_dujie) if (isinstance(raw_dujie, (int, float)) and not isinstance(raw_dujie, bool)
                                   and float(raw_dujie).is_integer() and raw_dujie >= 0) else None
        state = ('unknown' if all_met is None or dujie is None or not role.get('role_id') else
                 'ready' if all_met and role['can_upgrade'] else 'pending')
        pid, ticks = next(iter(identities))
        return {'complete': all_met is not None and dujie is not None, 'read_only': True, 'state': state,
                'role_id': role.get('role_id'),
                'level': role['level'], 'target_level': target, 'exp_ready': role['can_upgrade'],
                'current_exp': role['current_exp'], 'required_exp': role['required_exp'],
                'tribulation_dungeon_id': dujie, 'tribulation_dungeon_raw': raw_dujie,
                'breaking_limitation': limitation, **evaluated,
                'breakthrough_authorized': False,
                'source': 'RoleMgr + Realm.RealmResource[level+1] + Core.CoreBase + PropertyDic + ImmHoleData',
                'pid': pid, 'process_start_ticks': ticks}
    except Exception as exc:
        return {'complete': False, 'state': 'unknown', 'reason': str(exc), 'read_only': True}


def read_realm_breakthrough_identity_sources_snapshot() -> dict[str, Any]:
    """诊断当前窗口的副本字段；候选字段不构成挑战身份或执行授权。

    用于核验静态客户端与当前运行时的字段差异，只读取已打开且
    身份唯一的窗口。调用方不得按候选名称自行选择挑战。
    """
    def read(context):
        snapshot = _read_conditions(context)
        if not snapshot['complete']:
            return snapshot
        panels = read_active_ui_window_panels(context, 'LeiJieView')
        fields = context.reader.fields(panels[0])
        candidates = {
            key: value for key, value in fields.items()
            if isinstance(key, str) and 'dungeon' in key.lower()
            and isinstance(value, (str, int, float, bool, type(None)))
        }
        return {**snapshot, 'dungeon_field_candidates': candidates,
                'candidate_semantics_verified': False}
    return read_ui_runtime_snapshot([], read, fast=True)
