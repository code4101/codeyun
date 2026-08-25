from __future__ import annotations

from datetime import datetime
import json
import math
import re
import time
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    read_runtime_snapshot_with_rebind,
    resolve_lua_global_manager_root,
    resolve_manager_root,
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.runtime import mumu_control
from backend.core.temp_paths import codeyun_temp_root


LANDCONTEND_SKIP_SCORE = 1_000
LANDCONTEND_TRIPLE_SCORE = 15_000
LANDCONTEND_TRIPLE_COUNT = 3

_LANDCONTEND_MARKER = b"LuaLandcontendMgr"
_LANDCONTEND_METHODS = frozenset({"LuaLandcontendMgr", "Inst_get"})
_LANDCONTEND_MANAGER_CACHE_KEY = "landcontend-manager"
_IMMUNITY_TEXT = "免战"
_IMMUNITY_PATTERN = re.compile(
    r"免战[^0-9]{0,12}([0-9]{1,2})[^0-9]([0-9]{1,2})[^0-9]([0-9]{1,2})"
)


def _resolve_landcontend_root(
    memory: MumuProcessMemory,
    *,
    validate: Any,
    force_refresh: bool = False,
) -> tuple[int, bool, str]:
    """Resolve the one LandcontendMgr root for every battlefield projection."""

    try:
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key=_LANDCONTEND_MANAGER_CACHE_KEY,
            state_address=int(_lua_addresses(memory)["state"], 16),
            global_name="LandcontendMgr",
            required_methods=_LANDCONTEND_METHODS,
            # Resolve manager identity only.  A temporarily incomplete page
            # projection is a precise natural-loading result and must not
            # discard the correct global root or trigger a heap scan.
            validate=lambda _reader, _address: None,
            force_refresh=force_refresh,
        )
        return root, cache_hit, "lua_global"
    except (FanxiuRuntimeMemoryError, AttributeError, KeyError, TypeError, ValueError):
        root, cache_hit = resolve_manager_root(
            memory,
            manager_key=_LANDCONTEND_MANAGER_CACHE_KEY,
            marker=_LANDCONTEND_MARKER,
            required_methods=_LANDCONTEND_METHODS,
            validate=validate,
            force_refresh=force_refresh,
        )
        return root, cache_hit, "constructor_marker"


def _required_fields(
    reader: LuaJitReader,
    value: Any,
    names: tuple[str, ...],
    context: str,
) -> dict[Any, Any]:
    fields = reader.fields(value)
    missing = [name for name in names if name not in fields]
    if missing:
        raise FanxiuRuntimeMemoryError(
            f"{context} 尚未初始化，缺少字段：{','.join(missing)}"
        )
    return fields


