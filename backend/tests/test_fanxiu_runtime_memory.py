from __future__ import annotations

import struct

import pytest

from backend.core.fanxiu.instrumentation import runtime_memory
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MemoryRegion,
    MumuProcessMemory,
    _cross_region_manager_roots,
    _discover_data_table_roots,
    lua_jit_sparse_string_hash,
    lua_jit_legacy_string_hash,
    lua_jit_intern_state,
    read_runtime_snapshot_with_rebind,
    resolve_interned_lua_string,
    resolve_manager_root,
)
from backend.core.fanxiu.instrumentation.red_packet import (
    _aggregate_sources,
    _pending_group_contexts,
    _resolve_redbag_root,
    _snapshot as _redpacket_snapshot,
    _write_red_packet_snapshot,
    read_cached_red_packet_pending,
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
    _IL2CPP_SHA256,
    _TOLUA_SHA256,
    _agent_source,
    _lua_addresses,
)


def test_clist_reads_declared_keys_across_array_and_numeric_hash() -> None:
    reader = object.__new__(LuaJitReader)
    wrapper = LuaRef("table", 0x1000)
    storage = LuaRef("table", 0x2000)
    reader.fields = lambda value: (
        {"_dt_": storage, "count": 3} if value == wrapper else {}
    )
    reader.table = lambda _address: {
        "array": [None, "first", None],
        "fields": {2: "second", 3: "third"},
    }

    indexed, count = reader.indexed_list_items(wrapper)
    items, _ = reader.list_items(wrapper)

    assert count == 3
    assert indexed == [(1, "first"), (2, "second"), (3, "third")]
    assert items == ["first", "second", "third"]


def test_clist_fails_when_a_declared_numeric_key_is_missing() -> None:
    reader = object.__new__(LuaJitReader)
    wrapper = LuaRef("table", 0x1000)
    storage = LuaRef("table", 0x2000)
    reader.fields = lambda value: (
        {"_dt_": storage, "count": 3} if value == wrapper else {}
    )
    reader.table = lambda _address: {
        "array": [None, "first"],
        "fields": {3: "third"},
    }

    with pytest.raises(FanxiuRuntimeMemoryError, match="缺少数字键 2"):
        reader.list_items(wrapper)


def test_cdictionary_reads_numeric_keys_from_array_and_hash() -> None:
    reader = object.__new__(LuaJitReader)
    wrapper = LuaRef("table", 0x1000)
    storage = LuaRef("table", 0x2000)
    reader.fields = lambda value: {"_dt_": storage} if value == wrapper else {}
    reader.table = lambda _address: {
        "array": [None, "one", None, "three"],
        "fields": {14: "fourteen", "name": "value"},
    }

    assert reader.dictionary_fields(wrapper) == {
        1: "one",
        3: "three",
        14: "fourteen",
        "name": "value",
    }


def test_cdictionary_rejects_conflicting_numeric_storage() -> None:
    reader = object.__new__(LuaJitReader)
    wrapper = LuaRef("table", 0x1000)
    storage = LuaRef("table", 0x2000)
    reader.fields = lambda value: {"_dt_": storage} if value == wrapper else {}
    reader.table = lambda _address: {
        "array": [None, "array-value"],
        "fields": {1: "hash-value"},
    }

    with pytest.raises(FanxiuRuntimeMemoryError, match="数字键 1.*冲突"):
        reader.dictionary_fields(wrapper)


def test_runtime_snapshot_retry_rebinds_from_logical_root(monkeypatch):
    memories = [object(), object()]
    discovery_kwargs = []

    def discover(_cls, **kwargs):
        discovery_kwargs.append(kwargs)
        return memories.pop(0)

    monkeypatch.setattr(
        MumuProcessMemory,
        "discover_cached",
        classmethod(discover),
    )
    calls = []

    def read(memory, force_refresh):
        calls.append((memory, force_refresh))
        if len(calls) == 1:
            raise FanxiuRuntimeMemoryError("Runtime 内存地址越界")
        return "fresh-snapshot"

    result = read_runtime_snapshot_with_rebind(read)

    assert result == "fresh-snapshot"
    assert calls[0][0] is not calls[1][0]
    assert [force_refresh for _memory, force_refresh in calls] == [False, True]
    assert [kwargs["max_age_seconds"] for kwargs in discovery_kwargs] == [
        runtime_memory._PROCESS_CACHE_TTL_SECONDS,
        0.0,
    ]


def test_runtime_snapshot_can_require_logical_rebind_on_first_attempt(monkeypatch):
    memory = object()
    monkeypatch.setattr(
        MumuProcessMemory,
        "discover_cached",
        classmethod(lambda _cls, **_kwargs: memory),
    )
    force_refresh_values = []

    result = read_runtime_snapshot_with_rebind(
        lambda _memory, force_refresh: force_refresh_values.append(force_refresh)
        or "snapshot",
        force_rebind_first=True,
    )

    assert result == "snapshot"
    assert force_refresh_values == [True]


def test_small_runtime_reads_prefetch_and_reuse_one_memory_block(monkeypatch):
    calls = []
    region = MemoryRegion(
        start=0x10000,
        end=0x30000,
        permissions="rw-p",
    )
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="127.0.0.1:16384",
        regions=[region],
    )

    def fake_read(command, *, timeout_s):
        calls.append((command, timeout_s))
        return bytes(range(256)) * 256, {}

    monkeypatch.setattr(
        runtime_memory.mumu_control,
        "_mumu_adb_session_shell_bytes",
        fake_read,
    )

    first = memory.read(0x10008, 8)
    second = memory.read(0x10020, 8)

    assert first == bytes(range(8, 16))
    assert second == bytes(range(32, 40))
    assert len(calls) == 1
    assert "bs=4096" in calls[0][0]
    assert "count=16" in calls[0][0]


