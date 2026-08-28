from __future__ import annotations

"""Persist the current Lingzhuang strengthening resources and equipment levels."""

import time
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from backend.core.fanxiu.activity.exchange_event import is_exchange_activity_active
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_manager_root,
)
from backend.models import FanxiuExchangeActivity, FanxiuPacketBusinessRecord


LINGZHUANG_HUADAO_ACTIVITY_TYPE = "lingzhuang-huadao"
STRENGTHENING_SNAPSHOT_DOMAIN = "lingzhuang_strengthening_snapshot"
STRENGTHENING_SNAPSHOT_KEY = "current"

_BACKPACK_MARKER = b"LuaBackpackMgr"
_BACKPACK_METHODS = frozenset({"LuaBackpackMgr", "Inst_get"})
_EQUIPMENT_MARKER = b"GetTopSliderBgValue"
_EQUIPMENT_METHODS = frozenset({"GetTopSliderBgValue", "Inst_get"})
_QUEST_MARKER = b"LuaQuestMgr"
_QUEST_METHODS = frozenset({"LuaQuestMgr", "Inst_get"})
_SCORE_TOTAL_ROUNDS = 4
_SCORE_ROUND_SUBS = {1: 0, 2: 11_500_000, 3: 23_370_500, 4: 35_610_500}
_SCORE_ROUND_TARGETS = {1: 11_500_000, 2: 11_870_500, 3: 12_240_000, 4: 12_614_900}
_EQUIPMENT_TALENT_PILL_REWARDS = {1_000: 1, 2_000: 1, 4_000: 2, 8_000: 2, 12_000: 4}
_EQUIPMENT_TASK_TARGETS = (100, 200, 400, 600, 800, 1_000, 1_400, 2_000, 3_000, 4_000, 6_000, 8_000, 10_000, 12_000)
_CHINESE_ORDINALS = (
    "一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二", "十三", "十四",
)

_PARTS: tuple[tuple[str, int, str, int, str], ...] = (
    ("灵环", 10_001_001, "灵环玄铁石", 10_001_001, "灵环玄铁石"),
    ("气铠", 10_001_002, "气铠玄铁石", 10_001_002, "气铠玄铁石"),
    ("宝冠", 10_001_003, "宝冠玄铁石", 10_001_003, "宝冠玄铁石"),
    ("羽巾", 10_001_004, "羽巾玄铁石", 10_001_004, "羽巾玄铁石"),
    ("华履", 10_001_005, "华履玄铁石", 10_001_005, "华履玄铁石"),
    ("锦带", 10_001_006, "锦带玄铁石", 10_001_006, "锦带玄铁石"),
    ("灵坠", 10_001_007, "灵坠玄铁石", 10_001_007, "灵坠玄铁石"),
    ("仙符", 10_001_008, "仙符玄铁石", 10_001_008, "仙符玄铁石"),
    ("灵镯", 10_001_009, "灵镯玄铁石", 10_001_009, "灵镯玄铁石"),
    ("宝戒", 10_001_010, "宝戒玄铁石", 10_001_010, "宝戒玄铁石"),
)
_MATERIAL_IDS = frozenset(
    material_id
    for _, initial_id, _, dongxuan_id, _ in _PARTS
    for material_id in (initial_id, dongxuan_id)
)


class LingzhuangStrengtheningSide(BaseModel):
    material_id: int
    material_name: str
    material_count: int | None = None
    equipment_level: int | None = None
    equipment_raw_level: int | None = None
    equipped: bool | None = None


class LingzhuangStrengtheningRow(BaseModel):
    part: str
    initial: LingzhuangStrengtheningSide
    dongxuan: LingzhuangStrengtheningSide


class LingzhuangTaskProgress(BaseModel):
    task_id: int
    order: int
    name: str
    progress: int
    target: int
    finished: bool = False
    talent_pill_count: int = 0


class LingzhuangScoreRound(BaseModel):
    round: int
    target: int


class LingzhuangStrengtheningSnapshot(BaseModel):
    activity_id: str = ""
    game_task_activity_id: int | None = None
    captured_at: str = ""
    materials_captured_at: str = ""
    equipment_captured_at: str = ""
    task_progress_captured_at: str = ""
    source_kind: str = ""
    complete: bool = False
    warnings: list[str] = Field(default_factory=list)
    rows: list[LingzhuangStrengtheningRow] = Field(default_factory=list)
    equipment_tasks: list[LingzhuangTaskProgress] = Field(default_factory=list)
    equipment_current: int | None = None
    score_round: int | None = None
    score_total_rounds: int = _SCORE_TOTAL_ROUNDS
    score_current: int | None = None
    score_rounds: list[LingzhuangScoreRound] = Field(default_factory=list)
    score_tasks: list[LingzhuangTaskProgress] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value)