def _required_bool(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise FanxiuRuntimeMemoryError(f"{context} 不是有效布尔值")
    return value


def _landcontend_attack_options(
    reader: LuaJitReader,
    root_address: int,
) -> dict[str, Any]:
    manager = manager_index_fields(reader, root_address, _LANDCONTEND_METHODS)
    instance = _required_fields(reader, manager.get("inst"), ("Model",), "仙盟争霸管理器")
    model = _required_fields(
        reader,
        instance["Model"],
        ("LandcontendData",),
        "仙盟争霸模型",
    )
    data = _required_fields(
        reader,
        model["LandcontendData"],
        (
            "V_PlayerRankData",
            "_IsPassFight",
            "_MultiFightState",
            "_CurScheduleStage",
        ),
        "仙盟争霸攻击配置",
    )
    rank_data = _required_fields(
        reader,
        data["V_PlayerRankData"],
        ("selfRank",),
        "仙盟争霸个人榜",
    )
    self_rank = _required_fields(
        reader,
        rank_data["selfRank"],
        ("score",),
        "仙盟争霸个人积分",
    )

    score = as_int(self_rank.get("score"))
    stage = as_int(data.get("_CurScheduleStage"))
    multi_count = as_int(data.get("_MultiFightCount"))
    if score is None:
        raise FanxiuRuntimeMemoryError("仙盟争霸个人积分不是有效整数")
    if stage is None:
        raise FanxiuRuntimeMemoryError("仙盟争霸当前赛程不是有效整数")
    # GetMultiFightCount lazily materializes _MultiFightCount from static
    # config.  Its absence before the first multi-action is not an incomplete
    # attack page; when present, still verify the expected live value.
    if multi_count is not None and multi_count != LANDCONTEND_TRIPLE_COUNT:
        raise FanxiuRuntimeMemoryError(
            f"仙盟争霸连击次数异常：{multi_count}，预期 {LANDCONTEND_TRIPLE_COUNT}"
        )

    return {
        "score": score,
        "stage": stage,
        # #293 打开时，LandcontendCheckAttackContent.AddUI 会把这两个
        # LandcontendData 值同步写入 Toggle 和 B_*Fight；按钮回调也同步回写。
        # 因此这里读取的是该页面的开关权威状态，不做视觉/OCR 推断。
        "skip_checked": _required_bool(
            data.get("_IsPassFight"), "仙盟争霸跳过战斗状态"
        ),
        "triple_checked": _required_bool(
            data.get("_MultiFightState"), "仙盟争霸三连状态"
        ),
        "triple_count": multi_count,
    }


def _landcontend_model_state(
    reader: LuaJitReader,
    root_address: int,
) -> tuple[dict[Any, Any], dict[Any, Any]]:
    manager = manager_index_fields(reader, root_address, _LANDCONTEND_METHODS)
    instance = _required_fields(reader, manager.get("inst"), ("Model",), "仙盟争霸管理器")
    model = _required_fields(reader, instance["Model"], ("LandcontendData",), "仙盟争霸模型")
    data = reader.fields(model["LandcontendData"])
    if not data:
        raise FanxiuRuntimeMemoryError("仙盟争霸数据尚未加载")
    return instance, data


def _landcontend_count_state(
    reader: LuaJitReader,
    root_address: int,
) -> dict[str, Any]:
    _instance, data = _landcontend_model_state(reader, root_address)
    count_dic = _required_fields(
        reader,
        data.get("countInfoDic"),
        ("LuaDic_count",),
        "仙盟争霸次数",
    )
    expected_count = as_int(count_dic.get("LuaDic_count"))
    items, _declared_count = reader.list_items(data.get("countInfoDic"))
    rows: list[dict[str, Any]] = []
    for item in items:
        fields = _required_fields(reader, item, ("type", "count"), "仙盟争霸次数条目")
        count_type = as_int(fields.get("type"))
        count = as_int(fields.get("count"))
        if count_type is None or count is None or count < 0:
            raise FanxiuRuntimeMemoryError("仙盟争霸次数条目不完整")
        rows.append(
            {
                "type": count_type,
                "count": count,
                "recover_time": reader.long(fields.get("recoverTime")),
            }
        )
    if expected_count is None or expected_count != len(rows):
        raise FanxiuRuntimeMemoryError("仙盟争霸次数列表尚未完整加载")
    rows.sort(key=lambda row: int(row["type"]))
    by_type = {int(row["type"]): row for row in rows}
    if 1 not in by_type or 2 not in by_type:
        raise FanxiuRuntimeMemoryError("仙盟争霸缺少攻击/分身次数")
    # Live #293 before/after one triple attack cross-check: type=1 changed with
    # the rendered attack stamina (8 -> 6 after one intervening recovery),
    # while type=2 stayed at 8. Keep raw rows as build evidence.
    return {
        "attack_count": int(by_type[1]["count"]),
        "clone_count": int(by_type[2]["count"]),
        "counts": rows,
    }


def read_landcontend_count_snapshot() -> dict[str, Any]:
    """Read authoritative attack/clone counts from LandcontendData."""

    started_at = time.perf_counter()

    def read_once(memory: MumuProcessMemory, force_rebind: bool) -> dict[str, Any]:
        root, cache_hit, resolver = _resolve_landcontend_root(
            memory,
            validate=lambda reader, address: _landcontend_count_state(reader, address),
            force_refresh=force_rebind,
        )
        state = _landcontend_count_state(LuaJitReader(memory), root)
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **state,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "root_address": f"0x{root:x}",
                "root_cache_hit": cache_hit,
                "manager_resolver": resolver,
            },
        }

    result = read_runtime_snapshot_with_rebind(read_once)
    result["elapsed_seconds"] = time.perf_counter() - started_at
    return result


