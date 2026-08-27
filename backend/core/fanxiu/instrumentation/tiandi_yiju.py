from __future__ import annotations

"""Strict read-only Runtime projection for the 天地弈局 chess board.

The GUI is responsible for every action.  This module only reads the already
loaded ``AllianceplaychessMgr`` state so the task can prove the current board,
natural-strength budget and the exact score transition after an operation.
"""

from datetime import datetime
import time
from typing import Any, Mapping

from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
)


MANAGER_METHODS = frozenset(
    {"LuaAllianceplaychessMgr", "Inst_get", "GetPlayChessInfo"}
)
PLAYABLE_ACTIVITY_IDS = frozenset({8090001, 8090004})
GROUP_SELECTION_ACTIVITY_ID = 8090002


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value) if value is not None else {}


def _long(reader: LuaJitReader, value: Any, *, default: int = 0) -> int:
    decoded = reader.long(value)
    return int(decoded) if decoded is not None else int(default)


def _manager_state(
    reader: LuaJitReader,
    root_address: int,
) -> tuple[dict[Any, Any], dict[Any, Any], dict[Any, Any]]:
    manager = manager_index_fields(reader, root_address, MANAGER_METHODS)
    instance = _fields(reader, manager.get("inst"))
    model = _fields(reader, instance.get("Model"))
    data = _fields(reader, model.get("AllianceplaychessData"))
    if not instance or not model or "_ChessInfoDic" not in data:
        raise FanxiuRuntimeMemoryError("天地弈局 Runtime 尚未初始化棋局数据")
    return instance, model, data


def _decode_rank_rows(
    reader: LuaJitReader,
    value: Any,
) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {}
    for raw_kind, raw_group in reader.dictionary_fields(value).items():
        kind = as_int(raw_kind)
        if kind not in (1, 2):
            continue
        rows: list[dict[str, Any]] = []
        for raw_identity, raw_row in reader.dictionary_fields(raw_group).items():
            row = _fields(reader, raw_row)
            identity = _long(
                reader,
                row.get("allianceId")
                if kind == 1
                else (
                    row.get("roleId")
                    or row.get("userId")
                    or row.get("id")
                ),
            )
            rank = as_int(row.get("rank")) or as_int(raw_identity) or 0
            score = _long(reader, row.get("score"))
            if identity <= 0:
                continue
            rows.append(
                {
                    "id": identity,
                    "rank": max(0, int(rank)),
                    "score": max(0, score),
                    "name": str(row.get("name") or ""),
                }
            )
        rows.sort(key=lambda item: (item["rank"] <= 0, item["rank"], -item["score"], item["id"]))
        result[int(kind)] = rows
    return result


def _decode_pieces(
    reader: LuaJitReader,
    value: Any,
) -> list[dict[str, int]]:
    pieces: list[dict[str, int]] = []
    for raw_id, raw_piece in reader.dictionary_fields(value).items():
        row = _fields(reader, raw_piece)
        chess_id = as_int(row.get("id")) or as_int(raw_id)
        if chess_id is None or chess_id <= 0:
            continue
        pieces.append(
            {
                "id": int(chess_id),
                "belong_alliance": _long(reader, row.get("belongAlliance")),
                "belong_camp_64": _long(reader, row.get("belongCampId64")),
                "cross": max(0, as_int(row.get("cross")) or 0),
                "refresh_belong_time": _long(reader, row.get("refreshBelongTime")),
            }
        )
    pieces.sort(key=lambda item: item["id"])
    if not pieces:
        raise FanxiuRuntimeMemoryError("天地弈局 Runtime 未读取到任何棋点")
    return pieces


def _derive_own_alliance_id(
    play_info: Mapping[Any, Any],
    rank_rows: Mapping[int, list[dict[str, Any]]],
    *,
    reader: LuaJitReader,
) -> int:
    """Derive the non-cross camp without guessing a server/alliance constant."""

    expected_score = _long(reader, play_info.get("allianceScore"))
    expected_rank = as_int(play_info.get("allianceRank")) or 0
    candidates = [
        row
        for row in rank_rows.get(1, [])
        if row["score"] == expected_score
    ]
    if len(candidates) != 1:
        raise FanxiuRuntimeMemoryError(
            "天地弈局无法从宗门榜唯一反推本宗身份："
            f"score={expected_score}, rank={expected_rank}, candidates={len(candidates)}"
        )
    return int(candidates[0]["id"])


