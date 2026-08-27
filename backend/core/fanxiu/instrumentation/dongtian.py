from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from functools import lru_cache
from typing import Any

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_data_table_root,
    resolve_lua_global_manager_root,
    resolve_manager_root,
)


_MINES_MANAGER_ALIASES = (
    (
        "XianLvMinesMgr",
        b"XianLvMinesMgr",
        frozenset({"XianLvMinesMgr", "GetAttackDesLimitLength", "Inst_get"}),
    ),
    (
        "LuaXianLvMinesMgr",
        b"LuaXianLvMinesMgr",
        frozenset({"LuaXianLvMinesMgr", "GetAttackDesLimitLength", "Inst_get"}),
    ),
)
_CLUB_MARKER = b"LuaClubMgr"
_CLUB_MANAGER_ALIASES = ("ClubMgr", "LuaClubMgr")
_CLUB_METHODS = frozenset(
    {
        "LuaClubMgr",
        "HasCrossUnion",
        "Inst_get",
    }
)
_ROLE_MARKER = b"LuaRoleMgr"
_ROLE_MANAGER_ALIASES = ("RoleMgr", "LuaRoleMgr")
_ROLE_METHODS = frozenset({"LuaRoleMgr", "Inst_get", "IsCanUpGrade"})
_EXPECTED_TEAM_MEMBER_COUNT = 5
_EXPECTED_MASTER_SEAT_COUNT = 3
_EXPECTED_MINER_SEAT_COUNT = 9
_TEAM_STATE_FREE = 1
_TEAM_STATE_OCCUPY = 2
_DONGTIAN_SEATING_STRATEGY_NAME = "friendly_top_down_only"
_DONGTIAN_SEATING_ALLOW_NONFRIENDLY = False
_MINES_DATA_MARKER = b"V_AttackFatigueValue"
_MINES_DATA_FIELDS = frozenset(
    {
        "V_AttackFatigueValue",
        "_MaxAtkMaxTried",
        "V_MinesVoDic",
    }
)


@lru_cache(maxsize=1)
def _mines_place_static_config() -> tuple[dict[int, dict[str, Any]], str]:
    """Load the extracted, version-checked MinesPlace table read-only."""

    path = resolve_fanxiu_export_root() / "parsed_configs" / "MinesPlace" / "rows.json"
    try:
        payload = path.read_bytes()
        rows = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FanxiuRuntimeMemoryError(
            f"洞天 MinesPlace 静态配置不可用：{path}"
        ) from exc
    if not isinstance(rows, list):
        raise FanxiuRuntimeMemoryError("洞天 MinesPlace 静态配置格式错误")

    configs: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise FanxiuRuntimeMemoryError("洞天 MinesPlace 静态配置存在非对象行")
        mine_id = as_int(row.get("id"))
        name = str(row.get("minesName") or "").strip()
        pos = row.get("pos")
        special_mines = as_int(row.get("specialMines")) or 0
        people = as_int(row.get("people")) or 0
        group = as_int(row.get("group"))
        if (
            mine_id is None
            or mine_id <= 0
            or mine_id in configs
            or not name
            or people < 0
            or group is None
            or (
                special_mines == 0
                and (
                    not isinstance(pos, list)
                    or len(pos) < 2
                    or as_int(pos[1]) is None
                )
            )
        ):
            raise FanxiuRuntimeMemoryError("洞天 MinesPlace 静态配置字段不完整")
        configs[int(mine_id)] = {
            "id": int(mine_id),
            "pos": (
                [int(as_int(pos[0]) or 0), int(as_int(pos[1]))]
                if isinstance(pos, list)
                and len(pos) >= 2
                and as_int(pos[1]) is not None
                else []
            ),
            "pos_y": (
                int(as_int(pos[1]))
                if isinstance(pos, list)
                and len(pos) >= 2
                and as_int(pos[1]) is not None
                else 0
            ),
            "special_mines": int(special_mines),
            "people": int(people),
            "name": name,
            "group": int(group),
        }
    return configs, hashlib.sha256(payload).hexdigest()


class DongtianSeatingRuntimeSession:
    """Reuse one verified process/root identity during a short seating scan.

    Opening a Runtime snapshot is dominated by ADB process discovery and the
    Lua-state pointer chain.  A seating transaction may inspect several mines,
    so repeating those two cold steps for every mine is both slow and more
    likely to lose a useful seat.  This session resolves them once, but creates
    a fresh memory reader for every probe so decoded Lua tables are never
    reused across GUI actions.

    The session is deliberately short-lived.  A caller must invoke
    :meth:`revalidate_process_identity` immediately before any irreversible
    occupy/battle click; a changed process identity invalidates every cached
    root and fails closed.
    """

    def __init__(
        self,
        *,
        memory: MumuProcessMemory,
        state_address: int,
        mines_root: int,
        club_root: int,
        role_root: int,
        mines_root_kind: str,
        club_root_kind: str,
        role_root_kind: str,
        mines_cache_hit: bool,
        club_cache_hit: bool,
        role_cache_hit: bool,
        max_age_seconds: float = 120.0,
    ) -> None:
        self.pid = int(memory.pid)
        self.process_start_ticks = int(memory.process_start_ticks)
        self.adb_serial = str(memory.adb_serial)
        self.regions = tuple(memory.regions)
        self.state_address = int(state_address)
        self.mines_root = int(mines_root)
        self.club_root = int(club_root)
        self.role_root = int(role_root)
        self.mines_root_kind = str(mines_root_kind)
        self.club_root_kind = str(club_root_kind)
        self.role_root_kind = str(role_root_kind)
        self.mines_cache_hit = bool(mines_cache_hit)
        self.club_cache_hit = bool(club_cache_hit)
        self.role_cache_hit = bool(role_cache_hit)
        self.max_age_seconds = max(1.0, float(max_age_seconds))
        self.opened_at_monotonic = time.monotonic()
        self.shallow_exhausted_mine_ids: set[int] = set()

    @classmethod
    def open(
        cls,
        *,
        allow_legacy_scan: bool = False,
        max_age_seconds: float = 120.0,
    ) -> "DongtianSeatingRuntimeSession":
        memory = MumuProcessMemory.discover_cached()
        state_address = int(_lua_addresses(memory)["state"], 16)
        mines_root, mines_cache_hit, mines_root_kind = _resolve_mines_root(
            memory,
            state_address=state_address,
            allow_legacy_scan=allow_legacy_scan,
        )
        club_root, club_cache_hit, club_root_kind = _resolve_club_root(
            memory,
            state_address=state_address,
            allow_legacy_scan=allow_legacy_scan,
        )
        role_root, role_cache_hit, role_root_kind = _resolve_role_root(
            memory,
            state_address=state_address,
            allow_legacy_scan=allow_legacy_scan,
        )
        return cls(
            memory=memory,
            state_address=state_address,
            mines_root=mines_root,
            club_root=club_root,
            role_root=role_root,
            mines_root_kind=mines_root_kind,
            club_root_kind=club_root_kind,
            role_root_kind=role_root_kind,
            mines_cache_hit=mines_cache_hit,
            club_cache_hit=club_cache_hit,
            role_cache_hit=role_cache_hit,
            max_age_seconds=max_age_seconds,
        )

    def _require_live_session(self) -> None:
        age = time.monotonic() - self.opened_at_monotonic
        if age > self.max_age_seconds:
            raise FanxiuRuntimeMemoryError(
                f"洞天上座 Runtime 会话已过期：{age:.1f}s > {self.max_age_seconds:.1f}s"
            )

    def _fresh_memory(self) -> MumuProcessMemory:
        self._require_live_session()
        return MumuProcessMemory(
            pid=self.pid,
            process_start_ticks=self.process_start_ticks,
            adb_serial=self.adb_serial,
            regions=self.regions,
        )

    def probe(
        self,
        *,
        excluded_mine_ids: frozenset[int] | set[int] | None = None,
    ) -> dict[str, Any]:
        """Read the next top-to-bottom useful mine from fresh memory bytes."""

        started_at = time.perf_counter()
        memory = self._fresh_memory()
        requested_excluded = {
            int(item) for item in (excluded_mine_ids or set())
        }
        effective_excluded = requested_excluded | self.shallow_exhausted_mine_ids
        result = _seating_probe_snapshot(
            memory,
            self.mines_root,
            self.club_root,
            self.role_root,
            mines_cache_hit=self.mines_cache_hit,
            club_cache_hit=self.club_cache_hit,
            role_cache_hit=self.role_cache_hit,
            mines_root_kind=self.mines_root_kind,
            club_root_kind=self.club_root_kind,
            role_root_kind=self.role_root_kind,
            excluded_mine_ids=frozenset(effective_excluded),
        )
        self.shallow_exhausted_mine_ids.update(
            int(item)
            for item in result.get("shallow_exhausted_mine_ids") or []
        )
        result["elapsed_seconds"] = time.perf_counter() - started_at
        result.setdefault("evidence", {}).update(
            {
                "runtime_session": True,
                "session_age_seconds": time.monotonic()
                - self.opened_at_monotonic,
                "session_shallow_exhausted_mine_ids": sorted(
                    self.shallow_exhausted_mine_ids
                ),
            }
        )
        return result

    def cached_seat_detail(
        self,
        *,
        mine_id: int,
        quality: int,
        seat_id: int,
    ) -> dict[str, Any]:
        """Read one naturally loaded detail through the same process identity."""

        started_at = time.perf_counter()
        memory = self._fresh_memory()
        result = _cached_seat_detail_snapshot(
            memory,
            self.mines_root,
            mines_root_kind=self.mines_root_kind,
            mine_id=int(mine_id),
            quality=int(quality),
            seat_id=int(seat_id),
        )
        result["elapsed_seconds"] = time.perf_counter() - started_at
        result.setdefault("evidence", {})["runtime_session"] = True
        return result

    def cached_final_guard_team_detail(
        self,
        *,
        mine_id: int,
        quality: int,
        seat_id: int,
    ) -> dict[str, Any]:
        """Read the exact SiteInfoView guard-team cache for the final gate.

        Master-list detail chooses which shared master button will open.  Once
        SiteInfoView opens, both master and follower routes naturally refresh
        V_GuarderTeamDic; this separate accessor prevents the two freshness
        generations from being conflated.
        """

        started_at = time.perf_counter()
        memory = self._fresh_memory()
        result = _cached_guard_team_detail_snapshot(
            memory,
            self.mines_root,
            mines_root_kind=self.mines_root_kind,
            mine_id=int(mine_id),
            quality=int(quality),
            seat_id=int(seat_id),
        )
        result["elapsed_seconds"] = time.perf_counter() - started_at
        result.setdefault("evidence", {})["runtime_session"] = True
        return result

    def revalidate_process_identity(self) -> dict[str, Any]:
        """Cold-check the process and Lua roots before an irreversible click."""

        expected_identity = (
            self.pid,
            self.process_start_ticks,
            self.adb_serial,
        )
        expected_roots = (
            self.state_address,
            self.mines_root,
            self.club_root,
            self.mines_root_kind,
            self.club_root_kind,
            self.role_root,
            self.role_root_kind,
        )
        try:
            current = MumuProcessMemory.discover_cached(max_age_seconds=0.0)
            current_identity = (
                int(current.pid),
                int(current.process_start_ticks),
                str(current.adb_serial),
            )
            if current_identity != expected_identity:
                return {
                    "ok": False,
                    "reason": "process_identity_changed",
                    "expected": expected_identity,
                    "current": current_identity,
                    "expected_roots": expected_roots,
                    "current_roots": None,
                    "session_age_seconds": time.monotonic()
                    - self.opened_at_monotonic,
                }
            state_address = int(_lua_addresses(current)["state"], 16)
            mines_root, _mines_hit, mines_kind = _resolve_mines_root(
                current,
                state_address=state_address,
                allow_legacy_scan=False,
            )
            club_root, _club_hit, club_kind = _resolve_club_root(
                current,
                state_address=state_address,
                allow_legacy_scan=False,
            )
            role_root, _role_hit, role_kind = _resolve_role_root(
                current,
                state_address=state_address,
                allow_legacy_scan=False,
            )
            current_roots = (
                state_address,
                int(mines_root),
                int(club_root),
                str(mines_kind),
                str(club_kind),
                int(role_root),
                str(role_kind),
            )
            ok = current_roots == expected_roots
            reason = "" if ok else "lua_root_identity_changed"
        except Exception as exc:
            return {
                "ok": False,
                "reason": f"identity_revalidation_failed:{type(exc).__name__}:{exc}",
                "expected": expected_identity,
                "current": None,
                "expected_roots": expected_roots,
                "current_roots": None,
                "session_age_seconds": time.monotonic()
                - self.opened_at_monotonic,
            }
        return {
            "ok": ok,
            "reason": reason,
            "expected": expected_identity,
            "current": current_identity,
            "expected_roots": expected_roots,
            "current_roots": current_roots,
            "session_age_seconds": time.monotonic()
            - self.opened_at_monotonic,
        }


