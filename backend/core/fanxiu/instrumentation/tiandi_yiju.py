from __future__ import annotations

"""Strict read-only Runtime projection for the 天地弈局 chess board.

The GUI is responsible for every action.  This module only reads the already
loaded ``AllianceplaychessMgr`` state so the task can prove the current board,
natural-strength budget and the exact score transition after an operation.
"""

from datetime import datetime
from collections import deque
from fractions import Fraction
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
    table_ref,
)


MANAGER_METHODS = frozenset(
    {"LuaAllianceplaychessMgr", "Inst_get", "GetPlayChessInfo"}
)
PLAYABLE_ACTIVITY_IDS = frozenset({8090001, 8090004})
GROUP_SELECTION_ACTIVITY_ID = 8090002
TIANYUAN_PIECE_ID = 1


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


def _table_array_ints(reader: LuaJitReader, value: Any) -> list[int]:
    ref = table_ref(value)
    if ref is None:
        return []
    table = reader.table(ref.address)
    values = {
        index: int(decoded)
        for index, item in enumerate(table["array"])
        if index > 0 and (decoded := as_int(item)) is not None
    }
    for raw_index, item in table["fields"].items():
        index = as_int(raw_index)
        decoded = as_int(item)
        if index is not None and index > 0 and decoded is not None:
            values.setdefault(int(index), int(decoded))
    return [values[index] for index in sorted(values)]


def _config_value(reader: LuaJitReader, row: Any, index: int) -> Any:
    ref = table_ref(row)
    if ref is None:
        raise FanxiuRuntimeMemoryError("天地弈局棋点配置行无效")
    table = reader.table(ref.address)
    if index < len(table["array"]) and table["array"][index] is not None:
        return table["array"][index]
    value = table["fields"].get(index)
    return table["fields"].get(float(index)) if value is None else value