def test_large_lua_table_can_read_only_selected_string_fields(monkeypatch):
    table_address = 0x1000
    node_address = 0x2000
    mail_key_address = 0x3000
    other_key_address = 0x4000
    header = bytearray(64)
    struct.pack_into("<Q", header, 40, node_address)
    struct.pack_into("<II", header, 48, 0, 1)
    nodes = bytearray(48)
    struct.pack_into(
        "<QQQ",
        nodes,
        0,
        LuaJitReader.tagged_pointer(runtime_memory._LUA_INT_TAG, 7),
        LuaJitReader.tagged_pointer(
            runtime_memory._LUA_STRING_TAG,
            mail_key_address,
        ),
        0,
    )
    struct.pack_into(
        "<QQQ",
        nodes,
        24,
        LuaJitReader.tagged_pointer(runtime_memory._LUA_INT_TAG, 9),
        LuaJitReader.tagged_pointer(
            runtime_memory._LUA_STRING_TAG,
            other_key_address,
        ),
        0,
    )

    def string_object(value: bytes) -> bytes:
        result = bytearray(24 + len(value))
        struct.pack_into("<I", result, 16, len(value))
        result[24:] = value
        return bytes(result)

    blobs = {
        table_address: bytes(header),
        node_address: bytes(nodes),
        mail_key_address: string_object(b"MailMgr"),
        other_key_address: string_object(b"Ignored"),
    }
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )

    def fake_read(address, size, **_kwargs):
        for start, blob in blobs.items():
            if start <= address and address + size <= start + len(blob):
                offset = address - start
                return blob[offset : offset + size]
        raise AssertionError((address, size))

    monkeypatch.setattr(memory, "read", fake_read)
    monkeypatch.setattr(memory, "readable_region", lambda *_args: object())

    result = LuaJitReader(memory).string_fields(
        table_address,
        frozenset({"MailMgr"}),
    )

    assert result == {"MailMgr": 7}


def test_global_manager_resolution_uses_exact_interned_keys(monkeypatch, tmp_path):
    state_address = 0x1000
    environment_address = 0x2000
    manager_address = 0x3000
    package_address = 0x4000
    state = bytearray(96)
    struct.pack_into(
        "<Q",
        state,
        runtime_memory._LUA_MAIN_THREAD_ENV_OFFSET,
        environment_address,
    )

    class Memory:
        pid = 123
        process_start_ticks = 456

        def read(self, address, size):
            assert (address, size) == (state_address, 96)
            return bytes(state)

    exact_calls = []

    class Reader:
        def __init__(self, _memory):
            pass

        def string_fields(self, *_args, **_kwargs):
            pytest.fail("large global hash table must not be materialized")

        def interned_string_field(self, address, name, **kwargs):
            exact_calls.append((address, name, kwargs))
            values = {
                "_G": LuaRef("table", environment_address),
                "package": LuaRef("table", package_address),
                "WalletMgr": LuaRef("table", manager_address),
            }
            return values[name]

    monkeypatch.setattr(runtime_memory, "LuaJitReader", Reader)
    monkeypatch.setattr(
        runtime_memory,
        "lua_jit_intern_state",
        lambda _memory, _state: (0x5000, 0x6000, 1023, 0),
    )
    monkeypatch.setattr(runtime_memory, "_cache_path", lambda _key: tmp_path / "root.json")
    monkeypatch.setattr(runtime_memory, "_read_cached_root", lambda *_args: None)
    stored = []
    monkeypatch.setattr(
        runtime_memory,
        "_write_cached_root",
        lambda _memory, _path, address: stored.append(address),
    )
    validated = []
    monkeypatch.setattr(
        runtime_memory,
        "manager_index_fields",
        lambda _reader, address, methods: validated.append((address, methods)) or {},
    )

    root, cache_hit, environment = runtime_memory.resolve_lua_global_manager_root(
        Memory(),
        manager_key="wallet-currency-14",
        state_address=state_address,
        global_name="WalletMgr",
        required_methods=frozenset({"LuaWalletMgr"}),
        validate=lambda _reader, address: validated.append(("validate", address)),
    )

    assert (root, cache_hit, environment) == (
        manager_address,
        False,
        environment_address,
    )
    assert [name for _address, name, _kwargs in exact_calls] == [
        "_G",
        "package",
        "WalletMgr",
    ]
    assert stored == [manager_address]
    assert validated == [
        (manager_address, frozenset({"LuaWalletMgr"})),
        ("validate", manager_address),
    ]


def test_lua_jit_intern_bucket_then_stored_hash_table_bucket_lookup() -> None:
    seed = 0x0123456789ABCDEF
    assert lua_jit_sparse_string_hash(seed, "package") == 0x88B53810

    string_table_address = 0x1000
    string_address = 0x2000
    table_address = 0x3000
    node_address = 0x4000
    string_mask = 7
    padding = 5
    name = b"package"
    string_hash = lua_jit_legacy_string_hash(name)

    string_table = bytearray((string_mask + 1) * 8)
    struct.pack_into(
        "<Q", string_table, (string_hash & string_mask) * 8, string_address
    )
    string_object = bytearray(24 + len(name))
    struct.pack_into("<Q", string_object, 0, 0)
    struct.pack_into("<I", string_object, 12, string_hash)
    struct.pack_into("<I", string_object, 16, len(name))
    struct.pack_into("<I", string_object, 20, padding)
    string_object[24:] = name
    table_header = bytearray(64)
    struct.pack_into("<Q", table_header, 40, node_address)
    struct.pack_into("<II", table_header, 48, 0, 7)
    nodes = bytearray(8 * 24)
    struct.pack_into(
        "<QQQ",
        nodes,
        (string_hash & 7) * 24,
        LuaJitReader.tagged_pointer(runtime_memory._LUA_INT_TAG, 7),
        LuaJitReader.tagged_pointer(
            runtime_memory._LUA_STRING_TAG, string_address
        ),
        0,
    )
    blobs = {
        string_table_address: bytes(string_table),
        string_address: bytes(string_object),
        table_address: bytes(table_header),
        node_address: bytes(nodes),
    }

    class Memory:
        def __init__(self, pid=123, process_start_ticks=456):
            self.pid = pid
            self.process_start_ticks = process_start_ticks
            self.read_count = 0

        def read(self, address, size, **_kwargs):
            self.read_count += 1
            for start, blob in blobs.items():
                if start <= address and address + size <= start + len(blob):
                    offset = address - start
                    return blob[offset : offset + size]
            raise AssertionError((address, size))

    memory = Memory()
    key_address = resolve_interned_lua_string(
        memory,
        string_table_address=string_table_address,
        string_mask=string_mask,
        string_seed=seed,
        name="package",
    )
    assert key_address == string_address
    assert LuaJitReader(memory).interned_string_field(
        table_address,
        "package",
        string_table_address=string_table_address,
        string_mask=string_mask,
        string_seed=seed,
    ) == 7
    restarted = Memory(pid=123, process_start_ticks=457)
    assert resolve_interned_lua_string(
        restarted,
        string_table_address=string_table_address,
        string_mask=string_mask,
        string_seed=seed,
        name="package",
    ) == string_address
    assert restarted.read_count > 0


