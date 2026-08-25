from __future__ import annotations

import json
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

from backend.core.fanxiu.runtime import mumu_control
from backend.core.temp_paths import codeyun_temp_root


_LUA_NIL_TAG = 0xFFFFFFFF
_LUA_FALSE_TAG = 0xFFFFFFFE
_LUA_TRUE_TAG = 0xFFFFFFFD
_LUA_STRING_TAG = 0xFFFFFFFB
_LUA_INT_TAG = 0xFFFFFFF2
_LUA_TABLE_TAG = 0xFFFFFFF4
_LUA_USERDATA_TAG = 0xFFFFFFF3
_LUA_FUNCTION_TAG = 0xFFFFFFF7
_LUA_POINTER_MASK = (1 << 47) - 1
_LUA_MAIN_THREAD_ENV_OFFSET = 72
_LUA_STATE_GLOBAL_OFFSET = 16
_MAX_LUA_TABLE_HASH_MASK = 32767
_ROOT_CACHE_LOCK = threading.Lock()
_PROCESS_CACHE_LOCK = threading.Lock()
_PROCESS_CACHE_TTL_SECONDS = 300.0
_process_cache: tuple[
    float,
    int,
    int,
    str,
    tuple["MemoryRegion", ...],
] | None = None
_INTERNED_STRING_CACHE: dict[tuple[int, int, int, int, str], int] = {}
_SnapshotT = TypeVar("_SnapshotT")


def _rol32(value: int, bits: int) -> int:
    value &= 0xFFFFFFFF
    return ((value << bits) | (value >> (32 - bits))) & 0xFFFFFFFF


def lua_jit_sparse_string_hash(seed: int, value: bytes | str) -> int:
    """LuaJIT 2.1 ``hash_sparse`` for primary string interning buckets."""

    raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    if not raw:
        return int(seed) & 0xFFFFFFFF
    length = len(raw)
    h = (length ^ int(seed)) & 0xFFFFFFFF
    if length >= 4:
        a = struct.unpack_from("<I", raw, 0)[0]
        h ^= struct.unpack_from("<I", raw, length - 4)[0]
        b = struct.unpack_from("<I", raw, (length >> 1) - 2)[0]
        h ^= b
        h = (h - _rol32(b, 14)) & 0xFFFFFFFF
        b = (b + struct.unpack_from("<I", raw, (length >> 2) - 1)[0]) & 0xFFFFFFFF
    else:
        a = raw[0]
        h ^= raw[-1]
        b = raw[length >> 1]
        h ^= b
        h = (h - _rol32(b, 14)) & 0xFFFFFFFF
    a ^= h
    a = (a - _rol32(h, 11)) & 0xFFFFFFFF
    b ^= a
    b = (b - _rol32(a, 25)) & 0xFFFFFFFF
    h ^= b
    return (h - _rol32(b, 16)) & 0xFFFFFFFF


def lua_jit_legacy_string_hash(value: bytes | str) -> int:
    """LuaJIT 2.0 string hash (same sparse ARX mixer, without a seed)."""

    return lua_jit_sparse_string_hash(0, value)


def resolve_interned_lua_string(
    memory: "MumuProcessMemory",
    *,
    string_table_address: int,
    string_mask: int,
    string_seed: int,
    name: str,
) -> int:
    """Resolve one GCstr through a single LuaJIT intern bucket and chain."""

    cache_key = (
        int(memory.pid),
        int(memory.process_start_ticks),
        int(string_table_address),
        int(string_mask),
        str(name),
    )
    cached = _INTERNED_STRING_CACHE.get(cache_key)
    if cached is not None:
        try:
            if LuaJitReader(memory).string(cached) == str(name):
                return cached
        except FanxiuRuntimeMemoryError:
            pass
        _INTERNED_STRING_CACHE.pop(cache_key, None)

    encoded = str(name).encode("utf-8")
    # The shipped libtolua fork's lj_str_new calls the legacy two-argument
    # hash routine.  There is no per-state seed in this target ABI.
    string_hash = lua_jit_legacy_string_hash(encoded)
    anchor = struct.unpack(
        "<Q",
        memory.read(
            int(string_table_address) + (string_hash & int(string_mask)) * 8,
            8,
        ),
    )[0]
    current = anchor & _LUA_POINTER_MASK & ~1
    seen: set[int] = set()
    while current and current not in seen:
        seen.add(current)
        header = memory.read(current, 24)
        next_ref = struct.unpack_from("<Q", header, 0)[0]
        stored_hash = struct.unpack_from("<I", header, 12)[0]
        length = struct.unpack_from("<I", header, 16)[0]
        if stored_hash == string_hash and length == len(encoded):
            if memory.read(current + 24, length) == encoded:
                _INTERNED_STRING_CACHE[cache_key] = current
                return current
        current = next_ref & _LUA_POINTER_MASK & ~1
    raise FanxiuRuntimeMemoryError(f"Lua intern 表中没有字符串键 {name}")


def lua_jit_intern_state(
    memory: "MumuProcessMemory", state_address: int
) -> tuple[int, int, int, int]:
    """Read the target GC64 LuaJIT fork's global string intern state."""

    global_address = struct.unpack(
        "<Q",
        memory.read(int(state_address) + _LUA_STATE_GLOBAL_OFFSET, 8),
    )[0]
    # Verified against the shipped arm64 libtolua: StrTab is the first member
    # of global_State (tab +0, mask +8, num +12).
    string_table_address, string_mask, string_count = struct.unpack(
        "<QII", memory.read(global_address, 16)
    )
    if (
        not string_table_address
        or string_mask < 1023
        or string_mask > (1 << 24) - 1
        or string_mask & (string_mask + 1)
        or string_count > string_mask + 2
        or memory.readable_region(string_table_address, (string_mask + 1) * 8)
        is None
    ):
        raise FanxiuRuntimeMemoryError("LuaJIT 全局字符串 intern 状态无效")
    return global_address, string_table_address, string_mask, 0


def read_runtime_snapshot_with_rebind(
    reader: Callable[["MumuProcessMemory", bool], _SnapshotT],
    *,
    max_attempts: int = 2,
    force_rebind_first: bool = False,
) -> _SnapshotT:
    """Read one logical snapshot, rebuilding the object chain after failure.

    Raw Lua addresses are scoped to one attempt only.  A retry creates a fresh
    process-memory reader and asks the adapter to resolve its Manager from the
    current logical root instead of consulting a previously cached address.
    ``force_rebind_first`` applies that rule to the first attempt as well for
    volatile models whose nested objects are replaced during ordinary actions.
    """

    attempts = max(1, int(max_attempts))
    last_error: FanxiuRuntimeMemoryError | None = None
    for attempt in range(attempts):
        # A Lua/UI mutation can allocate a replacement table in a mapping
        # created after the process cache was populated.  Rebuilding only the
        # Lua reader/root while reusing the old /proc/maps snapshot makes that
        # valid new address look out-of-bounds on every retry.  The first read
        # keeps the bounded cache fast; a failed coherent snapshot refreshes
        # process mappings as part of rebuilding the *whole* snapshot.
        memory = MumuProcessMemory.discover_cached(
            max_age_seconds=0.0 if attempt > 0 else _PROCESS_CACHE_TTL_SECONDS
        )
        try:
            return reader(memory, bool(force_rebind_first) or attempt > 0)
        except FanxiuRuntimeMemoryError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


class FanxiuRuntimeMemoryError(RuntimeError):
    """The live game process did not provide a coherent read-only snapshot."""

    def __init__(self, message: str, *, code: str = "runtime_unavailable") -> None:
        super().__init__(message)
        self.code = str(code)