def _choose_tiandi_yiju_target(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        raise FanxiuRuntimeMemoryError("天地弈局当前没有可打棋点")
    return min(
        candidates,
        key=lambda item: (
            int(item["distance_to_tianyuan"]),
            -Fraction(int(item["own_score"]), max(1, int(item["total_score"]))),
            int(item["piece_id"]),
        ),
    )


def _derive_own_config_alliance_id(
    configs: Mapping[int, Mapping[str, list[int]]],
    pieces: list[dict[str, int]],
    *,
    own_alliance_id: int,
) -> int:
    """Map the live cross-alliance id onto the board config's 1001..1004 slot.

    Cross boards use dynamic alliance ids in chess state while piece config
    uses stable slot ids.  Exclusive home-quadrant pieces provide the runtime
    join between them; shared center pieces are deliberately ignored.
    """

    owner_by_piece = {int(item["id"]): int(item["belong_alliance"]) for item in pieces}
    scores: dict[int, int] = {}
    for piece_id, config in configs.items():
        allowed = [int(value) for value in config.get("allowed_alliances", [])]
        if len(allowed) != 1:
            continue
        if owner_by_piece.get(int(piece_id)) == int(own_alliance_id):
            slot = allowed[0]
            scores[slot] = scores.get(slot, 0) + 1
    if not scores:
        raise FanxiuRuntimeMemoryError("天地弈局无法从专属棋点映射本宗配置槽位")
    maximum = max(scores.values())
    winners = [slot for slot, score in scores.items() if score == maximum]
    if len(winners) != 1:
        raise FanxiuRuntimeMemoryError(
            f"天地弈局本宗配置槽位不唯一：scores={scores}"
        )
    return int(winners[0])


def _derive_own_alliance_id(
    play_info: Mapping[Any, Any],
    rank_rows: Mapping[int, list[dict[str, Any]]],
    *,
    reader: LuaJitReader,
) -> int:
    """Derive the non-cross camp without guessing a server/alliance constant."""

    expected_rank = as_int(play_info.get("allianceRank")) or 0
    expected_score = _long(reader, play_info.get("allianceScore"))
    rows = rank_rows.get(1, [])
    # Rank is the stable identity join on a live board.  The entry-panel score
    # is a stale cache and can legitimately differ after other alliances keep
    # playing; the current 8-cross board demonstrated exactly that condition.
    candidates = [row for row in rows if expected_rank > 0 and row["rank"] == expected_rank]
    identity_basis = "rank"
    if not candidates:
        candidates = [row for row in rows if row["score"] == expected_score]
        identity_basis = "score"
    if len(candidates) != 1:
        raise FanxiuRuntimeMemoryError(
            "天地弈局无法从宗门榜唯一反推本宗身份："
            f"score={expected_score}, rank={expected_rank}, "
            f"basis={identity_basis}, candidates={len(candidates)}"
        )
    return int(candidates[0]["id"])


def _derive_cross_own_alliance_id(
    reader: LuaJitReader,
    data: Mapping[Any, Any],
    pieces: list[dict[str, int]],
    play_info: Mapping[Any, Any],
    rank_rows: Mapping[int, list[dict[str, Any]]],
) -> int:
    """Derive the player's alliance from score, ally, and live-lane facts.

    A cross camp can contain both the player's alliance and ``allyAlliance``;
    both therefore legitimately own pieces on the connected lane.  The rank
    cache is not an identity fact because it can refresh independently.  The
    player's exact score row and the lane owner left after excluding the
    explicit ally must agree whenever both are available.
    """

    left, left_count = reader.list_items(data.get("_ChessConnectListL"))
    right, right_count = reader.list_items(data.get("_ChessConnectListR"))
    if left_count != right_count or len(left) != len(right) or not left:
        raise FanxiuRuntimeMemoryError("天地弈局跨服棋路不完整，无法确认本宗身份")
    connected = {int(value) for value in (*left, *right)}
    owner_by_piece = {int(item["id"]): int(item["belong_alliance"]) for item in pieces}
    owners = {
        owner_by_piece[piece_id]
        for piece_id in connected
        if owner_by_piece.get(piece_id, 0) > 0
    }

    candidates: list[int] = []
    ally_alliance_id = as_int(data.get("allyAlliance")) or 0
    player_lane_owners = owners - ({int(ally_alliance_id)} if ally_alliance_id > 0 else set())
    if len(player_lane_owners) == 1:
        candidates.append(int(next(iter(player_lane_owners))))
    elif len(player_lane_owners) > 1:
        raise FanxiuRuntimeMemoryError(
            "天地弈局跨服棋路排除盟友后仍有多个本人候选："
            f"owners={sorted(owners)}, ally={ally_alliance_id}"
        )

    expected_score = _long(reader, play_info.get("allianceScore"))
    score_matches = [
        row for row in rank_rows.get(1, []) if int(row["score"]) == int(expected_score)
    ]
    if len(score_matches) == 1:
        candidates.append(int(score_matches[0]["id"]))

    if not candidates and len(owners) == 1:
        candidates.append(int(next(iter(owners))))
    if not candidates:
        raise FanxiuRuntimeMemoryError(
            "天地弈局跨服身份缺少本人积分或排除盟友后的棋路证据"
        )
    if len(set(candidates)) != 1:
        raise FanxiuRuntimeMemoryError(
            f"天地弈局跨服本人身份多源证据冲突：candidates={candidates}"
        )
    return int(candidates[0])


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
    own_alliance_id = (
        _derive_cross_own_alliance_id(reader, data, pieces, play_info, ranks)
        if is_cross != 0
        else _derive_own_alliance_id(play_info, ranks, reader=reader)
    )
    own_alliance_row = next(
        (row for row in ranks.get(1, []) if row["id"] == own_alliance_id),
        None,
    )
    if own_alliance_row is None:
        raise FanxiuRuntimeMemoryError("天地弈局本宗榜单行在身份解析后消失")
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
    # #680 live Runtime-GUI alignment (2026-08-28): clicking the existing
    # semantic Shapes changed 4=自动仙弈盒, 3=妙手珠 and 2=四倍棋符.  The two
    # remaining persisted rows follow the dialog's contiguous common-option
    # order: 5=失败不中断, 6=跳过动画.  Keep absent keys absent so the
    # configuration planner fails closed instead of inventing ``False``.
    auto_choice_indices = {
        "auto_use_strength_item": 4,
        "continue_after_defeat": 5,
        "skip_animation": 6,
        "master_skill_item": 3,
        "quadruple_chess_token_item": 2,
    }
    auto_challenge_choices = {
        key: bool(choose_states[index])
        for key, index in auto_choice_indices.items()
        if index in choose_states
    }
    entry_personal_score = max(0, _long(reader, play_info.get("personalScore")))
    board_personal_score = max(0, _long(reader, data.get("_MyScore")))
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
        # Keep both cache layers visible.  Entry-panel data does not refresh
        # synchronously after a board action; the adapter must not silently
        # conflate it with the live rank cache.
        "personal_score": board_personal_score or entry_personal_score,
        "entry_personal_score": entry_personal_score,
        "board_personal_score": board_personal_score,
        "alliance_rank": max(0, as_int(play_info.get("allianceRank")) or 0),
        "alliance_score": max(0, int(own_alliance_row["score"])),
        "entry_alliance_score": max(0, _long(reader, play_info.get("allianceScore"))),
        "own_alliance_id": own_alliance_id,
        "piece_count": len(pieces),
        "owned_piece_count": len(owned_piece_ids),
        "owned_piece_ids": owned_piece_ids,
        "pieces": pieces,
        "rank_rows": {str(key): value for key, value in ranks.items()},
        # Keep the original three-field projection for historical transition
        # evidence. Production configuration consumes ``auto_challenge_choices``
        # and applies the five-option cross/local policy instead.
        "choose_states": choose_states,
        "auto_challenge_choices": auto_challenge_choices,
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


def read_tiandi_yiju_recommended_target() -> dict[str, Any]:
    """Choose the nearest currently attackable point to Tianyuan from live Runtime."""

    memory = MumuProcessMemory.discover_cached()
    state_address = int(_lua_addresses(memory)["state"], 16)
    root, _cache_hit, _environment = resolve_lua_global_manager_root(
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
    own_alliance_id = int(snapshot.get("own_alliance_id") or 0)
    if own_alliance_id <= 0:
        raise FanxiuRuntimeMemoryError("天地弈局尚未确定本宗身份")

    # Generated config columns: 1=id, 7=neighbors, 14=allowed alliances.
    configs: dict[int, dict[str, list[int]]] = {}
    config_rows, config_count = reader.list_items(data.get("_ChessPieceCfgList"))
    if config_count is not None and config_count != len(config_rows):
        raise FanxiuRuntimeMemoryError("天地弈局棋点配置不完整")
    for row in config_rows:
        piece_id = as_int(_config_value(reader, row, 1))
        if piece_id is None or piece_id <= 0:
            raise FanxiuRuntimeMemoryError("天地弈局棋点配置 ID 无效")
        configs[int(piece_id)] = {
            "neighbors": _table_array_ints(reader, _config_value(reader, row, 7)),
            "allowed_alliances": _table_array_ints(
                reader,
                _config_value(reader, row, 14),
            ),
        }
    if TIANYUAN_PIECE_ID not in configs:
        raise FanxiuRuntimeMemoryError("天地弈局棋点配置缺少天元")

    own_config_alliance_id = _derive_own_config_alliance_id(
        configs,
        list(snapshot.get("pieces") or []),
        own_alliance_id=own_alliance_id,
    )

    graph = {piece_id: set(config["neighbors"]) for piece_id, config in configs.items()}
    distances = {TIANYUAN_PIECE_ID: 0}
    pending = deque([TIANYUAN_PIECE_ID])
    while pending:
        piece_id = pending.popleft()
        for neighbor in sorted(graph[piece_id]):
            if neighbor not in graph:
                raise FanxiuRuntimeMemoryError("天地弈局棋点相邻关系引用未知棋点")
            graph[neighbor].add(piece_id)
            if neighbor not in distances:
                distances[neighbor] = distances[piece_id] + 1
                pending.append(neighbor)
    if len(distances) != len(configs):
        raise FanxiuRuntimeMemoryError("天地弈局棋盘拓扑不连通")

    left, left_count = reader.list_items(data.get("_ChessConnectListL"))
    right, right_count = reader.list_items(data.get("_ChessConnectListR"))
    if left_count != right_count or len(left) != len(right) or not left:
        raise FanxiuRuntimeMemoryError("天地弈局当前棋路不完整")
    connected = {int(value) for value in (*left, *right)}
    frontier = {
        neighbor
        for piece_id in connected
        for neighbor in graph[piece_id]
        if neighbor not in connected
    }
    candidate_ids = sorted(
        piece_id
        for piece_id in connected | frontier
        if own_config_alliance_id in configs[piece_id]["allowed_alliances"]
    )

    candidates: list[dict[str, Any]] = []
    for raw_id, raw_piece in reader.dictionary_fields(data.get("_ChessInfoDic")).items():
        fields = _fields(reader, raw_piece)
        piece_id = as_int(fields.get("id")) or as_int(raw_id)
        if piece_id is None or int(piece_id) not in candidate_ids:
            continue
        scores = {
            int(key): max(0, _long(reader, value))
            for raw_key, value in reader.dictionary_fields(
                fields.get("allianceToScore")
            ).items()
            if (key := as_int(raw_key)) is not None and int(key) > 0
        }
        own_score = scores.get(own_alliance_id, 0)
        total_score = sum(scores.values())
        candidates.append(
            {
                "piece_id": int(piece_id),
                "distance_to_tianyuan": distances[int(piece_id)],
                "own_score": own_score,
                "total_score": total_score,
                "own_score_ratio": own_score / total_score if total_score else 0.0,
            }
        )
    target = _choose_tiandi_yiju_target(candidates)
    return {
        "ok": True,
        "complete": True,
        "own_alliance_id": own_alliance_id,
        "own_config_alliance_id": own_config_alliance_id,
        "tianyuan_piece_id": TIANYUAN_PIECE_ID,
        "candidate_piece_ids": candidate_ids,
        "target": target,
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "runtime_memory.alliance_play_chess",
    }


def validate_tiandi_yiju_natural_play_transition(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    expected_plays: int,
    success_terminal: bool = False,
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
    if personal_after <= personal_before and not success_terminal:
        raise RuntimeError("天地弈局动作后个人棋符未增加")
    if alliance_after < alliance_before:
        raise RuntimeError("天地弈局动作后宗门棋符倒退")
    return {
        "plays": plays,
        "strength_spent": spent,
        "natural_strength_recovered": recovered,
        "personal_score_gained": personal_after - personal_before,
        "alliance_score_gained": alliance_after - alliance_before,
        "success_terminal_confirmed": int(bool(success_terminal)),
    }


__all__ = [
    "GROUP_SELECTION_ACTIVITY_ID",
    "PLAYABLE_ACTIVITY_IDS",
    "TIANYUAN_PIECE_ID",
    "read_tiandi_yiju_recommended_target",
    "read_tiandi_yiju_runtime_snapshot",
    "validate_tiandi_yiju_natural_play_transition",
]