def test_lua_jit_intern_lookup_supports_legacy_unkeyed_target_fork() -> None:
    name = b"package"
    string_hash = lua_jit_legacy_string_hash(name)
    assert string_hash == lua_jit_sparse_string_hash(0, name)
    string_table_address = 0x1000
    string_address = 0x2000
    string_mask = 7
    string_table = bytearray((string_mask + 1) * 8)
    struct.pack_into(
        "<Q", string_table, (string_hash & string_mask) * 8, string_address
    )
    string_object = bytearray(24 + len(name))
    struct.pack_into("<I", string_object, 12, string_hash)
    struct.pack_into("<I", string_object, 16, len(name))
    struct.pack_into("<I", string_object, 20, 3)
    string_object[24:] = name
    blobs = {
        string_table_address: bytes(string_table),
        string_address: bytes(string_object),
    }

    class Memory:
        pid = 123
        process_start_ticks = 456

        def read(self, address, size, **_kwargs):
            for start, blob in blobs.items():
                if start <= address and address + size <= start + len(blob):
                    offset = address - start
                    return blob[offset : offset + size]
            raise AssertionError((address, size))

    assert resolve_interned_lua_string(
        Memory(),
        string_table_address=string_table_address,
        string_mask=string_mask,
        string_seed=0xDEADBEEFCAFEBABE,
        name="package",
    ) == string_address


def test_lua_jit_table_lookup_supports_legacy_stored_hash_bucket() -> None:
    name = b"package"
    string_hash = lua_jit_legacy_string_hash(name)
    string_address = 0x2000
    table_address = 0x3000
    node_address = 0x4000
    hash_mask = 7
    sid = (string_hash + 1) & 0xFFFFFFFF
    assert (sid & hash_mask) != (string_hash & hash_mask)

    string_object = bytearray(24 + len(name))
    struct.pack_into("<I", string_object, 12, string_hash)
    struct.pack_into("<I", string_object, 16, len(name))
    struct.pack_into("<I", string_object, 20, sid)
    string_object[24:] = name
    table_header = bytearray(64)
    struct.pack_into("<Q", table_header, 40, node_address)
    struct.pack_into("<II", table_header, 48, 0, hash_mask)
    nodes = bytearray((hash_mask + 1) * 24)
    struct.pack_into(
        "<QQQ",
        nodes,
        (string_hash & hash_mask) * 24,
        LuaJitReader.tagged_pointer(runtime_memory._LUA_INT_TAG, 11),
        LuaJitReader.tagged_pointer(runtime_memory._LUA_STRING_TAG, string_address),
        0,
    )
    blobs = {
        string_address: bytes(string_object),
        table_address: bytes(table_header),
        node_address: bytes(nodes),
    }

    class Memory:
        def read(self, address, size, **_kwargs):
            for start, blob in blobs.items():
                if start <= address and address + size <= start + len(blob):
                    offset = address - start
                    return blob[offset : offset + size]
            raise AssertionError((address, size))

    assert LuaJitReader(Memory()).hashed_string_field(
        table_address,
        key_address=string_address,
        expected_name="package",
    ) == 11


def test_lua_closure_upvalues_follow_only_declared_uvptrs() -> None:
    function_address = 0x1000
    environment_address = 0x2000
    pc_address = 0x3000
    upvalue_address = 0x4000
    value_address = upvalue_address + 16
    table_address = 0x5000

    function = bytearray(48)
    function[10] = 0
    function[11] = 1
    struct.pack_into("<Q", function, 16, environment_address)
    struct.pack_into("<Q", function, 32, pc_address)
    struct.pack_into("<Q", function, 40, upvalue_address)
    upvalue = bytearray(40)
    upvalue[10] = 1
    struct.pack_into(
        "<Q",
        upvalue,
        16,
        LuaJitReader.tagged_pointer(runtime_memory._LUA_TABLE_TAG, table_address),
    )
    struct.pack_into("<Q", upvalue, 32, value_address)
    blobs = {function_address: bytes(function), upvalue_address: bytes(upvalue)}

    class Memory:
        def read(self, address, size, **_kwargs):
            for start, blob in blobs.items():
                if start <= address and address + size <= start + len(blob):
                    offset = address - start
                    return blob[offset : offset + size]
            raise AssertionError((address, size))

    assert LuaJitReader(Memory()).lua_closure_upvalues(function_address) == {
        "environment": environment_address,
        "pc": pc_address,
        "upvalue_addresses": [upvalue_address],
        "upvalues": [LuaRef("table", table_address)],
    }


def test_metatable_index_field_uses_two_exact_string_keys(monkeypatch) -> None:
    object_address = 0x1000
    metatable_address = 0x2000
    index_address = 0x3000
    object_header = bytearray(40)
    struct.pack_into("<Q", object_header, 32, metatable_address)

    class Memory:
        def read(self, address, size, **_kwargs):
            assert (address, size) == (object_address, 40)
            return bytes(object_header)

    reader = LuaJitReader(Memory())
    calls = []

    def exact_field(address, name, **_kwargs):
        calls.append((address, name))
        if (address, name) == (metatable_address, "__index"):
            return LuaRef("table", index_address)
        if (address, name) == (index_address, "Inst_get"):
            return LuaRef("function", 0x4000)
        raise AssertionError((address, name))

    monkeypatch.setattr(reader, "interned_string_field", exact_field)
    assert reader.metatable_index_string_field(
        object_address,
        "Inst_get",
        string_table_address=0x5000,
        string_mask=7,
        string_seed=0,
    ) == LuaRef("function", 0x4000)
    assert calls == [
        (metatable_address, "__index"),
        (index_address, "Inst_get"),
    ]


def test_lua_jit_intern_state_uses_target_gc64_offsets() -> None:
    state_address = 0x1000
    global_address = 0x2000
    state = bytearray(24)
    struct.pack_into("<Q", state, 0x10, global_address)
    global_fragment = bytearray(0xB8)
    struct.pack_into("<Q", global_fragment, 0, 0x3000)
    struct.pack_into("<I", global_fragment, 8, 0x7FFF)
    struct.pack_into("<I", global_fragment, 12, 1000)
    # Poison the previously guessed +0x98 layout.  Fixed target ABI must not
    # inspect or choose this plausible-looking false candidate.
    struct.pack_into("<Q", global_fragment, 0x98, 0x5000)
    struct.pack_into("<I", global_fragment, 0xA0, 0x3FFF)
    struct.pack_into("<I", global_fragment, 0xA4, 900)
    blobs = {
        state_address: bytes(state),
        global_address: bytes(global_fragment),
    }

    class Memory:
        def read(self, address, size, **_kwargs):
            for start, blob in blobs.items():
                if start <= address and address + size <= start + len(blob):
                    offset = address - start
                    return blob[offset : offset + size]
            raise AssertionError((address, size))

        def readable_region(self, address, size):
            return object() if address == 0x3000 and size == 0x40000 else None

    assert lua_jit_intern_state(Memory(), state_address) == (
        global_address,
        0x3000,
        0x7FFF,
        0,
    )


