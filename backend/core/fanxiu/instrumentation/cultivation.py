"""Read-only cultivation identities and loaded progress, independent of reward UI."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable

from .runtime_memory import FanxiuRuntimeMemoryError, LuaJitReader, LuaRef, MumuProcessMemory, as_int, manager_index_fields, resolve_lua_global_manager_root
from .redbag_runtime_loader import _lua_addresses

_CONDITIONS = {
    "IsGetFashionMax": ("fashion", "level"),
    "IsGetTalismanGradeMax": ("talisman", "stage"),
    "IsGetGongFaMax": ("gongfa", "jie"),
    "IsGetPetMax": ("pet", "level"),
}


def parse_cultivation_reward_targets(reward_limit: str, *, reward_item_id: int) -> list[dict[str, Any]]:
    """Parse live rewardLimit into distinct (kind, target_id, dimension) targets.

    Three arguments encode target/item/condition-count. Four encode
    target/reward-container/component-item/condition-count. Semicolon clauses
    preserve every component. The condition count is NOT a reward quantity;
    the latter must come from the actual reward.amount field.
    Unsupported or contradictory clauses fail closed.
    """
    targets = []
    seen = set()
    for clause in str(reward_limit).split(";"):
        match = re.fullmatch(r"([A-Za-z]+)\|([0-9]+(?:_[0-9]+){2,3})", clause.strip())
        if not match or match[1] not in _CONDITIONS:
            raise FanxiuRuntimeMemoryError(f"不支持的养成 rewardLimit：{clause}")
        args = [int(value) for value in match[2].split("_")]
        target_id, container_id = args[:2]
        component_item_id = args[2] if len(args) == 4 else container_id
        if container_id != int(reward_item_id) or min(args) <= 0:
            raise FanxiuRuntimeMemoryError(f"养成 rewardLimit 与奖励道具不一致：{clause}")
        kind, dimension = _CONDITIONS[match[1]]
        key = (kind, target_id, dimension)
        if key in seen:
            raise FanxiuRuntimeMemoryError(f"养成目标重复：{key}")
        seen.add(key)
        targets.append({"kind": kind, "target_id": target_id, "dimension": dimension,
                        "component_item_id": component_item_id,
                        "condition_count": args[-1]})
    return targets


def read_cultivation_progress_runtime(targets: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Read requested loaded managers once, returning full component identities.

    Never initializes Lua models or operates GUI. Missing target in a complete
    owned-list means rank 0; a missing/malformed list or field raises instead.
    Fashion's list includes unowned definitions, so absent identity is an error.
    Caches only process-bound manager locations; new observations use fresh
    readers. GUI reopen and process restart are not yet acceptance-tested for
    this composite reader; failures must be diagnosed in the affected manager.
    """
    requested = [dict(target) for target in targets]
    for target in requested:
        if (target.get("kind"), target.get("dimension")) not in _CONDITIONS.values() or int(target.get("target_id") or 0) <= 0:
            raise ValueError(f"Unsupported cultivation identity: {target}")
    memory = MumuProcessMemory.discover_cached()
    state_address = int(_lua_addresses(memory)["state"], 16)
    indices = {}
    evidence = {}
    for kind in sorted({target["kind"] for target in requested}):
        if kind == "fashion":
            global_name, model_name, methods = "FashionMgr", "FashionData", frozenset({"Inst_get", "GetFashionSexByHandPoint"})
        elif kind == "pet":
            global_name, model_name, methods = "PetMgr", "PetData", frozenset({"Inst_get"})
        elif kind == "talisman":
            from .magic_treasure import _TALISMAN_METHODS
            global_name, model_name, methods = "TalismanMgr", "TalismanData", _TALISMAN_METHODS
        else:
            from .gongfa_equipment import _GONGFA_METHODS
            global_name, model_name, methods = "GongFaNewMgr", "GongFaNewData", _GONGFA_METHODS

        def decode(reader: LuaJitReader, address: int) -> dict[int, dict[str, Any]]:
            fields = reader.fields
            manager = manager_index_fields(reader, address, methods)
            data = fields(fields(fields(manager.get("inst")).get("Model")).get(model_name))
            if kind == "fashion":
                container, identity, rank_field = data.get("AllFashionInfoVoList"), "id", "level"
            elif kind == "pet":
                container, identity, rank_field = fields(data.get("_PetInfoVo")).get("petInfoVOList"), "petId", "level"
            elif kind == "talisman":
                container, identity, rank_field = fields(data.get("_TalismanFightData")).get("talismanVos"), "baseId", "stage"
            else:
                container, identity, rank_field = data.get("gongFaDic"), None, "jie"
            if not isinstance(container, LuaRef) or container.kind != "table":
                raise FanxiuRuntimeMemoryError(f"{global_name} 拥有列表未加载")
            if kind == "gongfa":
                storage = fields(container).get("_dt_")
                if not isinstance(storage, LuaRef) or storage.kind != "table":
                    raise FanxiuRuntimeMemoryError("GongFaNewMgr 拥有字典未完整加载")
                entries = [(as_int(key), fields(fields(value).get("vo"))) for key, value in reader.dictionary_fields(container).items()]
            else:
                values, count = reader.list_items(container)
                if count is None or count != len(values):
                    raise FanxiuRuntimeMemoryError(f"{global_name} 拥有列表不完整")
                entries = [(as_int(fields(value).get(identity)), fields(value)) for value in values]
            index = {}
            for target_id, row in entries:
                rank = as_int(row.get(rank_field))
                if target_id is None or target_id <= 0 or rank is None or rank < 0 or target_id in index:
                    raise FanxiuRuntimeMemoryError(f"{global_name} 身份或阶数字段无效")
                # FashionInfoVo.IsGet (Lua lines 370-379) lazily caches this
                # flag; untouched/unowned definitions legitimately omit it.
                # Match the already-accepted wardrobe reader's observation
                # contract instead of requiring every definition to contain
                # an eagerly materialized bool. List/id/level remain checked.
                owned = bool(row.get("isGet")) if kind == "fashion" else True
                index[target_id] = {"owned": owned, "rank": rank if owned else 0}
            return index

        root, hit, _ = resolve_lua_global_manager_root(memory, manager_key=f"cultivation-{kind}", state_address=state_address,
                global_name=global_name, required_methods=methods, validate=decode)
        indices[kind] = decode(LuaJitReader(memory), root)
        evidence[kind] = {"manager": global_name, "cache_hit": hit}
    components = []
    for target in requested:
        progress = indices[target["kind"]].get(int(target["target_id"]))
        if progress is None and target["kind"] == "fashion":
            raise FanxiuRuntimeMemoryError(f"FashionMgr 缺少目标定义：{target['target_id']}")
        components.append({**target, **(progress or {"owned": False, "rank": 0}), "complete": True})
    return {"ok": True, "complete": True, "read_only": True,
            "captured_at": datetime.now(timezone.utc).isoformat(), "components": components,
            "evidence": {"pid": memory.pid, "process_start_ticks": memory.process_start_ticks, "managers": evidence}}
