from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
    resolve_manager_root,
)
from backend.core.fanxiu.instrumentation.role_progression import (
    read_role_profile_from_memory,
)


_DAOFA_MARKER = b"LuaImmortalRaceMgr"
_DAOFA_METHODS = frozenset({"LuaImmortalRaceMgr", "Inst_get", "ReqUserImmortalInfo"})
_XIANYUAN_MARKER = b"LuaPartnerarenaMgr"
_XIANYUAN_METHODS = frozenset({"LuaPartnerarenaMgr", "Inst_get", "GetActivityId"})


def _object_fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    fields = dict(reader.fields(value))
    inherited = fields.get("_super")
    seen: set[int] = set()
    while isinstance(inherited, LuaRef) and inherited.kind == "table":
        if inherited.address in seen:
            break
        seen.add(inherited.address)
        parent = dict(reader.fields(inherited))
        fields = {**parent, **fields}
        inherited = parent.get("_super")
    return fields


def _identity(reader: LuaJitReader, value: Any) -> int | None:
    return reader.long(value) if isinstance(value, LuaRef) else as_int(value)


def _number(reader: LuaJitReader, value: Any) -> int | float | None:
    identity = _identity(reader, value)
    if identity is not None:
        return identity
    if isinstance(value, float):
        return value
    return None


def _daofa_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _DAOFA_METHODS)
    instance = reader.fields(manager.get("inst"))
    model = reader.fields(instance.get("Model"))
    data = reader.fields(model.get("ImmortalRaceData"))
    if "immortalRaceInfo" not in data or "ravalList" not in data:
        raise FanxiuRuntimeMemoryError(
            "道法争锋 Runtime 模型尚未初始化",
            code="data_not_loaded",
        )
    return data


def _xianyuan_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _XIANYUAN_METHODS)
    instance = reader.fields(manager.get("inst"))
    model = reader.fields(instance.get("Model"))
    data = reader.fields(model.get("PartnerarenaData"))
    if "joinerVO" not in data or "targets" not in data:
        raise FanxiuRuntimeMemoryError(
            "仙缘斗法 Runtime 模型尚未初始化",
            code="data_not_loaded",
        )
    return data


def _main_lua_state_address(memory: MumuProcessMemory) -> int:
    from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
        _lua_addresses,
    )

    return int(_lua_addresses(memory)["state"], 16)


def _resolve_snapshot_root(
    memory: MumuProcessMemory,
    *,
    manager_key: str,
    marker: bytes,
    required_methods: frozenset[str],
    validate: Any,
    global_name: str | None,
) -> tuple[int, bool, str]:
    if global_name:
        try:
            root, cache_hit, _environment = resolve_lua_global_manager_root(
                memory,
                manager_key=manager_key,
                state_address=_main_lua_state_address(memory),
                global_name=global_name,
                required_methods=required_methods,
                validate=validate,
            )
            return root, cache_hit, "lua_global"
        except FanxiuRuntimeMemoryError as exc:
            # The exact global proves the Manager exists. Missing page data is
            # a precise natural-loading result; a constructor heap scan cannot
            # initialize it and would only turn it into a slow failure.
            if exc.code == "data_not_loaded":
                raise
    root, cache_hit = resolve_manager_root(
        memory,
        manager_key=manager_key,
        marker=marker,
        required_methods=required_methods,
        validate=validate,
    )
    return root, cache_hit, "constructor_marker"