def _object_fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    """Flatten the packet VO's table-backed inheritance chain."""

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


def _mines_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    # The exact method set is version-dependent.  The global name already
    # identifies the manager; validate the stable Model.Data shape instead of
    # rejecting a valid live manager because a helper method was renamed.
    manager_fields = manager_index_fields(reader, root_address, frozenset())
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    data_fields = reader.fields(model_fields.get("Data"))
    required = {"V_MinesVoDic"}
    if not required.issubset(data_fields):
        raise FanxiuRuntimeMemoryError("洞天 Runtime 模型尚未初始化")
    return data_fields


def _mines_data_table_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    """Read Model.Data directly when the manager's global alias has drifted."""

    data_fields = reader.table(root_address)["fields"]
    if not _MINES_DATA_FIELDS.issubset(data_fields):
        raise FanxiuRuntimeMemoryError("洞天 Runtime 数据表尚未初始化")
    if (
        as_int(data_fields.get("V_AttackFatigueValue")) is None
        or as_int(data_fields.get("_MaxAtkMaxTried")) is None
        or not reader.dictionary_fields(data_fields.get("V_MinesVoDic"))
    ):
        raise FanxiuRuntimeMemoryError("洞天 Runtime 数据表事实不完整")
    return data_fields


def _mines_data_table_score(
    reader: LuaJitReader,
    root_address: int,
) -> tuple[int, int, int]:
    data_fields = reader.table(root_address)["fields"]
    mines = reader.dictionary_fields(data_fields.get("V_MinesVoDic"))
    return (
        len(mines),
        as_int(data_fields.get("_MaxAtkMaxTried")) or -1,
        int(root_address),
    )


def _resolve_mines_root(
    memory: MumuProcessMemory,
    *,
    state_address: int,
    allow_legacy_scan: bool = False,
    force_refresh: bool = False,
) -> tuple[int, bool, str]:
    """Resolve the loaded Manager directly from the main Lua environment.

    The formal job must not silently degrade into a multi-minute LuaJIT heap
    scan.  Marker/data-table discovery remains available only to explicit
    diagnostics via ``allow_legacy_scan=True``.
    """

    reasons: list[str] = []
    for alias, _marker, required_methods in _MINES_MANAGER_ALIASES:
        try:
            root, cache_hit, _environment = resolve_lua_global_manager_root(
                memory,
                manager_key=f"dongtian-mines-{alias.lower()}",
                state_address=state_address,
                global_name=alias,
                required_methods=frozenset(),
                validate=lambda reader, address: _mines_data_fields(reader, address),
                force_refresh=force_refresh,
            )
            return root, cache_hit, f"lua_global:{alias}"
        except (FanxiuRuntimeMemoryError, RuntimeError) as exc:
            reasons.append(f"global:{alias}: {exc}")

    for alias, marker, required_methods in _MINES_MANAGER_ALIASES:
        try:
            root, cache_hit = resolve_manager_root(
                memory,
                manager_key=f"dongtian-mines-{alias.lower()}",
                marker=marker,
                required_methods=required_methods,
                validate=lambda reader, address: _mines_data_fields(reader, address),
                allow_discovery=allow_legacy_scan,
                # Formal jobs may use a freshly validated marker cache, but
                # must not discard it and then start a forbidden heap scan.
                force_refresh=force_refresh and allow_legacy_scan,
            )
            return root, cache_hit, f"manager_cache:{alias}"
        except FanxiuRuntimeMemoryError as exc:
            reasons.append(f"{alias}: {exc}")

    if not allow_legacy_scan:
        raise FanxiuRuntimeMemoryError(
            "洞天 Runtime 快速根解析失败；正式作业拒绝退化为全堆扫描；"
            + "；".join(reasons)
        )

    try:
        root, cache_hit = resolve_data_table_root(
            memory,
            manager_key="dongtian-mines",
            marker=_MINES_DATA_MARKER,
            required_fields=_MINES_DATA_FIELDS,
            validate=_mines_data_table_fields,
            score=_mines_data_table_score,
        )
        return root, cache_hit, "data_table"
    except FanxiuRuntimeMemoryError as exc:
        reasons.append(f"data_table: {exc}")
    raise FanxiuRuntimeMemoryError("洞天 Runtime 根发现失败；" + "；".join(reasons))