def test_cached_process_discovery_reuses_identity_and_maps(monkeypatch):
    region = MemoryRegion(
        start=0x10000,
        end=0x30000,
        permissions="rw-p",
    )
    cached = (
        runtime_memory.time.monotonic(),
        123,
        456,
        "127.0.0.1:16384",
        (region,),
    )
    monkeypatch.setattr(runtime_memory, "_process_cache", cached)
    validation_calls = []

    def fake_shell(command, *, timeout_s, preferred_serials=None):
        validation_calls.append((command, timeout_s, preferred_serials))
        if command.startswith("pidof "):
            return "123", {"adb_serial": "127.0.0.1:16384"}
        if command == "cat /proc/123/stat":
            fields = ["S", *(["0"] * 18), "456"]
            return f"123 (com.frxx) {' '.join(fields)}", {}
        raise AssertionError(command)

    monkeypatch.setattr(
        runtime_memory.mumu_control,
        "_run_mumu_adb_shell_text",
        fake_shell,
    )
    monkeypatch.setattr(
        MumuProcessMemory,
        "discover",
        classmethod(
            lambda _cls: pytest.fail("fresh process discovery should be skipped")
        ),
    )

    memory = MumuProcessMemory.discover_cached()

    assert memory.pid == 123
    assert memory.process_start_ticks == 456
    assert memory.adb_serial == "127.0.0.1:16384"
    assert memory.regions == (region,)
    assert memory._read_cache == {}
    assert [item[0] for item in validation_calls] == [
        f"pidof {runtime_memory.mumu_control.FANXIU_ANDROID_PACKAGE}",
        "cat /proc/123/stat",
    ]


def test_small_read_page_prefetch_batches_fresh_pages(monkeypatch):
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="127.0.0.1:16384",
        regions=(
            MemoryRegion(
                start=0x10000,
                end=0x30000,
                permissions="rw-p",
            ),
        ),
    )
    calls = []

    def fake_shell(command, *, timeout_s):
        calls.append((command, timeout_s))
        return b"A" * 0x10000 + b"B" * 0x10000, {}

    monkeypatch.setattr(
        runtime_memory.mumu_control,
        "_mumu_adb_session_shell_bytes",
        fake_shell,
    )

    memory.prefetch_small_read_pages((0x10010, 0x10020, 0x20010))

    assert len(calls) == 1
    assert calls[0][0].count("dd if=/proc/123/mem") == 2
    assert memory.read(0x10010, 1) == b"A"
    assert memory.read(0x20010, 1) == b"B"
    assert len(calls) == 1


def test_small_read_page_prefetch_cache_is_snapshot_local(monkeypatch):
    region = MemoryRegion(start=0x10000, end=0x20000, permissions="rw-p")
    payloads = iter((b"A" * 0x10000, b"B" * 0x10000))
    calls = []

    def fake_shell(command, *, timeout_s):
        calls.append(command)
        return next(payloads), {}

    monkeypatch.setattr(
        runtime_memory.mumu_control,
        "_mumu_adb_session_shell_bytes",
        fake_shell,
    )
    first = MumuProcessMemory(
        pid=123, process_start_ticks=456, adb_serial="serial", regions=(region,)
    )
    second = MumuProcessMemory(
        pid=123, process_start_ticks=456, adb_serial="serial", regions=(region,)
    )

    first.prefetch_small_read_pages((0x10010,))
    second.prefetch_small_read_pages((0x10010,))

    assert first.read(0x10010, 1) == b"A"
    assert second.read(0x10010, 1) == b"B"
    assert len(calls) == 2


def test_cached_process_discovery_refreshes_after_game_restart(monkeypatch):
    region = MemoryRegion(
        start=0x10000,
        end=0x30000,
        permissions="rw-p",
    )
    cached = (
        runtime_memory.time.monotonic(),
        123,
        456,
        "127.0.0.1:16384",
        (region,),
    )
    monkeypatch.setattr(runtime_memory, "_process_cache", cached)
    monkeypatch.setattr(
        runtime_memory.mumu_control,
        "_run_mumu_adb_shell_text",
        lambda command, **_kwargs: (
            ("999", {"adb_serial": "127.0.0.1:16384"})
            if command.startswith("pidof ")
            else pytest.fail(f"unexpected validation command: {command}")
        ),
    )
    fresh = MumuProcessMemory(
        pid=999,
        process_start_ticks=789,
        adb_serial="127.0.0.1:16384",
        regions=[region],
    )
    monkeypatch.setattr(
        MumuProcessMemory,
        "discover",
        classmethod(lambda _cls: fresh),
    )

    assert MumuProcessMemory.discover_cached() is fresh


def test_strict_cached_process_discovery_never_falls_back(monkeypatch):
    monkeypatch.setattr(runtime_memory, "_process_cache", None)
    monkeypatch.setattr(
        MumuProcessMemory,
        "discover",
        classmethod(
            lambda _cls: pytest.fail("strict patrol should not discover process")
        ),
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="缓存尚未预热"):
        MumuProcessMemory.discover_cached(fallback_to_discovery=False)


def test_find_marker_regions_does_not_stop_at_first_source_copy(monkeypatch):
    preferred = [
        MemoryRegion(
            start=0x100000,
            end=0x900000,
            permissions="rw-p",
        )
    ]
    fallback = [
        MemoryRegion(
            start=0xA00000,
            end=0xB00000,
            permissions="rw-p",
        )
    ]
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[*preferred, *fallback],
    )
    calls: list[list[MemoryRegion]] = []

    def fake_find(batch, marker_text):
        calls.append(batch)
        region = batch[0]
        return [(region, 64)]

    monkeypatch.setattr(memory, "_find_marker_in_batch", fake_find)

    matches = memory.find_marker_regions(b"LuaRedbagMgr")

    assert len(calls) == 2
    assert matches == [(preferred[0], 64), (fallback[0], 64)]


def test_marker_scan_prioritizes_regions_near_live_cached_roots(monkeypatch):
    distant_preferred = MemoryRegion(
        start=0x100000,
        end=0x900000,
        permissions="rw-p",
    )
    nearby_fallback = MemoryRegion(
        start=0x20000000,
        end=0x20100000,
        permissions="rw-p",
    )
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[distant_preferred, nearby_fallback],
    )
    calls: list[list[MemoryRegion]] = []
    monkeypatch.setattr(
        runtime_memory,
        "_cached_runtime_root_anchors",
        lambda _memory: (nearby_fallback.start + 0x100,),
    )
    monkeypatch.setattr(
        memory,
        "_find_marker_in_batch",
        lambda batch, _marker: calls.append(batch) or [],
    )

    list(memory.iter_marker_region_batches(b"LuaXianLvMinesMgr", max_scan_bytes=1 * 1024 * 1024))

    assert calls == [[nearby_fallback]]