def _daofa_snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
    self_power_hint: int | float | None = None,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    data = _daofa_data_fields(reader, root_address)
    play_info = _object_fields(reader, data.get("immortalRaceInfo"))
    joiner = _object_fields(reader, play_info.get("joinerVO"))
    raw_targets, declared_count = reader.list_items(data.get("ravalList"))
    targets: list[dict[str, Any]] = []
    self_power: int | float | None = self_power_hint
    self_power_source = "job_cache" if self_power_hint is not None else "arena_self_row"
    for wrapped in raw_targets:
        wrapper = _object_fields(reader, wrapped)
        if bool(wrapper.get("isMySelf")):
            self_fields = _object_fields(reader, wrapper.get("data"))
            self_power = _number(reader, self_fields.get("power"))
            continue
        fields = _object_fields(reader, wrapper.get("data") or wrapped)
        rank = as_int(fields.get("rank"))
        if rank is None:
            continue
        targets.append(
            {
                "id": _identity(reader, fields.get("id")),
                "rank": rank,
                "name": str(fields.get("name") or ""),
                "server_id": as_int(fields.get("server")),
                "power": _number(reader, fields.get("power")) or 0,
                "player": bool(fields.get("player")),
                "is_npc": not bool(fields.get("player")),
                "club": str(fields.get("club") or ""),
            }
        )
    if self_power is None:
        role_profile = read_role_profile_from_memory(memory)
        role_power = role_profile.get("battle_score") if role_profile.get("available") else None
        if isinstance(role_power, int | float) and role_power > 0:
            self_power = role_power
            self_power_source = "role_profile"
    remain_times = as_int(joiner.get("remainTimes"))
    rank = as_int(joiner.get("rank"))
    base_complete = rank is not None and remain_times is not None and bool(targets or remain_times == 0)
    target_powers_complete = all(
        not bool(target.get("player")) or float(target.get("power") or 0) > 0
        for target in targets
    )
    complete = bool(
        base_complete
        and isinstance(self_power, int | float)
        and self_power > 0
        and target_powers_complete
    )
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "base_complete": base_complete,
        "source": "runtime_memory",
        "protocol": "ImmortalRaceMgr.Model.ImmortalRaceData",
        "rank": rank,
        "old_rank": None,
        "remain_times": remain_times,
        "self_power": self_power,
        "self_power_source": self_power_source if self_power is not None else "unavailable",
        "target_powers_complete": target_powers_complete,
        "targets": sorted(targets, key=lambda item: int(item["rank"])),
        "declared_target_count": declared_count,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
            "self_power_source": self_power_source if self_power is not None else "unavailable",
        },
    }


def _xianyuan_target(
    reader: LuaJitReader,
    value: Any,
    *,
    include_formation: bool = True,
) -> dict[str, Any]:
    fields = _object_fields(reader, value)
    rank = _object_fields(reader, fields.get("rankVO"))
    team = _object_fields(reader, fields.get("teamVO"))
    normalized_team = _xianyuan_team(
        reader,
        fields.get("teamVO"),
        include_formation=include_formation,
    )
    return {
        "target_id": _identity(reader, fields.get("id")),
        "player": bool(fields.get("player")),
        "will_score": _identity(reader, fields.get("willScore")),
        "name": str(rank.get("name") or ""),
        "server_id": as_int(rank.get("server")),
        "score": _identity(reader, rank.get("score")),
        "rank": as_int(rank.get("rank")),
        "team_power": _identity(reader, team.get("power"))
        or _identity(reader, rank.get("power")),
        "team": normalized_team,
    }


def _xianyuan_team(
    reader: LuaJitReader,
    value: Any,
    *,
    include_formation: bool = True,
) -> dict[str, Any]:
    fields = _object_fields(reader, value)
    if not include_formation:
        return {
            "id": _identity(reader, fields.get("id")),
            "type": as_int(fields.get("type")),
            "power": _identity(reader, fields.get("power")),
            "partner_ids": [],
            "members": [],
            "formation_complete": False,
        }
    partner_ids_ref = fields.get("partnerIds")
    raw_partner_ids, _ = reader.list_items(partner_ids_ref) if isinstance(partner_ids_ref, LuaRef) else ([], 0)
    partner_ids = [identity for item in raw_partner_ids if (identity := _identity(reader, item)) is not None]
    team_detail_ref = fields.get("teamDetail")
    raw_members, _ = reader.list_items(team_detail_ref) if isinstance(team_detail_ref, LuaRef) else ([], 0)
    members: list[dict[str, Any]] = []
    for slot, item in enumerate(raw_members, start=1):
        member = _object_fields(reader, item)
        partner_id = _identity(reader, member.get("partnerId"))
        if partner_id is None:
            continue
        members.append(
            {
                "slot": slot,
                "partner_id": partner_id,
                "level": as_int(member.get("level")),
                "jie": as_int(member.get("jie")),
                "fight_power": _identity(reader, member.get("fightPower")),
            }
        )
    member_ids = [int(member["partner_id"]) for member in members]
    return {
        "id": _identity(reader, fields.get("id")),
        "type": as_int(fields.get("type")),
        "power": _identity(reader, fields.get("power")),
        "partner_ids": partner_ids,
        "members": members,
        "formation_complete": len(partner_ids) == 5 and member_ids == partner_ids,
    }


def _xianyuan_snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
    include_formations: bool = True,
    self_power_hint: int | float | None = None,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    data = _xianyuan_data_fields(reader, root_address)
    return _xianyuan_snapshot_from_data(
        memory,
        reader,
        data,
        root_address=root_address,
        root_cache_hit=root_cache_hit,
        root_kind="manager",
        include_formations=include_formations,
        self_power_hint=self_power_hint,
    )


