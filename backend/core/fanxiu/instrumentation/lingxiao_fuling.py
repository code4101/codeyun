from __future__ import annotations

"""Read-only projection of Lingxiao's loaded #571 free-reward panel.

Neither its red dot nor the generated ``v_has_reward`` field is a claim
authorization.  Individual normal-track reward items must be mapped before
this adapter can certify any free claim.
"""

from datetime import datetime
import time
from typing import Any

from backend.core.fanxiu.instrumentation.activity_menu import (
    active_ui_component_objects,
    read_ui_object_field,
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
    table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    UiRuntimeContext,
    read_ui_runtime_snapshot,
)


# Field names stay lazy.  Different activity panels do not necessarily intern
# all Fuling-specific strings, so requiring them while binding UIShowMgr would
# incorrectly make unrelated pages fail before candidate identification.
_FULING_KEYS = frozenset()
_BATTLE_PASS_METHODS = frozenset({"Inst_get"})


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value) if isinstance(value, LuaRef) and value.kind == "table" else {}


def _long_or_int(reader: LuaJitReader, value: Any) -> int | None:
    """Decode the activity protocol's Lua ``Long`` wrapper when present."""

    return reader.long(value) if isinstance(value, LuaRef) else as_int(value)


def _boolean_table_value(reader: LuaJitReader, value: Any, key: int) -> bool | None:
    """Read an exact numeric key from Lua's sparse dictionary/table array."""

    if not isinstance(value, LuaRef) or value.kind != "table":
        return None
    table = reader.table(value.address)
    raw = table.get("fields", {}).get(key)
    if raw is None:
        array = table.get("array") or ()
        raw = array[key] if 0 <= key < len(array) else None
    return raw if isinstance(raw, bool) else None


def _int_list(reader: LuaJitReader, value: Any, *, field: str) -> list[int]:
    if not isinstance(value, LuaRef) or value.kind != "table":
        raise FanxiuRuntimeMemoryError(f"NotLoaded: 活动战令 {field} 未自然加载")
    values, declared = reader.list_items(value)
    if declared is None or not 0 <= int(declared) <= 128 or len(values) != int(declared):
        raise FanxiuRuntimeMemoryError(f"活动战令 {field} 列表不完整")
    decoded = [as_int(item) for item in values]
    if any(item is None or int(item) <= 0 for item in decoded):
        raise FanxiuRuntimeMemoryError(f"活动战令 {field} 含非法 ID")
    result = [int(item) for item in decoded if item is not None]
    if len(set(result)) != len(result):
        raise FanxiuRuntimeMemoryError(f"活动战令 {field} 含重复 ID")
    return result


def _normal_track_state(
    memory: MumuProcessMemory, *, expected_battle_pass_activity_id: int
) -> tuple[bool, set[int], bool]:
    """Read the server-synchronized battle-pass VO, never its optional cache."""

    state_address = int(_lua_addresses(memory)["state"], 16)

    def _validate(reader: LuaJitReader, root_address: int) -> None:
        manager = manager_index_fields(reader, root_address, _BATTLE_PASS_METHODS)
        instance = _fields(reader, manager.get("inst"))
        model = _fields(reader, instance.get("Model"))
        data = _fields(reader, model.get("ActivityBattlePassData"))
        info = _fields(reader, data.get("_battlePassInfo"))
        vo = info.get("activityBattlePassVO")
        # The parent #575 Bothdraw activity and the nested #571 battle-pass
        # activity deliberately have different IDs (for example 3001003 vs
        # 3001006).  The VO belongs to the latter, which is exposed by this
        # live child panel's ``V_ActivityVO.activityId``.
        observed_activity_id = _long_or_int(reader, info.get("activityId"))
        if observed_activity_id != int(expected_battle_pass_activity_id):
            raise FanxiuRuntimeMemoryError(
                "活动战令同步 VO 活动身份不一致："
                f"expected={expected_battle_pass_activity_id}, observed={observed_activity_id}, "
                f"info_fields={sorted(str(key) for key in info.keys())[:32]}"
            )
        if not isinstance(vo, LuaRef) or vo.kind != "table":
            raise FanxiuRuntimeMemoryError("NotLoaded: 活动战令同步 VO 尚未自然加载")
        fields = _fields(reader, vo)
        if not isinstance(fields.get("buys"), LuaRef) or not isinstance(fields.get("gotNormalRewardIds"), LuaRef):
            raise FanxiuRuntimeMemoryError("NotLoaded: 活动战令同步奖励状态尚未自然加载")

    root, _cache_hit, _environment = resolve_lua_global_manager_root(
        memory,
        manager_key="lingxiao-fuling-client-state",
        state_address=state_address,
        global_name="ActivityBattlePassMgr",
        required_methods=_BATTLE_PASS_METHODS,
        validate=_validate,
        force_refresh=True,
    )
    reader = LuaJitReader(memory)
    manager = manager_index_fields(reader, root, _BATTLE_PASS_METHODS)
    instance = _fields(reader, manager.get("inst"))
    model = _fields(reader, instance.get("Model"))
    data = _fields(reader, model.get("ActivityBattlePassData"))
    info = _fields(reader, data.get("_battlePassInfo"))
    vo = _fields(reader, info.get("activityBattlePassVO"))
    buys = _int_list(reader, vo.get("buys"), field="buys")
    claimed = set(_int_list(reader, vo.get("gotNormalRewardIds"), field="gotNormalRewardIds"))
    return 1 in buys, claimed, True


