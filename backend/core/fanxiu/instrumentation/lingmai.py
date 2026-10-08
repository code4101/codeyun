from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
    resolve_manager_root,
)
from backend.core.fanxiu.instrumentation.role_progression import (
    read_role_profile_from_memory,
)
from backend.core.fanxiu.instrumentation.seat_runtime import (
    object_fields,
    room_roster_facts,
    runtime_number,
    seat_fact,
    self_profile_from_seat,
    self_seat_facts,
)


_UNION_VENIS_MARKER = b"LuaUnionVenisMgr"


def read_lingmai_interaction_target() -> dict[str, Any]:
    """读取当前 NPC 功能对白实际绑定的座位，不以列表点击意图代替落点。

    原生 OnClickNpcTakeSeatFuncBtn 使用 NPC 对应座位列表的首项；同一个
    NPC 可展示多个座位，寻路后该首项可能已改变。必须从活动对白 npcLo
    与 SceneMgr 当前有序列表互证后，再决定是否点击驱离。
    未打开对白、座位列表缺失或身份不完整时失败关闭；不调用 Lua。
    """
    from .runtime_memory import table_ref
    from .resource_auto_use import read_runtime_config_rows
    from .ui_runtime_context import (
        read_active_ui_window_panels, read_ui_object_field, read_ui_runtime_snapshot,
    )

    def read(c):
        def fail(message):
            raise FanxiuRuntimeMemoryError('灵脉互动目标：' + message, code='lingmai_target_incomplete')

        def field(obj, key):
            obj = table_ref(obj)
            if obj is None:
                fail(f'{key} 父对象未加载')
            return read_ui_object_field(c, obj.address, key)

        panels = read_active_ui_window_panels(c, 'TalkFunctionSelectedView')
        if len(panels) != 1:
            fail('当前功能对白不唯一或未打开')
        # npcLo 是 Npc.NpcResource 打包配置行，id 不在普通对象字段上。
        npc_config = read_runtime_config_rows(c.reader, [field(panels[0], 'npcLo')],
            environment_address=c.binding.environment_address,
            group_name='Npc', table_name='NpcResource', fields=('id',))[0]
        npc_id = as_int(npc_config['id'])
        if npc_id is None or npc_id <= 0:
            fail('当前对白 NPC 身份缺失')
        mgr = field(c.field(c.binding.environment_address, 'UnionVenisMgr'), 'inst')
        scene_mgr = field(mgr, 'UnionVenisSceneMgr')
        dictionary = table_ref(field(scene_mgr, 'npcToSeatVOListDic'))
        if dictionary is None:
            fail('场景座位投影缺失')
        raw_list = c.reader.dictionary_fields(dictionary).get(npc_id)
        values, count = c.reader.list_items(raw_list)
        if not values or count is None or count != len(values):
            fail('NPC 当前座位列表不完整')
        seat = seat_fact(c.reader, values[0], source_index=0)
        data = field(field(mgr, 'Model'), 'data')
        group = c.reader.long(field(data, 'UnionVeinsGroup'))
        owner = (seat or {}).get('owner') or {}
        if (not seat or not owner.get('role_id') or owner.get('team_uid') is None
                or group is None):
            fail('当前座位或联盟身份缺失')
        return dict(source='active_npc_dialogue_scene_seat_projection', npc_id=npc_id,
            seat=seat, is_ally=owner['team_uid'] == group, self_team_uid=group,
            observed_at=time.time(), evidence=dict(pid=c.binding.pid,
                process_start_ticks=c.binding.process_start_ticks))

    return read_ui_runtime_snapshot(['UnionVenisMgr'], read)
_UNION_VENIS_METHODS = frozenset(
    {
        "LuaUnionVenisMgr",
        "Inst_get",
        "ReqMsg",
    }
)