def _backpack_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _BACKPACK_METHODS)
    data = _fields(
        reader,
        _fields(reader, _fields(reader, manager.get("inst")).get("Model")).get("BackpackData"),
    )
    if not _fields(reader, data.get("ItemVoDic")):
        raise FanxiuRuntimeMemoryError("背包物品索引尚未加载")
    return data


def _equipment_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _EQUIPMENT_METHODS)
    data = _fields(
        reader,
        _fields(reader, _fields(reader, manager.get("inst")).get("Model")).get("EquipmentData"),
    )
    clients, _ = reader.list_items(data.get("_EquipVoDic"))
    if len(clients) < 20:
        raise FanxiuRuntimeMemoryError("装备格子索引尚未加载")
    return data


def _lua_dictionary_items(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return _fields(reader, _fields(reader, value).get("_dt_"))


def _quest_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _QUEST_METHODS)
    quest_data = _fields(
        reader,
        _fields(reader, _fields(reader, manager.get("inst")).get("Model")).get("QuestData"),
    )
    if not _fields(reader, quest_data.get("V_AllTaskInfoDic")):
        raise FanxiuRuntimeMemoryError("Quest 任务索引尚未加载")
    return quest_data


def _quest_activity_tasks(
    reader: LuaJitReader,
    root_address: int,
    game_activity_id: int,
) -> Any:
    quest_data = _quest_data_fields(reader, root_address)
    activities = _lua_dictionary_items(reader, quest_data.get("AllTaskAcDic"))
    activity_tasks = activities.get(game_activity_id)
    _, total = reader.list_items(activity_tasks)
    # Full cross-server phases expose the equipment tasks together with the
    # repeating score tasks, while the local preliminary phase exposes only
    # the 14 equipment-consumption tiers.  Fourteen is therefore a complete
    # task group for the operation implemented by this module.
    if int(total or 0) < len(_EQUIPMENT_TASK_TARGETS):
        raise FanxiuRuntimeMemoryError(
            f"灵装化道任务 {game_activity_id} 尚未加载到 Quest Runtime"
        )
    return activity_tasks


def _task_progress_row(reader: LuaJitReader, raw_task: Any) -> dict[str, Any] | None:
    task = _fields(reader, raw_task)
    task_id = as_int(task.get("id"))
    server = _fields(reader, task.get("serverData"))
    progress_items, _ = reader.list_items(server.get("progressList"))
    if task_id is None or not progress_items:
        return None
    progress = _fields(reader, progress_items[0])
    current = as_int(progress.get("progress"))
    target = as_int(progress.get("target"))
    if current is None or target is None:
        return None
    return {
        "task_id": task_id,
        "progress": current,
        "target": target,
        "finished": bool(task.get("isFinished")) or bool(progress.get("finish")),
        "claimed": bool(task.get("isFinished")),
    }