def _resolve_club_root(
    memory: MumuProcessMemory,
    *,
    state_address: int,
    allow_legacy_scan: bool = False,
    force_refresh: bool = False,
) -> tuple[int, bool, str]:
    reasons: list[str] = []
    for alias in _CLUB_MANAGER_ALIASES:
        try:
            root, cache_hit, _environment = resolve_lua_global_manager_root(
                memory,
                manager_key="dongtian-club",
                state_address=state_address,
                global_name=alias,
                required_methods=frozenset(),
                validate=lambda reader, address: _club_data_fields(reader, address),
                force_refresh=force_refresh,
            )
            return root, cache_hit, f"lua_global:{alias}"
        except (FanxiuRuntimeMemoryError, RuntimeError) as exc:
            reasons.append(f"global:{alias}: {exc}")

    try:
        root, cache_hit = resolve_manager_root(
            memory,
            manager_key="dongtian-club",
            marker=_CLUB_MARKER,
            required_methods=_CLUB_METHODS,
            validate=lambda reader, address: _club_data_fields(reader, address),
            allow_discovery=allow_legacy_scan,
            force_refresh=force_refresh and allow_legacy_scan,
        )
        return root, cache_hit, "manager_cache:LuaClubMgr"
    except FanxiuRuntimeMemoryError as exc:
        reasons.append(f"LuaClubMgr: {exc}")
    raise FanxiuRuntimeMemoryError(
        "洞天联盟 Runtime 快速根解析失败；"
        + ("" if allow_legacy_scan else "正式作业拒绝退化为全堆扫描；")
        + "；".join(reasons)
    )


def _resolve_role_root(
    memory: MumuProcessMemory,
    *,
    state_address: int,
    allow_legacy_scan: bool = False,
    force_refresh: bool = False,
) -> tuple[int, bool, str]:
    """Resolve RoleMgr without initializing it or scanning on formal jobs."""

    reasons: list[str] = []
    for alias in _ROLE_MANAGER_ALIASES:
        try:
            root, cache_hit, _environment = resolve_lua_global_manager_root(
                memory,
                manager_key="dongtian-role",
                state_address=state_address,
                global_name=alias,
                required_methods=frozenset(),
                validate=lambda reader, address: _role_id(reader, address),
                force_refresh=force_refresh,
            )
            return root, cache_hit, f"lua_global:{alias}"
        except (FanxiuRuntimeMemoryError, RuntimeError) as exc:
            reasons.append(f"global:{alias}: {exc}")

    try:
        root, cache_hit = resolve_manager_root(
            memory,
            manager_key="dongtian-role",
            marker=_ROLE_MARKER,
            required_methods=_ROLE_METHODS,
            validate=lambda reader, address: _role_id(reader, address),
            allow_discovery=allow_legacy_scan,
            force_refresh=force_refresh and allow_legacy_scan,
        )
        return root, cache_hit, "manager_cache:LuaRoleMgr"
    except FanxiuRuntimeMemoryError as exc:
        reasons.append(f"LuaRoleMgr: {exc}")
    raise FanxiuRuntimeMemoryError(
        "洞天角色 Runtime 快速根解析失败；"
        + ("" if allow_legacy_scan else "正式作业拒绝退化为全堆扫描；")
        + "；".join(reasons)
    )


def _role_id(reader: LuaJitReader, root_address: int) -> int:
    manager_fields = manager_index_fields(reader, root_address, frozenset())
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    role_id = _identity(reader, model_fields.get("V_ID"))
    if role_id is None or role_id <= 0:
        raise FanxiuRuntimeMemoryError("洞天角色 Runtime 身份尚未加载")
    return int(role_id)


def _club_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager_fields = manager_index_fields(reader, root_address, frozenset())
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    data_fields = reader.fields(model_fields.get("data"))
    if not data_fields:
        raise FanxiuRuntimeMemoryError("联盟 Runtime 模型尚未初始化")
    return data_fields


def _union(
    reader: LuaJitReader,
    value: Any,
) -> dict[str, Any]:
    fields = _object_fields(reader, value)
    return {
        "id": _identity(reader, fields.get("id") or fields.get("crossUnionId")),
        "name": str(fields.get("name") or "").strip(),
    }


def _int_list(reader: LuaJitReader, value: Any) -> tuple[list[int], int | None]:
    items, declared_count = reader.list_items(value)
    values = [number for item in items if (number := as_int(item)) is not None and number > 0]
    return values, declared_count


def _team(reader: LuaJitReader, value: Any) -> dict[str, Any]:
    fields = _object_fields(reader, value)
    xianlv_ids, declared_member_count = _int_list(reader, fields.get("xianlvIds"))
    team_id = as_int(fields.get("id"))
    state = as_int(fields.get("state"))
    mine_id = as_int(fields.get("mineId"))
    seat_index = as_int(fields.get("index"))
    fight_score = as_int(fields.get("fightScore"))
    dead = fields.get("dead") if isinstance(fields.get("dead"), bool) else None
    complete = (
        team_id is not None
        and state is not None
        and mine_id is not None
        and seat_index is not None
        and fight_score is not None
        and dead is not None
        and len(xianlv_ids) == _EXPECTED_TEAM_MEMBER_COUNT
    )
    idle = bool(
        complete
        and state == _TEAM_STATE_FREE
        and dead is False
        and mine_id == 0
    )
    return {
        "id": team_id,
        "state": state,
        "mine_id": mine_id,
        "seat_index": seat_index,
        "fight_score": fight_score,
        "dead": dead,
        "xianlv_ids": xianlv_ids,
        "declared_member_count": declared_member_count,
        "complete": complete,
        "idle": idle,
    }


def _occupied_mine_ids(teams: list[dict[str, Any]]) -> frozenset[int]:
    """Return locations already occupied by any of the current role's teams.

    A role may place at most one team in a location.  Master and follower
    seats share this location-level exclusion, so the probe must reject the
    whole mine before decoding its twelve seats.
    """

    return frozenset(
        mine_id
        for team in teams
        if as_int(team.get("state")) == _TEAM_STATE_OCCUPY
        and (mine_id := as_int(team.get("mine_id"))) is not None
        and mine_id > 0
    )


def _seat(
    reader: LuaJitReader,
    value: Any,
    *,
    quality: int,
    display_order: int,
) -> dict[str, Any]:
    fields = _object_fields(reader, value)
    guarder_fields = _object_fields(reader, fields.get("guarder"))
    guarder_present = bool(guarder_fields)
    guarder_type = as_int(guarder_fields.get("type")) if guarder_fields else 0
    guarder_identity_value = guarder_fields.get("id")
    if guarder_identity_value is None:
        guarder_identity_value = guarder_fields.get("roleId")
    seat_id = as_int(fields.get("id"))
    empty = guarder_type in {None, 0}
    return {
        "quality": int(quality),
        "display_order": int(display_order),
        "primary_master": bool(int(quality) == 1 and int(display_order) == 0),
        "id": seat_id,
        "empty": empty,
        "guarder_present": guarder_present,
        "guarder_type": 0 if empty else guarder_type,
        "guarder_cross_union_id": (
            _identity(reader, guarder_fields.get("crossUnionId"))
            if guarder_fields
            else None
        ),
        "guarder_role_id": (
            _identity(reader, guarder_identity_value)
            if guarder_fields
            else None
        ),
        "complete": seat_id is not None and (empty or guarder_type is not None),
    }


def _mine_seats(reader: LuaJitReader, fields: dict[Any, Any]) -> tuple[list[dict[str, Any]], bool]:
    masters, master_declared_count = reader.list_items(fields.get("mineMasters"))
    miners, miner_declared_count = reader.list_items(fields.get("miners"))
    seats = [
        *(_seat(reader, item, quality=1, display_order=index) for index, item in enumerate(masters)),
        *(_seat(reader, item, quality=2, display_order=index) for index, item in enumerate(miners)),
    ]
    complete = (
        len(masters) == _EXPECTED_MASTER_SEAT_COUNT
        and len(miners) == _EXPECTED_MINER_SEAT_COUNT
        and master_declared_count in {None, _EXPECTED_MASTER_SEAT_COUNT}
        and miner_declared_count in {None, _EXPECTED_MINER_SEAT_COUNT}
        and all(bool(seat.get("complete")) for seat in seats)
        # Native V_GuarderTeamDic and GetSiteVO address seats by mineId+id,
        # without quality.  Duplicate ids across master/follower are therefore
        # ambiguous even when (quality, id) pairs differ.
        and len({seat["id"] for seat in seats}) == len(seats)
    )
    return seats, complete


