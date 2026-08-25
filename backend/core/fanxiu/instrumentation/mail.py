from __future__ import annotations

"""Read the already-loaded client mail model from external process memory."""

import time
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
    _lua_addresses,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    read_runtime_snapshot_with_rebind,
    resolve_lua_global_manager_root,
)
from backend.core.fanxiu.mail.visual_alignment import mail_snapshot_fingerprint


_MAIL_METHODS = frozenset(
    {
        "Inst_get",
        "HasGotReward",
        "ClaimAllRewards",
        "DeleteAll",
    }
)


def _object_fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    """Flatten a packet VO's table-backed inheritance chain."""

    fields: dict[Any, Any] = {}
    seen: set[int] = set()
    current = value
    while isinstance(current, LuaRef) and current.kind == "table":
        if current.address in seen:
            break
        seen.add(current.address)
        current_fields = reader.fields(current)
        fields = {**current_fields, **fields}
        current = current_fields.get("_super")
    return fields


def _identity(reader: LuaJitReader, value: Any) -> int | None:
    return reader.long(value) if isinstance(value, LuaRef) else as_int(value)


def _mail_data_fields(
    reader: LuaJitReader,
    root_address: int,
) -> dict[Any, Any]:
    manager_fields = manager_index_fields(reader, root_address, _MAIL_METHODS)
    instance_fields = reader.fields(manager_fields.get("inst"))
    model_fields = reader.fields(instance_fields.get("Model"))
    data_fields = reader.fields(model_fields.get("data"))
    if not {"mailList", "lockList"}.issubset(data_fields):
        raise FanxiuRuntimeMemoryError("MailMgr 本地邮件模型尚未初始化")
    return data_fields


def _reward(reader: LuaJitReader, value: Any) -> dict[str, Any]:
    fields = _object_fields(reader, value)
    return {
        "type": as_int(fields.get("type")),
        "code": as_int(fields.get("code")),
        "amount": _identity(reader, fields.get("amount")),
        "content": str(fields.get("content") or ""),
        "extra_mark": as_int(fields.get("extraMark")),
        "client_content": str(fields.get("clientContent") or ""),
    }


def _snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    root_cache_hit: bool,
    state_address: int,
    environment_address: int,
) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    data_fields = _mail_data_fields(reader, root_address)
    mail_values, declared_mail_count = reader.list_items(data_fields.get("mailList"))
    lock_values, declared_lock_count = reader.list_items(data_fields.get("lockList"))
    locked_ids = {
        identity
        for value in lock_values
        if (identity := _identity(reader, value)) is not None
    }

    items: list[dict[str, Any]] = []
    malformed_count = 0
    for runtime_index, value in enumerate(mail_values):
        fields = _object_fields(reader, value)
        mail_id = _identity(reader, fields.get("id"))
        mail_type = as_int(fields.get("type"))
        rewards, declared_reward_count = reader.list_items(fields.get("rewards"))
        reward_items = [_reward(reader, reward) for reward in rewards]
        reward_count = (
            declared_reward_count
            if declared_reward_count is not None
            else len(reward_items)
        )
        if (
            mail_id is None
            or mail_type is None
            or declared_reward_count is None
            or len(reward_items) != declared_reward_count
        ):
            malformed_count += 1
        reward_getted = fields.get("rewardGetted")
        read = fields.get("read")
        has_attachment = reward_count > 0
        items.append(
            {
                "runtime_index": runtime_index,
                "id": str(mail_id) if mail_id is not None else None,
                "type": mail_type,
                "title": str(fields.get("title") or ""),
                "content": str(fields.get("content") or ""),
                "create_time": _identity(reader, fields.get("createTime")),
                "expire_time": _identity(reader, fields.get("expireTime")),
                "read": read if isinstance(read, bool) else None,
                "reward_getted": (
                    reward_getted if isinstance(reward_getted, bool) else None
                ),
                "sender_name": str(fields.get("senderName") or ""),
                "locked": mail_id in locked_ids if mail_id is not None else None,
                "has_attachment": has_attachment,
                "attachment_count": reward_count,
                "unclaimed_attachment": (
                    has_attachment and reward_getted is False
                ),
                "rewards": reward_items,
            }
        )

    decoded_mail_count = len(items)
    decoded_lock_count = len(locked_ids)
    complete = (
        declared_mail_count is not None
        and declared_lock_count is not None
        and decoded_mail_count == declared_mail_count
        and len(lock_values) == declared_lock_count
        and malformed_count == 0
    )
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": "MailMgr.Model.data.mailList",
        "total": declared_mail_count,
        "decoded_count": decoded_mail_count,
        "unread_count": sum(item["read"] is False for item in items),
        "unclaimed_count": sum(item["unclaimed_attachment"] for item in items),
        "locked_count": declared_lock_count,
        "decoded_locked_count": decoded_lock_count,
        "malformed_count": malformed_count,
        "sequence_fingerprint": mail_snapshot_fingerprint(items),
        "items": items,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "captured_at_epoch": time.time(),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "state_address": f"0x{state_address:x}",
            "environment_address": f"0x{environment_address:x}",
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
        },
    }