def _xianyuan_snapshot_from_data(
    memory: MumuProcessMemory,
    reader: LuaJitReader,
    data: dict[Any, Any],
    *,
    root_address: int,
    root_cache_hit: bool,
    root_kind: str,
    include_formations: bool = True,
    self_power_hint: int | float | None = None,
) -> dict[str, Any]:
    joiner = _object_fields(reader, data.get("joinerVO"))
    raw_targets, declared_count = reader.list_items(data.get("targets"))
    targets = [
        _xianyuan_target(
            reader,
            value,
            include_formation=include_formations,
        )
        for value in raw_targets
    ]
    targets = [
        value
        for value in targets
        if value["target_id"] is not None
        and value["name"]
        and value["score"] is not None
        and value["team_power"] is not None
    ]
    raw_teams, _ = (
        reader.list_items(joiner.get("teams"))
        if include_formations or self_power_hint is None
        else ([], 0)
    )
    self_teams = [
        _xianyuan_team(reader, value, include_formation=include_formations)
        for value in raw_teams
    ]
    self_team = next((team for team in self_teams if team.get("type") == 0), None)
    if self_team is None and len(self_teams) == 1:
        self_team = self_teams[0]
    self_powers = [team["power"] for team in self_teams if team.get("power") is not None]
    self_power = (
        self_team.get("power")
        if self_team
        else (max(self_powers) if self_powers else self_power_hint)
    )
    remaining_challenges = as_int(joiner.get("remainChallengeTimes"))
    remaining_refreshes = as_int(joiner.get("remainRefreshTimes"))
    complete = (
        len(targets) == 3
        and isinstance(self_power, int | float)
        and self_power > 0
        and remaining_challenges is not None
        and remaining_refreshes is not None
    )
    captured_epoch = time.time()
    return {
        "ok": complete,
        "available": complete,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": "PartnerarenaMgr.Model.PartnerarenaData",
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "self_power": self_power,
        "self_power_source": "job_cache" if not self_teams and self_power_hint is not None else "partnerarena_joiner",
        "self_team": self_team,
        "self_teams": self_teams,
        "remaining_challenges": remaining_challenges,
        "remaining_refreshes": remaining_refreshes,
        "current_score": _identity(reader, joiner.get("current")),
        "rank": as_int(joiner.get("rank")),
        "targets": targets,
        "targets_complete": len(targets) == 3,
        "declared_target_count": declared_count,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
            "root_kind": root_kind,
            "sn": as_int(data.get("sn")),
            "captured_at_epoch": captured_epoch,
            "order_key": [captured_epoch],
            "formations_included": include_formations,
            "self_power_source": "job_cache" if not self_teams and self_power_hint is not None else "partnerarena_joiner",
        },
    }


def _read_snapshot(
    *,
    manager_key: str,
    marker: bytes,
    required_methods: frozenset[str],
    validate: Any,
    build: Any,
    global_name: str | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover()
        root, cache_hit, manager_resolver = _resolve_snapshot_root(
            memory,
            manager_key=manager_key,
            marker=marker,
            required_methods=required_methods,
            validate=validate,
            global_name=global_name,
        )
        result = build(memory, root, root_cache_hit=cache_hit)
        evidence = result.get("evidence")
        if isinstance(evidence, dict):
            evidence["manager_resolver"] = manager_resolver
        result["elapsed_seconds"] = time.perf_counter() - started_at
        return result
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "reason": str(exc)
            if isinstance(exc, FanxiuRuntimeMemoryError)
            else f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
            },
        }


def read_daofa_snapshot(*, self_power_hint: int | float | None = None) -> dict[str, Any]:
    return _read_snapshot(
        manager_key="daofa-arena",
        marker=_DAOFA_MARKER,
        required_methods=_DAOFA_METHODS,
        validate=_daofa_data_fields,
        build=lambda memory, root, *, root_cache_hit: _daofa_snapshot(
            memory,
            root,
            root_cache_hit=root_cache_hit,
            self_power_hint=self_power_hint,
        ),
        global_name="ImmortalRaceMgr",
    )


def read_xianyuan_duel_snapshot(
    *,
    include_formations: bool = True,
    self_power_hint: int | float | None = None,
) -> dict[str, Any]:
    # PartnerarenaMgr 是长期存活且原地更新的权威模型；根地址可在进程生命周期内
    # 持续校验复用。不要每轮扫描短命协议数据表，否则冷启动会读取整个 Lua 堆。
    return _read_snapshot(
        manager_key="xianyuan-duel-manager",
        marker=_XIANYUAN_MARKER,
        required_methods=_XIANYUAN_METHODS,
        validate=_xianyuan_data_fields,
        build=lambda memory, root, *, root_cache_hit: _xianyuan_snapshot(
            memory,
            root,
            root_cache_hit=root_cache_hit,
            include_formations=include_formations,
            self_power_hint=self_power_hint,
        ),
        global_name="PartnerarenaMgr",
    )