def _manager_instance_and_data(
    reader: LuaJitReader,
    root_address: int,
) -> tuple[dict[Any, Any], dict[Any, Any]]:
    """Return the loaded manager instance and its authoritative model data."""

    manager = manager_index_fields(
        reader,
        root_address,
        _UNION_VENIS_METHODS,
    )
    instance = reader.fields(manager.get("inst"))
    model = reader.fields(instance.get("Model"))
    data = reader.fields(model.get("data"))
    required = {
        "myRoomId",
        "leftListenTime",
        "strength",
        "roomList",
        "UnionVeinsGroup",
    }
    if not required.issubset(data):
        raise FanxiuRuntimeMemoryError(
            "联盟灵脉 Runtime 模型尚未初始化",
            code="data_not_loaded",
        )
    return instance, data


def _data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    _instance, data = _manager_instance_and_data(reader, root_address)
    return data


def _battle_replay_facts(
    reader: LuaJitReader,
    instance: dict[Any, Any],
) -> dict[str, Any]:
    """Expose the client's pending Lingmai battle transaction.

    ``UnionVenisSceneMgr.replayRecord`` is written before the replay request
    and cleared only after the real win/loss tail is consumed.  It is the
    authoritative discriminator between an idle #588 room and the same room
    temporarily visible while a kick battle is entering or returning.
    """

    scene_manager = object_fields(reader, instance.get("UnionVenisSceneMgr"))
    if not scene_manager:
        return {
            "ok": False,
            "available": False,
            "pending": None,
            "reason": "scene_manager_not_loaded",
            "source": "runtime_memory",
        }
    record_value = scene_manager.get("replayRecord")
    if record_value is None:
        return {
            "ok": True,
            "available": True,
            "pending": False,
            "result": None,
            "win": None,
            "need_pop": None,
            "loot_count": None,
            "seat": None,
            "need_check_window_open": scene_manager.get("needCheckWinOpen") is True,
            "source": "runtime_memory",
        }
    record = object_fields(reader, record_value)
    if not record:
        return {
            "ok": False,
            "available": False,
            "pending": None,
            "reason": "replay_record_not_decodable",
            "source": "runtime_memory",
        }
    win = record.get("win") if isinstance(record.get("win"), bool) else None
    need_pop = (
        record.get("needPop")
        if isinstance(record.get("needPop"), bool)
        else None
    )
    return {
        "ok": win is not None,
        "available": True,
        "pending": True,
        "result": "win" if win is True else "loss" if win is False else "unknown",
        "win": win,
        "need_pop": need_pop,
        "loot_count": as_int(record.get("lootNum")),
        "seat": seat_fact(reader, record.get("seatVO")),
        "need_check_window_open": scene_manager.get("needCheckWinOpen") is True,
        "source": "runtime_memory",
    }


def _resolve_union_venis_root(
    memory: MumuProcessMemory,
) -> tuple[int, bool, str]:
    """Resolve the loaded Manager before using constructor-marker discovery."""

    from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
        _lua_addresses,
    )

    try:
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="lingmai-union-venis",
            state_address=int(_lua_addresses(memory)["state"], 16),
            global_name="UnionVenisMgr",
            required_methods=_UNION_VENIS_METHODS,
            validate=lambda reader, root: _data_fields(reader, root),
        )
        return root, cache_hit, "lua_global"
    except FanxiuRuntimeMemoryError as exc:
        # Once the exact Manager is proven, missing Model.data fields mean the
        # page has not naturally loaded yet. A constructor-marker heap scan
        # cannot load them and only turns a precise result into a slow failure.
        if exc.code == "data_not_loaded":
            raise
    root, cache_hit = resolve_manager_root(
        memory,
        manager_key="lingmai-union-venis",
        marker=_UNION_VENIS_MARKER,
        required_methods=_UNION_VENIS_METHODS,
        validate=lambda reader, root: _data_fields(reader, root),
    )
    return root, cache_hit, "constructor_marker"