def _decode_snapshot(
    reader: LuaJitReader,
    instance: Mapping[Any, Any],
    model: Mapping[Any, Any],
    data: Mapping[Any, Any],
) -> dict[str, Any]:
    play_info = _fields(reader, instance.get("playChessInfo"))
    if not play_info:
        raise FanxiuRuntimeMemoryError("天地弈局 Runtime 尚未同步入口积分信息")
    # The live model stores the mutable value as ``strength``; generated Lua
    # accessors name the concept StrengthValue, but there is no such backing
    # field.  Keep the concrete Runtime field authoritative.
    strength = as_int(data.get("strength"))
    max_strength = as_int(data.get("_MaxEnergyValue"))
    consume = as_int(data.get("_ConsumeNum"))
    recover_ms = _long(reader, data.get("_EnergyRecoverTime"))
    is_cross = as_int(data.get("_IsCross")) or 0
    if strength is None or strength < 0 or max_strength is None or max_strength <= 0:
        raise FanxiuRuntimeMemoryError("天地弈局体力事实无效")
    if consume is None or consume <= 0:
        raise FanxiuRuntimeMemoryError("天地弈局单次体力消耗无效")

    ranks = _decode_rank_rows(reader, data.get("rankDic"))
    pieces = _decode_pieces(reader, data.get("_ChessInfoDic"))
    own_alliance_id = None
    if is_cross == 0:
        own_alliance_id = _derive_own_alliance_id(play_info, ranks, reader=reader)
    owned_piece_ids = [
        item["id"]
        for item in pieces
        if own_alliance_id is not None and item["belong_alliance"] == own_alliance_id
    ]
    choose_states = {
        int(key): bool(value)
        for raw_key, value in reader.dictionary_fields(data.get("chooseStateDic")).items()
        if (key := as_int(raw_key)) is not None and 1 <= int(key) <= 6
    }
    return {
        "strength": int(strength),
        "entry_strength": max(0, as_int(instance.get("enterPanelStrength")) or 0),
        "max_strength": int(max_strength),
        "consume_per_play": int(consume),
        "natural_play_budget": int(strength) // int(consume),
        "recover_interval_ms": max(0, recover_ms),
        "strength_item_id": max(0, as_int(data.get("_StrengthItem")) or 0),
        "max_multiple": max(1, as_int(data.get("_MaxMulNum")) or 1),
        "is_cross": int(is_cross),
        "personal_rank": max(0, as_int(play_info.get("personalRank")) or as_int(data.get("_MyRank")) or 0),
        "personal_score": max(0, _long(reader, play_info.get("personalScore") or data.get("_MyScore"))),
        "alliance_rank": max(0, as_int(play_info.get("allianceRank")) or 0),
        "alliance_score": max(0, _long(reader, play_info.get("allianceScore"))),
        "own_alliance_id": own_alliance_id,
        "piece_count": len(pieces),
        "owned_piece_count": len(owned_piece_ids),
        "owned_piece_ids": owned_piece_ids,
        "pieces": pieces,
        "rank_rows": {str(key): value for key, value in ranks.items()},
        # These are persisted UI choices.  The task must visually force all
        # three resource-spending switches off before it starts an operation.
        "choose_states": choose_states,
        "resource_spending_choices": {
            "multiple_score_item": bool(choose_states.get(2, False)),
            "double_reward_item": bool(choose_states.get(3, False)),
            "auto_use_strength_item": bool(choose_states.get(4, False)),
        },
        "choose_state_loaded": data.get("chooseStateDic") is not None,
        "read_only": True,
    }


def read_tiandi_yiju_runtime_snapshot() -> dict[str, Any]:
    """Read the loaded board and natural-strength budget without game calls."""

    started_at = time.perf_counter()
    memory = MumuProcessMemory.discover_cached()
    state_address = int(_lua_addresses(memory)["state"], 16)
    root, cache_hit, environment = resolve_lua_global_manager_root(
        memory,
        manager_key="tiandi-yiju-board",
        state_address=state_address,
        global_name="AllianceplaychessMgr",
        required_methods=MANAGER_METHODS,
        validate=lambda current_reader, address: _manager_state(current_reader, address),
    )
    reader = LuaJitReader(memory)
    instance, model, data = _manager_state(reader, root)
    snapshot = _decode_snapshot(reader, instance, model, data)
    snapshot.update(
        {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory.alliance_play_chess",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "manager_root": f"0x{root:x}",
                "root_cache_hit": cache_hit,
                "lua_environment": environment,
            },
        }
    )
    return snapshot


def validate_tiandi_yiju_natural_play_transition(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    expected_plays: int,
) -> dict[str, int]:
    """Prove a GUI batch spent only natural strength and advanced score."""

    plays = int(expected_plays)
    if plays <= 0:
        raise RuntimeError("天地弈局验证要求正数局次")
    for label, snapshot in (("before", before), ("after", after)):
        if not snapshot.get("ok") or not snapshot.get("available") or not snapshot.get("complete"):
            raise RuntimeError(f"天地弈局 {label} Runtime 事实不完整")
        spending = dict(snapshot.get("resource_spending_choices") or {})
        if any(bool(value) for value in spending.values()):
            raise RuntimeError(f"天地弈局 {label} 检测到资源型开关开启")

    consume = int(before.get("consume_per_play") or 0)
    if consume <= 0 or int(after.get("consume_per_play") or 0) != consume:
        raise RuntimeError("天地弈局单次体力消耗在动作前后不一致")
    strength_before = int(before.get("strength") or 0)
    strength_after = int(after.get("strength") or 0)
    max_strength = int(after.get("max_strength") or before.get("max_strength") or 0)
    spent = plays * consume
    if strength_before < spent:
        raise RuntimeError("天地弈局动作前自然弈力不足")
    # A batch may straddle one natural recovery boundary.  Derive that gain
    # from the exact delta; item switches have already been proven false.
    recovered = strength_after - (strength_before - spent)
    if recovered < 0 or strength_after > max_strength:
        raise RuntimeError(
            "天地弈局体力迁移不符合自然消耗："
            f"before={strength_before}, after={strength_after}, plays={plays}"
        )

    personal_before = int(before.get("personal_score") or 0)
    personal_after = int(after.get("personal_score") or 0)
    alliance_before = int(before.get("alliance_score") or 0)
    alliance_after = int(after.get("alliance_score") or 0)
    if personal_after <= personal_before:
        raise RuntimeError("天地弈局动作后个人棋符未增加")
    if alliance_after < alliance_before:
        raise RuntimeError("天地弈局动作后宗门棋符倒退")
    return {
        "plays": plays,
        "strength_spent": spent,
        "natural_strength_recovered": recovered,
        "personal_score_gained": personal_after - personal_before,
        "alliance_score_gained": alliance_after - alliance_before,
    }


__all__ = [
    "GROUP_SELECTION_ACTIVITY_ID",
    "PLAYABLE_ACTIVITY_IDS",
    "read_tiandi_yiju_runtime_snapshot",
    "validate_tiandi_yiju_natural_play_transition",
]