def _reconstruct_equipment_task_rows(
    raw_equipment_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_targets = tuple(row["target"] for row in raw_equipment_rows)
    if not raw_targets:
        return []
    first_order = (
        _EQUIPMENT_TASK_TARGETS.index(raw_targets[0]) + 1
        if raw_targets[0] in _EQUIPMENT_TASK_TARGETS
        else 0
    )
    expected_suffix = _EQUIPMENT_TASK_TARGETS[first_order - 1:] if first_order else ()
    if raw_targets != expected_suffix:
        return []
    task_id_bases = {
        row["task_id"] - _EQUIPMENT_TASK_TARGETS.index(row["target"]) - 1
        for row in raw_equipment_rows
    }
    if len(task_id_bases) != 1:
        return []
    task_id_base = task_id_bases.pop()
    current_progress = max(row["progress"] for row in raw_equipment_rows)
    live_by_target = {row["target"]: row for row in raw_equipment_rows}
    reconstructed = []
    for order, target in enumerate(_EQUIPMENT_TASK_TARGETS, 1):
        live = live_by_target.get(target)
        reconstructed.append({
            "task_id": live["task_id"] if live else task_id_base + order,
            "progress": live["progress"] if live else current_progress,
            "target": target,
            "finished": live["finished"] if live else current_progress >= target,
            "order": order,
            "name": f"装备强化{_CHINESE_ORDINALS[order - 1]}",
            "talent_pill_count": _EQUIPMENT_TALENT_PILL_REWARDS.get(target, 0),
        })
    return reconstructed


def _theme_week_task_progress(
    reader: LuaJitReader,
    raw_tasks: Any,
) -> tuple[list[dict[str, Any]], int | None, list[dict[str, Any]]]:
    equipment_by_target: dict[int, dict[str, Any]] = {}
    task_items, _ = reader.list_items(raw_tasks)
    score_candidates: list[tuple[Any, dict[str, Any]]] = []
    for raw_task in task_items:
        row = _task_progress_row(reader, raw_task)
        if row is None:
            continue
        if row["target"] <= 12_000:
            equipment_by_target[row["target"]] = row
        else:
            score_candidates.append((raw_task, row))
    equipment_rows = []
    raw_equipment_rows = sorted(equipment_by_target.values(), key=lambda item: item["target"])
    for order, row in enumerate(raw_equipment_rows, 1):
        equipment_rows.append(
            {
                **{key: value for key, value in row.items() if key != "claimed"},
                "order": order,
                "name": f"装备强化{_CHINESE_ORDINALS[order - 1]}",
                "talent_pill_count": _EQUIPMENT_TALENT_PILL_REWARDS.get(row["target"], 0),
            }
        )

    # Quest Runtime removes already-completed equipment tasks after its list
    # refreshes.  The remaining rows are therefore a suffix such as
    # 6000/8000/10000/12000, not an incomplete load.  Reconstruct the claimed
    # prefix from the stable target sequence while keeping the live cumulative
    # progress and task-id base.
    reconstructed = _reconstruct_equipment_task_rows(raw_equipment_rows)
    if reconstructed:
        equipment_rows = reconstructed

    score_groups: dict[int, list[dict[str, Any]]] = {}
    for raw_task, row in score_candidates:
        task = _fields(reader, raw_task)
        task_id = as_int(task.get("id"))
        if task_id is None:
            continue
        suffix = task_id % 100
        if not 1 <= suffix <= _SCORE_TOTAL_ROUNDS * 10:
            continue
        round_number = (suffix - 1) // 10 + 1
        score_groups.setdefault(round_number, []).append(row)

    current_round = next(
        (
            round_number
            for round_number in range(1, _SCORE_TOTAL_ROUNDS + 1)
            if score_groups.get(round_number)
            and any(not item["claimed"] for item in score_groups[round_number])
        ),
        max(score_groups, default=None),
    )
    score_rows: list[dict[str, Any]] = []
    if current_round is not None:
        round_sub = _SCORE_ROUND_SUBS[current_round]
        current_items = sorted(score_groups.get(current_round, []), key=lambda item: item["task_id"] % 100)
        for order, row in enumerate(current_items, 1):
            if row["target"] <= 0:
                continue
            score_rows.append(
                {
                    "task_id": row["task_id"],
                    "order": order,
                    "name": f"装备强化{_CHINESE_ORDINALS[order - 1]}",
                    "progress": max(0, row["progress"] - round_sub),
                    "target": max(0, row["target"] - round_sub),
                    "finished": row["finished"],
                }
            )
    return equipment_rows, current_round, score_rows


def _task_progress_complete(
    *,
    raw_task_total: int,
    equipment_task_count: int,
    score_task_count: int,
) -> tuple[bool, bool]:
    """Return (complete, equipment_only_phase) for the live task group."""

    equipment_complete = equipment_task_count == len(_EQUIPMENT_TASK_TARGETS)
    equipment_only_phase = (
        equipment_complete and score_task_count == 0
    )
    return (
        equipment_complete and (equipment_only_phase or score_task_count == 10),
        equipment_only_phase,
    )


def _enrich_static_task_reference(
    snapshot: LingzhuangStrengtheningSnapshot,
) -> LingzhuangStrengtheningSnapshot:
    for task in snapshot.equipment_tasks:
        task.talent_pill_count = _EQUIPMENT_TALENT_PILL_REWARDS.get(task.target, 0)
    snapshot.equipment_current = max(
        (task.progress for task in snapshot.equipment_tasks),
        default=snapshot.equipment_current,
    )
    snapshot.score_rounds = [
        LingzhuangScoreRound(round=round_number, target=target)
        for round_number, target in _SCORE_ROUND_TARGETS.items()
    ]
    if snapshot.score_tasks:
        snapshot.score_current = max(task.progress for task in snapshot.score_tasks)
    return snapshot


def _material_counts(reader: LuaJitReader, backpack_data: dict[Any, Any]) -> dict[int, int]:
    counts = {material_id: 0 for material_id in _MATERIAL_IDS}
    item_index = _fields(reader, backpack_data.get("ItemVoDic"))
    for raw_base_id, raw_dictionary in item_index.items():
        base_id = as_int(raw_base_id)
        if base_id not in counts:
            continue
        values = _fields(reader, _fields(reader, raw_dictionary).get("_valueTable_"))
        for raw_item in values.values():
            item = _fields(reader, raw_item)
            if as_int(item.get("baseId")) == base_id:
                counts[base_id] += max(0, as_int(item.get("num")) or 0)
    return counts


def _equipment_levels(
    reader: LuaJitReader,
    equipment_data: dict[Any, Any],
) -> dict[tuple[int, int], dict[str, Any]]:
    levels: dict[tuple[int, int], dict[str, Any]] = {}
    clients, _ = reader.list_items(equipment_data.get("_EquipVoDic"))
    for raw_client in clients:
        client = _fields(reader, raw_client)
        equip_vo = _fields(reader, client.get("_CurEquipVo"))
        slot = as_int(equip_vo.get("idx"))
        if slot is None or not 1 <= slot <= 20:
            continue
        suit = 1 if slot <= 10 else 2
        part = (slot - 1) % 10 + 1
        raw_level = as_int(equip_vo.get("level")) if equip_vo else 0
        item_base_id = as_int(equip_vo.get("itemBaseId")) if equip_vo else 0
        # The server grade advances through nine small nodes per level. The
        # strengthening screen renders the completed-node quotient: e.g.
        # raw 2795 is shown as +310 with five of nine nodes completed.
        display_level = raw_level // 9 if raw_level is not None else None
        levels[(suit, part)] = {
            "equipment_level": display_level,
            "equipment_raw_level": raw_level,
            "equipped": bool(item_base_id),
        }
    return levels


def _side_payload(
    material_id: int,
    material_name: str,
    counts: dict[int, int],
    levels: dict[tuple[int, int], dict[str, Any]],
    *,
    suit: int,
    part: int,
) -> dict[str, Any]:
    equipment = levels.get(
        (suit, part),
        {"equipment_level": None, "equipment_raw_level": None, "equipped": None},
    )
    return {
        "material_id": material_id,
        "material_name": material_name,
        "material_count": counts[material_id],
        **equipment,
    }


def read_lingzhuang_strengthening_runtime_snapshot(
    *,
    cross_count: int = 16,
    game_task_activity_id: int | None = None,
) -> dict[str, Any]:
    """Read one coherent, strictly external process-memory snapshot."""

    memory = MumuProcessMemory.discover_cached()
    reader = LuaJitReader(memory)
    backpack_root, backpack_cache_hit = resolve_manager_root(
        memory,
        manager_key="lingzhuang-strengthening-backpack",
        marker=_BACKPACK_MARKER,
        required_methods=_BACKPACK_METHODS,
        validate=_backpack_data_fields,
    )
    counts = _material_counts(reader, _backpack_data_fields(reader, backpack_root))
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    levels: dict[tuple[int, int], dict[str, Any]] = {}
    equipment_root: int | None = None
    equipment_cache_hit = False
    warnings: list[str] = []
    try:
        equipment_root, equipment_cache_hit = resolve_manager_root(
            memory,
            manager_key="lingzhuang-strengthening-equipment-v2",
            marker=_EQUIPMENT_MARKER,
            required_methods=_EQUIPMENT_METHODS,
            validate=_equipment_data_fields,
        )
        levels = _equipment_levels(reader, _equipment_data_fields(reader, equipment_root))
    except FanxiuRuntimeMemoryError as exc:
        warnings.append(str(exc))
    expected_slots = {(suit, part) for suit in (1, 2) for part in range(1, 11)}
    missing_slots = sorted(expected_slots - levels.keys())
    if missing_slots:
        warnings.append(f"初灵/洞玄装备格子尚未加载完整：{missing_slots}")
    equipment_complete = not missing_slots
    resolved_game_task_activity_id = (
        int(game_task_activity_id)
        if game_task_activity_id is not None
        else int(cross_count) * 1_000_000 + 44_301
    )
    equipment_tasks: list[dict[str, Any]] = []
    score_round: int | None = None
    score_tasks: list[dict[str, Any]] = []
    quest_root: int | None = None
    quest_cache_hit = False
    raw_task_total = 0
    try:
        quest_root, quest_cache_hit = resolve_manager_root(
            memory,
            manager_key="quest-manager",
            marker=_QUEST_MARKER,
            required_methods=_QUEST_METHODS,
            validate=_quest_data_fields,
        )
        raw_tasks = _quest_activity_tasks(
            reader,
            quest_root,
            resolved_game_task_activity_id,
        )
        _, raw_task_total_value = reader.list_items(raw_tasks)
        raw_task_total = int(raw_task_total_value or 0)
        equipment_tasks, score_round, score_tasks = _theme_week_task_progress(reader, raw_tasks)
    except FanxiuRuntimeMemoryError as exc:
        warnings.append(str(exc))
    tasks_complete, equipment_only_phase = _task_progress_complete(
        raw_task_total=raw_task_total,
        equipment_task_count=len(equipment_tasks),
        score_task_count=len(score_tasks),
    )
    if not tasks_complete:
        warnings.append(
            f"灵装化道任务进度尚未加载完整：装备 {len(equipment_tasks)}/14，积分 {len(score_tasks)}/10"
        )
    rows = []
    for part, (part_name, initial_id, initial_name, dongxuan_id, dongxuan_name) in enumerate(_PARTS, 1):
        rows.append(
            {
                "part": part_name,
                "initial": _side_payload(initial_id, initial_name, counts, levels, suit=1, part=part),
                "dongxuan": _side_payload(dongxuan_id, dongxuan_name, counts, levels, suit=2, part=part),
            }
        )
    return {
        "captured_at": captured_at,
        "materials_captured_at": captured_at,
        "equipment_captured_at": captured_at if equipment_complete else "",
        "task_progress_captured_at": captured_at if tasks_complete else "",
        "game_task_activity_id": resolved_game_task_activity_id,
        "source_kind": "read_only_runtime_memory",
        "complete": equipment_complete and tasks_complete,
        "warnings": warnings,
        "rows": rows,
        "equipment_tasks": equipment_tasks,
        "equipment_current": max((item["progress"] for item in equipment_tasks), default=None),
        "score_round": score_round,
        "score_total_rounds": _SCORE_TOTAL_ROUNDS,
        "score_current": max((item["progress"] for item in score_tasks), default=None),
        "score_rounds": [
            {"round": round_number, "target": target}
            for round_number, target in _SCORE_ROUND_TARGETS.items()
        ],
        "score_tasks": score_tasks,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "backpack_root": f"0x{backpack_root:x}",
            "backpack_root_cache_hit": backpack_cache_hit,
            "equipment_root": f"0x{equipment_root:x}" if equipment_root else "",
            "equipment_root_cache_hit": equipment_cache_hit,
            "quest_root": f"0x{quest_root:x}" if quest_root else "",
            "quest_root_cache_hit": quest_cache_hit,
            "quest_task_total": raw_task_total,
            "equipment_only_phase": equipment_only_phase,
        },
    }


