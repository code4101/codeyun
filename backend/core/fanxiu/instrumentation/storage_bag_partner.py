from __future__ import annotations

"""Read-only owned-partner snapshot for storage-bag choice outcomes."""

import hashlib
import json
from typing import Any

from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
)


PARTNER_SNAPSHOT_SOURCE = (
    "runtime_memory:PartnerMgr.Model.PartnerData.PartnerInfoVoList"
)
PARTNER_MANAGER_METHODS = frozenset(
    {"LuaPartnerMgr", "CheckHasWaitingActivePartner"}
)


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


def _partner_rows(reader: LuaJitReader, root_address: int) -> list[dict[str, Any]]:
    manager = manager_index_fields(reader, root_address, PARTNER_MANAGER_METHODS)
    instance = reader.fields(manager.get("inst"))
    model = reader.fields(instance.get("Model") or manager.get("Model"))
    partner_data = reader.fields(model.get("PartnerData"))
    values, declared_count = reader.list_items(partner_data.get("PartnerInfoVoList"))
    if declared_count is None or declared_count != len(values) or not values:
        raise FanxiuRuntimeMemoryError(
            "PartnerInfoVoList 不是完整非空 CList"
        )
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for value in values:
        fields = _object_fields(reader, value)
        partner_id = None
        for key in ("id", "partnerId", "partnerID"):
            partner_id = reader.long(fields.get(key)) or as_int(fields.get(key))
            if partner_id is not None:
                break
        if partner_id is None or partner_id <= 0 or partner_id in seen:
            raise FanxiuRuntimeMemoryError("PartnerInfoVoList 含无效或重复仙侣 ID")
        seen.add(partner_id)
        partner_vo = fields.get("partnerVO")
        owned = isinstance(partner_vo, LuaRef) and partner_vo.kind == "table"
        partner_vo_fields = _object_fields(reader, partner_vo) if owned else {}
        level = as_int(partner_vo_fields.get("level")) if owned else None
        stage = as_int(partner_vo_fields.get("stage")) if owned else None
        rows.append(
            {
                "id": partner_id,
                "owned": owned,
                "level": level,
                "stage": stage,
            }
        )
    return sorted(rows, key=lambda row: int(row["id"]))


def _validate_partner_root(reader: LuaJitReader, root_address: int) -> None:
    _partner_rows(reader, root_address)


def read_storage_bag_partner_snapshot() -> dict[str, Any]:
    """Walk the already-loaded PartnerMgr only; never invoke Lua methods."""

    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        state_address = int(_lua_addresses(memory)["state"], 16)
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="storage-bag-partner",
            state_address=state_address,
            global_name="PartnerMgr",
            required_methods=PARTNER_MANAGER_METHODS,
            validate=_validate_partner_root,
        )
        rows = _partner_rows(LuaJitReader(memory), root)
        fingerprint = hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return {
            "complete": True,
            "source": PARTNER_SNAPSHOT_SOURCE,
            "fingerprint": fingerprint,
            "partners": rows,
            "partner_count": len(rows),
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "partner_root": f"0x{root:x}",
                "partner_root_cache_hit": cache_hit,
                "read_only": True,
            },
        }
    except Exception as exc:
        return {
            "complete": False,
            "source": PARTNER_SNAPSHOT_SOURCE,
            "fingerprint": "",
            "partners": [],
            "reason": (
                str(exc)
                if isinstance(exc, FanxiuRuntimeMemoryError)
                else f"{type(exc).__name__}: {exc}"
            ),
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
                "read_only": True,
            },
        }


__all__ = [
    "PARTNER_MANAGER_METHODS",
    "PARTNER_SNAPSHOT_SOURCE",
    "read_storage_bag_partner_snapshot",
]