def _room(reader: LuaJitReader, value: Any) -> dict[str, Any]:
    fields = object_fields(reader, value)
    return {
        "id": as_int(fields.get("id")),
        "available_count": as_int(fields.get("left")),
        "theme_id": as_int(fields.get("themeId")),
        "npc_id": as_int(fields.get("npcId")),
    }


def _role_info(reader: LuaJitReader, value: Any) -> dict[str, Any]:
    fields = object_fields(reader, value)
    return {
        "battle_id": as_int(fields.get("battleId")),
        "room_id": as_int(fields.get("roomId")),
        "seat_id": as_int(fields.get("seatId")),
        "local_server": as_int(fields.get("localServer")),
        "remaining_milliseconds": reader.long(fields.get("leftListenTime")),
        "sit_down_time": reader.long(fields.get("sitDownTime")),
        "skill_level": as_int(fields.get("skillLv")),
    }


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
    manager_resolver: str = "constructor_marker",
) -> dict[str, Any]:
    captured_at_epoch = time.time()
    roster_evidence = {
        "pid": memory.pid,
        "process_start_ticks": memory.process_start_ticks,
        "captured_at_epoch": captured_at_epoch,
        "order_key": [captured_at_epoch],
    }
    reader = LuaJitReader(memory)
    instance, data = _manager_instance_and_data(reader, root_address)
    raw_rooms, declared_room_count = reader.list_items(data.get("roomList"))
    rooms = [
        room
        for value in raw_rooms
        if (room := _room(reader, value))["id"] is not None
    ]
    remaining_milliseconds = reader.long(data.get("leftListenTime"))
    strength = runtime_number(reader, data.get("strength"))
    union_group = reader.long(data.get("UnionVeinsGroup"))
    own_room_id = as_int(data.get("myRoomId"))
    own_seat_id = as_int(data.get("seatId"))
    self_seat = self_seat_facts(reader, data)
    self_profile = self_profile_from_seat(self_seat)
    if not self_profile.get("available"):
        # Account identity is role state, not seat state.  An unseated player
        # has no self seat by definition, but still needs a role id and battle
        # score to choose an empty seat or a safe opponent.
        self_profile = read_role_profile_from_memory(memory)
    shenmai_roster = room_roster_facts(
        reader,
        data,
        room_id=17,
        room_summaries=rooms,
        evidence=roster_evidence,
    )
    shengmai_roster = room_roster_facts(
        reader,
        data,
        room_id=18,
        room_summaries=rooms,
        evidence=roster_evidence,
    )
    complete = (
        remaining_milliseconds is not None
        and strength is not None
        and union_group is not None
        and bool(rooms)
    )
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": "UnionVenisMgr.Model.data",
        "mode": "union",
        "remaining_milliseconds": remaining_milliseconds,
        "completed": (
            remaining_milliseconds <= 0
            if remaining_milliseconds is not None
            else None
        ),
        "strength": strength,
        "fight_consume": as_int(data.get("fightConsume")),
        "sit_consume": as_int(data.get("sitConsume")),
        "initial_strength": as_int(data.get("initialStrength")),
        "maximum_milliseconds": (
            (as_int(data.get("maxVenisTime")) or 0) * 1000
            or None
        ),
        "union_group": union_group,
        "own_room_id": own_room_id,
        "own_seat_id": own_seat_id,
        "battle_id": as_int(data.get("battleId")),
        "local_server": as_int(data.get("localServer")),
        "rooms": rooms,
        "declared_room_count": declared_room_count,
        "decoded_room_count": len(rooms),
        "role_info": _role_info(reader, data.get("roleInfo")),
        "battle_replay": _battle_replay_facts(reader, instance),
        "self_seat_facts": self_seat,
        "self_profile": self_profile,
        "union_group_facts": {
            "ok": union_group is not None,
            "available": union_group is not None,
            "veins_group": union_group,
            "source": "runtime_memory",
        },
        "shenmai_roster": shenmai_roster,
        "shengmai_roster": shengmai_roster,
        "room_rosters": {
            "17": shenmai_roster,
            "18": shengmai_roster,
        },
        "captured_at": datetime.fromtimestamp(captured_at_epoch).strftime("%Y-%m-%d %H:%M:%S"),
        "captured_at_epoch": captured_at_epoch,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
            "manager_resolver": manager_resolver,
            "order_key": [captured_at_epoch],
        },
    }


