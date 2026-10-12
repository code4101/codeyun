"""Read the active Dongtian swap picker; never submit a swap or invoke Lua."""
from .runtime_memory import FanxiuRuntimeMemoryError, as_int, table_ref
from .ui_runtime_context import (
    active_ui_component_objects, has_ui_object_fields,
    read_ui_object_field, read_ui_runtime_snapshot,
)


def validate_dongtian_swap_picker(snapshot: dict, target: dict, *, team_score: int, team3_score: int) -> dict:
    """Fail closed when a row click opened a different live seat or defender.

    List row position is not a native seat ID. Both the exact target and the
    client's strict combat comparison must hold before the application click.
    """
    if snapshot.get("complete") is not True:
        return {"ok": False, "reason": "选队页目标未完整加载"}
    expected = (target.get("team_id"), target.get("mine_id"), target.get("quality"), target.get("seat_id"))
    actual = tuple(snapshot.get(key) for key in
                   ("selected_team_id", "target_mine_id", "native_quality", "target_seat_id"))
    if actual != expected:
        return {"ok": False, "reason": "选队页绑定的实际席位与计划不一致", "expected": expected, "actual": actual}
    score = snapshot.get("target_fight_score")
    if (type(score) is not int or type(team_score) is not int or type(team3_score) is not int
            or min(score, team_score, team3_score) <= 0
            or score >= team_score or score * 5 >= team3_score * 4):
        return {"ok": False, "reason": "实际守军不满足客户端战力或三队80%安全线", "actual_score": score}
    return {"ok": True}


def read_dongtian_swap_picker_snapshot() -> dict:
    """Expose the actual selected team and target used by the click handler.

    A recognized #612 image does not prove V_Data/V_HotIndex are initialized.
    Missing state remains diagnostic; this projection grants no action.
    """
    def read(ctx):
        panels = [obj for obj in active_ui_component_objects(ctx)
                  if has_ui_object_fields(ctx, obj.address,
                                          {"ConfirmBtn", "XianlvMineMyTeamItem", "V_HotIndex"})]
        if not panels:
            return {"complete": False, "read_only": True, "reason": "洞天互换选队页未打开"}
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError("洞天互换选队实例不唯一", code="runtime_incomplete")
        field = lambda key: read_ui_object_field(ctx, panels[0].address, key)
        target = ctx.reader.fields(field("V_Data"))
        team_id, mine_id, quality = (as_int(field(key)) for key in ("V_HotIndex", "V_MineId", "V_type"))
        seat_id, score = as_int(target.get("id")), as_int(target.get("fightScore"))
        complete = (team_id in (1, 2, 3) and mine_id is not None and mine_id > 0
                    and quality is not None and seat_id is not None and score is not None)
        return {"complete": complete, "read_only": True,
                "source": "active_XianlvMineMyTeamView",
                "selected_team_id": team_id, "target_mine_id": mine_id,
                "native_quality": quality, "target_seat_id": seat_id,
                "target_fight_score": score, "target_loaded": bool(target),
                "evidence": {"pid": ctx.binding.pid,
                             "process_start_ticks": ctx.binding.process_start_ticks}}
    return read_ui_runtime_snapshot((), read, fast=True)


def read_dongtian_master_list_snapshot(expected_mine_id: int) -> dict:
    """Bind #342's physical row order to native master IDs without selecting.

    The client renders GetMasterDetailList's server order unchanged. V_Index
    belongs to a rendered row; V_Data.id belongs to the seat. They are separate
    identities, and a cached numeric seat ID never authorizes a row coordinate.
    """
    if type(expected_mine_id) is not int or expected_mine_id <= 0:
        raise ValueError('需要精确洞天地点ID')

    def read(ctx):
        panels = [obj for obj in active_ui_component_objects(ctx)
                  if has_ui_object_fields(ctx, obj.address, {'teamItem', 'enterBtn', 'V_ShowList', 'V_MineId'})]
        if len(panels) != 1:
            raise FanxiuRuntimeMemoryError('洞天尊主列表未唯一打开', code='data_not_loaded')
        panel = panels[0].address
        mine_id = as_int(read_ui_object_field(ctx, panel, 'V_MineId'))
        if mine_id != expected_mine_id:
            raise FanxiuRuntimeMemoryError('尊主列表地点与请求不一致', code='snapshot_incoherent')
        values, count = ctx.reader.list_items(read_ui_object_field(ctx, panel, 'V_ShowList'))
        if count is None or not 0 < count == len(values) <= 12:
            raise FanxiuRuntimeMemoryError('尊主列表未完整加载', code='runtime_incomplete')
        rows = []
        for index, value in enumerate(values):
            data = ctx.reader.fields(value)
            guarder = ctx.reader.fields(data.get('guarder'))
            seat_id, score = as_int(data.get('id')), as_int(data.get('fightScore'))
            if not seat_id or (guarder and (score is None or score <= 0)):
                raise FanxiuRuntimeMemoryError('尊主身份或守军战力缺失', code='runtime_incomplete')
            rows.append({'display_position': index + 1, 'native_seat_id': seat_id,
                         'fight_score': score, 'guarder_name': guarder.get('name'),
                         'guarder_role_id': ctx.reader.long(guarder.get('id') if guarder.get('id') is not None else guarder.get('roleId')),
                         'guarder_type': as_int(guarder.get('type')), 'occupied': bool(guarder)})
        if len({r['native_seat_id'] for r in rows}) != count:
            raise FanxiuRuntimeMemoryError('尊主列表席位重复', code='runtime_incomplete')
        scroll = table_ref(read_ui_object_field(ctx, panel, 'scrollView'))
        if scroll is None:
            raise FanxiuRuntimeMemoryError('尊主列表渲染器缺失', code='runtime_incomplete')
        materialized = set()
        for value in ctx.reader.dictionary_fields(read_ui_object_field(ctx, scroll.address, 'ItemClassDic')).values():
            view = ctx.reader.fields(value)
            index = as_int(view.get('V_Index'))
            if as_int(view.get('V_MineId')) != mine_id or index is None or not 0 <= index < count:
                continue
            bound_id = as_int(ctx.reader.fields(view.get('V_Data')).get('id'))
            if bound_id != rows[index]['native_seat_id'] or index in materialized:
                raise FanxiuRuntimeMemoryError('尊主物理行与原生席位未唯一对齐', code='snapshot_incoherent')
            materialized.add(index)
        for index, row in enumerate(rows):
            row['materialized'] = index in materialized
        return {'complete': True, 'read_only': True, 'mine_id': mine_id, 'rows': rows,
                'source': 'activeXianLvMinesHightLevelPointListView.V_ShowList+V_Index/V_Data.id',
                'evidence': {'pid': ctx.binding.pid, 'process_start_ticks': ctx.binding.process_start_ticks}}

    try:
        return read_ui_runtime_snapshot((), read, fast=True)
    except (FanxiuRuntimeMemoryError, OSError, ValueError) as exc:
        return {'complete': False, 'read_only': True, 'mine_id': expected_mine_id,
                'reason': str(exc), 'code': getattr(exc, 'code', 'runtime_unavailable')}