def _validated_mine_records(
    reader: LuaJitReader,
    mines_data: dict[Any, Any],
) -> tuple[list[tuple[Any, dict[Any, Any], int]], int | None, int, str]:
    """Join GUI place configuration to the accumulated dynamic mine map.

    ``V_Mines`` is overwritten by every server update and can therefore be a
    one-item incremental batch.  Live ``V_MinesPlaceList`` entries are tolua
    config rows whose stable numeric id occupies array slot 1; older fixtures
    may expose that id directly.  Authoritative fields still come from the
    extracted, version-checked ``XianLvMines_MinesPlace`` table; current
    ownership and seats live in ``V_MinesVoDic`` keyed by the same id.
    """

    last_update_values, _last_update_declared = reader.list_items(
        mines_data.get("V_Mines")
    )
    place_values, declared_count = reader.list_items(
        mines_data.get("V_MinesPlaceList")
    )
    if not place_values:
        raise FanxiuRuntimeMemoryError("洞天地点 V_MinesPlaceList 尚未初始化")
    if declared_count not in {None, len(place_values)}:
        raise FanxiuRuntimeMemoryError(
            "洞天地点配置 ID 列表计数不一致："
            f"items={len(place_values)}, declared={declared_count}"
        )

    place_ids: list[int] = []
    for value in place_values:
        mine_id = _identity(reader, value)
        if mine_id is None and isinstance(value, LuaRef) and value.kind == "table":
            array = reader.table(value.address)["array"]
            mine_id = as_int(array[1]) if len(array) > 1 else None
        if mine_id is None or mine_id <= 0:
            raise FanxiuRuntimeMemoryError("洞天地点配置 ID 列表存在非标量项")
        place_ids.append(int(mine_id))
    if len(set(place_ids)) != len(place_ids):
        raise FanxiuRuntimeMemoryError("洞天地点配置 ID 重复，拒绝生成席位目标")

    configs, config_sha256 = _mines_place_static_config()
    configured_place_ids = {
        mine_id
        for mine_id, config in configs.items()
        if int(config["special_mines"]) == 0
    }
    if set(place_ids) != configured_place_ids:
        raise FanxiuRuntimeMemoryError(
            "洞天地点 Runtime ID 集合与 MinesPlace 静态配置版本不匹配"
        )
    member_num = as_int(mines_data.get("_memberNum"))
    if member_num is None or member_num < 0:
        raise FanxiuRuntimeMemoryError("洞天 Runtime 联盟人数事实不完整")
    visible_ids = {
        mine_id
        for mine_id in place_ids
        if int(configs[mine_id]["people"]) <= member_num
    }

    dynamic_by_id: dict[int, tuple[Any, dict[Any, Any]]] = {}
    for key, value in reader.dictionary_fields(
        mines_data.get("V_MinesVoDic")
    ).items():
        fields = _object_fields(reader, value)
        value_id = as_int(fields.get("id"))
        key_id = _identity(reader, key)
        mine_id = value_id if value_id is not None else key_id
        if mine_id is None or mine_id <= 0 or (
            value_id is not None and key_id is not None and value_id != key_id
        ):
            raise FanxiuRuntimeMemoryError("洞天地点动态字典身份不完整")
        if int(mine_id) in dynamic_by_id:
            raise FanxiuRuntimeMemoryError("洞天地点动态字典 ID 重复")
        dynamic_by_id[int(mine_id)] = (value, fields)

    dynamic_ids = set(dynamic_by_id)
    if dynamic_ids != visible_ids:
        raise FanxiuRuntimeMemoryError(
            "洞天地点动态字典与当前可见配置 ID 集合不一致："
            f"missing={sorted(visible_ids - dynamic_ids)}, "
            f"extra={sorted(dynamic_ids - visible_ids)}"
        )

    records = [
        (*dynamic_by_id[mine_id], mine_id)
        for mine_id in sorted(
            visible_ids,
            key=lambda item: (-int(configs[item]["pos_y"]), item),
        )
    ]
    return records, declared_count, len(last_update_values), config_sha256


def _guard_team_detail(
    reader: LuaJitReader,
    value: Any,
    *,
    mine_id: int,
    quality: int,
    seat_id: int,
) -> dict[str, Any]:
    """Decode one naturally loaded defender team from the client cache."""

    fields = _object_fields(reader, value)
    xianlv_ids, declared_member_count = _int_list(reader, fields.get("xianlvIds"))
    observed_mine_id = as_int(fields.get("mineId"))
    observed_seat_id = as_int(fields.get("index"))
    fight_score = as_int(fields.get("fightScore"))
    complete = bool(
        observed_mine_id == int(mine_id)
        and observed_seat_id == int(seat_id)
        and fight_score is not None
        and fight_score > 0
        and len(xianlv_ids) == _EXPECTED_TEAM_MEMBER_COUNT
    )
    return {
        "mine_id": int(mine_id),
        "quality": int(quality),
        "seat_id": int(seat_id),
        "observed_mine_id": observed_mine_id,
        "observed_seat_id": observed_seat_id,
        "team_id": as_int(fields.get("id") or fields.get("teamId")),
        "fight_score": fight_score,
        "xianlv_ids": xianlv_ids,
        "declared_member_count": declared_member_count,
        "complete": complete,
    }


def _master_detail(
    reader: LuaJitReader,
    value: Any,
    *,
    mine_id: int,
    seat_id: int,
) -> dict[str, Any]:
    fields = _object_fields(reader, value)
    guarder = _object_fields(reader, fields.get("guarder"))
    guarder_identity_value = guarder.get("id")
    if guarder_identity_value is None:
        guarder_identity_value = guarder.get("roleId")
    fight_score = as_int(fields.get("fightScore"))
    observed_seat_id = as_int(fields.get("id"))
    return {
        "mine_id": int(mine_id),
        "quality": 1,
        "seat_id": int(seat_id),
        "observed_seat_id": observed_seat_id,
        "team_id": as_int(fields.get("teamId")),
        "fight_score": fight_score,
        "guarder_present": bool(guarder),
        "guarder_role_id": _identity(reader, guarder_identity_value) if guarder else None,
        "guarder_cross_union_id": (
            _identity(reader, guarder.get("crossUnionId")) if guarder else None
        ),
        "complete": bool(
            observed_seat_id == int(seat_id)
            and fight_score is not None
            and fight_score > 0
            and guarder
        ),
    }


def _cached_seat_detail_snapshot(
    memory: MumuProcessMemory,
    mines_root: int,
    *,
    mines_root_kind: str,
    mine_id: int,
    quality: int,
    seat_id: int,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    data = (
        _mines_data_table_fields(reader, mines_root)
        if mines_root_kind == "data_table"
        else _mines_data_fields(reader, mines_root)
    )
    detail: dict[str, Any] | None = None
    cache_generation_address: int | None = None
    cache_record_address: int | None = None
    if int(quality) == 1:
        by_mine = reader.dictionary_fields(data.get("V_MineMasterDetailListDic"))
        detail_list = by_mine.get(int(mine_id))
        if detail_list is None:
            detail_list = by_mine.get(str(int(mine_id)))
        # XianLvMinesData:SM_XianLvMineMasterDetailListFun stores the current
        # response with AddOrSetItem(mineId, msg.masterDetails).  Therefore a
        # changed Lua table address is a natural-response generation marker,
        # not a timestamp inferred from unchanged business fields.
        if isinstance(detail_list, LuaRef):
            cache_generation_address = int(detail_list.address)
        items, declared_count = reader.list_items(detail_list)
        for item in items:
            fields = _object_fields(reader, item)
            if as_int(fields.get("id")) == int(seat_id):
                detail = _master_detail(reader, item, mine_id=mine_id, seat_id=seat_id)
                detail["declared_detail_count"] = declared_count
                if isinstance(item, LuaRef):
                    cache_record_address = int(item.address)
                break
    elif int(quality) == 2:
        key = f"{int(mine_id)}_{int(seat_id)}"
        values = reader.dictionary_fields(data.get("V_GuarderTeamDic"))
        value = values.get(key)
        if value is not None:
            # The follower response likewise replaces the cached team VO.
            if isinstance(value, LuaRef):
                cache_generation_address = int(value.address)
                cache_record_address = int(value.address)
            detail = _guard_team_detail(
                reader,
                value,
                mine_id=mine_id,
                quality=quality,
                seat_id=seat_id,
            )
    else:
        raise FanxiuRuntimeMemoryError(f"不支持的洞天席位类型：{quality}")
    if detail is not None:
        detail["cache_generation_address"] = cache_generation_address
        detail["cache_record_address"] = cache_record_address
    return {
        "ok": detail is not None and bool(detail.get("complete")),
        "available": True,
        "complete": detail is not None and bool(detail.get("complete")),
        "source": "runtime_memory_cache",
        "protocol": "dongtian.seat-detail.cache.v1",
        "mine_id": int(mine_id),
        "quality": int(quality),
        "seat_id": int(seat_id),
        "cache_found": detail is not None,
        "detail": detail,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
        },
    }