@dataclass(frozen=True)
class MemoryRegion:
    start: int
    end: int
    permissions: str
    path: str = ""

    @property
    def size(self) -> int:
        return self.end - self.start

    def contains(self, address: int, size: int = 1) -> bool:
        return self.start <= address and size >= 0 and address + size <= self.end


@dataclass(frozen=True)
class LuaRef:
    kind: str
    address: int


def as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def table_ref(value: Any) -> LuaRef | None:
    return value if isinstance(value, LuaRef) and value.kind == "table" else None


def _parse_process_start_ticks(stat_text: str) -> int:
    suffix = str(stat_text or "").rsplit(")", 1)
    if len(suffix) != 2:
        raise FanxiuRuntimeMemoryError("无法解析凡修进程启动标识")
    fields = suffix[1].strip().split()
    if len(fields) <= 19:
        raise FanxiuRuntimeMemoryError("凡修进程启动标识字段不完整")
    return int(fields[19])


def _parse_memory_regions(maps_text: str) -> list[MemoryRegion]:
    regions: list[MemoryRegion] = []
    for line in str(maps_text or "").splitlines():
        parts = line.split(maxsplit=5)
        if len(parts) < 2 or "-" not in parts[0]:
            continue
        try:
            start_text, end_text = parts[0].split("-", 1)
            start, end = int(start_text, 16), int(end_text, 16)
        except ValueError:
            continue
        if end > start:
            regions.append(
                MemoryRegion(
                    start=start,
                    end=end,
                    permissions=parts[1],
                    path=parts[5] if len(parts) > 5 else "",
                )
            )
    return regions