def test_data_table_discovery_skips_unreadable_candidate_region(monkeypatch):
    unreadable = MemoryRegion(
        start=0x100000,
        end=0x200000,
        permissions="rw-p",
    )
    readable = MemoryRegion(
        start=0x300000,
        end=0x400000,
        permissions="rw-p",
    )
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[unreadable, readable],
    )
    string_address = 0x500000
    key_node = readable.start + 0x100
    table_address = readable.start + 0x200
    region_bytes = bytearray(readable.size)
    struct.pack_into(
        "<Q",
        region_bytes,
        key_node - readable.start + 8,
        LuaJitReader.tagged_pointer(0xFFFFFFFB, string_address),
    )
    struct.pack_into(
        "<QII",
        region_bytes,
        table_address - readable.start + 40,
        key_node,
        0,
        0,
    )
    monkeypatch.setattr(
        runtime_memory,
        "_valid_lua_string_addresses",
        lambda *_args, **_kwargs: [string_address],
    )

    def fake_read_region(region):
        if region == unreadable:
            raise FanxiuRuntimeMemoryError("short read")
        return bytes(region_bytes)

    monkeypatch.setattr(memory, "read_region", fake_read_region)
    monkeypatch.setattr(
        LuaJitReader,
        "table",
        lambda _reader, address: (
            {"fields": {"V_AttackFatigueValue": 0, "V_MinesVoDic": {}}}
            if address == table_address
            else {"fields": {}}
        ),
    )

    roots = _discover_data_table_roots(
        memory,
        marker=b"V_AttackFatigueValue",
        required_fields=frozenset({"V_AttackFatigueValue", "V_MinesVoDic"}),
    )

    assert roots == [table_address]


def test_manager_root_cache_only_mode_never_starts_discovery(
    monkeypatch,
    tmp_path,
):
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.runtime_memory._cache_path",
        lambda _key: tmp_path / "missing.json",
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.runtime_memory._discover_manager_root",
        lambda *_args, **_kwargs: pytest.fail("cache-only patrol started discovery"),
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="缓存尚未预热"):
        resolve_manager_root(
            memory,
            manager_key="chat",
            marker=b"LuaRedbagMgr",
            required_methods=frozenset({"Inst_get"}),
            validate=lambda *_args: None,
            allow_discovery=False,
        )


def test_cross_region_manager_root_handles_relocated_hash_node(monkeypatch):
    string_region = MemoryRegion(
        start=0x100000,
        end=0x300000,
        permissions="rw-p",
    )
    table_region = MemoryRegion(
        start=0x400000,
        end=0x600000,
        permissions="rw-p",
    )
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[string_region, table_region],
    )
    string_address = string_region.start + 0x1000
    string_hash = 0
    key_node = table_region.start + 0x2000
    hash_mask = 3
    node_address = key_node - 2 * 24
    table_address = string_region.start + 0x3000
    root_address = table_region.start + 0x4000
    string_bytes = bytearray(string_region.size)
    table_bytes = bytearray(table_region.size)
    struct.pack_into("<I", string_bytes, string_address - string_region.start + 12, string_hash)
    tagged = LuaJitReader.tagged_pointer(0xFFFFFFFB, string_address)
    struct.pack_into("<Q", table_bytes, key_node - table_region.start + 8, tagged)
    struct.pack_into("<Q", string_bytes, table_address - string_region.start + 40, node_address)

    monkeypatch.setattr(
        memory,
        "read_region",
        lambda region: bytes(string_bytes if region == string_region else table_bytes),
    )

    def fake_read(address, size, **_kwargs):
        region = memory.readable_region(address, size)
        assert region is not None
        source = string_bytes if region == string_region else table_bytes
        return bytes(source[address - region.start : address - region.start + size])

    monkeypatch.setattr(memory, "read", fake_read)
    monkeypatch.setattr(
        LuaJitReader,
        "table",
        lambda _reader, address: {
            "node_address": node_address,
            "hash_mask": hash_mask,
            "fields": {
                "LuaUnionVenisMgr": LuaRef("function", 0x700000),
                "Inst_get": LuaRef("function", 0x700100),
                "_type_": LuaRef("table", root_address),
            },
        }
        if address == table_address
        else {},
    )
    validated: list[int] = []

    roots = list(
        _cross_region_manager_roots(
            memory,
            string_addresses=[string_address],
            required_methods=frozenset({"LuaUnionVenisMgr", "Inst_get"}),
            validate=lambda _reader, address: validated.append(address),
        )
    )

    assert roots == [root_address]
    assert validated == [root_address]


def test_cross_region_manager_root_retries_same_table_with_larger_hash_mask(
    monkeypatch,
):
    string_region = MemoryRegion(
        start=0x100000,
        end=0x300000,
        permissions="rw-p",
    )
    table_region = MemoryRegion(
        start=0x400000,
        end=0x600000,
        permissions="rw-p",
    )
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[string_region, table_region],
    )
    string_address = string_region.start + 0x1000
    string_hash = 4
    key_node = table_region.start + 0x2000
    hash_mask = 63
    node_address = key_node - 4 * 24
    table_address = string_region.start + 0x3000
    root_address = table_region.start + 0x4000
    string_bytes = bytearray(string_region.size)
    table_bytes = bytearray(table_region.size)
    struct.pack_into(
        "<I",
        string_bytes,
        string_address - string_region.start + 12,
        string_hash,
    )
    tagged = LuaJitReader.tagged_pointer(0xFFFFFFFB, string_address)
    struct.pack_into("<Q", table_bytes, key_node - table_region.start + 8, tagged)
    struct.pack_into(
        "<Q",
        string_bytes,
        table_address - string_region.start + 40,
        node_address,
    )
    monkeypatch.setattr(
        memory,
        "read_region",
        lambda region: bytes(
            string_bytes if region == string_region else table_bytes
        ),
    )

    def fake_read(address, size, **_kwargs):
        region = memory.readable_region(address, size)
        assert region is not None
        source = string_bytes if region == string_region else table_bytes
        return bytes(source[address - region.start : address - region.start + size])

    monkeypatch.setattr(memory, "read", fake_read)
    monkeypatch.setattr(
        LuaJitReader,
        "table",
        lambda _reader, address: {
            "node_address": node_address,
            "hash_mask": hash_mask,
            "fields": {
                "LuaXianLvMinesMgr": LuaRef("function", 0x700000),
                "Inst_get": LuaRef("function", 0x700100),
                "_type_": LuaRef("table", root_address),
            },
        }
        if address == table_address
        else {},
    )

    roots = list(
        _cross_region_manager_roots(
            memory,
            string_addresses=[string_address],
            required_methods=frozenset({"LuaXianLvMinesMgr", "Inst_get"}),
            validate=lambda _reader, _address: None,
        )
    )

    assert roots == [root_address]