def _item_list_snapshot(context: UiRuntimeContext, panel_address: int) -> list[dict[str, Any]]:
    """Project instantiated #571 reward items, without inferring clickability.

    These are the active panel's own ``ActivityBattlePassViewItem`` instances,
    not a global ActivityBattlePass manager.  They expose per-reward identity
    and view state, but a later action reader must additionally prove that the
    left claim mask is visibly active before it can authorize a normal-track
    claim.
    """

    raw_list = table_ref(read_ui_object_field(context, panel_address, "_viewItemList"))
    if raw_list is None:
        raise FanxiuRuntimeMemoryError("NotLoaded: 仙门福令奖励项尚未自然实例化")
    values, declared = context.reader.list_items(raw_list)
    if declared is None or not 1 <= int(declared) <= 128 or len(values) != int(declared):
        raise FanxiuRuntimeMemoryError("仙门福令奖励项列表不完整或超出边界")
    result: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, LuaRef) or item.kind != "table":
            raise FanxiuRuntimeMemoryError("仙门福令奖励项含非表对象")
        reward_id = as_int(read_ui_object_field(context, item.address, "rewardId"))
        is_box = read_ui_object_field(context, item.address, "_isBoxCfg")
        mask = table_ref(read_ui_object_field(context, item.address, "bg_leftGetMask"))
        reached = read_ui_object_field(context, item.address, "reached")
        claimed = read_ui_object_field(context, item.address, "claimed_normal")
        if reward_id is None or reward_id <= 0 or not isinstance(is_box, bool) or mask is None:
            raise FanxiuRuntimeMemoryError("仙门福令奖励项字段不完整")
        # In this client build ``data`` is an opaque UI wrapper (its Lua table
        # has no configuration fields), not the reward config row.  Treating
        # ``data.id`` as the business identity was disproved by the live
        # snapshot.  ``rewardId`` is the ViewItem's direct identity and is
        # subsequently cross-checked against the normal-track claimed map.
        row = {
            "reward_id": int(reward_id),
            "is_box": is_box,
            "left_mask_bound": True,
        }
        if is_box:
            # The final repeatable box uses a different score/wallet formula
            # and does not set ordinary reward fields in UpdateItem.
            row["reached"] = None
            row["claimed_normal"] = None
            can_get = read_ui_object_field(context, item.address, "_normalCanGet")
            if not isinstance(can_get, bool):
                raise FanxiuRuntimeMemoryError("仙门福令循环箱普通轨状态不完整")
            row["logical_left_mask_active"] = can_get
        elif isinstance(reached, bool) and (isinstance(claimed, bool) or claimed is None):
            row["reached"] = reached
            # Current ViewItem instances expose ``reached`` but omit the
            # generated ``claimed_normal`` cache.  Preserve a future bool for
            # consistency checking; otherwise the activity client state's
            # normalRewardDicTb is the sole claimed truth.
            row["claimed_normal"] = claimed if isinstance(claimed, bool) else None
            row["logical_left_mask_active"] = None
        else:
            fields = _fields(context.reader, item)
            raise FanxiuRuntimeMemoryError(
                "仙门福令普通奖励项字段不完整："
                f"rewardId={reward_id}, reached={type(reached).__name__}, "
                f"claimed_normal={type(claimed).__name__}, "
                f"item_fields={sorted(str(key) for key in fields.keys())[:32]}"
            )
        result.append(row)
    if len({row["reward_id"] for row in result}) != len(result):
        raise FanxiuRuntimeMemoryError("仙门福令奖励项 rewardId 重复")
    return result