class MumuProcessMemory:
    _SMALL_READ_PREFETCH_BYTES = 64 * 1024
    _MEMORY_PAGE_BYTES = 4096

    def __init__(
        self,
        *,
        pid: int,
        process_start_ticks: int,
        adb_serial: str,
        regions: Iterable[MemoryRegion],
    ) -> None:
        self.pid = int(pid)
        self.process_start_ticks = int(process_start_ticks)
        self.adb_serial = str(adb_serial)
        self.regions = tuple(regions)
        self._read_cache: dict[tuple[int, int], bytes] = {}

    @classmethod
    def discover(cls) -> "MumuProcessMemory":
        global _process_cache
        pid_text, adb_meta = mumu_control._run_mumu_adb_shell_text(
            f"pidof {mumu_control.FANXIU_ANDROID_PACKAGE}",
            timeout_s=8,
        )
        pid_values = [item for item in str(pid_text or "").split() if item.isdigit()]
        if len(pid_values) != 1:
            raise FanxiuRuntimeMemoryError(
                f"凡修游戏进程发现异常：pidof 返回 {pid_values or '空'}",
                code="process_unavailable",
            )
        pid = int(pid_values[0])
        adb_serial = str(adb_meta.get("adb_serial") or "")
        if not adb_serial:
            raise FanxiuRuntimeMemoryError(
                "凡修 MuMu ADB 设备发现结果缺少 serial",
                code="process_unavailable",
            )
        stat_text, _ = mumu_control._run_mumu_adb_shell_text(
            f"cat /proc/{pid}/stat",
            timeout_s=8,
            preferred_serials=[adb_serial],
        )
        maps_text, _ = mumu_control._run_mumu_adb_shell_text(
            f"cat /proc/{pid}/maps",
            timeout_s=8,
            preferred_serials=[adb_serial],
        )
        regions = _parse_memory_regions(maps_text)
        if not regions:
            raise FanxiuRuntimeMemoryError(
                "凡修进程没有可读取的内存映射",
                code="process_unavailable",
            )
        memory = cls(
            pid=pid,
            process_start_ticks=_parse_process_start_ticks(stat_text),
            adb_serial=adb_serial,
            regions=regions,
        )
        with _PROCESS_CACHE_LOCK:
            _process_cache = (
                time.monotonic(),
                memory.pid,
                memory.process_start_ticks,
                memory.adb_serial,
                memory.regions,
            )
        return memory

    @classmethod
    def discover_cached(
        cls,
        *,
        max_age_seconds: float | None = _PROCESS_CACHE_TTL_SECONDS,
        fallback_to_discovery: bool = True,
    ) -> "MumuProcessMemory":
        """Reuse process identity/maps for bounded read-only patrol snapshots."""

        with _PROCESS_CACHE_LOCK:
            cached = _process_cache
        if cached is not None:
            (
                cached_at,
                pid,
                process_start_ticks,
                adb_serial,
                regions,
            ) = cached
            if (
                max_age_seconds is None
                or time.monotonic() - cached_at
                <= max(0.0, float(max_age_seconds))
            ):
                try:
                    pid_text, pid_meta = mumu_control._run_mumu_adb_shell_text(
                        f"pidof {mumu_control.FANXIU_ANDROID_PACKAGE}",
                        timeout_s=8,
                        preferred_serials=[adb_serial],
                    )
                    current_pids = [
                        int(item)
                        for item in str(pid_text or "").split()
                        if item.isdigit()
                    ]
                    current_serial = str(pid_meta.get("adb_serial") or "")
                    if current_pids == [pid] and current_serial == adb_serial:
                        stat_text, _ = mumu_control._run_mumu_adb_shell_text(
                            f"cat /proc/{pid}/stat",
                            timeout_s=8,
                            preferred_serials=[adb_serial],
                        )
                        if _parse_process_start_ticks(stat_text) == process_start_ticks:
                            return cls(
                                pid=pid,
                                process_start_ticks=process_start_ticks,
                                adb_serial=adb_serial,
                                regions=regions,
                            )
                except Exception:
                    # A cached identity is only an optimization.  Any failed
                    # validation must use normal discovery rather than expose
                    # stale process memory as current game state.
                    pass
        if fallback_to_discovery:
            return cls.discover()
        raise FanxiuRuntimeMemoryError(
            "凡修进程缓存尚未预热或身份已变化",
            code="process_cache_miss",
        )

    def readable_region(self, address: int, size: int = 1) -> MemoryRegion | None:
        return next(
            (
                region
                for region in self.regions
                if "r" in region.permissions and region.contains(int(address), int(size))
            ),
            None,
        )

    def read(self, address: int, size: int, *, max_size: int = 1 << 20) -> bytes:
        address, size = int(address), int(size)
        if size < 0 or size > int(max_size):
            raise FanxiuRuntimeMemoryError(f"Runtime 内存读取长度越界：{size}")
        if size == 0:
            return b""
        region = self.readable_region(address, size)
        if region is None:
            raise FanxiuRuntimeMemoryError(
                f"Runtime 内存地址越界：0x{address:x}+{size}"
            )
        key = (address, size)
        if key in self._read_cache:
            return self._read_cache[key]
        for (cached_address, cached_size), cached_data in self._read_cache.items():
            if (
                cached_address <= address
                and address + size <= cached_address + cached_size
            ):
                offset = address - cached_address
                return cached_data[offset : offset + size]
        if size <= self._MEMORY_PAGE_BYTES:
            # Lua table traversal performs many adjacent pointer-sized reads.
            # One ADB shell round-trip per 8-byte pointer turns a cached-root
            # snapshot into a 30+ second operation.  Prefetch a bounded block
            # from the same mapping and reuse it for this coherent snapshot.
            page = self._MEMORY_PAGE_BYTES
            block_start = max(region.start, address - (address % page))
            block_end = min(
                region.end,
                block_start + self._SMALL_READ_PREFETCH_BYTES,
            )
            if address + size <= block_end and block_start % page == 0:
                block_size = block_end - block_start
                block_count = block_size // page
                if block_count > 0 and block_count * page == block_size:
                    command = (
                        f"dd if=/proc/{self.pid}/mem bs={page} "
                        f"skip={block_start // page} "
                        f"count={block_count} 2>/dev/null"
                    )
                    try:
                        block, _ = mumu_control._mumu_adb_session_shell_bytes(
                            command,
                            timeout_s=20,
                        )
                    except Exception as exc:
                        raise FanxiuRuntimeMemoryError(
                            f"读取凡修 Runtime 内存失败：{exc}"
                        ) from exc
                    if len(block) == block_size:
                        self._read_cache[(block_start, block_size)] = block
                        offset = address - block_start
                        return block[offset : offset + size]
        command = (
            f"dd if=/proc/{self.pid}/mem bs=1 skip={address} "
            f"count={size} 2>/dev/null"
        )
        try:
            data, _ = mumu_control._mumu_adb_session_shell_bytes(
                command,
                timeout_s=20,
            )
        except Exception as exc:
            raise FanxiuRuntimeMemoryError(
                f"读取凡修 Runtime 内存失败：{exc}"
            ) from exc
        if len(data) != size:
            raise FanxiuRuntimeMemoryError(
                f"读取凡修 Runtime 内存不完整：期望 {size}，实际 {len(data)}"
            )
        self._read_cache[key] = data
        return data

    def prefetch_small_read_pages(self, addresses: Iterable[int]) -> None:
        """Batch immutable-for-this-snapshot pages used by exact small reads.

        ``MumuProcessMemory`` itself is recreated for every UI snapshot, so
        these bytes are never reused across observations.  Grouping addresses
        only removes one ADB round trip per scattered Lua object; callers must
        still decode and validate every requested field from the fresh bytes.
        """

        page = self._MEMORY_PAGE_BYTES
        span = self._SMALL_READ_PREFETCH_BYTES
        blocks: dict[tuple[int, int], MemoryRegion] = {}
        for raw_address in addresses:
            address = int(raw_address)
            if any(
                cached_address <= address < cached_address + cached_size
                for cached_address, cached_size in self._read_cache
            ):
                continue
            region = self.readable_region(address)
            if region is None:
                raise FanxiuRuntimeMemoryError(
                    f"Runtime 内存地址越界：0x{address:x}"
                )
            block_start = region.start + ((address - region.start) // span) * span
            block_end = min(region.end, block_start + span)
            block_size = block_end - block_start
            if block_start % page or block_size <= 0 or block_size % page:
                raise FanxiuRuntimeMemoryError("Runtime 批量预取页未对齐")
            blocks[(block_start, block_size)] = region

        pending = sorted(blocks)
        batch_size = 24
        for index in range(0, len(pending), batch_size):
            batch = pending[index : index + batch_size]
            command = "; ".join(
                f"dd if=/proc/{self.pid}/mem bs={page} "
                f"skip={start // page} count={size // page} 2>/dev/null"
                for start, size in batch
            )
            try:
                data, _ = mumu_control._mumu_adb_session_shell_bytes(
                    command,
                    timeout_s=20,
                )
            except Exception as exc:
                raise FanxiuRuntimeMemoryError(
                    f"批量读取凡修 Runtime 内存失败：{exc}"
                ) from exc
            expected_size = sum(size for _start, size in batch)
            if len(data) != expected_size:
                raise FanxiuRuntimeMemoryError(
                    "批量读取凡修 Runtime 内存不完整："
                    f"期望 {expected_size}，实际 {len(data)}"
                )
            offset = 0
            for start, size in batch:
                self._read_cache[(start, size)] = data[offset : offset + size]
                offset += size

    def read_region(self, region: MemoryRegion) -> bytes:
        if region.size > 64 * 1024 * 1024:
            raise FanxiuRuntimeMemoryError(f"Runtime 根发现区域过大：{region.size}")
        key = (region.start, region.size)
        if key in self._read_cache:
            return self._read_cache[key]
        block_size = 4096
        if region.start % block_size or region.size % block_size:
            return self.read(
                region.start,
                region.size,
                max_size=64 * 1024 * 1024,
            )
        command = (
            f"dd if=/proc/{self.pid}/mem bs={block_size} "
            f"skip={region.start // block_size} "
            f"count={region.size // block_size} 2>/dev/null"
        )
        try:
            data, _ = mumu_control._mumu_adb_session_shell_bytes(
                command,
                timeout_s=30,
            )
        except Exception as exc:
            raise FanxiuRuntimeMemoryError(
                f"读取凡修 Runtime 连续内存失败：{exc}"
            ) from exc
        if len(data) != region.size:
            raise FanxiuRuntimeMemoryError(
                f"读取凡修 Runtime 连续内存不完整："
                f"期望 {region.size}，实际 {len(data)}"
            )
        self._read_cache[key] = data
        return data

    def iter_marker_region_batches(
        self,
        marker: bytes,
        *,
        max_scan_bytes: int = 256 * 1024 * 1024,
    ) -> Iterable[list[tuple[MemoryRegion, int]]]:
        if not marker or any(byte in b"' \r\n\t" for byte in marker):
            raise FanxiuRuntimeMemoryError("Runtime 管理器标记不安全")
        candidates = [
            region
            for region in self.regions
            if region.permissions.startswith("rw")
            and not region.path.startswith("/")
            and 1 * 1024 * 1024 <= region.size <= 64 * 1024 * 1024
        ]
        anchors = _cached_runtime_root_anchors(self)
        anchored = sorted(
            (
                region
                for region in candidates
                if anchors
                and min(
                    (
                        0
                        if region.start <= anchor < region.end
                        else min(
                            abs(region.start - anchor),
                            abs(region.end - anchor),
                        )
                    )
                    for anchor in anchors
                )
                <= 512 * 1024 * 1024
            ),
            key=lambda region: (
                min(
                    (
                        0
                        if region.start <= anchor < region.end
                        else min(
                            abs(region.start - anchor),
                            abs(region.end - anchor),
                        )
                    )
                    for anchor in anchors
                ),
                region.size,
                region.start,
            ),
        )
        preferred = [
            region
            for region in candidates
            if region not in anchored
            and 8 * 1024 * 1024 <= region.size <= 16 * 1024 * 1024
        ]
        fallback = [
            region
            for region in candidates
            if region not in anchored and region not in preferred
        ]
        marker_text = marker.decode("ascii")
        scanned_bytes = 0
        for source_batch in (anchored, preferred, fallback):
            if not source_batch:
                continue
            # Keep the Windows adb command line comfortably below its limit.
            batches = [
                source_batch[index : index + 6]
                for index in range(0, len(source_batch), 6)
            ]
            for batch in batches:
                batch_bytes = sum(region.size for region in batch)
                if scanned_bytes + batch_bytes > max(1, int(max_scan_bytes)):
                    remaining = max(0, int(max_scan_bytes) - scanned_bytes)
                    bounded_batch: list[MemoryRegion] = []
                    for region in batch:
                        if region.size > remaining:
                            continue
                        bounded_batch.append(region)
                        remaining -= region.size
                    batch = bounded_batch
                if not batch:
                    return
                matches = self._find_marker_in_batch(batch, marker_text)
                scanned_bytes += sum(region.size for region in batch)
                if matches:
                    yield matches
                if scanned_bytes >= max(1, int(max_scan_bytes)):
                    return

    def find_marker_regions(self, marker: bytes) -> list[tuple[MemoryRegion, int]]:
        return [
            match
            for batch in self.iter_marker_region_batches(marker)
            for match in batch
        ]

    def _find_marker_in_batch(
        self,
        batch: list[MemoryRegion],
        marker_text: str,
    ) -> list[tuple[MemoryRegion, int]]:
        command = "; ".join(
            (
                f"dd if=/proc/{self.pid}/mem bs=4096 "
                f"skip={region.start // 4096} count={region.size // 4096} "
                f"2>/dev/null | grep -abo '{marker_text}' "
                f"| sed 's|^|{region.start} |'"
            )
            for region in batch
        )
        try:
            output, _ = mumu_control._run_mumu_adb_shell_text(
                command,
                timeout_s=90,
                preferred_serials=[self.adb_serial],
            )
        except Exception as exc:
            raise FanxiuRuntimeMemoryError(
                f"扫描凡修 Runtime 管理器失败：{exc}"
            ) from exc
        matches: list[tuple[MemoryRegion, int]] = []
        by_start = {region.start: region for region in batch}
        for line in str(output or "").splitlines():
            try:
                start_text, hit_text = line.split(None, 1)
                region = by_start[int(start_text)]
                offset = int(hit_text.split(":", 1)[0])
            except (ValueError, KeyError):
                continue
            if 24 <= offset < region.size:
                matches.append((region, offset))
        return matches


class LuaJitReader:
    def __init__(self, memory: MumuProcessMemory) -> None:
        self.memory = memory
        self._table_cache: dict[int, dict[str, Any]] = {}

    @staticmethod
    def tag(raw: int) -> int:
        signed = raw if raw < (1 << 63) else raw - (1 << 64)
        return (signed >> 47) & 0xFFFFFFFF

    @staticmethod
    def pointer(raw: int) -> int:
        return raw & _LUA_POINTER_MASK

    @staticmethod
    def tagged_pointer(tag: int, address: int) -> int:
        return (
            ((int(tag) & 0xFFFFFFFF) << 47) | int(address)
        ) & ((1 << 64) - 1)

    def value(self, raw: int) -> Any:
        tag = self.tag(raw)
        if tag == _LUA_NIL_TAG:
            return None
        if tag == _LUA_FALSE_TAG:
            return False
        if tag == _LUA_TRUE_TAG:
            return True
        if tag == _LUA_STRING_TAG:
            return self.string(self.pointer(raw))
        if tag == _LUA_INT_TAG:
            return struct.unpack("<i", struct.pack("<I", raw & 0xFFFFFFFF))[0]
        if tag == _LUA_TABLE_TAG:
            return LuaRef("table", self.pointer(raw))
        if tag == _LUA_USERDATA_TAG:
            return LuaRef("userdata", self.pointer(raw))
        if tag == _LUA_FUNCTION_TAG:
            return LuaRef("function", self.pointer(raw))
        return struct.unpack("<d", struct.pack("<Q", raw))[0]

    def string(self, address: int) -> str:
        header = self.memory.read(address, 24)
        length = struct.unpack_from("<I", header, 16)[0]
        if length > 16 * 1024:
            raise FanxiuRuntimeMemoryError(f"Lua 字符串长度越界：{length}")
        return self.memory.read(
            address + 24,
            length,
            max_size=16 * 1024,
        ).decode("utf-8", "replace")

    def table(self, address: int) -> dict[str, Any]:
        address = int(address)
        if address in self._table_cache:
            return self._table_cache[address]
        header = self.memory.read(address, 64)
        array_address = struct.unpack_from("<Q", header, 16)[0]
        metatable_address = struct.unpack_from("<Q", header, 32)[0]
        node_address = struct.unpack_from("<Q", header, 40)[0]
        array_size, hash_mask = struct.unpack_from("<II", header, 48)
        if array_size > 4096 or hash_mask > _MAX_LUA_TABLE_HASH_MASK:
            raise FanxiuRuntimeMemoryError(
                f"Lua table 结构越界：0x{address:x}"
            )
        array: list[Any] = []
        if array_size:
            if self.memory.readable_region(array_address, array_size * 8) is None:
                raise FanxiuRuntimeMemoryError(
                    f"Lua table array 地址无效：0x{array_address:x}"
                )
            raw_array = self.memory.read(array_address, array_size * 8)
            array = [
                self.value(struct.unpack_from("<Q", raw_array, index * 8)[0])
                for index in range(array_size)
            ]
        fields: dict[Any, Any] = {}
        node_count = hash_mask + 1
        if node_count:
            if self.memory.readable_region(node_address, node_count * 24) is None:
                raise FanxiuRuntimeMemoryError(
                    f"Lua table node 地址无效：0x{node_address:x}"
                )
            raw_nodes = self.memory.read(node_address, node_count * 24)
            for index in range(node_count):
                value_raw, key_raw, _ = struct.unpack_from(
                    "<QQQ",
                    raw_nodes,
                    index * 24,
                )
                if self.tag(value_raw) == _LUA_NIL_TAG:
                    continue
                key = self.value(key_raw)
                if isinstance(key, (str, int, float, LuaRef)):
                    fields[key] = self.value(value_raw)
        result = {
            "address": address,
            "metatable": metatable_address,
            "node_address": node_address,
            "hash_mask": hash_mask,
            "fields": fields,
            "array": array,
        }
        self._table_cache[address] = result
        return result

    def fields(self, value: Any) -> dict[Any, Any]:
        if not isinstance(value, LuaRef) or value.kind != "table":
            return {}
        return self.table(value.address)["fields"]

    def string_fields(
        self,
        address: int,
        names: frozenset[str],
    ) -> dict[str, Any]:
        """Read only selected string-keyed fields from a large Lua table."""

        wanted = frozenset(str(name) for name in names if str(name))
        if not wanted:
            return {}
        header = self.memory.read(int(address), 64)
        node_address = struct.unpack_from("<Q", header, 40)[0]
        _array_size, hash_mask = struct.unpack_from("<II", header, 48)
        if hash_mask > _MAX_LUA_TABLE_HASH_MASK:
            raise FanxiuRuntimeMemoryError(
                f"Lua table 结构越界：0x{int(address):x}"
            )
        node_count = hash_mask + 1
        if self.memory.readable_region(node_address, node_count * 24) is None:
            raise FanxiuRuntimeMemoryError(
                f"Lua table node 地址无效：0x{node_address:x}"
            )
        raw_nodes = self.memory.read(node_address, node_count * 24)
        wanted_lengths = {len(name.encode("utf-8")) for name in wanted}
        result: dict[str, Any] = {}
        for index in range(node_count):
            value_raw, key_raw, _ = struct.unpack_from(
                "<QQQ", raw_nodes, index * 24
            )
            if (
                self.tag(value_raw) == _LUA_NIL_TAG
                or self.tag(key_raw) != _LUA_STRING_TAG
            ):
                continue
            key_address = self.pointer(key_raw)
            length = struct.unpack(
                "<I", self.memory.read(key_address + 16, 4)
            )[0]
            if length not in wanted_lengths:
                continue
            key = self.string(key_address)
            if key not in wanted:
                continue
            result[key] = self.value(value_raw)
            if len(result) == len(wanted):
                break
        return result

    def hashed_string_field(
        self,
        address: int,
        *,
        key_address: int,
        expected_name: str | None = None,
    ) -> Any:
        """Read one field through the target's stored-hash node chain."""

        table_header = self.memory.read(int(address), 64)
        node_address = struct.unpack_from("<Q", table_header, 40)[0]
        _array_size, hash_mask = struct.unpack_from("<II", table_header, 48)
        if hash_mask > _MAX_LUA_TABLE_HASH_MASK:
            raise FanxiuRuntimeMemoryError(
                f"Lua table 结构越界：0x{int(address):x}"
            )
        if expected_name is not None and self.string(int(key_address)) != expected_name:
            raise FanxiuRuntimeMemoryError("Lua 字符串键身份校验失败")
        stored_hash = struct.unpack(
            "<I", self.memory.read(int(key_address) + 12, 4)
        )[0]
        current = node_address + (stored_hash & hash_mask) * 24
        seen: set[int] = set()
        while current and current not in seen:
            seen.add(current)
            if not (
                node_address <= current <= node_address + hash_mask * 24
                and (current - node_address) % 24 == 0
            ):
                raise FanxiuRuntimeMemoryError("Lua table 字符串键冲突链越界")
            value_raw, key_raw, next_raw = struct.unpack(
                "<QQQ", self.memory.read(current, 24)
            )
            if (
                self.tag(key_raw) == _LUA_STRING_TAG
                and self.pointer(key_raw) == int(key_address)
            ):
                return self.value(value_raw)
            current = self.pointer(next_raw) if next_raw else 0
        return None

    def prefetch_hashed_string_fields(
        self,
        addresses: Iterable[int],
        *,
        key_addresses: Iterable[int],
    ) -> None:
        """Warm this reader's snapshot cache for exact hashed field reads."""

        table_addresses = tuple(dict.fromkeys(int(value) for value in addresses))
        keys = tuple(dict.fromkeys(int(value) for value in key_addresses))
        if not table_addresses or not keys:
            return
        self.memory.prefetch_small_read_pages((*keys, *table_addresses))
        stored_hashes = {
            key: struct.unpack("<I", self.memory.read(key + 12, 4))[0]
            for key in keys
        }
        node_pages: list[int] = []
        for address in table_addresses:
            header = self.memory.read(address, 64)
            node_address = struct.unpack_from("<Q", header, 40)[0]
            _array_size, hash_mask = struct.unpack_from("<II", header, 48)
            if hash_mask > _MAX_LUA_TABLE_HASH_MASK:
                raise FanxiuRuntimeMemoryError(
                    f"Lua table 结构越界：0x{address:x}"
                )
            node_size = (hash_mask + 1) * 24
            if self.memory.readable_region(node_address, node_size) is None:
                raise FanxiuRuntimeMemoryError(
                    f"Lua table node 地址无效：0x{node_address:x}"
                )
            node_pages.extend(
                node_address + (stored_hash & hash_mask) * 24
                for stored_hash in stored_hashes.values()
            )
        self.memory.prefetch_small_read_pages(node_pages)

    def interned_string_field(
        self,
        address: int,
        name: str,
        *,
        string_table_address: int,
        string_mask: int,
        string_seed: int,
    ) -> Any:
        key_address = resolve_interned_lua_string(
            self.memory,
            string_table_address=string_table_address,
            string_mask=string_mask,
            string_seed=string_seed,
            name=str(name),
        )
        return self.hashed_string_field(
            int(address),
            key_address=key_address,
            expected_name=str(name),
        )

    def state_string_field(
        self,
        address: int,
        name: str,
        *,
        state_address: int,
    ) -> Any:
        _global, string_table, string_mask, string_seed = lua_jit_intern_state(
            self.memory, int(state_address)
        )
        return self.interned_string_field(
            int(address),
            str(name),
            string_table_address=string_table,
            string_mask=string_mask,
            string_seed=string_seed,
        )

    def lua_closure_upvalues(self, function_address: int) -> dict[str, Any]:
        """Read a Lua GCfuncL and only its declared upvalue pointer array."""

        header = self.memory.read(int(function_address), 40)
        ffid = header[10]
        nupvalues = header[11]
        if ffid != 0 or nupvalues > 64:
            raise FanxiuRuntimeMemoryError("目标函数不是有效的 Lua closure")
        environment = struct.unpack_from("<Q", header, 16)[0]
        pc = struct.unpack_from("<Q", header, 32)[0]
        uvptr_raw = self.memory.read(int(function_address) + 40, nupvalues * 8)
        upvalues: list[Any] = []
        upvalue_addresses: list[int] = []
        for index in range(nupvalues):
            upvalue_address = (
                struct.unpack_from("<Q", uvptr_raw, index * 8)[0]
                & _LUA_POINTER_MASK
            )
            if not upvalue_address:
                raise FanxiuRuntimeMemoryError("Lua closure 含空 upvalue 指针")
            upvalue_header = self.memory.read(upvalue_address, 40)
            value_address = struct.unpack_from("<Q", upvalue_header, 32)[0]
            if not value_address:
                raise FanxiuRuntimeMemoryError("Lua closure upvalue 值指针为空")
            value_raw = struct.unpack("<Q", self.memory.read(value_address, 8))[0]
            upvalue_addresses.append(upvalue_address)
            upvalues.append(self.value(value_raw))
        return {
            "environment": environment,
            "pc": pc,
            "upvalue_addresses": upvalue_addresses,
            "upvalues": upvalues,
        }

    def metatable_index_string_field(
        self,
        address: int,
        name: str,
        *,
        string_table_address: int,
        string_mask: int,
        string_seed: int,
    ) -> Any:
        """Read ``object.metatable.__index[name]`` through exact hash chains."""

        metatable_address = struct.unpack_from(
            "<Q", self.memory.read(int(address), 40), 32
        )[0]
        if not metatable_address:
            return None
        index_ref = table_ref(
            self.interned_string_field(
                metatable_address,
                "__index",
                string_table_address=string_table_address,
                string_mask=string_mask,
                string_seed=string_seed,
            )
        )
        if index_ref is None:
            return None
        return self.interned_string_field(
            index_ref.address,
            str(name),
            string_table_address=string_table_address,
            string_mask=string_mask,
            string_seed=string_seed,
        )

    def indexed_list_items(
        self,
        value: Any,
    ) -> tuple[list[tuple[int, Any]], int | None]:
        """Read a CList without losing its logical Lua numeric keys.

        LuaJIT may place integer keys in either the array or numeric-hash area.
        A declared CList count is an exact completeness contract: every key
        ``1..count`` must exist, and sparse values must never be compressed and
        silently re-numbered.
        """

        wrapper = self.fields(value)
        data_ref = table_ref(wrapper.get("_dt_"))
        if data_ref is None:
            return [], None
        data = self.table(data_ref.address)
        count = as_int(wrapper.get("count"))
        array = data["array"]
        fields = data["fields"]
        if count is not None:
            if count < 0:
                raise FanxiuRuntimeMemoryError("CList count 不能为负数")
            indexed: list[tuple[int, Any]] = []
            for key in range(1, count + 1):
                array_value = array[key] if key < len(array) else None
                hash_value = fields.get(key)
                if (
                    array_value is not None
                    and hash_value is not None
                    and array_value != hash_value
                ):
                    raise FanxiuRuntimeMemoryError(
                        f"CList 数字键 {key} 在 array/hash 区内容冲突"
                    )
                item = array_value if array_value is not None else hash_value
                if item is None:
                    raise FanxiuRuntimeMemoryError(
                        f"CList 声明 count={count}，但缺少数字键 {key}"
                    )
                indexed.append((key, item))
            return indexed, count

        indexed_by_key = {
            key: item
            for key, item in enumerate(array[1:], start=1)
            if item is not None
        }
        for raw_key, item in fields.items():
            key = as_int(raw_key)
            if key is not None and key > 0 and item is not None:
                indexed_by_key.setdefault(key, item)
        return sorted(indexed_by_key.items()), None

    def list_items(self, value: Any) -> tuple[list[Any], int | None]:
        indexed, count = self.indexed_list_items(value)
        return [item for _key, item in indexed], count

    def dictionary_fields(self, value: Any) -> dict[Any, Any]:
        wrapper = self.fields(value)
        storage_ref = table_ref(wrapper.get("_dt_"))
        if storage_ref is None:
            return {}
        storage = self.table(storage_ref.address)
        result = dict(storage["fields"])
        for key, array_value in enumerate(storage["array"]):
            if array_value is None:
                continue
            hash_value = result.get(key)
            if hash_value is not None and hash_value != array_value:
                raise FanxiuRuntimeMemoryError(
                    f"CDictionary 数字键 {key} 在 array/hash 区内容冲突"
                )
            result.setdefault(key, array_value)
        return result

    def long(self, value: Any) -> int | None:
        direct = as_int(value)
        if direct is not None:
            return direct
        fields = self.fields(value)
        if not fields.get("_IS_LUSUOLONG"):
            return None
        low = as_int(fields.get("_low"))
        high = as_int(fields.get("_internalHigh"))
        if low is None or high is None:
            return None
        return (high << 32) | (low & 0xFFFFFFFF)


def _cache_path(manager_key: str) -> Path:
    return (
        codeyun_temp_root("fanxiu-runtime-memory")
        / f"{manager_key.lower()}-root.json"
    )


def _cached_runtime_root_anchors(memory: MumuProcessMemory) -> tuple[int, ...]:
    """Return Lua addresses already proven for this exact game process."""

    cache_dir = codeyun_temp_root("fanxiu-runtime-memory")
    anchors: set[int] = set()
    try:
        paths = tuple(cache_dir.glob("*-root.json"))
    except OSError:
        return ()
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                as_int(payload.get("pid")) != memory.pid
                or as_int(payload.get("process_start_ticks"))
                != memory.process_start_ticks
            ):
                continue
            address = as_int(
                payload.get("root_address")
                or payload.get("data_address")
            )
            if address:
                anchors.add(address)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return tuple(sorted(anchors))