_TRANSIENT_LINGMAI_RUNTIME_MESSAGES = (
    "Runtime 内存地址越界",
    "读取凡修 Runtime 内存失败",
    "读取凡修 Runtime 内存不完整",
    "Lua table node 数量越界",
    "Lua table array 数量越界",
)


def _is_transient_lingmai_runtime_error(exc: BaseException) -> bool:
    """Whether one same-process full snapshot reread can repair the failure."""

    return isinstance(exc, FanxiuRuntimeMemoryError) and any(
        marker in str(exc) for marker in _TRANSIENT_LINGMAI_RUNTIME_MESSAGES
    )


def _read_lingmai_with_generation_retry(read_once):
    """Reread one volatile Lua generation without crossing process identity."""

    first_identity: tuple[int, int] | None = None
    first_error: str | None = None
    refresh_maps = False
    for attempt in range(2):
        # A fresh reader alone cannot repair an address missing from cached
        # /proc/maps. Refresh mappings once for that exact error, as the shared
        # coherent reader does; this does not request a global/heap scan.
        memory = (MumuProcessMemory.discover_cached(max_age_seconds=0.0)
                  if refresh_maps else MumuProcessMemory.discover_cached())
        identity = (int(memory.pid), int(memory.process_start_ticks))
        if first_identity is None:
            first_identity = identity
        elif identity != first_identity:
            raise FanxiuRuntimeMemoryError(
                "灵脉 Runtime 复读期间凡修进程身份已变化，拒绝拼接跨进程快照"
            )
        try:
            result = read_once(memory)
            if refresh_maps:
                result.setdefault("evidence", {})["process_maps_refreshed_on_retry"] = True
            return result, attempt + 1, first_error
        except FanxiuRuntimeMemoryError as exc:
            setattr(exc, "lingmai_process_maps_refreshed_on_retry", refresh_maps)
            if attempt or not _is_transient_lingmai_runtime_error(exc):
                setattr(exc, "lingmai_decode_attempt_count", attempt + 1)
                if first_error is not None:
                    setattr(exc, "lingmai_retried_generation_error", first_error)
                raise
            first_error = str(exc)
            refresh_maps = exc.code == "memory_address_unmapped"
    raise AssertionError("unreachable")


def read_lingmai_snapshot() -> dict[str, Any]:
    """Read the current alliance Lingmai model without packets or GUI OCR."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    def read_once(attempt_memory: MumuProcessMemory) -> dict[str, Any]:
        nonlocal memory
        memory = attempt_memory
        root_address, root_cache_hit, manager_resolver = (
            _resolve_union_venis_root(memory)
        )
        return _snapshot(
            memory,
            root_address,
            root_cache_hit=root_cache_hit,
            manager_resolver=manager_resolver,
        )

    try:
        result, attempt_count, retried_error = (
            _read_lingmai_with_generation_retry(read_once)
        )
        result["elapsed_seconds"] = time.perf_counter() - started_at
        evidence = result.setdefault("evidence", {})
        evidence["decode_attempt_count"] = attempt_count
        if retried_error is not None:
            evidence["retried_generation_error"] = retried_error
        return result
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, FanxiuRuntimeMemoryError)
            else f"{type(exc).__name__}: {exc}"
        )
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "reason": reason,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "error_code": getattr(exc, "code", None),
                "process_maps_refreshed_on_retry": getattr(
                    exc, "lingmai_process_maps_refreshed_on_retry", False
                ),
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
                "decode_attempt_count": getattr(
                    exc, "lingmai_decode_attempt_count", 1
                ),
                "retried_generation_error": getattr(
                    exc, "lingmai_retried_generation_error", None
                ),
            },
        }