def _landcontend_command_target(
    reader: LuaJitReader,
    root_address: int,
    *,
    self_club_id: int | None = None,
) -> dict[str, Any]:
    instance, data = _landcontend_model_state(reader, root_address)
    stage = as_int(data.get("_CurScheduleStage"))
    has_command = data.get("_HasShowCommand")
    camps, camp_count = reader.list_items(data.get("v_campList"))
    if stage is None or camp_count is None:
        raise FanxiuRuntimeMemoryError("仙盟争霸战场列表尚未完整加载")

    rows: list[dict[str, Any]] = []
    command_rows: list[dict[str, Any]] = []
    raw_camps: list[tuple[dict[str, Any], dict[Any, Any]]] = []
    for index, camp in enumerate(camps, start=1):
        fields = reader.fields(camp)
        camp_id = reader.long(fields.get("id"))
        name = fields.get("name")
        if camp_id is None or not isinstance(name, str) or not name.strip():
            raise FanxiuRuntimeMemoryError("仙盟争霸战场条目缺少 id/name")
        row = {
            "id": camp_id,
            "name": name,
            "slot": index,
            "server_id": as_int(fields.get("serverId")),
            "pillar_cur_hp": (
                float(fields["pillarCurHp"])
                if isinstance(fields.get("pillarCurHp"), (int, float))
                and not isinstance(fields.get("pillarCurHp"), bool)
                else None
            ),
            "pillar_max_hp": (
                float(fields["pillarMaxHp"])
                if isinstance(fields.get("pillarMaxHp"), (int, float))
                and not isinstance(fields.get("pillarMaxHp"), bool)
                else None
            ),
            "protect_end_time": reader.long(fields.get("protectEndTime")),
            "has_super_mirror_hp_protect": fields.get("hasSuperMirrorHpProtect") is True,
            "has_xiaoyan_mirror": fields.get("hasXiaoyanMirrir") is True,
            "pivot_name": (
                fields.get("pivotName")
                if isinstance(fields.get("pivotName"), str)
                else ""
            ),
            # Protocol uint64 wrapper: the current battlefield-level ally.
            # This is more specific than the shared server relation policy.
            "ally_camp_id": reader.long(fields.get("allyClub")),
            "command_state": as_int(fields.get("commandState")),
            "command_desc": fields.get("desc") if isinstance(fields.get("desc"), str) else "",
        }
        rows.append(row)
        raw_camps.append((row, fields))
        if row["command_state"] not in (None, 0):
            command_rows.append(row)

    # Production truth follows LandcontendMgr.GetCampCommandState: commands
    # are stored on our own camp VO as target-id -> description dictionaries.
    # They are not properties of the target row or the scene ranking list.
    if self_club_id is not None:
        own_fields = next(
            (
                fields
                for row, fields in raw_camps
                if int(row["id"]) == int(self_club_id)
            ),
            None,
        )
        if own_fields is None:
            raise FanxiuRuntimeMemoryError("仙盟争霸阵营列表缺少我方仙盟")
        command_rows = []
        attack_commands = reader.dictionary_fields(own_fields.get("attackClubs"))
        by_id = {int(row["id"]): row for row in rows}
        for target_ref, description in attack_commands.items():
            target_id = reader.long(target_ref)
            if target_id not in by_id:
                raise FanxiuRuntimeMemoryError("仙盟争霸进攻指挥目标不在当前阵营列表")
            row = dict(by_id[int(target_id)])
            row["command_state"] = 1
            row["command_desc"] = (
                description if isinstance(description, str) else ""
            )
            command_rows.append(row)

    # Some builds keep the command VO list separate from the live camp list.
    # Join it back by the protocol id, never by rank or list order.
    scene_rows, _scene_count = reader.list_items(data.get("_SceneRankList"))
    by_id = {int(row["id"]): row for row in rows}
    for item in scene_rows:
        fields = reader.fields(item)
        command_state = as_int(fields.get("commandState"))
        item_id = reader.long(fields.get("id"))
        if command_state in (None, 0) or item_id not in by_id:
            continue
        row = dict(by_id[int(item_id)])
        row["command_state"] = command_state
        row["command_desc"] = fields.get("desc") if isinstance(fields.get("desc"), str) else ""
        command_rows.append(row)

    unique_commands = {int(row["id"]): row for row in command_rows}
    target = next(iter(unique_commands.values())) if len(unique_commands) == 1 else None
    focus_id = reader.long(instance.get("_CurFocusCampId"))
    return {
        "stage": stage,
        # Some qualifying-stage builds render the server command from
        # _SceneRankList without materializing _HasShowCommand.  Keep it as
        # optional diagnostic evidence; it is not target identity.
        "has_command": has_command if isinstance(has_command, bool) else None,
        "camp_count": camp_count,
        "camps": rows,
        "command_count": len(unique_commands),
        "target": target,
        "focus_camp_id": focus_id,
    }


