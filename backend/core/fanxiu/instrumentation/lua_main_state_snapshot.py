from __future__ import annotations

"""Run read-only snapshot scripts inside the game's single main Lua state."""

import hashlib
import time
from pathlib import Path
from typing import Any

from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
    _agent_source,
    _asset_path,
    _lua_addresses,
    _remote_sha256,
    _verify_game_binaries,
)
from backend.core.fanxiu.instrumentation.runtime_memory import MumuProcessMemory
from backend.core.fanxiu.instrumentation.policy import reject_active_instrumentation
from backend.core.temp_paths import codeyun_temp_root


_ORIGINAL_CHUNK = b"local M=require('GameSystem.Game.Redbag.Mgr.RedbagMgr');M.Inst_get()"
_ORIGINAL_SONAME = b"libcodeyun_fanxiu_redbag_bridge.so"


class FanxiuLuaSnapshotError(RuntimeError):
    pass


def _safe_name(name: str) -> str:
    value = "".join(char for char in str(name) if char.isalnum() or char in "-_")
    if not value:
        raise ValueError("Lua 快照名称不能为空")
    return value[:48]


def _patched_bridge_bytes(source: bytes, *, script_path: str, identity: str) -> bytes:
    chunk = f"dofile('{script_path}')".encode("ascii")
    if len(chunk) > len(_ORIGINAL_CHUNK):
        raise FanxiuLuaSnapshotError("Lua 快照脚本路径超过适配器固定容量")
    offset = source.find(_ORIGINAL_CHUNK)
    if offset < 0 or source.find(_ORIGINAL_CHUNK, offset + 1) >= 0:
        raise FanxiuLuaSnapshotError("Lua 快照适配器模板指纹不唯一")
    replacement = chunk + b"\0" * (len(_ORIGINAL_CHUNK) - len(chunk))
    patched = source[:offset] + replacement + source[offset + len(_ORIGINAL_CHUNK):]
    soname = f"libcodeyun_fanxiu_{identity[:6]}_bridge.so".encode("ascii")
    if len(soname) != len(_ORIGINAL_SONAME) or patched.count(_ORIGINAL_SONAME) <= 0:
        raise FanxiuLuaSnapshotError("Lua 快照适配器 SONAME 指纹无效")
    return patched.replace(_ORIGINAL_SONAME, soname)


def run_lua_main_state_snapshot(
    *,
    name: str,
    script_source: str,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    """Execute one bounded Lua producer and return its raw result-file content.

    ``script_source`` must write the final snapshot to the global ``resultPath``.
    The helper only supplies that path and performs the version-pinned bridge call.
    """

    reject_active_instrumentation("向游戏主 Lua 状态执行快照脚本")

    safe_name = _safe_name(name)
    identity = hashlib.sha256(script_source.encode("utf-8")).hexdigest()
    remote_script_path = f"/data/local/tmp/cy-{safe_name}.lua"
    remote_bridge_path = f"/data/local/tmp/cy-{safe_name}-{identity[:8]}.so"
    remote_result_path = (
        "/sdcard/Android/data/com.frxxcrjpwssc3.ggws/files/"
        f"codeyun-{safe_name}.snapshot"
    )
    source = f'local resultPath="{remote_result_path}"\n{script_source.strip()}\n'
    temp_root = codeyun_temp_root("fanxiu-lua-main-state", safe_name)
    script_path = temp_root / f"{identity}.lua"
    bridge_path = temp_root / f"{identity}.so"
    script_path.write_text(source, encoding="utf-8")
    bridge_path.write_bytes(
        _patched_bridge_bytes(
            _asset_path("redbag_runtime_bridge.arm64.bin").read_bytes(),
            script_path=remote_script_path,
            identity=identity,
        )
    )

    from backend.core.fanxiu.instrumentation.service import (
        fanxiu_instrumentation_service as service,
    )

    memory = MumuProcessMemory.discover()
    device_id = memory.adb_serial
    _verify_game_binaries(service, device_id, memory)
    addresses = _lua_addresses(memory)
    for local_path, remote_path in (
        (script_path, remote_script_path),
        (bridge_path, remote_bridge_path),
    ):
        local_hash = hashlib.sha256(local_path.read_bytes()).hexdigest()
        if _remote_sha256(service, device_id, remote_path) != local_hash:
            service._run_adb(["-s", device_id, "push", str(local_path), remote_path], timeout=20.0)
            service._shell(device_id, "chmod", "755", remote_path, timeout=5.0)
    service._shell(device_id, "rm", "-f", remote_result_path, timeout=5.0)
    service.ensure_server(device_id=device_id)
    frida = service._frida_loader()
    device = service._frida_device(frida, device_id, 8.0)
    session = None
    script = None
    try:
        session = device.attach(memory.pid)
        script = session.create_script(_agent_source())
        script.load()
        probe = script.exports_sync.probe(remote_bridge_path)
        if not probe.get("ok"):
            raise FanxiuLuaSnapshotError("Lua 快照 ARM64 适配器 ABI 探针失败")
        result = script.exports_sync.ensure(remote_bridge_path, addresses)
        if not result.get("ok"):
            raise FanxiuLuaSnapshotError(f"Lua 快照执行状态码：{result.get('status')}")
    finally:
        if script is not None:
            try:
                script.unload()
            except Exception:
                pass
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass

    deadline = time.monotonic() + max(1.0, timeout_seconds)
    while time.monotonic() < deadline:
        output = service._shell(
            device_id, "cat", remote_result_path, timeout=5.0, check=False
        )
        if str(output or "").strip():
            return {
                "content": output,
                "source": "lua_main_state_snapshot",
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "script_sha256": identity,
                "bridge_sha256": hashlib.sha256(bridge_path.read_bytes()).hexdigest(),
            }
        time.sleep(0.2)
    raise FanxiuLuaSnapshotError(f"Lua 快照 {safe_name} 未在限时内写出结果")