def test_redpacket_partial_sources_are_not_reported_as_negative_success():
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )

    result = _aggregate_sources(
        {
            "npc": {
                "ok": True,
                "pending": False,
                "pending_count": 0,
            }
        },
        {"chat": "RedbagMgr 尚未加载"},
        memory=memory,
        started_at=0.0,
        manager_loads={
            "chat": {
                "ok": False,
                "reason": "版本指纹不匹配",
            }
        },
    )

    assert result["ok"] is False
    assert result["complete"] is False
    assert result["pending"] is False
    assert "不能判定" in result["reason"]
    assert result["manager_loads"]["chat"]["ok"] is False


def test_redpacket_snapshot_decodes_special_event_long_and_triggers_gui_deep_check(
    monkeypatch,
):
    class FakeReader:
        def __init__(self, _memory):
            pass

        def table(self, _address):
            return {"fields": data}

        def fields(self, value):
            return value if isinstance(value, dict) else {}

        def list_items(self, value):
            return list(value), len(value)

        def dictionary_fields(self, value):
            return dict(value)

        def long(self, value):
            return value[1] if isinstance(value, tuple) and value[0] == "long" else None

    now_ms = 1_800_000_000_000
    data = {
        "_RedBagList": [
            {"uid": 101, "id": 5022, "channel": 101, "subChannelId": 7,
             "endTimeStamp": ("long", now_ms - 1)},
            {"uid": 102, "id": 5022, "channel": 101, "subChannelId": 7,
             "endTimeStamp": ("long", now_ms + 1)},
        ],
        "_RedBagDetailDic": {},
        "_UserGrabRedBagDic": {},
        "_HasOverdueUidDic": {},
        "_BeLimitRedBagIdDic": {},
        "_ReceiveRedBagList": [],
        "_idIndependentMap": {},
        "_eventMap": {},
        "_MainUiRedBagShowList": [],
    }
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet.LuaJitReader",
        FakeReader,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet.time.time",
        lambda: now_ms / 1000,
    )
    memory = MumuProcessMemory(pid=1, process_start_ticks=2, adb_serial="test", regions=[])

    result = _redpacket_snapshot(memory, 0x1234, cache_hit=True)

    assert result["pending_count"] == 1
    assert [item["uid"] for item in result["items"]] == [102]
    assert result["items"][0]["trigger_candidate"] is True
    assert result["items"][0]["action_authorized"] is False
    assert result["semantic_complete"] is False
    assert [item["end_time_epoch_ms"] for item in result["special_event_items"]] == [
        now_ms - 1,
        now_ms + 1,
    ]
    assert [item["classification"] for item in result["special_event_items"]] == [
        "special_event_expired",
        "special_event_gui_deep_check_candidate",
    ]


def test_redpacket_snapshot_unknown_survivor_fails_semantics_closed(monkeypatch):
    class FakeReader:
        def __init__(self, _memory):
            pass

        def table(self, _address):
            return {"fields": data}

        def fields(self, value):
            return value if isinstance(value, dict) else {}

        def list_items(self, value):
            return list(value), len(value)

        def dictionary_fields(self, value):
            return dict(value)

        def long(self, _value):
            return None

    data = {
        "_RedBagList": [{"uid": 201, "id": 999999, "channel": 6, "subChannelId": 0}],
        "_RedBagDetailDic": {201: {}},
        "_UserGrabRedBagDic": {},
        "_HasOverdueUidDic": {},
        "_BeLimitRedBagIdDic": {},
        "_ReceiveRedBagList": [],
        "_idIndependentMap": {},
        "_eventMap": {},
        "_MainUiRedBagShowList": [],
    }
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet.LuaJitReader",
        FakeReader,
    )
    memory = MumuProcessMemory(pid=1, process_start_ticks=2, adb_serial="test", regions=[])

    result = _redpacket_snapshot(memory, 0x1234, cache_hit=True)

    assert result["complete"] is True
    assert result["semantic_complete"] is False
    assert result["pending_count"] == 1
    assert result["trigger_candidate_count"] == 1
    assert result["items"][0]["claimability"] == "unknown"
    assert result["items"][0]["action_authorized"] is False
    assert result["structural_items"][0]["id"] == 999999
    assert result["semantic_incomplete_reasons"] == [
        "redbag_config_or_condition_uncovered:999999"
    ]


def test_redpacket_rewarded_special_event_is_not_a_trigger_candidate(monkeypatch):
    class FakeReader:
        def __init__(self, _memory):
            pass

        def table(self, _address):
            return {"fields": data}

        def fields(self, value):
            return value if isinstance(value, dict) else {}

        def list_items(self, value):
            return list(value), len(value)

        def dictionary_fields(self, value):
            return dict(value)

        def long(self, value):
            return value[1] if isinstance(value, tuple) else None

    now_ms = 1_800_000_000_000
    data = {
        "_RedBagList": [{
            "uid": 301,
            "id": 5022,
            "channel": 101,
            "subChannelId": 7,
            "endTimeStamp": ("long", now_ms + 60_000),
        }],
        "_RedBagDetailDic": {301: {"isReward": True, "num": 10, "receiveNum": 1}},
        "_UserGrabRedBagDic": {301: True},
        "_HasOverdueUidDic": {},
        "_BeLimitRedBagIdDic": {},
        "_ReceiveRedBagList": [],
        "_idIndependentMap": {},
        "_eventMap": {},
        "_MainUiRedBagShowList": [],
    }
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet.LuaJitReader",
        FakeReader,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet.time.time",
        lambda: now_ms / 1000,
    )
    memory = MumuProcessMemory(pid=1, process_start_ticks=2, adb_serial="test", regions=[])

    result = _redpacket_snapshot(memory, 0x1234, cache_hit=True)

    assert result["pending_count"] == 0
    assert result["trigger_candidate_count"] == 0
    assert result["special_event_items"][0]["classification"] == "special_event_excluded"
    assert result["special_event_items"][0]["claimability"] == "definitively_excluded"
    assert set(result["special_event_items"][0]["exclusion_reasons"]) == {
        "server_rewarded",
        "detail_rewarded",
    }