def _empty_snapshot() -> LingzhuangStrengtheningSnapshot:
    return _enrich_static_task_reference(LingzhuangStrengtheningSnapshot(
        rows=[
            LingzhuangStrengtheningRow(
                part=part,
                initial=LingzhuangStrengtheningSide(material_id=initial_id, material_name=initial_name),
                dongxuan=LingzhuangStrengtheningSide(material_id=dongxuan_id, material_name=dongxuan_name),
            )
            for part, initial_id, initial_name, dongxuan_id, dongxuan_name in _PARTS
        ]
    ))


def load_lingzhuang_strengthening_snapshot(session: Session) -> LingzhuangStrengtheningSnapshot:
    row = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == STRENGTHENING_SNAPSHOT_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == STRENGTHENING_SNAPSHOT_KEY,
        )
    ).first()
    if row is None or not isinstance(row.payload, dict):
        return _empty_snapshot()
    return _enrich_static_task_reference(
        LingzhuangStrengtheningSnapshot.model_validate(row.payload)
    )


def collect_and_store_lingzhuang_strengthening_snapshot(
    session: Session,
    *,
    activity_id: str,
    today: date | None = None,
    observed_snapshot: LingzhuangStrengtheningSnapshot | dict[str, Any] | None = None,
) -> LingzhuangStrengtheningSnapshot:
    activity = session.get(FanxiuExchangeActivity, activity_id)
    if activity is None or activity.activity_type != LINGZHUANG_HUADAO_ACTIVITY_TYPE:
        raise ValueError("灵装化道活动不存在")
    current_day = today or datetime.now().astimezone().date()
    if not is_exchange_activity_active(activity, today=current_day):
        raise ValueError("灵装化道活动不在有效日期内")

    if observed_snapshot is None:
        try:
            source_game_activity_id = int(
                (activity.evidence or {}).get("game_activity_id") or 0
            ) or None
            read_options: dict[str, Any] = {"cross_count": activity.cross_count}
            if source_game_activity_id is not None:
                read_options["game_task_activity_id"] = source_game_activity_id
            payload = read_lingzhuang_strengthening_runtime_snapshot(**read_options)
        except FanxiuRuntimeMemoryError as exc:
            raise ValueError(str(exc)) from exc
    elif isinstance(observed_snapshot, LingzhuangStrengtheningSnapshot):
        payload = observed_snapshot.model_dump(mode="python")
    else:
        payload = dict(observed_snapshot)
    snapshot = _enrich_static_task_reference(
        LingzhuangStrengtheningSnapshot.model_validate(payload)
    )
    snapshot.activity_id = activity_id
    if len(snapshot.rows) != len(_PARTS) or any(
        side.material_count is None
        for item in snapshot.rows
        for side in (item.initial, item.dongxuan)
    ):
        raise ValueError("强化原料运行态数据不完整，已保留上次快照")

    row = session.exec(
        select(FanxiuPacketBusinessRecord).where(
            FanxiuPacketBusinessRecord.domain == STRENGTHENING_SNAPSHOT_DOMAIN,
            FanxiuPacketBusinessRecord.record_key == STRENGTHENING_SNAPSHOT_KEY,
        )
    ).first()
    previous = (
        LingzhuangStrengtheningSnapshot.model_validate(row.payload)
        if row is not None and isinstance(row.payload, dict)
        else None
    )
    if previous is not None and not snapshot.equipment_captured_at:
        previous_by_part = {item.part: item for item in previous.rows}
        for item in snapshot.rows:
            old = previous_by_part.get(item.part)
            if old is None:
                continue
            for current_side, old_side in ((item.initial, old.initial), (item.dongxuan, old.dongxuan)):
                current_side.equipment_level = old_side.equipment_level
                current_side.equipment_raw_level = old_side.equipment_raw_level
                current_side.equipped = old_side.equipped
        snapshot.equipment_captured_at = previous.equipment_captured_at
    if previous is not None and not snapshot.task_progress_captured_at:
        snapshot.equipment_tasks = previous.equipment_tasks
        snapshot.equipment_current = previous.equipment_current
        snapshot.score_round = previous.score_round
        snapshot.score_total_rounds = previous.score_total_rounds
        snapshot.score_current = previous.score_current
        snapshot.score_rounds = previous.score_rounds
        snapshot.score_tasks = previous.score_tasks
        snapshot.task_progress_captured_at = previous.task_progress_captured_at
    if not snapshot.complete:
        snapshot.captured_at = previous.captured_at if previous is not None else ""
    now = time.time()
    if row is None:
        row = FanxiuPacketBusinessRecord(
            domain=STRENGTHENING_SNAPSHOT_DOMAIN,
            record_key=STRENGTHENING_SNAPSHOT_KEY,
            source_kind=snapshot.source_kind,
            entity_name="灵装强化现状",
            captured_at=snapshot.captured_at,
            captured_date=snapshot.captured_at[:10],
            payload=snapshot.model_dump(mode="json"),
            evidence=dict(payload.get("evidence") or {}),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.source_kind = snapshot.source_kind
        row.captured_at = snapshot.captured_at
        row.captured_date = snapshot.captured_at[:10]
        row.payload = snapshot.model_dump(mode="json")
        row.evidence = dict(payload.get("evidence") or {})
        row.updated_at = now
        session.add(row)
    session.commit()
    return snapshot