def read_landcontend_command_target_snapshot() -> dict[str, Any]:
    """Read the unique server-commanded battlefield target without OCR."""

    started_at = time.perf_counter()

    def read_once(memory: MumuProcessMemory, force_rebind: bool) -> dict[str, Any]:
        state_address = int(_lua_addresses(memory)["state"], 16)
        root, cache_hit, resolver = _resolve_landcontend_root(
            memory,
            validate=lambda reader, address: _landcontend_command_target(reader, address),
            force_refresh=force_rebind,
        )
        club_root, club_cache_hit, _club_environment = resolve_lua_global_manager_root(
            memory,
            manager_key="club-manager-for-landcontend",
            state_address=state_address,
            global_name="ClubMgr",
            required_methods=frozenset(),
            validate=lambda _reader, _address: None,
            force_refresh=force_rebind,
        )
        reader = LuaJitReader(memory)
        club_manager = manager_index_fields(reader, club_root, frozenset())
        club_instance = reader.fields(club_manager.get("inst"))
        club_model = reader.fields(club_instance.get("Model"))
        club_data = reader.fields(club_model.get("data"))
        self_club = reader.fields(club_data.get("_SelfClubVo"))
        self_club_id = reader.long(self_club.get("id"))
        if self_club_id is None:
            raise FanxiuRuntimeMemoryError("仙盟 Runtime 缺少我方仙盟 id")
        state = _landcontend_command_target(
            reader,
            root,
            self_club_id=self_club_id,
        )
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **state,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "root_address": f"0x{root:x}",
                "root_cache_hit": cache_hit,
                "manager_resolver": resolver,
                "club_root_address": f"0x{club_root:x}",
                "club_root_cache_hit": club_cache_hit,
                "self_club_id": self_club_id,
            },
        }

    result = read_runtime_snapshot_with_rebind(read_once)
    result["elapsed_seconds"] = time.perf_counter() - started_at
    return result


def _immunity_countdowns(payload: bytes) -> dict[int, int]:
    """Return immutable Unity countdown strings as offset -> seconds."""

    needle = _IMMUNITY_TEXT.encode("utf-8")
    rows: dict[int, int] = {}
    start = 0
    while True:
        offset = payload.find(needle, start)
        if offset < 0:
            break
        start = offset + 1
        text = payload[offset : min(len(payload), offset + 120)].decode(
            "utf-8", "replace"
        )
        match = _IMMUNITY_PATTERN.search(text)
        if match is None:
            continue
        hours, minutes, seconds = (int(value) for value in match.groups())
        if minutes >= 60 or seconds >= 60:
            continue
        rows[offset] = hours * 3600 + minutes * 60 + seconds
    return rows