def test_redpacket_snapshot_preserves_all_real_list_rows_and_triggers_gui_deep_check(
    monkeypatch,
):
    class FakeReader:
        def __init__(self, _memory):
            pass

        def table(self, _address):
            return {"fields": data}

        def fields(self, value):
            return value if isinstance(value, dict) else {}

        def list_items(self, value):
            return list(value or []), len(value or [])

        def dictionary_fields(self, value):
            return dict(value or {})

        def long(self, value):
            return value if isinstance(value, int) and value > 10_000 else None

    ids = [5022, 520, 521, 522, 523, 524, 5022]
    data = {
        "_RedBagList": [
            {
                "uid": 10_000 + index,
                "id": bag_id,
                "channel": 6 if bag_id != 5022 else 101,
                "subChannelId": 0,
                "endTimeStamp": 2_000_000_000_000,
            }
            for index, bag_id in enumerate(ids)
        ],
        "_RedBagDetailDic": {},
        "_UserGrabRedBagDic": {},
        "_HasOverdueUidDic": {},
        "_BeLimitRedBagIdDic": {},
        "_ReceiveRedBagList": [],
        "_idIndependentMap": {},
        "_eventMap": {},
        # This transient presentation queue is deliberately empty.
        "_MainUiRedBagShowList": [],
    }
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet.LuaJitReader",
        FakeReader,
    )
    memory = MumuProcessMemory(pid=1, process_start_ticks=2, adb_serial="test", regions=[])

    result = _redpacket_snapshot(memory, 0x1234, cache_hit=True)

    assert [item["id"] for item in result["structural_items"]] == ids
    assert result["decoded_count"] == result["declared_count"] == 7
    assert result["trigger_candidate_count"] == 7
    assert result["pending_count"] == 7
    assert result["main_ui_queue_count"] == 0
    assert result["trigger_complete"] is True
    assert result["claimability_complete"] is False
    assert all(item["action_authorized"] is False for item in result["structural_items"])
    assert all(
        item["trigger_candidate"]
        for item in result["structural_items"]
    )
    assert all(
        item["claimability"] == "visual_deep_check_required"
        for item in result["structural_items"]
        if item["id"] == 5022
    )


def test_redpacket_snapshot_keeps_definitive_exclusions_as_structural_facts(
    monkeypatch,
):
    class FakeReader:
        def __init__(self, _memory):
            pass

        def table(self, _address):
            return {"fields": data}

        def fields(self, value):
            return value if isinstance(value, dict) else {}

        def list_items(self, value):
            return list(value or []), len(value or [])

        def dictionary_fields(self, value):
            return dict(value or {})

        def long(self, _value):
            return None

    data = {
        "_RedBagList": [
            {"uid": 1, "id": 520, "channel": 6, "subChannelId": 0},
            {"uid": 2, "id": 521, "channel": 6, "subChannelId": 0},
            {"uid": 3, "id": 522, "channel": 6, "subChannelId": 0},
        ],
        "_RedBagDetailDic": {3: {"num": 10, "receiveNum": 10}},
        "_UserGrabRedBagDic": {1: True},
        "_HasOverdueUidDic": {2: True},
        "_BeLimitRedBagIdDic": {},
        "_ReceiveRedBagList": [],
        "_idIndependentMap": {},
        "_eventMap": {},
        "_MainUiRedBagShowList": [],
    }
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet.LuaJitReader",
        FakeReader,
    )
    memory = MumuProcessMemory(pid=1, process_start_ticks=2, adb_serial="test", regions=[])

    result = _redpacket_snapshot(memory, 0x1234, cache_hit=True)

    assert len(result["structural_items"]) == 3
    assert result["pending_count"] == 0
    assert result["claimability_complete"] is True
    by_uid = {item["uid"]: item for item in result["structural_items"]}
    assert by_uid[1]["exclusion_reasons"] == ["server_rewarded"]
    assert by_uid[2]["exclusion_reasons"] == ["server_overdue"]
    assert by_uid[3]["exclusion_reasons"] == ["detail_full"]
    assert all(
        item["claimability"] == "definitively_excluded"
        for item in by_uid.values()
    )


def test_redpacket_snapshot_exposes_receive_queue_transition_as_incomplete_positive(
    monkeypatch,
):
    class FakeReader:
        def __init__(self, _memory):
            pass

        def table(self, _address):
            return {"fields": data}

        def fields(self, value):
            return value if isinstance(value, dict) else {}

        def list_items(self, value):
            return list(value or []), len(value or [])

        def dictionary_fields(self, value):
            return dict(value or {})

        def long(self, _value):
            return None

    data = {
        "_RedBagList": [],
        "_RedBagDetailDic": {},
        "_UserGrabRedBagDic": {},
        "_HasOverdueUidDic": {},
        "_BeLimitRedBagIdDic": {},
        "_ReceiveRedBagList": [{"uid": 88}],
        "_idIndependentMap": {},
        "_eventMap": {},
        "_MainUiRedBagShowList": [],
    }
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet.LuaJitReader",
        FakeReader,
    )
    memory = MumuProcessMemory(pid=1, process_start_ticks=2, adb_serial="test", regions=[])

    result = _redpacket_snapshot(memory, 0x1234, cache_hit=True)

    assert result["pending"] is True
    assert result["pending_count"] == 1
    assert result["receive_queue_in_transition"] is True
    assert result["semantic_complete"] is False
    assert result["action_authorized"] is False
    assert result["semantic_incomplete_reasons"] == [
        "receive_redbag_queue_in_transition:1"
    ]


def test_redpacket_snapshot_state_evolution_never_drops_structural_rows(
    monkeypatch,
):
    class FakeReader:
        def __init__(self, _memory):
            pass

        def table(self, _address):
            return {"fields": current["data"]}

        def fields(self, value):
            return value if isinstance(value, dict) else {}

        def list_items(self, value):
            return list(value or []), len(value or [])

        def dictionary_fields(self, value):
            return dict(value or {})

        def long(self, _value):
            return None

    base = {
        "_RedBagList": [{"uid": 1, "id": 523, "channel": 6, "subChannelId": 0}],
        "_RedBagDetailDic": {1: {}},
        "_UserGrabRedBagDic": {},
        "_HasOverdueUidDic": {},
        "_BeLimitRedBagIdDic": {},
        "_ReceiveRedBagList": [],
        "_idIndependentMap": {},
        "_eventMap": {},
        "_MainUiRedBagShowList": [],
    }
    current = {"data": base}
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet.LuaJitReader",
        FakeReader,
    )
    memory = MumuProcessMemory(pid=1, process_start_ticks=2, adb_serial="test", regions=[])

    states = []
    for changes in (
        {},
        {"_UserGrabRedBagDic": {1: True}},
        {"_HasOverdueUidDic": {1: True}},
        {"_RedBagDetailDic": {1: {"num": 10, "receiveNum": 10}}},
        {"_RedBagList": []},
    ):
        current["data"] = {**base, **changes}
        states.append(_redpacket_snapshot(memory, 0x1234, cache_hit=True))

    assert [state["pending_count"] for state in states] == [1, 0, 0, 0, 0]
    assert [len(state["structural_items"]) for state in states] == [1, 1, 1, 1, 0]