def _cached_guard_team_detail_snapshot(
    memory: MumuProcessMemory,
    mines_root: int,
    *,
    mines_root_kind: str,
    mine_id: int,
    quality: int,
    seat_id: int,
) -> dict[str, Any]:
    """Read the natural SiteInfoView response cache for either seat quality."""

    if int(quality) not in {1, 2}:
        raise FanxiuRuntimeMemoryError(f"不支持的洞天席位类型：{quality}")
    reader = LuaJitReader(memory)
    data = (
        _mines_data_table_fields(reader, mines_root)
        if mines_root_kind == "data_table"
        else _mines_data_fields(reader, mines_root)
    )
    key = f"{int(mine_id)}_{int(seat_id)}"
    value = reader.dictionary_fields(data.get("V_GuarderTeamDic")).get(key)
    detail: dict[str, Any] | None = None
    cache_generation_address: int | None = None
    if value is not None:
        if isinstance(value, LuaRef):
            cache_generation_address = int(value.address)
        detail = _guard_team_detail(
            reader,
            value,
            mine_id=mine_id,
            quality=quality,
            seat_id=seat_id,
        )
        detail["cache_generation_address"] = cache_generation_address
        detail["cache_record_address"] = cache_generation_address
    complete = detail is not None and bool(detail.get("complete"))
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory_cache",
        "protocol": "dongtian.seat-detail.final-guard-team-cache.v1",
        "detail_layer": "site_info_guard_team",
        "mine_id": int(mine_id),
        "quality": int(quality),
        "seat_id": int(seat_id),
        "cache_found": detail is not None,
        "detail": detail,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
        },
    }


def _mine_has_shallow_seating_candidate(
    seats: list[dict[str, Any]],
    *,
    own_union_id: int,
    own_role_id: int,
    mine_union_id: int | None,
) -> bool:
    """Return whether one mine is worth opening for the next seating step.

    This is deliberately shallow.  Empty seats are immediately actionable;
    monsters, neutral players and enemy players require one GUI-triggered
    detail read.  This also mirrors the native master-list entry contract so a
    mine is not selected merely for an occupied master that the UI cannot
    challenge.
    """

    friendly_master_has_self = bool(
        mine_union_id == own_union_id
        and any(
            as_int(seat.get("quality")) == 1
            and bool(seat.get("guarder_present"))
            and as_int(seat.get("guarder_role_id")) == own_role_id
            for seat in seats
        )
    )
    for seat in seats:
        quality = as_int(seat.get("quality"))
        guarder_type = as_int(seat.get("guarder_type"))
        guarder_union_id = as_int(seat.get("guarder_cross_union_id"))
        if quality == 1:
            if friendly_master_has_self:
                # Native unified master button recalls V_SelfData before it
                # searches for an empty row.  No master seat is occupiable via
                # this route while the current role already owns one here.
                continue
            if mine_union_id != own_union_id and not bool(
                seat.get("primary_master")
            ):
                # On a non-friendly mine only the first displayed master is
                # actionable; even an empty later row cannot be selected by
                # the shared bottom button.
                continue
            if bool(seat.get("empty")):
                return True
            if mine_union_id == own_union_id:
                # A friendly master-list button only chooses an empty row; it
                # is disabled when all rows are occupied by other roles.
                continue
        elif bool(seat.get("empty")):
            return True
        if guarder_type == 2 and guarder_union_id == own_union_id:
            continue
        # The native XianLvMinesSiteItem blocks an occupied follower when its
        # guarder belongs to the location-owning alliance ("联盟保护").  This
        # is authoritative shallow Runtime data, so skip it without opening a
        # detail page.  Masters use a separate list/occupation flow.
        if (
            quality == 2
            and guarder_type == 2
            and mine_union_id is not None
            and guarder_union_id == mine_union_id
        ):
            continue
        if guarder_type != 2 or guarder_union_id != own_union_id:
            return True
    return False


def _seating_probe_snapshot(
    memory: MumuProcessMemory,
    mines_root: int,
    club_root: int,
    role_root: int,
    *,
    mines_cache_hit: bool,
    club_cache_hit: bool,
    mines_root_kind: str,
    club_root_kind: str,
    role_root_kind: str,
    role_cache_hit: bool,
    excluded_mine_ids: frozenset[int],
) -> dict[str, Any]:
    """Decode only enough Runtime state to choose the next mine to inspect.

    Mine references are ordered by the game's own top-to-bottom list.  We
    decode team facts once, then stop at the first friendly mine that may have
    a seat.  Non-friendly locations belong to the separate stamina-clearing
    workflow and are never candidates for seating.
    """

    reader = LuaJitReader(memory)
    mines_data = (
        _mines_data_table_fields(reader, mines_root)
        if mines_root_kind == "data_table"
        else _mines_data_fields(reader, mines_root)
    )
    club_data = _club_data_fields(reader, club_root)
    fatigue_used = as_int(mines_data.get("V_AttackFatigueValue"))
    action_power_max = as_int(mines_data.get("_MaxAtkMaxTried"))
    action_power = (
        max(0, action_power_max - fatigue_used)
        if action_power_max is not None and fatigue_used is not None
        else None
    )
    own_union = _union(
        reader,
        club_data.get("v_crossUnionInfo") or club_data.get("v_redInfo"),
    )
    if own_union["id"] is None:
        own_union["id"] = _union(reader, club_data.get("v_redInfo"))["id"]
    own_union_id = as_int(own_union["id"])
    if own_union_id is None:
        raise FanxiuRuntimeMemoryError("洞天联盟 Runtime 缺少本方联盟 ID")
    own_role_id = _role_id(reader, role_root)

    team_values = reader.dictionary_fields(mines_data.get("V_TeamDic"))
    teams = sorted(
        (_team(reader, value) for value in team_values.values()),
        key=lambda item: int(item.get("id") or 0),
    )
    expected_team_count = as_int(mines_data.get("_MaxTeamNum"))
    teams_complete = bool(
        expected_team_count is not None
        and expected_team_count > 0
        and len(teams) == expected_team_count
        and all(bool(team.get("complete")) for team in teams)
    )
    if not teams_complete:
        raise FanxiuRuntimeMemoryError("洞天 Runtime 队伍事实不完整")
    idle_teams = [team for team in teams if bool(team.get("idle"))]
    occupied_mine_ids = _occupied_mine_ids(teams)
    if not idle_teams:
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "status": "noop_no_idle",
            "teams": teams,
            "idle_teams": [],
            "own_union_id": own_union_id,
            "own_union_name": own_union["name"],
            "own_role_id": own_role_id,
            "occupied_mine_ids": sorted(occupied_mine_ids),
            "selected_mine": None,
            "scanned_mine_count": 0,
            "strategy_name": _DONGTIAN_SEATING_STRATEGY_NAME,
            "allow_nonfriendly": _DONGTIAN_SEATING_ALLOW_NONFRIENDLY,
        }

    mine_records, declared_count, last_update_batch_count, config_sha256 = (
        _validated_mine_records(reader, mines_data)
    )
    place_configs, _cached_config_sha256 = _mines_place_static_config()

    ordered_mines: list[
        tuple[dict[Any, Any], int, dict[str, Any], int]
    ] = []
    for display_order, (_value, fields, mine_id) in enumerate(mine_records):
        if mine_id in excluded_mine_ids or mine_id in occupied_mine_ids:
            continue
        union = _union(reader, fields.get("crossUnion"))
        ordered_mines.append((fields, mine_id, union, display_order))

    selected: dict[str, Any] | None = None
    decoded_mine_count = 0
    shallow_exhausted_mine_ids: list[int] = []

    def decode_candidate(
        fields: dict[Any, Any],
        mine_id: int,
        union: dict[str, Any],
        display_order: int,
    ) -> dict[str, Any] | None:
        nonlocal decoded_mine_count
        decoded_mine_count += 1
        config = place_configs.get(mine_id)
        if not isinstance(config, dict) or not str(config.get("name") or "").strip():
            raise FanxiuRuntimeMemoryError(
                f"洞天地点 {mine_id} 缺少 MinesPlace 结构化配置"
            )
        seats, seats_complete = _mine_seats(reader, fields)
        if not seats_complete:
            raise FanxiuRuntimeMemoryError(f"洞天地点 {mine_id} 的 12 席浅层事实不完整")
        mine_union_id = as_int(union.get("id"))
        if not _mine_has_shallow_seating_candidate(
            seats,
            own_union_id=own_union_id,
            own_role_id=own_role_id,
            mine_union_id=mine_union_id,
        ):
            shallow_exhausted_mine_ids.append(mine_id)
            return None
        return {
            "id": mine_id,
            "config_id": mine_id,
            "name": str(config["name"]),
            "config_name": str(config["name"]),
            "config_group": int(config["group"]),
            "config_pos_y": int(config["pos_y"]),
            "config_people": int(config["people"]),
            "cross_union_id": union["id"],
            "cross_union_name": union["name"],
            "friendly": union["id"] == own_union_id,
            "display_order": int(display_order),
            "own_role_id": own_role_id,
            "seats": seats,
            "seats_complete": True,
        }

    # Seating is restricted to friendly-owned locations.  Only mine headers
    # are decoded globally; the expensive twelve-seat traversal follows native
    # display order and stops as soon as one friendly mine can produce the
    # next action.
    for fields, mine_id, union, display_order in ordered_mines:
        if union["id"] != own_union_id:
            continue
        selected = decode_candidate(fields, mine_id, union, display_order)
        if selected is not None:
            break

    return {
        "ok": True,
        "available": True,
        "complete": True,
        "status": "ready" if selected is not None else "no_shallow_candidate",
        "source": "runtime_memory",
        "protocol": "dongtian.seating.probe.v1",
        "selection_policy": "friendly_native_display_order_only",
        "strategy_name": _DONGTIAN_SEATING_STRATEGY_NAME,
        "allow_nonfriendly": _DONGTIAN_SEATING_ALLOW_NONFRIENDLY,
        "teams": teams,
        "idle_teams": idle_teams,
        "own_union_id": own_union_id,
        "own_union_name": own_union["name"],
        "own_role_id": own_role_id,
        "occupied_mine_ids": sorted(occupied_mine_ids),
        "selected_mine": selected,
        "scanned_mine_count": decoded_mine_count,
        "scanned_mine_header_count": len(ordered_mines),
        "declared_mine_count": declared_count,
        "last_update_batch_count": last_update_batch_count,
        "mines_place_config_sha256": config_sha256,
        "excluded_mine_ids": sorted(excluded_mine_ids),
        "shallow_exhausted_mine_ids": shallow_exhausted_mine_ids,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "mines_root_cache_hit": mines_cache_hit,
            "club_root_cache_hit": club_cache_hit,
            "role_root_cache_hit": role_cache_hit,
            "role_root_address": f"0x{role_root:x}",
            "role_root_kind": role_root_kind,
        },
    }