def read_mail_snapshot() -> dict[str, Any]:
    """Return the current local mail list without GUI or network actions."""

    started_at = time.perf_counter()
    stage_started_at = started_at
    timings: dict[str, float] = {}
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        timings["process_discovery"] = time.perf_counter() - stage_started_at
        stage_started_at = time.perf_counter()
        state_address = int(_lua_addresses(memory)["state"], 16)
        timings["lua_state"] = time.perf_counter() - stage_started_at
        stage_started_at = time.perf_counter()
        root, root_cache_hit, environment_address = (
            resolve_lua_global_manager_root(
                memory,
                manager_key="mail",
                state_address=state_address,
                global_name="MailMgr",
                required_methods=_MAIL_METHODS,
                validate=_mail_data_fields,
            )
        )
        timings["manager_resolve"] = time.perf_counter() - stage_started_at
        stage_started_at = time.perf_counter()
        result = _snapshot(
            memory,
            root,
            root_cache_hit=root_cache_hit,
            state_address=state_address,
            environment_address=environment_address,
        )
        timings["snapshot_decode"] = time.perf_counter() - stage_started_at
        result["elapsed_seconds"] = time.perf_counter() - started_at
        result["timings"] = timings
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
            "timings": timings,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
            },
        }


def _header_snapshot(
    memory: MumuProcessMemory,
    root_address: int,
    *,
    limit: int,
    root_cache_hit: bool,
    state_address: int,
    environment_address: int,
) -> dict[str, Any]:
    """Decode only the newest mail identities used by the patrol cursor."""

    reader = LuaJitReader(memory)
    data_fields = _mail_data_fields(reader, root_address)
    mail_values, declared_count = reader.list_items(data_fields.get("mailList"))
    if declared_count is None or declared_count != len(mail_values):
        raise FanxiuRuntimeMemoryError("MailMgr 邮件列表声明数量不完整")
    selected = mail_values[: max(1, int(limit or 1))]
    items: list[dict[str, Any]] = []
    for runtime_index, value in enumerate(selected):
        fields = _object_fields(reader, value)
        mail_id = _identity(reader, fields.get("id"))
        mail_type = as_int(fields.get("type"))
        create_time = _identity(reader, fields.get("createTime"))
        if mail_id is None or mail_type is None or create_time is None:
            raise FanxiuRuntimeMemoryError(
                f"MailMgr 最新邮件第 {runtime_index + 1} 项身份不完整"
            )
        items.append({
            "runtime_index": runtime_index,
            "id": str(mail_id),
            "type": mail_type,
            "create_time": create_time,
        })
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "runtime_memory",
        "protocol": "MailMgr.Model.data.mailList.headers",
        "total": declared_count,
        "decoded_count": len(items),
        "items": items,
        "head": items[0] if items else None,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "captured_at_epoch": time.time(),
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "state_address": f"0x{state_address:x}",
            "environment_address": f"0x{environment_address:x}",
            "root_address": f"0x{root_address:x}",
            "root_cache_hit": root_cache_hit,
        },
    }


def read_mail_header_snapshot(*, limit: int = 16) -> dict[str, Any]:
    """Read a bounded newest-first mail header prefix without opening mail UI."""

    started_at = time.perf_counter()
    timings: dict[str, float] = {}
    memory: MumuProcessMemory | None = None
    attempt_count = 0

    def read_once(
        current_memory: MumuProcessMemory,
        force_rebind: bool,
    ) -> dict[str, Any]:
        nonlocal memory, timings, attempt_count
        memory = current_memory
        attempt_count += 1
        attempt_timings: dict[str, float] = {}
        stage_started_at = time.perf_counter()
        state_address = int(_lua_addresses(current_memory)["state"], 16)
        attempt_timings["lua_state"] = time.perf_counter() - stage_started_at
        stage_started_at = time.perf_counter()
        root, root_cache_hit, environment_address = resolve_lua_global_manager_root(
            current_memory,
            manager_key="mail",
            state_address=state_address,
            global_name="MailMgr",
            required_methods=_MAIL_METHODS,
            validate=_mail_data_fields,
            force_refresh=force_rebind,
        )
        attempt_timings["manager_resolve"] = time.perf_counter() - stage_started_at
        stage_started_at = time.perf_counter()
        result = _header_snapshot(
            current_memory,
            root,
            limit=limit,
            root_cache_hit=root_cache_hit,
            state_address=state_address,
            environment_address=environment_address,
        )
        attempt_timings["header_decode"] = time.perf_counter() - stage_started_at
        timings = attempt_timings
        result["evidence"]["snapshot_attempts"] = attempt_count
        return result

    try:
        result = read_runtime_snapshot_with_rebind(read_once)
        result["elapsed_seconds"] = time.perf_counter() - started_at
        result["timings"] = timings
        return result
    except Exception as exc:
        reason = str(exc) if isinstance(exc, FanxiuRuntimeMemoryError) else f"{type(exc).__name__}: {exc}"
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "reason": reason,
            "snapshot_attempts": attempt_count,
            "elapsed_seconds": time.perf_counter() - started_at,
            "timings": timings,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
            },
        }


__all__ = ["read_mail_header_snapshot", "read_mail_snapshot"]