def test_redpacket_root_prefers_exact_lua_global(monkeypatch):
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )
    calls = []
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet._main_lua_state_address",
        lambda value: calls.append(("state", value)) or 0x1000,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet.resolve_lua_global_manager_root",
        lambda *args, **kwargs: calls.append(("global", kwargs))
        or (0x2000, True, 0x3000),
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet.resolve_manager_root",
        lambda *args, **kwargs: pytest.fail("global success must not scan marker roots"),
    )

    result = _resolve_redbag_root(memory, allow_discovery=True)

    assert result == (0x2000, True, "lua_global")
    global_kwargs = next(value for kind, value in calls if kind == "global")
    assert global_kwargs["state_address"] == 0x1000
    assert global_kwargs["global_name"] == "RedbagMgr"
    assert global_kwargs["manager_key"] == "chat"


def test_redpacket_root_uses_bounded_marker_fallback_when_global_is_absent(monkeypatch):
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet._main_lua_state_address",
        lambda _memory: 0x1000,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet.resolve_lua_global_manager_root",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("global missing", code="manager_not_found")
        ),
    )
    marker_calls = []
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet.resolve_manager_root",
        lambda *args, **kwargs: marker_calls.append(kwargs) or (0x4000, False),
    )

    result = _resolve_redbag_root(memory, allow_discovery=True)

    assert result == (0x4000, False, "constructor_marker")
    assert marker_calls[0]["allow_discovery"] is True
    assert marker_calls[0]["marker"] == b"LuaRedbagMgr"


def test_redpacket_root_preserves_typed_data_not_loaded_without_marker_scan(monkeypatch):
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet._main_lua_state_address",
        lambda _memory: 0x1000,
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet.resolve_lua_global_manager_root",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError(
                "RedbagMgr RedbagData 尚未初始化",
                code="data_not_loaded",
            )
        ),
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet.resolve_manager_root",
        lambda *args, **kwargs: pytest.fail("data_not_loaded must not scan marker roots"),
    )

    with pytest.raises(FanxiuRuntimeMemoryError) as captured:
        _resolve_redbag_root(memory, allow_discovery=True)

    assert captured.value.code == "data_not_loaded"


def test_redpacket_partial_result_preserves_source_error_code():
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )

    result = _aggregate_sources(
        {
            "npc": {
                "ok": True,
                "pending": False,
                "pending_count": 0,
            }
        },
        {"chat": "RedbagMgr RedbagData 尚未初始化"},
        memory=memory,
        started_at=0.0,
        source_error_codes={"chat": "data_not_loaded"},
    )

    assert result["unavailable_source_codes"] == {
        "chat": "data_not_loaded",
    }


def test_redpacket_runtime_items_are_grouped_with_live_alliance_context():
    groups = _pending_group_contexts(
        [
            {
                "uid": 1001,
                "id": 523,
                "channel": 6,
                "sub_channel_id": 0,
                "sender_name": "车老妖",
            },
            {
                "uid": 1002,
                "id": 524,
                "channel": 6,
                "sub_channel_id": 0,
                "sender_name": "车老妖",
            },
        ],
        {
            6: {
                "channel": 6,
                "channel_key": "ALLIANCE",
                "channel_label": "宗门",
                "target_name": "万妖谷",
            }
        },
    )

    assert groups == [{
        "channel": 6,
        "sub_channel_id": 0,
        "group_key": "6_0",
        "pending_count": 2,
        "packet_ids": [523, 524],
        "packet_uids": [1001, 1002],
        "sender_names": ["车老妖"],
        "channel_key": "ALLIANCE",
        "channel_label": "宗门",
        "target_name": "万妖谷",
        "display_name": "宗门 / 万妖谷",
    }]


def test_redpacket_patrol_reads_only_fresh_published_snapshot(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "snapshot.json"
    monkeypatch.setattr(
        "backend.core.fanxiu.instrumentation.red_packet._red_packet_snapshot_path",
        lambda: path,
    )
    _write_red_packet_snapshot({
        "ok": True,
        "available": True,
        "complete": True,
        "pending": True,
        "pending_count": 4,
        "pending_groups": [{"target_name": "万妖谷", "pending_count": 4}],
    })

    result = read_cached_red_packet_pending(max_age_seconds=75.0)

    assert result["pending_count"] == 4
    assert result["snapshot_cache_hit"] is True
    assert result["snapshot_age_seconds"] < 1.0


def test_redbag_runtime_loader_resolves_version_guarded_lua_addresses():
    il2cpp_base = 0x10000000
    tolua_base = 0x20000000
    heap_base = 0x30000000
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[
            MemoryRegion(
                start=il2cpp_base,
                end=il2cpp_base + 0x4000000,
                permissions="r--p",
                path="/app/lib/arm64/libil2cpp.so",
            ),
            MemoryRegion(
                start=tolua_base,
                end=tolua_base + 0x100000,
                permissions="r--p",
                path="/app/lib/arm64/libtolua.so",
            ),
            MemoryRegion(
                start=heap_base,
                end=heap_base + 0x10000,
                permissions="rw-p",
            ),
        ],
    )
    pointers = {
        il2cpp_base + 0x2FB3FE8: heap_base + 0x100,
        heap_base + 0x100: heap_base + 0x200,
        heap_base + 0x200 + 0xB8: heap_base + 0x300,
        heap_base + 0x300: heap_base + 0x400,
        heap_base + 0x400 + 0x10: heap_base + 0x500,
    }
    memory.read = lambda address, size, **_kwargs: (
        int(pointers[address]).to_bytes(8, "little")
        if size == 8
        else bytes(size)
    )

    addresses = _lua_addresses(memory)

    assert addresses == {
        "state": "0x30000500",
        "gettop": "0x2003f358",
        "loadstring": "0x2004b934",
        "pcall": "0x20041a04",
        "settop": "0x2003f36c",
    }


def test_native_bridge_loader_reuses_game_namespace_and_guards_trampolines():
    source = _agent_source()

    assert "NativeBridgeCreateNamespace" not in source
    assert 'Memory.allocUtf8String("classloader-namespace")' in source
    assert 'address.toString().toLowerCase().startsWith("0xdead")' in source
    assert "Process.findRangeByAddress(address)" in source
    assert "await Process.runOnThread" in source
    assert len(_IL2CPP_SHA256) == 64
    assert len(_TOLUA_SHA256) == 64