def _snapshot(
    memory: MumuProcessMemory,
    mines_root: int,
    club_root: int,
    role_root: int,
    *,
    mines_cache_hit: bool,
    club_cache_hit: bool,
    role_cache_hit: bool,
    mines_root_kind: str = "manager",
    club_root_kind: str = "manager",
    role_root_kind: str = "manager",
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    mines_data = (
        _mines_data_table_fields(reader, mines_root)
        if mines_root_kind == "data_table"
        else _mines_data_fields(reader, mines_root)
    )
    club_data = _club_data_fields(reader, club_root)

    fatigue_used = as_int(mines_data.get("V_AttackFatigueValue"))
    action_power_max = as_int(mines_data.get("_MaxAtkMaxTried"))
    action_power = (
        max(0, action_power_max - fatigue_used)
        if action_power_max is not None and fatigue_used is not None
        else None
    )
    reward_red_dot = mines_data.get("rewardRedDot")
    reward_available = (
        bool(reward_red_dot)
        if isinstance(reward_red_dot, bool)
        else None
    )
    mine_records, declared_count, last_update_batch_count, config_sha256 = (
        _validated_mine_records(reader, mines_data)
    )
    place_configs, _cached_config_sha256 = _mines_place_static_config()

    mines: list[dict[str, Any]] = []
    mines_seating_complete = True
    for _value, fields, mine_id in mine_records:
        config = place_configs.get(mine_id)
        if not isinstance(config, dict) or not str(config.get("name") or "").strip():
            raise FanxiuRuntimeMemoryError(
                f"洞天地点 {mine_id} 缺少 MinesPlace 结构化配置"
            )
        union = _union(reader, fields.get("crossUnion"))
        seats, seats_complete = _mine_seats(reader, fields)
        mines_seating_complete = mines_seating_complete and seats_complete
        mines.append(
            {
                "id": mine_id,
                "config_id": mine_id,
                "name": str(config["name"]),
                "config_name": str(config["name"]),
                "config_group": int(config["group"]),
                "config_pos_y": int(config["pos_y"]),
                "config_people": int(config["people"]),
                "cross_union_id": union["id"],
                "cross_union_name": union["name"],
                "seats": seats,
                "seats_complete": seats_complete,
            }
        )

    team_values = reader.dictionary_fields(mines_data.get("V_TeamDic"))
    teams = sorted(
        (_team(reader, value) for value in team_values.values()),
        key=lambda item: int(item.get("id") or 0),
    )
    expected_team_count = as_int(mines_data.get("_MaxTeamNum"))
    teams_complete = bool(
        expected_team_count is not None
        and expected_team_count > 0
        and len(teams) == expected_team_count
        and all(bool(team.get("complete")) for team in teams)
        and len({team.get("id") for team in teams}) == len(teams)
    )
    map_complete = bool(mines)
    seating_summary_complete = bool(
        map_complete
        and mines_seating_complete
        and teams_complete
    )
    idle_teams = [team for team in teams if bool(team.get("idle"))]

    own_union = _union(
        reader,
        club_data.get("v_crossUnionInfo") or club_data.get("v_redInfo"),
    )
    if own_union["id"] is None:
        red_union = _union(reader, club_data.get("v_redInfo"))
        own_union["id"] = red_union["id"]
    own_role_id = _role_id(reader, role_root)

    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    complete = (
        action_power is not None
        and reward_available is not None
        and map_complete
        and own_union["id"] is not None
        and own_role_id > 0
    )
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": (
            "XianLvMinesMgr.Model.Data + ClubMgr.Model.data + "
            "RoleMgr.Model.V_ID"
        ),
        "action_power": action_power,
        "action_power_max": action_power_max,
        "fatigue_used": fatigue_used,
        "reward_available": reward_available,
        "own_union_id": own_union["id"],
        "own_union_name": own_union["name"],
        "own_role_id": own_role_id,
        "mines": mines,
        "map_complete": map_complete,
        "expected_mine_count": len(mine_records),
        "declared_mine_count": declared_count,
        "last_update_batch_count": last_update_batch_count,
        "mines_place_config_sha256": config_sha256,
        "decoded_mine_count": len(mines),
        "teams": teams,
        "idle_teams": idle_teams,
        "expected_team_count": expected_team_count,
        "decoded_team_count": len(teams),
        "teams_complete": teams_complete,
        "mines_seating_complete": mines_seating_complete,
        "seating_summary_complete": seating_summary_complete,
        "captured_at": captured_at,
        "captured_at_epoch": time.time(),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "mines_root_address": f"0x{mines_root:x}",
            "mines_root_kind": mines_root_kind,
            "club_root_address": f"0x{club_root:x}",
            "club_root_kind": club_root_kind,
            "role_root_address": f"0x{role_root:x}",
            "role_root_kind": role_root_kind,
            "mines_root_cache_hit": mines_cache_hit,
            "club_root_cache_hit": club_cache_hit,
            "role_root_cache_hit": role_cache_hit,
        },
    }


def _action_power_snapshot(
    memory: MumuProcessMemory,
    mines_root: int,
    *,
    mines_cache_hit: bool,
    mines_root_kind: str = "manager",
) -> dict[str, Any]:
    """Read only the two scalar fields needed by the clear-stamina loop."""

    reader = LuaJitReader(memory)
    mines_data = (
        _mines_data_table_fields(reader, mines_root)
        if mines_root_kind == "data_table"
        else _mines_data_fields(reader, mines_root)
    )
    fatigue_used = as_int(mines_data.get("V_AttackFatigueValue"))
    action_power_max = as_int(mines_data.get("_MaxAtkMaxTried"))
    action_power = (
        max(0, action_power_max - fatigue_used)
        if action_power_max is not None and fatigue_used is not None
        else None
    )
    complete = action_power is not None
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": "XianLvMinesMgr.Model.Data.action_power.v1",
        "action_power": action_power,
        "action_power_max": action_power_max,
        "fatigue_used": fatigue_used,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "captured_at_epoch": time.time(),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "mines_root_address": f"0x{mines_root:x}",
            "mines_root_kind": mines_root_kind,
            "mines_root_cache_hit": mines_cache_hit,
        },
    }