def _with_normal_track_state(
    items: list[dict[str, Any]], *, activated: bool, claimed_ids: set[int]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for original in items:
        row = dict(original)
        if not row["is_box"]:
            reward_id = int(row["reward_id"])
            panel_claimed = row.get("claimed_normal")
            derived_claimed = reward_id in claimed_ids
            if panel_claimed is not None and bool(panel_claimed) != derived_claimed:
                raise FanxiuRuntimeMemoryError("仙门福令普通奖励面板状态与客户端领取字典冲突")
            row["claimed_normal"] = derived_claimed
            row["logical_left_mask_active"] = (
                bool(activated) and bool(row["reached"]) and not derived_claimed
            )
        result.append(row)
    return result
def build_lingxiao_fuling_panel_snapshot(
    *,
    expected_activity_id: int,
    panel_activity_id: int | None,
    fuling_activity_id: int | None,
    activity_type: int | None,
    has_any_reward: Any,
    score_min: int | None,
    score_max: int | None,
    current_score_index: int | None,
    show_index: int | None,
    panel_address: int | None = None,
    normal_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize only fields whose semantics were observed on the live panel."""

    expected = int(expected_activity_id)
    if panel_activity_id != expected:
        raise FanxiuRuntimeMemoryError("仙门寻宝父面板活动身份与目标活动不一致")
    if fuling_activity_id is None or fuling_activity_id <= 0:
        raise FanxiuRuntimeMemoryError("仙门福令子面板缺少独立活动身份")
    if score_min is None or score_max is None or score_min <= 0 or score_max < score_min:
        raise FanxiuRuntimeMemoryError("仙门福令面板奖励分数窗口不完整")

    # ``_curScoreIndex`` is a view index, not a score value.  It is retained
    # as evidence only; claiming an exact score from it would be fabrication.
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "active_activity_panel.lingxiao_fuling",
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "activity_id": expected,
        "fuling_activity_id": fuling_activity_id,
        "activity_type": activity_type,
        "has_any_reward": has_any_reward,
        # ``v_has_reward`` is a generated but unused view field in the
        # current client.  It cannot prove either a claimable or a completed
        # free reward.  A future panel-item reader must establish individual
        # normal-track reward IDs and their left-mask state instead.
        "free_reward_claimable": None,
        "free_reward_state": "unmapped_panel_items",
        "score_window": {"min": score_min, "max": score_max},
        "current_score_index": current_score_index,
        "show_index": show_index,
        "panel_address": f"0x{panel_address:x}" if panel_address is not None else None,
        "normal_items": list(normal_items or ()),
    }


def _panel_snapshot(
    context: UiRuntimeContext, *, expected_activity_id: int
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    hosts: dict[int, Any] = {}
    for component in active_ui_component_objects(context):
        host = table_ref(read_ui_object_field(context, component.address, "m_panel")) or component
        if as_int(read_ui_object_field(context, host.address, "V_ActivityId")) != int(expected_activity_id):
            continue
        tab_group = table_ref(read_ui_object_field(context, host.address, "tabPanelGroup"))
        panels = table_ref(
            read_ui_object_field(context, tab_group.address, "panelShowComps")
        ) if tab_group is not None else None
        storage = table_ref(read_ui_object_field(context, panels.address, "_dt_")) if panels is not None else None
        count = as_int(read_ui_object_field(context, panels.address, "count")) if panels is not None else None
        if storage is None or count is None or not 1 <= count <= 16:
            continue
        hosts[host.address] = (host, storage, count)

    for host, storage, count in hosts.values():
        # ``panelShowComps`` is a sparse CDictionary wrapper in the live
        # client.  Its backing table array contains the tab components, while
        # its public numeric fields are empty; do not derive a slot from
        # ``curTabIndex`` or assume an array item exists at every index.
        values = context.reader.table(storage.address)["array"]
        if len(values) > count + 2:
            raise FanxiuRuntimeMemoryError("仙门寻宝 Tab 子面板数组超出有界范围")
        for raw in values:
            child_component = table_ref(raw)
            candidate = table_ref(read_ui_object_field(context, child_component.address, "m_panel")) if child_component is not None else None
            if candidate is None:
                continue
            activity_vo = table_ref(read_ui_object_field(context, candidate.address, "V_ActivityVO"))
            base_cfg = table_ref(read_ui_object_field(context, candidate.address, "V_ActivityBaseCfg"))
            free_button = table_ref(read_ui_object_field(context, candidate.address, "FreeBuyBtn"))
            score_text = table_ref(read_ui_object_field(context, candidate.address, "ScoreTxt"))
            red_root = table_ref(read_ui_object_field(context, candidate.address, "FreeRewardRedRoot"))
            has_reward = read_ui_object_field(context, candidate.address, "v_has_reward")
            if activity_vo is None or base_cfg is None or free_button is None or score_text is None or red_root is None:
                continue
            fuling_activity_id = as_int(read_ui_object_field(context, activity_vo.address, "activityId"))
            if fuling_activity_id is None or fuling_activity_id <= 0:
                continue
            items = _item_list_snapshot(context, candidate.address)
            try:
                activated, claimed_ids, _complete = _normal_track_state(
                    context.memory,
                    expected_battle_pass_activity_id=int(fuling_activity_id),
                )
                items = _with_normal_track_state(
                    items, activated=activated, claimed_ids=claimed_ids
                )
                normal_track_state: dict[str, Any] = {
                    "complete": True,
                    "activated": activated,
                    "claimed_reward_ids": sorted(claimed_ids),
                }
            except FanxiuRuntimeMemoryError as exc:
                normal_track_state = {
                    "complete": False,
                    "reason": str(exc),
                    "activity_vo_fields": sorted(
                        str(key) for key in _fields(context.reader, activity_vo).keys()
                    )[:48],
                }
            snapshot = build_lingxiao_fuling_panel_snapshot(
                expected_activity_id=expected_activity_id,
                panel_activity_id=as_int(read_ui_object_field(context, host.address, "V_ActivityId")),
                fuling_activity_id=fuling_activity_id,
                activity_type=as_int(read_ui_object_field(context, activity_vo.address, "activityType")),
                has_any_reward=has_reward,
                score_min=as_int(read_ui_object_field(context, candidate.address, "v_score_min")),
                score_max=as_int(read_ui_object_field(context, candidate.address, "v_score_max")),
                current_score_index=as_int(read_ui_object_field(context, candidate.address, "_curScoreIndex")),
                show_index=as_int(read_ui_object_field(context, candidate.address, "v_showIndex")),
                panel_address=candidate.address,
                normal_items=items,
            )
            snapshot["normal_track_state"] = normal_track_state
            candidates.append(snapshot)
    unique = {str(row["panel_address"]): row for row in candidates}
    if not unique:
        raise FanxiuRuntimeMemoryError("NotLoaded: 当前仙门福令面板未完整自然加载")
    if len(unique) != 1:
        raise FanxiuRuntimeMemoryError(f"Ambiguous: 同时发现 {len(unique)} 个仙门福令面板")
    return next(iter(unique.values()))


def read_lingxiao_fuling_panel_runtime(*, expected_activity_id: int) -> dict[str, Any]:
    """Read #571 without inferring a paid/free reward from its red indicator."""

    started = time.perf_counter()
    try:
        return {
            **read_ui_runtime_snapshot(
                _FULING_KEYS,
                lambda context: _panel_snapshot(context, expected_activity_id=expected_activity_id),
            ),
            "elapsed_seconds": time.perf_counter() - started,
            "evidence": {"read_only": True},
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "active_activity_panel.lingxiao_fuling",
            "reason": str(exc),
            "elapsed_seconds": time.perf_counter() - started,
            "evidence": {"read_only": True},
        }


def _daily_task_ui_snapshot(context: UiRuntimeContext, *, expected_activity_id: int) -> dict[str, Any]:
    """Read #570's current ScrollView order, not the task-config order.

    ``RankTaskItem`` receives the exact ``GetShowDataListByType`` output in
    ``ItemInfoList``.  The list legitimately sorts claimable rows ahead of
    completed rows, so config ``position`` is not a GUI coordinate.
    """
    candidates: list[list[dict[str, Any]]] = []
    for component in active_ui_component_objects(context):
        host = table_ref(read_ui_object_field(context, component.address, "m_panel")) or component
        if as_int(read_ui_object_field(context, host.address, "V_ActivityId")) != int(expected_activity_id):
            continue
        outer = table_ref(read_ui_object_field(context, host.address, "tabPanelGroup"))
        outer_panels = table_ref(read_ui_object_field(context, outer.address, "panelShowComps")) if outer else None
        outer_storage = table_ref(read_ui_object_field(context, outer_panels.address, "_dt_")) if outer_panels else None
        if outer_storage is None:
            continue
        for outer_raw in context.reader.table(outer_storage.address).get("array") or ():
            outer_component = table_ref(outer_raw)
            outer_panel = table_ref(read_ui_object_field(context, outer_component.address, "m_panel")) if outer_component else None
            inner = table_ref(read_ui_object_field(context, outer_panel.address, "tabPanelGroup")) if outer_panel else None
            inner_panels = table_ref(read_ui_object_field(context, inner.address, "panelShowComps")) if inner else None
            inner_storage = table_ref(read_ui_object_field(context, inner_panels.address, "_dt_")) if inner_panels else None
            if inner_storage is None:
                continue
            for inner_raw in context.reader.table(inner_storage.address).get("array") or ():
                inner_component = table_ref(inner_raw)
                panel = table_ref(read_ui_object_field(context, inner_component.address, "m_panel")) if inner_component else None
                if panel is None or as_int(read_ui_object_field(context, panel.address, "V_SubType")) != 4:
                    continue
                scroll = table_ref(read_ui_object_field(context, panel.address, "scrollView"))
                items = table_ref(read_ui_object_field(context, scroll.address, "ItemInfoList")) if scroll else None
                values, declared = context.reader.list_items(items) if items else ([], None)
                if declared is None or int(declared) != len(values) or not 1 <= len(values) <= 4:
                    raise FanxiuRuntimeMemoryError("福令日常 ScrollView 行数不完整")
                rows: list[dict[str, Any]] = []
                for index, value in enumerate(values):
                    if not isinstance(value, LuaRef) or value.kind != "table":
                        raise FanxiuRuntimeMemoryError("福令日常 ScrollView 含非表行")
                    task_id = as_int(read_ui_object_field(context, value.address, "id"))
                    finished = read_ui_object_field(context, value.address, "isFinished")
                    if task_id is None or task_id <= 0 or not isinstance(finished, bool):
                        raise FanxiuRuntimeMemoryError("福令日常 ScrollView 行身份不完整")
                    rows.append({"ui_index": index + 1, "task_id": int(task_id), "is_finished": finished})
                if len({row["task_id"] for row in rows}) != len(rows):
                    raise FanxiuRuntimeMemoryError("福令日常 ScrollView task_id 重复")
                candidates.append(rows)
    # UIShowMgr can expose the same live child through both its registry and
    # the parent tab tree.  Identical ordered task sequences are one logical
    # projection; different sequences remain a real ambiguity.
    unique = {tuple(row["task_id"] for row in rows): rows for rows in candidates}
    if len(unique) != 1:
        raise FanxiuRuntimeMemoryError(f"Ambiguous: 福令日常活跃 ScrollView 序列={sorted(unique)}")
    return {"ok": True, "available": True, "complete": True,
            "source": "active_activity_panel.lingxiao_daily_task_scroll",
            "activity_id": int(expected_activity_id), "rows": next(iter(unique.values())),
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "evidence": {"read_only": True}}


def read_lingxiao_daily_task_ui_runtime(*, expected_activity_id: int) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = read_ui_runtime_snapshot(_FULING_KEYS, lambda context: _daily_task_ui_snapshot(context, expected_activity_id=expected_activity_id))
        return {**result, "elapsed_seconds": time.perf_counter() - started}
    except Exception as exc:
        return {"ok": False, "available": False, "complete": False,
                "source": "active_activity_panel.lingxiao_daily_task_scroll", "reason": str(exc),
                "elapsed_seconds": time.perf_counter() - started, "evidence": {"read_only": True}}


__all__ = [
    "build_lingxiao_fuling_panel_snapshot",
    "read_lingxiao_fuling_panel_runtime",
    "read_lingxiao_daily_task_ui_runtime",
]