def _active_immunity_seconds(
    before: dict[int, int],
    after: dict[int, int],
    *,
    elapsed_seconds: float,
) -> int | None:
    """Pick a newly rendered, actively decreasing countdown.

    Unity strings are immutable, so old countdown text remains in the heap.
    A live timer creates a new lower value on the second sample.  Choosing the
    smallest active value is conservative: an early retry rechecks the same
    gate, while a stale larger timer must not delay the commanded target.
    """

    previous = set(before.values())
    new_values = [value for offset, value in after.items() if offset not in before]
    max_delta = max(2, int(math.ceil(elapsed_seconds)) + 5)
    active = [
        value
        for value in new_values
        if any(1 <= old - value <= max_delta for old in previous)
    ]
    return min(active) if active else None


def _immunity_region_cache_path(memory: MumuProcessMemory):
    return codeyun_temp_root("fanxiu-runtime-memory") / "landcontend-immunity-ui-region.json"


def _read_cached_immunity_region(memory: MumuProcessMemory) -> int | None:
    path = _immunity_region_cache_path(memory)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if (
        int(payload.get("pid") or 0) != memory.pid
        or int(payload.get("process_start_ticks") or 0) != memory.process_start_ticks
    ):
        return None
    start = int(payload.get("region_start") or 0)
    return start or None