def _clear_plan_snapshot(
    memory: MumuProcessMemory,
    mines_root: int,
    club_root: int,
    *,
    mines_cache_hit: bool,
    club_cache_hit: bool,
    mines_root_kind: str = "manager",
    club_root_kind: str = "manager",
) -> dict[str, Any]:
    """Read the stable ownership plan without decoding seats or teams."""

    reader = LuaJitReader(memory)
    mines_data = (
        _mines_data_table_fields(reader, mines_root)
        if mines_root_kind == "data_table"
        else _mines_data_fields(reader, mines_root)
    )
    club_data = _club_data_fields(reader, club_root)
    mine_records, declared_count, last_update_batch_count, config_sha256 = (
        _validated_mine_records(reader, mines_data)
    )
    place_configs, _cached_config_sha256 = _mines_place_static_config()
    mines: list[dict[str, Any]] = []
    for _value, fields, mine_id in mine_records:
        config = place_configs.get(mine_id)
        if not isinstance(config, dict) or not str(config.get("name") or "").strip():
            raise FanxiuRuntimeMemoryError(
                f"洞天地点 {mine_id} 缺少 MinesPlace 结构化配置"
            )
        union = _union(reader, fields.get("crossUnion"))
        mines.append(
            {
                "id": mine_id,
                "config_id": mine_id,
                "name": str(config["name"]),
                "config_name": str(config["name"]),
                "cross_union_id": union["id"],
                "cross_union_name": union["name"],
            }
        )

    own_union = _union(
        reader,
        club_data.get("v_crossUnionInfo") or club_data.get("v_redInfo"),
    )
    if own_union["id"] is None:
        red_union = _union(reader, club_data.get("v_redInfo"))
        own_union["id"] = red_union["id"]
    complete = bool(
        action_power is not None
        and mines
        and (own_union["id"] is not None or own_union["name"])
    )
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": "XianLvMinesMgr.Model.Data.clear_plan.v1",
        "action_power": action_power,
        "action_power_max": action_power_max,
        "fatigue_used": fatigue_used,
        "own_union_id": own_union["id"],
        "own_union_name": own_union["name"],
        "mines": mines,
        "map_complete": bool(mines),
        "expected_mine_count": len(mine_records),
        "declared_mine_count": declared_count,
        "last_update_batch_count": last_update_batch_count,
        "mines_place_config_sha256": config_sha256,
        "decoded_mine_count": len(mines),
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "captured_at_epoch": time.time(),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "mines_root_address": f"0x{mines_root:x}",
            "mines_root_kind": mines_root_kind,
            "club_root_address": f"0x{club_root:x}",
            "club_root_kind": club_root_kind,
            "mines_root_cache_hit": mines_cache_hit,
            "club_root_cache_hit": club_cache_hit,
        },
    }


_TRANSIENT_DONGTIAN_RUNTIME_MESSAGES = (
    "Runtime 内存地址越界",
    "读取凡修 Runtime 内存失败",
    "读取凡修 Runtime 内存不完整",
    "Lua table node 数量越界",
    "Lua table array 数量越界",
    "地点配置 ID 列表计数不一致",
    "地点动态字典与当前可见配置 ID 集合不一致",
    "Runtime 队伍事实不完整",
    "的 12 席浅层事实不完整",
)


def _is_transient_dongtian_runtime_error(exc: BaseException) -> bool:
    """Whether a full fresh-root read can repair this decode failure."""

    return isinstance(exc, FanxiuRuntimeMemoryError) and any(
        marker in str(exc) for marker in _TRANSIENT_DONGTIAN_RUNTIME_MESSAGES
    )


def _read_dongtian_with_generation_retry(read_once):
    """Retry one volatile Lua generation, never crossing process identity."""

    first_identity: tuple[int, int] | None = None
    first_error: str | None = None
    for attempt in range(2):
        memory = MumuProcessMemory.discover_cached()
        identity = (int(memory.pid), int(memory.process_start_ticks))
        if first_identity is None:
            first_identity = identity
        elif identity != first_identity:
            raise FanxiuRuntimeMemoryError(
                "洞天 Runtime 复读期间凡修进程身份已变化，拒绝拼接跨进程快照"
            )
        try:
            result = read_once(memory, attempt > 0)
            return result, attempt + 1, first_error
        except FanxiuRuntimeMemoryError as exc:
            if attempt or not _is_transient_dongtian_runtime_error(exc):
                setattr(exc, "dongtian_decode_attempt_count", attempt + 1)
                if first_error is not None:
                    setattr(exc, "dongtian_retried_generation_error", first_error)
                raise
            first_error = str(exc)
    raise AssertionError("unreachable")


def read_dongtian_snapshot(*, allow_legacy_scan: bool = False) -> dict[str, Any]:
    """Read the current Dongtian client model without packets or GUI OCR.

    Keep per-stage timings in every result.  This path is used inside a short
    21:30-22:00 activity window, so a correct snapshot that arrives minutes
    late is still an operational failure.  In particular, do not conflate:

    * global-root lookup (cold-path performance);
    * stable ``Model.Data`` validation (schema correctness); and
    * snapshot decoding (business completeness).

    The 2026-08-07 incident did exactly that: the live global
    ``XianLvMinesMgr`` was present, but a version-sensitive method-set check
    rejected it.  A later structural read decoded all 39 mines, but the cold
    global-table lookup took 108 seconds.  See
    ``docs/domains/fanxiu/jobs/凡修洞天行动力作业.md`` before changing this resolver.
    """

    started_at = time.perf_counter()
    stage_started_at = started_at
    phase = "process_discovery"
    timings: dict[str, float] = {}

    def finish_stage(name: str) -> None:
        nonlocal stage_started_at
        now = time.perf_counter()
        timings[name] = now - stage_started_at
        stage_started_at = now

    memory: MumuProcessMemory | None = None

    def read_once(attempt_memory: MumuProcessMemory, force_refresh: bool):
        nonlocal memory, phase, stage_started_at, timings
        memory = attempt_memory
        phase = "process_discovery"
        timings = {}
        stage_started_at = time.perf_counter()
        finish_stage("process_discovery")
        phase = "lua_state"
        state_address = int(_lua_addresses(memory)["state"], 16)
        finish_stage("lua_state")
        phase = "mines_root"
        mines_root, mines_cache_hit, mines_root_kind = _resolve_mines_root(
            memory,
            state_address=state_address,
            allow_legacy_scan=allow_legacy_scan,
            force_refresh=force_refresh,
        )
        finish_stage("mines_root")
        phase = "club_root"
        club_root, club_cache_hit, club_root_kind = _resolve_club_root(
            memory,
            state_address=state_address,
            allow_legacy_scan=allow_legacy_scan,
            force_refresh=force_refresh,
        )
        finish_stage("club_root")
        phase = "role_root"
        role_root, role_cache_hit, role_root_kind = _resolve_role_root(
            memory,
            state_address=state_address,
            allow_legacy_scan=allow_legacy_scan,
            force_refresh=force_refresh,
        )
        finish_stage("role_root")
        phase = "decode"
        result = _snapshot(
            memory,
            mines_root,
            club_root,
            role_root,
            mines_cache_hit=mines_cache_hit,
            club_cache_hit=club_cache_hit,
            role_cache_hit=role_cache_hit,
            mines_root_kind=mines_root_kind,
            club_root_kind=club_root_kind,
            role_root_kind=role_root_kind,
        )
        finish_stage("decode")
        return result

    try:
        result, attempt_count, retried_error = _read_dongtian_with_generation_retry(
            read_once
        )
        result["elapsed_seconds"] = time.perf_counter() - started_at
        evidence = result.setdefault("evidence", {})
        evidence["phase_timings_seconds"] = timings
        evidence["decode_attempt_count"] = attempt_count
        if retried_error is not None:
            evidence["retried_generation_error"] = retried_error
        return result
    except Exception as exc:
        timings[f"{phase}_failed"] = time.perf_counter() - stage_started_at
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
            "reason": f"{reason}；失败阶段={phase}；阶段耗时={timings}",
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
                "failed_phase": phase,
                "phase_timings_seconds": timings,
                "decode_attempt_count": getattr(
                    exc, "dongtian_decode_attempt_count", 1
                ),
                "retried_generation_error": getattr(
                    exc, "dongtian_retried_generation_error", None
                ),
            },
        }


def read_dongtian_action_power_snapshot(
    *,
    allow_legacy_scan: bool = False,
) -> dict[str, Any]:
    """Read current action power without decoding the map, seats or teams."""

    started_at = time.perf_counter()
    stage_started_at = started_at
    phase = "process_discovery"
    timings: dict[str, float] = {}

    def finish_stage(name: str) -> None:
        nonlocal stage_started_at
        now = time.perf_counter()
        timings[name] = now - stage_started_at
        stage_started_at = now

    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        finish_stage("process_discovery")
        phase = "lua_state"
        state_address = int(_lua_addresses(memory)["state"], 16)
        finish_stage("lua_state")
        phase = "mines_root"
        mines_root, mines_cache_hit, mines_root_kind = _resolve_mines_root(
            memory,
            state_address=state_address,
            allow_legacy_scan=allow_legacy_scan,
        )
        finish_stage("mines_root")
        phase = "decode"
        result = _action_power_snapshot(
            memory,
            mines_root,
            mines_cache_hit=mines_cache_hit,
            mines_root_kind=mines_root_kind,
        )
        finish_stage("decode")
        result["elapsed_seconds"] = time.perf_counter() - started_at
        result.setdefault("evidence", {})["phase_timings_seconds"] = timings
        return result
    except Exception as exc:
        timings[f"{phase}_failed"] = time.perf_counter() - stage_started_at
        reason = str(exc) if isinstance(exc, FanxiuRuntimeMemoryError) else f"{type(exc).__name__}: {exc}"
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "protocol": "XianLvMinesMgr.Model.Data.action_power.v1",
            "reason": f"{reason}；失败阶段={phase}；阶段耗时={timings}",
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
                "failed_phase": phase,
                "phase_timings_seconds": timings,
            },
        }


