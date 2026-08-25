from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    MumuProcessMemory,
)
from backend.core.fanxiu.instrumentation.policy import reject_active_instrumentation


_IL2CPP_SHA256 = "3f11fd7a27ec413ad725cc7a02adcabbbafb154806045735e701496b214fc679"
_TOLUA_SHA256 = "176274e75a380e97c209deacc63e372a3c9adc066d6ff59c671e62ebeb376a2f"
_LUA_STATE_CLASS_SLOT_OFFSET = 0x2FB3FE8
_LUA_FUNCTION_OFFSETS = {
    "gettop": 0x3F358,
    "loadstring": 0x4B934,
    "pcall": 0x41A04,
    "settop": 0x3F36C,
}
_REMOTE_BRIDGE_PATH = "/data/local/tmp/codeyun-redbag-runtime-bridge.so"


class FanxiuRedbagRuntimeLoadError(RuntimeError):
    pass


def _asset_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "native" / name


def _agent_source() -> str:
    return (
        Path(__file__).resolve().parent
        / "agents"
        / "redbag_runtime_loader.js"
    ).read_text(encoding="utf-8")


def _module(memory: MumuProcessMemory, name: str) -> tuple[int, str]:
    regions = [
        region
        for region in memory.regions
        if Path(region.path).name == name
    ]
    if not regions:
        raise FanxiuRedbagRuntimeLoadError(f"凡修尚未加载 {name}")
    paths = {region.path for region in regions}
    if len(paths) != 1:
        raise FanxiuRedbagRuntimeLoadError(f"凡修 {name} 映射路径不唯一")
    return min(region.start for region in regions), paths.pop()


def _read_pointer(memory: MumuProcessMemory, address: int) -> int:
    try:
        return struct.unpack("<Q", memory.read(address, 8))[0]
    except (FanxiuRuntimeMemoryError, struct.error) as exc:
        raise FanxiuRedbagRuntimeLoadError(
            f"红包 Runtime 指针链无效：0x{address:x}"
        ) from exc


def _lua_addresses(memory: MumuProcessMemory) -> dict[str, str]:
    il2cpp_base, _ = _module(memory, "libil2cpp.so")
    tolua_base, _ = _module(memory, "libtolua.so")
    class_cell = _read_pointer(
        memory,
        il2cpp_base + _LUA_STATE_CLASS_SLOT_OFFSET,
    )
    class_object = _read_pointer(memory, class_cell)
    static_fields = _read_pointer(memory, class_object + 0xB8)
    main_state = _read_pointer(memory, static_fields)
    state = _read_pointer(memory, main_state + 0x10)
    if not all((class_cell, class_object, static_fields, main_state, state)):
        raise FanxiuRedbagRuntimeLoadError("凡修主 LuaState 尚未就绪")
    if memory.readable_region(state, 64) is None:
        raise FanxiuRedbagRuntimeLoadError("凡修主 lua_State 地址不可读")
    return {
        "state": f"0x{state:x}",
        **{
            name: f"0x{tolua_base + offset:x}"
            for name, offset in _LUA_FUNCTION_OFFSETS.items()
        },
    }


def _remote_sha256(service: Any, device_id: str, path: str) -> str:
    output = service._shell(
        device_id,
        "sha256sum",
        path,
        timeout=20.0,
        check=False,
    )
    first = str(output or "").strip().split()
    return first[0].lower() if first else ""


def _verify_game_binaries(
    service: Any,
    device_id: str,
    memory: MumuProcessMemory,
) -> None:
    for name, expected in (
        ("libil2cpp.so", _IL2CPP_SHA256),
        ("libtolua.so", _TOLUA_SHA256),
    ):
        _, path = _module(memory, name)
        actual = _remote_sha256(service, device_id, path)
        if actual != expected:
            raise FanxiuRedbagRuntimeLoadError(
                f"{name} 指纹不匹配，拒绝调用版本相关的红包加载桥"
            )


def _deploy_bridge(service: Any, device_id: str) -> Path:
    reject_active_instrumentation("向设备部署 Redbag Runtime 桥接库")
    local_path = _asset_path("redbag_runtime_bridge.arm64.bin")
    if not local_path.is_file():
        raise FanxiuRedbagRuntimeLoadError("红包 ARM64 适配器资产缺失")
    local_hash = hashlib.sha256(local_path.read_bytes()).hexdigest()
    if _remote_sha256(service, device_id, _REMOTE_BRIDGE_PATH) != local_hash:
        service._run_adb(
            [
                "-s",
                device_id,
                "push",
                str(local_path),
                _REMOTE_BRIDGE_PATH,
            ],
            timeout=20.0,
        )
        service._shell(
            device_id,
            "chmod",
            "755",
            _REMOTE_BRIDGE_PATH,
            timeout=5.0,
        )
    return local_path


def ensure_redbag_runtime_manager() -> dict[str, Any]:
    """Idempotently instantiate RedbagMgr through the game main Lua state."""

    reject_active_instrumentation("初始化游戏 RedbagMgr Runtime")

    memory: MumuProcessMemory | None = None
    try:
        from backend.core.fanxiu.instrumentation.service import (
            fanxiu_instrumentation_service as service,
        )

        memory = MumuProcessMemory.discover()
        device_id = memory.adb_serial
        _verify_game_binaries(service, device_id, memory)
        addresses = _lua_addresses(memory)
        bridge_path = _deploy_bridge(service, device_id)
        service.ensure_server(device_id=device_id)
        frida = service._frida_loader()
        device = service._frida_device(frida, device_id, 8.0)
        session = None
        script = None
        try:
            session = device.attach(memory.pid)
            script = session.create_script(_agent_source())
            script.load()
            probe = script.exports_sync.probe(_REMOTE_BRIDGE_PATH)
            if not probe.get("ok"):
                raise FanxiuRedbagRuntimeLoadError(
                    "红包 ARM64 适配器 ABI 探针失败"
                )
            result = script.exports_sync.ensure(
                _REMOTE_BRIDGE_PATH,
                addresses,
            )
            if not result.get("ok"):
                raise FanxiuRedbagRuntimeLoadError(
                    f"RedbagMgr 初始化 Lua 状态码：{result.get('status')}"
                )
            return {
                "ok": True,
                "loaded": True,
                "source": "native_bridge_lua_main_state",
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "thread": result.get("thread"),
                "bridge_sha256": hashlib.sha256(
                    bridge_path.read_bytes()
                ).hexdigest(),
            }
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
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, FanxiuRedbagRuntimeLoadError)
            else f"{type(exc).__name__}: {exc}"
        )
        return {
            "ok": False,
            "loaded": False,
            "source": "native_bridge_lua_main_state",
            "reason": reason,
            "pid": memory.pid if memory is not None else None,
            "process_start_ticks": (
                memory.process_start_ticks if memory is not None else None
            ),
        }