def _read_cached_root(memory: MumuProcessMemory, path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if (
        as_int(payload.get("pid")) != memory.pid
        or as_int(payload.get("process_start_ticks"))
        != memory.process_start_ticks
    ):
        return None
    return as_int(payload.get("root_address"))


def _write_cached_root(
    memory: MumuProcessMemory,
    path: Path,
    root_address: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "root_address": int(root_address),
                "updated_at": time.time(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def manager_index_fields(
    reader: LuaJitReader,
    root_address: int,
    required_methods: frozenset[str],
) -> dict[Any, Any]:
    root = reader.table(root_address)
    metatable_address = as_int(root.get("metatable"))
    if not metatable_address:
        raise FanxiuRuntimeMemoryError("Runtime 管理器缺少 metatable")
    index_ref = table_ref(
        reader.table(metatable_address)["fields"].get("__index")
    )
    if index_ref is None:
        raise FanxiuRuntimeMemoryError("Runtime 管理器缺少 __index")
    fields = reader.fields(index_ref)
    if not required_methods.issubset(fields):
        raise FanxiuRuntimeMemoryError("Runtime 管理器方法签名校验失败")
    type_ref = table_ref(fields.get("_type_"))
    if type_ref is None or type_ref.address != int(root_address):
        raise FanxiuRuntimeMemoryError("Runtime 管理器 _type_ 回链校验失败")
    return fields


def _valid_lua_string_addresses(
    memory: MumuProcessMemory,
    marker: bytes,
) -> list[int]:
    """Find actual GC string objects, excluding copies embedded in Lua source."""

    if not marker or any(byte in b"' \r\n\t" for byte in marker):
        raise FanxiuRuntimeMemoryError("Runtime Lua 字段标记不安全")
    cache_path = _cache_path(f"lua-string-{marker.decode('ascii').lower()}")
    cached = _read_cached_root(memory, cache_path)
    if cached is not None:
        try:
            if LuaJitReader(memory).string(cached).encode("ascii") == marker:
                return [cached]
        except (FanxiuRuntimeMemoryError, UnicodeEncodeError):
            cache_path.unlink(missing_ok=True)
            memory._read_cache.clear()
    reader = LuaJitReader(memory)
    expected = marker.decode("ascii")
    for matches in memory.iter_marker_region_batches(marker):
        addresses: list[int] = []
        for region, offset in matches:
            address = region.start + offset - 24
            try:
                if reader.string(address) == expected:
                    addresses.append(address)
            except FanxiuRuntimeMemoryError:
                continue
        if addresses:
            result = sorted(set(addresses))
            _write_cached_root(memory, cache_path, result[0])
            return result
    return []


def _nearby_lua_heap_regions(
    memory: MumuProcessMemory,
    anchors: Iterable[int],
    *,
    radius: int = 128 * 1024 * 1024,
    max_total_bytes: int = 256 * 1024 * 1024,
) -> list[MemoryRegion]:
    """Bound cross-region lookup to the anonymous LuaJIT heap neighborhood."""

    anchor_values = tuple(int(value) for value in anchors)
    if not anchor_values:
        return []

    def distance(region: MemoryRegion, address: int) -> int:
        if region.start <= address < region.end:
            return 0
        if address < region.start:
            return region.start - address
        return address - region.end

    candidates = [
        region
        for region in memory.regions
        if region.permissions.startswith("rw")
        and not region.path
        and 1 * 1024 * 1024 <= region.size <= 64 * 1024 * 1024
        and min(distance(region, anchor) for anchor in anchor_values) <= radius
    ]
    candidates.sort(
        key=lambda region: (
            min(distance(region, anchor) for anchor in anchor_values),
            region.size,
            region.start,
        )
    )
    selected: list[MemoryRegion] = []
    selected_bytes = 0
    for region in candidates:
        if selected_bytes + region.size > max(1, int(max_total_bytes)):
            continue
        selected.append(region)
        selected_bytes += region.size
    return selected


def _cross_region_manager_roots(
    memory: MumuProcessMemory,
    *,
    string_addresses: Iterable[int],
    required_methods: frozenset[str],
    validate: Callable[[LuaJitReader, int], None],
) -> Iterable[int]:
    """Resolve global manager tables whose string key and hash table are split."""

    strings = tuple(sorted(set(int(value) for value in string_addresses)))
    regions = _nearby_lua_heap_regions(memory, strings)
    if not strings or not regions:
        return
    payloads: dict[MemoryRegion, bytes] = {}
    for region in regions:
        try:
            payloads[region] = memory.read_region(region)
        except FanxiuRuntimeMemoryError:
            # Anonymous mappings can disappear or become unreadable between
            # /proc/maps discovery and /proc/mem collection.  Root discovery
            # is best-effort across candidates; business validation below is
            # still strict for any root that is returned.
            continue
    reader = LuaJitReader(memory)
    seen_tables: set[int] = set()

    def validated_root(
        table_address: int,
        *,
        key_nodes: Iterable[int],
        expected_node_address: int | None = None,
        expected_hash_mask: int | None = None,
    ) -> int | None:
        if table_address in seen_tables:
            return None
        try:
            table = reader.table(table_address)
            node_address = int(table["node_address"])
            hash_mask = int(table["hash_mask"])
            if (
                expected_node_address is not None
                and node_address != expected_node_address
            ):
                return None
            if expected_hash_mask is not None and hash_mask != expected_hash_mask:
                return None
            if not any(
                0 <= int(key_node) - node_address <= hash_mask * 24
                and (int(key_node) - node_address) % 24 == 0
                for key_node in key_nodes
            ):
                return None
            # The same Lua node base can be derived from several candidate
            # hash masks when their low hash bits happen to be identical.
            # Mark the table as seen only after the candidate constraints
            # match, otherwise an early wrong mask suppresses the real one.
            seen_tables.add(table_address)
            fields = table["fields"]
            if not required_methods.issubset(fields):
                return None
            root_ref = table_ref(fields.get("_type_"))
            if root_ref is None:
                return None
            validate(reader, root_ref.address)
            return root_ref.address
        except (
            FanxiuRuntimeMemoryError,
            KeyError,
            TypeError,
            ValueError,
            struct.error,
        ):
            return None

    for string_address in strings:
        string_hash = struct.unpack(
            "<I",
            memory.read(string_address + 12, 4),
        )[0]
        tagged_string = struct.pack(
            "<Q",
            LuaJitReader.tagged_pointer(_LUA_STRING_TAG, string_address),
        )
        key_nodes: list[int] = []
        for region, region_bytes in payloads.items():
            search_from = 0
            while True:
                key_offset = region_bytes.find(tagged_string, search_from)
                if key_offset < 0:
                    break
                search_from = key_offset + 1
                if key_offset >= 8:
                    key_nodes.append(region.start + key_offset - 8)

        for key_node in key_nodes:
            for exponent in range(13):
                hash_mask = (1 << exponent) - 1
                node_index = string_hash & hash_mask
                node_address = key_node - node_index * 24
                if memory.readable_region(node_address, (hash_mask + 1) * 24) is None:
                    continue
                pointer_bytes = struct.pack("<Q", node_address)
                for region, region_bytes in payloads.items():
                    pointer_from = 0
                    while True:
                        pointer_offset = region_bytes.find(
                            pointer_bytes,
                            pointer_from,
                        )
                        if pointer_offset < 0:
                            break
                        pointer_from = pointer_offset + 1
                        table_address = region.start + pointer_offset - 40
                        if memory.readable_region(table_address, 64) is None:
                            continue
                        root_address = validated_root(
                            table_address,
                            key_nodes=[key_node],
                            expected_node_address=node_address,
                            expected_hash_mask=hash_mask,
                        )
                        if root_address is not None:
                            yield root_address

        # LuaJIT can relocate a colliding key away from hash & hmask.  In that
        # case infer candidate table headers from their raw node-base pointer,
        # then let the actual table hash_mask prove that the key belongs to it.
        for region, region_bytes in payloads.items():
            for pointer_offset in range(40, len(region_bytes) - 8, 8):
                node_address = struct.unpack_from(
                    "<Q",
                    region_bytes,
                    pointer_offset,
                )[0]
                if not any(
                    0 <= key_node - node_address <= 4095 * 24
                    and (key_node - node_address) % 24 == 0
                    for key_node in key_nodes
                ):
                    continue
                table_address = region.start + pointer_offset - 40
                root_address = validated_root(
                    table_address,
                    key_nodes=key_nodes,
                    expected_node_address=node_address,
                )
                if root_address is not None:
                    yield root_address


def _discover_data_table_roots(
    memory: MumuProcessMemory,
    *,
    marker: bytes,
    required_fields: frozenset[str],
) -> list[int]:
    """Find live Lua tables by a string field key instead of a class manager."""

    string_addresses = _valid_lua_string_addresses(memory, marker)
    if not string_addresses:
        raise FanxiuRuntimeMemoryError(
            f"未发现有效 Lua 字段字符串 {marker.decode('ascii')}"
        )
    tagged_keys = tuple(
        struct.pack(
            "<Q",
            LuaJitReader.tagged_pointer(_LUA_STRING_TAG, address),
        )
        for address in string_addresses
    )
    candidates = [
        region
        for region in memory.regions
        if region.permissions.startswith("rw")
        and not region.path.startswith("/")
        and 1 * 1024 * 1024 <= region.size <= 64 * 1024 * 1024
    ]
    preferred = [
        region
        for region in candidates
        if 8 * 1024 * 1024 <= region.size <= 16 * 1024 * 1024
    ]
    fallback = [region for region in candidates if region not in preferred]
    reader = LuaJitReader(memory)
    for region_batch in (preferred, fallback):
        roots: list[int] = []
        for region in region_batch:
            try:
                region_bytes = memory.read_region(region)
            except FanxiuRuntimeMemoryError:
                # Android /proc maps can retain anonymous readable-looking
                # mappings that still return a short read while the translated
                # game runtime is changing them.  One stale candidate must not
                # abort discovery before later LuaJIT heap regions are checked.
                continue
            key_nodes: list[int] = []
            for tagged_key in tagged_keys:
                search_from = 0
                while True:
                    key_offset = region_bytes.find(tagged_key, search_from)
                    if key_offset < 0:
                        break
                    search_from = key_offset + 1
                    if key_offset >= 8:
                        key_nodes.append(region.start + key_offset - 8)
            if not key_nodes:
                continue

            for offset in range(0, len(region_bytes) - 64, 8):
                node_address = struct.unpack_from(
                    "<Q",
                    region_bytes,
                    offset + 40,
                )[0]
                array_size, hash_mask = struct.unpack_from(
                    "<II",
                    region_bytes,
                    offset + 48,
                )
                if array_size > 4096 or hash_mask > 511:
                    continue
                if not any(
                    0 <= key_node - node_address <= hash_mask * 24
                    and (key_node - node_address) % 24 == 0
                    for key_node in key_nodes
                ):
                    continue
                table_address = region.start + offset
                try:
                    fields = reader.table(table_address)["fields"]
                except (
                    FanxiuRuntimeMemoryError,
                    KeyError,
                    TypeError,
                    ValueError,
                    struct.error,
                ):
                    continue
                if required_fields.issubset(fields):
                    roots.append(table_address)
        if roots:
            return sorted(set(roots))
    raise FanxiuRuntimeMemoryError(
        f"未发现包含 {','.join(sorted(required_fields))} 的活跃 Lua 数据表"
    )


def resolve_data_table_root(
    memory: MumuProcessMemory,
    *,
    manager_key: str,
    marker: bytes,
    required_fields: frozenset[str],
    validate: Callable[[LuaJitReader, int], None],
    score: Callable[[LuaJitReader, int], tuple[Any, ...]],
    cache_max_age_seconds: float = 10.0,
) -> tuple[int, bool]:
    """Resolve and cache the newest coherent protocol/model data table."""

    path = _cache_path(f"{manager_key}-data")
    with _ROOT_CACHE_LOCK:
        cached = _read_cached_root(memory, path)
        if cached is not None:
            try:
                cached_payload = json.loads(path.read_text(encoding="utf-8"))
                cache_age = time.time() - float(cached_payload.get("updated_at") or 0)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                cache_age = float("inf")
            if cache_age > max(0.0, float(cache_max_age_seconds)):
                cached = None
        if cached is not None:
            try:
                validate(LuaJitReader(memory), cached)
                return cached, True
            except (
                FanxiuRuntimeMemoryError,
                KeyError,
                TypeError,
                ValueError,
                struct.error,
            ):
                path.unlink(missing_ok=True)
                memory._read_cache.clear()
        roots = _discover_data_table_roots(
            memory,
            marker=marker,
            required_fields=required_fields,
        )
        reader = LuaJitReader(memory)
        valid: list[int] = []
        for root in roots:
            try:
                validate(reader, root)
                valid.append(root)
            except (
                FanxiuRuntimeMemoryError,
                KeyError,
                TypeError,
                ValueError,
                struct.error,
            ):
                continue
        if not valid:
            raise FanxiuRuntimeMemoryError("发现的 Lua 数据表均未通过业务校验")
        selected = max(valid, key=lambda address: score(reader, address))
        _write_cached_root(memory, path, selected)
        return selected, False


def _discover_manager_root(
    memory: MumuProcessMemory,
    *,
    marker: bytes,
    required_methods: frozenset[str],
    validate: Callable[[LuaJitReader, int], None],
) -> int:
    valid_string_addresses: list[int] = []
    reader = LuaJitReader(memory)
    expected_marker = marker.decode("ascii")
    for marker_batch in memory.iter_marker_region_batches(marker):
        for region, marker_offset in marker_batch:
            try:
                region_bytes = memory.read_region(region)
            except FanxiuRuntimeMemoryError:
                continue
            string_address = region.start + marker_offset - 24
            try:
                if reader.string(string_address) != expected_marker:
                    continue
            except FanxiuRuntimeMemoryError:
                continue
            valid_string_addresses.append(string_address)
            tagged_string = struct.pack(
                "<Q",
                LuaJitReader.tagged_pointer(_LUA_STRING_TAG, string_address),
            )
            search_from = 0
            while True:
                key_offset = region_bytes.find(tagged_string, search_from)
                if key_offset < 0:
                    break
                search_from = key_offset + 1
                key_address = region.start + key_offset
                for node_index in range(512):
                    node_address = key_address - 8 - node_index * 24
                    if node_address < region.start:
                        break
                    pointer_bytes = struct.pack("<Q", node_address)
                    pointer_from = 0
                    while True:
                        pointer_offset = region_bytes.find(pointer_bytes, pointer_from)
                        if pointer_offset < 0:
                            break
                        pointer_from = pointer_offset + 1
                        table_address = region.start + pointer_offset - 40
                        if (
                            table_address < region.start
                            or table_address + 64 > region.end
                        ):
                            continue
                        try:
                            reader = LuaJitReader(memory)
                            table = reader.table(table_address)
                            if (
                                table["node_address"] != node_address
                                or table["hash_mask"] < node_index
                            ):
                                continue
                            fields = table["fields"]
                            if not required_methods.issubset(fields):
                                continue
                            root_ref = table_ref(fields.get("_type_"))
                            if root_ref is None:
                                continue
                            validate(reader, root_ref.address)
                            return root_ref.address
                        except (
                            FanxiuRuntimeMemoryError,
                            KeyError,
                            TypeError,
                            ValueError,
                            struct.error,
                        ):
                            continue
    for root_address in _cross_region_manager_roots(
        memory,
        string_addresses=valid_string_addresses,
        required_methods=required_methods,
        validate=validate,
    ):
        return root_address
    raise FanxiuRuntimeMemoryError(
        f"未在当前凡修进程中发现有效 {marker.decode('ascii')} Runtime 根",
        code="manager_not_found",
    )


def resolve_manager_root(
    memory: MumuProcessMemory,
    *,
    manager_key: str,
    marker: bytes,
    required_methods: frozenset[str],
    validate: Callable[[LuaJitReader, int], None],
    allow_discovery: bool = True,
    force_refresh: bool = False,
) -> tuple[int, bool]:
    path = _cache_path(manager_key)
    with _ROOT_CACHE_LOCK:
        cached = None if force_refresh else _read_cached_root(memory, path)
        if cached is not None:
            try:
                # Validation reads only the small Lua objects it traverses.
                # Never materialize the cached root's entire memory region:
                # a 64 MiB read on every patrol defeats the purpose of caching.
                validate(LuaJitReader(memory), cached)
                return cached, True
            except (
                FanxiuRuntimeMemoryError,
                KeyError,
                TypeError,
                ValueError,
                struct.error,
            ):
                path.unlink(missing_ok=True)
                memory._read_cache.clear()
        if not allow_discovery:
            raise FanxiuRuntimeMemoryError(
                f"{marker.decode('ascii')} Runtime 根缓存尚未预热",
                code="root_cache_miss",
            )
        root = _discover_manager_root(
            memory,
            marker=marker,
            required_methods=required_methods,
            validate=validate,
        )
        _write_cached_root(memory, path, root)
        return root, False


def resolve_lua_global_manager_root(
    memory: MumuProcessMemory,
    *,
    manager_key: str,
    state_address: int,
    global_name: str,
    required_methods: frozenset[str],
    validate: Callable[[LuaJitReader, int], None],
    force_refresh: bool = False,
) -> tuple[int, bool, int]:
    """Resolve a loaded manager from the main Lua thread environment.

    The adapter only reads the already-existing ``lua_State`` and global table.
    It never calls ``require`` or any Lua function.  Cached roots remain bound to
    the process identity by the same cache contract as marker-based discovery.

    :return tuple: ``(manager_root, cache_hit, environment_address)``.
    """

    path = _cache_path(f"{manager_key}-lua-global")
    with _ROOT_CACHE_LOCK:
        cached = None if force_refresh else _read_cached_root(memory, path)
        if cached is not None:
            try:
                reader = LuaJitReader(memory)
                manager_index_fields(reader, cached, required_methods)
                validate(reader, cached)
                state = memory.read(int(state_address), 96)
                environment_address = struct.unpack_from(
                    "<Q", state, _LUA_MAIN_THREAD_ENV_OFFSET
                )[0]
                return cached, True, environment_address
            except (
                FanxiuRuntimeMemoryError,
                KeyError,
                TypeError,
                ValueError,
                struct.error,
            ):
                path.unlink(missing_ok=True)
                memory._read_cache.clear()

        state = memory.read(int(state_address), 96)
        environment_address = struct.unpack_from(
            "<Q", state, _LUA_MAIN_THREAD_ENV_OFFSET
        )[0]
        reader = LuaJitReader(memory)
        # The main environment can contain tens of thousands of hash nodes.
        # Reading its complete node array merely to resolve three known global
        # names turned a wallet read into a 70+ second probe.  Lua strings are
        # interned, so resolve each exact key from its one intern bucket and
        # then follow the table's stored-hash collision chain.  This is still
        # strictly read-only, and retains the _G/package identity guards.
        (
            _global_state_address,
            string_table_address,
            string_mask,
            string_seed,
        ) = lua_jit_intern_state(memory, int(state_address))
        exact_globals = {
            name: reader.interned_string_field(
                environment_address,
                name,
                string_table_address=string_table_address,
                string_mask=string_mask,
                string_seed=string_seed,
            )
            for name in ("_G", "package", global_name)
        }
        self_ref = table_ref(exact_globals.get("_G"))
        package_ref = table_ref(exact_globals.get("package"))
        manager_ref = table_ref(exact_globals.get(global_name))
        if (
            self_ref is None
            or self_ref.address != environment_address
            or package_ref is None
            or manager_ref is None
        ):
            raise FanxiuRuntimeMemoryError(
                f"主 Lua 全局环境中没有已加载的 {global_name}"
            )
        manager_index_fields(reader, manager_ref.address, required_methods)
        validate(reader, manager_ref.address)
        _write_cached_root(memory, path, manager_ref.address)
        return manager_ref.address, False, environment_address