def read_dongtian_clear_plan_snapshot(
    *,
    allow_legacy_scan: bool = False,
) -> dict[str, Any]:
    """Read and validate the enemy-place plan once for one clear job."""

    started_at = time.perf_counter()
    stage_started_at = started_at
    phase = "process_discovery"
    timings: dict[str, float] = {}

    def finish_stage(name: str) -> None:
        nonlocal stage_started_at
        now = time.perf_counter()
        timings[name] = now - stage_started_at
        stage_started_at = now

    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        finish_stage("process_discovery")
        phase = "lua_state"
        state_address = int(_lua_addresses(memory)["state"], 16)
        finish_stage("lua_state")
        phase = "mines_root"
        mines_root, mines_cache_hit, mines_root_kind = _resolve_mines_root(
            memory,
            state_address=state_address,
            allow_legacy_scan=allow_legacy_scan,
        )
        finish_stage("mines_root")
        phase = "club_root"
        club_root, club_cache_hit, club_root_kind = _resolve_club_root(
            memory,
            state_address=state_address,
            allow_legacy_scan=allow_legacy_scan,
        )
        finish_stage("club_root")
        phase = "decode"
        result = _clear_plan_snapshot(
            memory,
            mines_root,
            club_root,
            mines_cache_hit=mines_cache_hit,
            club_cache_hit=club_cache_hit,
            mines_root_kind=mines_root_kind,
            club_root_kind=club_root_kind,
        )
        finish_stage("decode")
        result["elapsed_seconds"] = time.perf_counter() - started_at
        result.setdefault("evidence", {})["phase_timings_seconds"] = timings
        return result
    except Exception as exc:
        timings[f"{phase}_failed"] = time.perf_counter() - stage_started_at
        reason = str(exc) if isinstance(exc, FanxiuRuntimeMemoryError) else f"{type(exc).__name__}: {exc}"
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "protocol": "XianLvMinesMgr.Model.Data.clear_plan.v1",
            "reason": f"{reason}；失败阶段={phase}；阶段耗时={timings}",
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
                "failed_phase": phase,
                "phase_timings_seconds": timings,
            },
        }


def read_dongtian_seating_probe(
    *,
    excluded_mine_ids: frozenset[int] | set[int] | None = None,
    allow_legacy_scan: bool = False,
) -> dict[str, Any]:
    """Read the next useful Dongtian mine with an early-stop Runtime probe.

    The probe never sends a game command and never OCRs the UI.  It returns the
    first top-to-bottom friendly mine with a shallow candidate.  Non-friendly
    locations are excluded by policy; callers can exclude a friendly mine
    after proving that its twelve seats contain no safe target.
    """

    started_at = time.perf_counter()
    stage_started_at = started_at
    phase = "process_discovery"
    timings: dict[str, float] = {}
    excluded = frozenset(int(item) for item in (excluded_mine_ids or set()))

    def finish_stage(name: str) -> None:
        nonlocal stage_started_at
        now = time.perf_counter()
        timings[name] = now - stage_started_at
        stage_started_at = now

    memory: MumuProcessMemory | None = None

    def read_once(attempt_memory: MumuProcessMemory, force_refresh: bool):
        nonlocal memory, phase, stage_started_at, timings
        memory = attempt_memory
        phase = "process_discovery"
        timings = {}
        stage_started_at = time.perf_counter()
        finish_stage("process_discovery")
        phase = "lua_state"
        state_address = int(_lua_addresses(memory)["state"], 16)
        finish_stage("lua_state")
        phase = "mines_root"
        mines_root, mines_cache_hit, mines_root_kind = _resolve_mines_root(
            memory,
            state_address=state_address,
            allow_legacy_scan=allow_legacy_scan,
            force_refresh=force_refresh,
        )
        finish_stage("mines_root")
        phase = "club_root"
        club_root, club_cache_hit, club_root_kind = _resolve_club_root(
            memory,
            state_address=state_address,
            allow_legacy_scan=allow_legacy_scan,
            force_refresh=force_refresh,
        )
        finish_stage("club_root")
        phase = "role_root"
        role_root, role_cache_hit, role_root_kind = _resolve_role_root(
            memory,
            state_address=state_address,
            allow_legacy_scan=allow_legacy_scan,
            force_refresh=force_refresh,
        )
        finish_stage("role_root")
        phase = "decode_probe"
        result = _seating_probe_snapshot(
            memory,
            mines_root,
            club_root,
            role_root,
            mines_cache_hit=mines_cache_hit,
            club_cache_hit=club_cache_hit,
            role_cache_hit=role_cache_hit,
            mines_root_kind=mines_root_kind,
            club_root_kind=club_root_kind,
            role_root_kind=role_root_kind,
            excluded_mine_ids=excluded,
        )
        finish_stage("decode_probe")
        return result

    try:
        result, attempt_count, retried_error = _read_dongtian_with_generation_retry(
            read_once
        )
        result["elapsed_seconds"] = time.perf_counter() - started_at
        evidence = result.setdefault("evidence", {})
        evidence["phase_timings_seconds"] = timings
        evidence["decode_attempt_count"] = attempt_count
        if retried_error is not None:
            evidence["retried_generation_error"] = retried_error
        return result
    except Exception as exc:
        timings[f"{phase}_failed"] = time.perf_counter() - stage_started_at
        reason = str(exc) if isinstance(exc, FanxiuRuntimeMemoryError) else f"{type(exc).__name__}: {exc}"
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "status": "incomplete",
            "source": "runtime_memory",
            "reason": f"{reason}；失败阶段={phase}；阶段耗时={timings}",
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
                "failed_phase": phase,
                "phase_timings_seconds": timings,
                "decode_attempt_count": getattr(
                    exc, "dongtian_decode_attempt_count", 1
                ),
                "retried_generation_error": getattr(
                    exc, "dongtian_retried_generation_error", None
                ),
            },
        }


def read_dongtian_cached_seat_detail(
    *,
    mine_id: int,
    quality: int,
    seat_id: int,
    allow_legacy_scan: bool = False,
) -> dict[str, Any]:
    """Read one GUI-populated defender detail without sending a game command.

    This accessor is intentionally cache-only.  The caller must first record
    absence/fingerprint, use a normal GUI click to make the game request the
    detail, and then classify freshness.  Merely finding an old compatible
    cache entry never authorizes combat.
    """

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        state_address = int(_lua_addresses(memory)["state"], 16)
        root, cache_hit, root_kind = _resolve_mines_root(
            memory,
            state_address=state_address,
            allow_legacy_scan=allow_legacy_scan,
        )
        result = _cached_seat_detail_snapshot(
            memory,
            root,
            mines_root_kind=root_kind,
            mine_id=int(mine_id),
            quality=int(quality),
            seat_id=int(seat_id),
        )
        result["elapsed_seconds"] = time.perf_counter() - started_at
        result["evidence"]["mines_root_cache_hit"] = cache_hit
        result["evidence"]["mines_root_kind"] = root_kind
        return result
    except Exception as exc:
        reason = str(exc) if isinstance(exc, FanxiuRuntimeMemoryError) else f"{type(exc).__name__}: {exc}"
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory_cache",
            "protocol": "dongtian.seat-detail.cache.v1",
            "mine_id": int(mine_id),
            "quality": int(quality),
            "seat_id": int(seat_id),
            "cache_found": False,
            "detail": None,
            "reason": reason,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
            },
        }


def read_dongtian_cached_final_guard_team_detail(
    *,
    mine_id: int,
    quality: int,
    seat_id: int,
    allow_legacy_scan: bool = False,
) -> dict[str, Any]:
    """Read the final SiteInfoView guard-team cache without sending commands."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        state_address = int(_lua_addresses(memory)["state"], 16)
        root, cache_hit, root_kind = _resolve_mines_root(
            memory,
            state_address=state_address,
            allow_legacy_scan=allow_legacy_scan,
        )
        result = _cached_guard_team_detail_snapshot(
            memory,
            root,
            mines_root_kind=root_kind,
            mine_id=int(mine_id),
            quality=int(quality),
            seat_id=int(seat_id),
        )
        result["elapsed_seconds"] = time.perf_counter() - started_at
        result["evidence"]["mines_root_cache_hit"] = cache_hit
        result["evidence"]["mines_root_kind"] = root_kind
        return result
    except Exception as exc:
        reason = str(exc) if isinstance(exc, FanxiuRuntimeMemoryError) else f"{type(exc).__name__}: {exc}"
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory_cache",
            "protocol": "dongtian.seat-detail.final-guard-team-cache.v1",
            "detail_layer": "site_info_guard_team",
            "mine_id": int(mine_id),
            "quality": int(quality),
            "seat_id": int(seat_id),
            "cache_found": False,
            "detail": None,
            "reason": reason,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
            },
        }
