"""Read the naturally loaded 升仙会 pinnacle board; never invoke game Lua.

Only shared process/manager roots are cached. Every observation resolves the
currently selected UI panel anew; leaving the activity clears its rank pages.
Live validated on the 2026-09-29 16-server occurrence. Process restart recovery
is delegated to the shared root provider and still needs live acceptance.
"""
from datetime import datetime

from .runtime_memory import (
    FanxiuRuntimeMemoryError, LuaRef, as_int, manager_index_fields,
    resolve_lua_global_manager_root,
)
from .ui_runtime_context import (
    active_ui_component_objects, has_ui_object_fields, read_ui_runtime_snapshot,
    read_ui_selected_tab_panel,
)
from backend.core.fanxiu.catalog.server_mapping import resolve_fanxiu_region_server_by_id

PEAK_STAGE = 4
_METHODS = frozenset({'LuaImmortalMgr', 'Inst_get'})


def _data(reader, root):
    manager = manager_index_fields(reader, root, _METHODS)
    instance = reader.fields(manager.get('inst'))
    model = reader.fields(instance.get('Model'))
    data = reader.fields(model.get('ImmortalData'))
    if '_CurRankInfo' not in data:
        raise FanxiuRuntimeMemoryError('升仙会模型未加载', code='data_not_loaded')
    return data


def _snapshot(ctx):
    reader = ctx.reader
    candidates = {}
    for obj in active_ui_component_objects(ctx):
        selected = read_ui_selected_tab_panel(ctx, obj.address)
        if selected and has_ui_object_fields(ctx, selected[0],
                                            {'_CurStage', '_CurSelectArea', '_RankInfo'}):
            candidates[selected[0]] = reader.fields(LuaRef('table', selected[0]))
    if len(candidates) != 1:
        raise FanxiuRuntimeMemoryError('升仙会排名页未唯一加载', code='data_not_loaded')
    panel = next(iter(candidates.values()))
    stage = as_int(panel.get('_CurStage'))
    if stage != PEAK_STAGE:
        raise FanxiuRuntimeMemoryError('当前为正赛榜，必须切换巅峰榜', code='wrong_rank_stage')
    root, _, _ = resolve_lua_global_manager_root(
        ctx.memory, manager_key='shengxian-hui', state_address=ctx.binding.state_address,
        global_name='ImmortalMgr', required_methods=_METHODS, validate=_data,
    )
    data = _data(reader, root)
    joiner = reader.fields(data.get('_CurJoinerInfo'))
    phase = reader.fields(joiner.get('immortalStageVO'))
    phase_end = reader.long(phase.get('closeTime'))
    if as_int(phase.get('stageType')) != PEAK_STAGE or not phase_end:
        raise FanxiuRuntimeMemoryError('本期巅峰赛阶段时间未加载', code='data_not_loaded')
    zone = as_int(panel.get('_CurSelectArea'))
    ranks_ref = reader.dictionary_fields(data['_CurRankInfo']).get(zone)
    if ranks_ref != panel.get('_RankInfo'):
        raise FanxiuRuntimeMemoryError('当前榜页与模型分区不一致', code='runtime_incomplete')
    info = reader.fields(ranks_ref)
    pages = as_int(info.get('totalPage'))
    page_offsets = {as_int(k): as_int(v) for k, v in
                    reader.dictionary_fields(info.get('pageCountDic')).items()}
    values, count = reader.list_items(info.get('ranks'))
    if not pages or not 1 <= pages <= 32 or count != len(values) or not values:
        raise FanxiuRuntimeMemoryError('巅峰榜分页结构不完整', code='runtime_incomplete')
    own_rank = as_int(reader.dictionary_fields(data.get('_UserRankDic')).get(PEAK_STAGE))
    rows = []
    for value in values:
        raw = reader.fields(value)
        rank, role_id, score = as_int(raw.get('rank')), reader.long(raw.get('id')), reader.long(raw.get('score'))
        if not rank or not role_id or score is None:
            raise FanxiuRuntimeMemoryError('巅峰榜行缺少名次、角色或积分', code='runtime_incomplete')
        server = as_int(raw.get('server'))
        rows.append(dict(
            ranking_scope='personal', rank=rank, role_key=str(role_id),
            score=score, name=str(raw.get('name') or ''), server_id=server,
            server_name=str(resolve_fanxiu_region_server_by_id(server).get('server_name') or ''),
            club_name=str(raw.get('club') or ''), is_self=rank == own_rank,
            raw_data={'stage': stage, 'zone': zone, 'role_id': str(role_id),
                      'score_time_ms': reader.long(raw.get('time'))},
        ))
    rows.sort(key=lambda row: row['rank'])
    if [row['rank'] for row in rows] != list(range(1, len(rows) + 1)) or len({row['role_key'] for row in rows}) != len(rows):
        raise FanxiuRuntimeMemoryError('巅峰榜名次不连续或角色重复', code='runtime_incomplete')
    loaded_pages = sorted(page_offsets)
    if loaded_pages != list(range(1, len(loaded_pages) + 1)):
        raise FanxiuRuntimeMemoryError('巅峰榜存在未加载的中间页', code='runtime_incomplete')
    if any(page_offsets[p] != (p - 1) * 20 for p in loaded_pages):
        raise FanxiuRuntimeMemoryError('巅峰榜分页偏移改变，需检查客户端版本', code='schema_mismatch')
    complete = loaded_pages == list(range(1, pages + 1))
    if not (20 * (len(loaded_pages) - 1) < len(rows) <= 20 * len(loaded_pages)):
        raise FanxiuRuntimeMemoryError('巅峰榜分页行数不一致', code='runtime_incomplete')
    if complete and len(rows) > int(data.get('_MaxPeakMember') or 0):
        raise FanxiuRuntimeMemoryError('巅峰榜人数超出本期上限', code='runtime_incomplete')
    now = datetime.now().astimezone()
    return dict(complete=complete, stage=stage, zone=zone, total_pages=pages,
                loaded_pages=loaded_pages, rankings=rows, self_rank=own_rank,
                peak_end_ms=phase_end, settled=now.timestamp() * 1000 >= phase_end,
                captured_at=now.isoformat(timespec='seconds'),
                source='ImmortalMgr.ImmortalData._CurRankInfo / active ImmortalRankPanel',
                process_id=ctx.binding.pid, process_start_ticks=ctx.binding.process_start_ticks)


def read_shengxian_peak_rank_snapshot():
    """Observe the selected pinnacle board. Missing pages require GUI scrolling."""
    return read_ui_runtime_snapshot((), _snapshot)