def _write_immunity_region_cache(memory: MumuProcessMemory, region_start: int) -> None:
    path = _immunity_region_cache_path(memory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "region_start": int(region_start),
                "updated_at": time.time(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _sample_immunity_region(
    memory: MumuProcessMemory,
    region_start: int,
    *,
    sample_seconds: float,
) -> tuple[int | None, dict[str, Any]]:
    region = next((item for item in memory.regions if item.start == int(region_start)), None)
    if region is None:
        return None, {"region_start": int(region_start), "reason": "region_missing"}
    before = _immunity_countdowns(memory.read_region(region))
    if not before:
        return None, {"region_start": region.start, "reason": "countdown_missing"}
    started = time.monotonic()
    time.sleep(max(0.8, float(sample_seconds)))
    refreshed = MumuProcessMemory.discover_cached()
    refreshed_region = next(
        (item for item in refreshed.regions if item.start == region.start),
        None,
    )
    if refreshed_region is None:
        return None, {"region_start": region.start, "reason": "region_relocated"}
    after = _immunity_countdowns(refreshed.read_region(refreshed_region))
    elapsed = time.monotonic() - started
    seconds = _active_immunity_seconds(before, after, elapsed_seconds=elapsed)
    return seconds, {
        "region_start": region.start,
        "region_size": region.size,
        "before_count": len(before),
        "after_count": len(after),
        "sample_elapsed_seconds": elapsed,
    }


def _discover_immunity_regions(
    memory: MumuProcessMemory,
    *,
    anchor: int,
    max_scan_bytes: int,
):
    def distance(region: Any) -> int:
        if region.start <= anchor < region.end:
            return 0
        return min(abs(region.start - anchor), abs(region.end - anchor))

    regions = [
        region
        for region in memory.regions
        if region.permissions.startswith("rw")
        and not region.path.startswith("/")
        and 1 * 1024 * 1024 <= region.size <= 64 * 1024 * 1024
    ]
    regions.sort(key=lambda region: (distance(region), region.size, region.start))
    scanned = 0
    for index in range(0, len(regions), 5):
        batch = regions[index : index + 5]
        if scanned + sum(region.size for region in batch) > max_scan_bytes:
            break
        command = "; ".join(
            f"dd if=/proc/{memory.pid}/mem bs=4096 "
            f"skip={region.start // 4096} count={region.size // 4096} "
            f"2>/dev/null | grep -aob '{_IMMUNITY_TEXT}' | sed 's|^|{region.start} |'"
            for region in batch
        )
        output, _meta = mumu_control._run_mumu_adb_shell_text(
            command,
            timeout_s=90,
            preferred_serials=[memory.adb_serial],
        )
        scanned += sum(region.size for region in batch)
        hit_starts: set[int] = set()
        for line in str(output or "").splitlines():
            try:
                start_text, _offset_text = line.split(None, 1)
                hit_starts.add(int(start_text))
            except (TypeError, ValueError):
                continue
        for region in batch:
            if region.start in hit_starts:
                yield region, scanned


def _landcontend_immunity_state(
    reader: LuaJitReader,
    root_address: int,
    *,
    now_ms: int,
) -> dict[str, Any]:
    instance, data = _landcontend_model_state(reader, root_address)
    focus_id = reader.long(instance.get("_CurFocusCampId"))
    camps, _count = reader.list_items(data.get("v_campList"))
    focused_fields = next(
        (
            fields
            for fields in (reader.fields(item) for item in camps)
            if reader.long(fields.get("id")) == focus_id
        ),
        None,
    )
    if focus_id is None or focused_fields is None:
        raise FanxiuRuntimeMemoryError("仙盟争霸当前免战目标尚未加载")
    protect_end_time = reader.long(focused_fields.get("protectEndTime"))
    if protect_end_time is None or protect_end_time <= 0:
        raise FanxiuRuntimeMemoryError("仙盟争霸当前目标缺少免战结束时间")
    cooldown_seconds = max(0, math.ceil((protect_end_time - int(now_ms)) / 1000))
    return {
        "target_id": focus_id,
        "target_name": (
            focused_fields.get("name")
            if isinstance(focused_fields.get("name"), str)
            else ""
        ),
        "protect_end_time": protect_end_time,
        "cooldown_seconds": cooldown_seconds,
        "ready": cooldown_seconds <= 0,
    }


def read_landcontend_immunity_snapshot(
    *,
    sample_seconds: float = 1.2,
    max_scan_bytes: int = 512 * 1024 * 1024,
) -> dict[str, Any]:
    """Read the focused camp protection deadline from the loaded model.

    ``sample_seconds`` and ``max_scan_bytes`` remain accepted for API
    compatibility. The old implementation scanned hundreds of megabytes of UI
    strings; the model already exposes the authoritative millisecond deadline.
    """

    del sample_seconds, max_scan_bytes
    started_at = time.perf_counter()

    def read_once(memory: MumuProcessMemory, force_rebind: bool) -> dict[str, Any]:
        root, cache_hit, resolver = _resolve_landcontend_root(
            memory,
            validate=lambda reader, address: _landcontend_command_target(reader, address),
            force_refresh=force_rebind,
        )
        reader = LuaJitReader(memory)
        now_ms = int(time.time() * 1000)
        state = _landcontend_immunity_state(reader, root, now_ms=now_ms)
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **state,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "root_address": f"0x{root:x}",
                "root_cache_hit": cache_hit,
                "manager_resolver": resolver,
            },
        }

    result = read_runtime_snapshot_with_rebind(read_once)
    result["elapsed_seconds"] = time.perf_counter() - started_at
    return result


def read_landcontend_attack_options_snapshot() -> dict[str, Any]:
    """Read the already-loaded #293 attack options without executing game Lua."""

    started_at = time.perf_counter()

    def read_once(memory: MumuProcessMemory, force_rebind: bool) -> dict[str, Any]:
        root, cache_hit, resolver = _resolve_landcontend_root(
            memory,
            validate=lambda reader, address: _landcontend_attack_options(reader, address),
            force_refresh=force_rebind,
        )
        state = _landcontend_attack_options(LuaJitReader(memory), root)
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory",
            "probe_type": "legacy-memory-scan",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **state,
            "skip_score_threshold": LANDCONTEND_SKIP_SCORE,
            "triple_score_threshold": LANDCONTEND_TRIPLE_SCORE,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "root_address": f"0x{root:x}",
                "root_cache_hit": cache_hit,
                "manager_resolver": resolver,
            },
        }

    result = read_runtime_snapshot_with_rebind(read_once)
    result["elapsed_seconds"] = time.perf_counter() - started_at
    return result
