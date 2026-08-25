from __future__ import annotations

"""Process-bound, read-only roots shared by loaded UI projections."""

import struct
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, TypeVar

from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MemoryRegion,
    MumuProcessMemory,
    as_int,
    lua_jit_intern_state,
    resolve_interned_lua_string,
    table_ref,
    _parse_process_start_ticks,
)
from backend.core.fanxiu.runtime import mumu_control


_ROOT_KEYS = frozenset(
    {
        "package",
        "loaded",
        "Core.UIManager.Manager.UIShowMgr",
        "__index",
        "inst",
        "V_M_compDic",
        "_dt_",
    }
)
_KNOWN_UI_KEYS = frozenset(
    {
        "m_panel",
        "configBtn",
        "ItemContent",
        "BackPackQuickItem",
        "isSelectedOpenBox",
        "isSelectedFenJie",
        "isSelectedMerge",
        "isSelectedUse",
        "isShow",
        "tabPanelGroup",
        "curTabIndex",
        "panelShowComps",
        "v_showList",
        "scrollview",
        "tablo",
        "tabNum",
        "ItemListScroll",
        "ItemInfoList",
        "ItemClassDic",
        "itemvo",
        "V_Data",
        "root",
        "id",
        "isEmpty",
    }
)
_CACHE_LOCK = threading.RLock()

# UIShowMgr's registry is the only trusted root set for a currently active
# window.  The object graph below it is deliberately *not* a general Lua-table
# graph: live UI evidence supports only ``m_panel`` and ``m_ChildCompList``.
# Keep this traversal bounded so a corrupted/stale projection cannot turn a
# read-only UI reader into a process heap walk.
_ACTIVE_UI_COMPONENT_MAX_LIST_ITEMS = 64
_ACTIVE_UI_COMPONENT_MAX_NODES = 256


@dataclass(frozen=True)
class UiRuntimeBinding:
    pid: int
    process_start_ticks: int
    adb_serial: str
    regions: tuple[MemoryRegion, ...]
    state_address: int
    environment_address: int
    string_table_address: int
    string_mask: int
    string_seed: int
    key_addresses: dict[str, int]
    manager_module_address: int
    manager_instance_address: int
    components_address: int
    component_storage_address: int


@dataclass
class UiRuntimeContext:
    memory: MumuProcessMemory
    reader: LuaJitReader
    binding: UiRuntimeBinding
    timings: dict[str, float]
    cache_mode: str

    def field(self, address: int, name: str) -> Any:
        key_address = self.binding.key_addresses.get(str(name))
        if key_address is None:
            raise FanxiuRuntimeMemoryError(f"UI Runtime 未缓存字符串键 {name}")
        return self.reader.hashed_string_field(
            int(address), key_address=key_address, expected_name=str(name)
        )

    def object_field(self, address: int, name: str) -> Any:
        """Read a direct field, then the object's table-backed ``__index``.

        UI config rows are frequently class-backed Lua objects whose payload
        table is empty while ``id/name/sort`` live on the prototype.  This is
        still an exact interned-key read; it does not scan or execute Lua.
        """

        value = self.field(address, name)
        if value is not None:
            return value
        return self.reader.metatable_index_string_field(
            int(address),
            str(name),
            string_table_address=self.binding.string_table_address,
            string_mask=self.binding.string_mask,
            string_seed=self.binding.string_seed,
        )


def read_ui_object_field(ctx: UiRuntimeContext, address: int, name: str) -> Any:
    """Read one exact field from an active UI object or its prototype.

    This is intentionally a *field* reader, not an address-discovery API.
    It keeps UI-specific strings lazy because a field used by one panel may
    not exist in another currently loaded panel.  Readers must obtain the
    address from :func:`active_ui_component_objects` or another independently
    validated business structure.
    """

    try:
        value = ctx.field(int(address), str(name))
    except (FanxiuRuntimeMemoryError, KeyError, AttributeError):
        value = None
    if value is not None:
        return value
    try:
        value = ctx.reader.interned_string_field(
            int(address),
            str(name),
            string_table_address=ctx.binding.string_table_address,
            string_mask=ctx.binding.string_mask,
            string_seed=ctx.binding.string_seed,
        )
        if value is not None:
            return value
        return ctx.reader.metatable_index_string_field(
            int(address),
            str(name),
            string_table_address=ctx.binding.string_table_address,
            string_mask=ctx.binding.string_mask,
            string_seed=ctx.binding.string_seed,
        )
    except (FanxiuRuntimeMemoryError, AttributeError):
        return None


def _required_active_component_link(
    ctx: UiRuntimeContext,
    address: int,
    name: str,
) -> Any:
    """Read one traversal link without masking an invalid runtime read.

    Unlike the public business-field helper, traversal must not silently turn
    a memory fault into an absent edge: doing that could present a partial
    component set as a complete one.  The two link keys are fixed and common
    to the verified UI schema, so a failure to resolve/read either is an
    explicit ``runtime_incomplete`` condition.
    """

    try:
        if str(name) in ctx.binding.key_addresses:
            value = ctx.field(int(address), str(name))
        else:
            value = ctx.reader.interned_string_field(
                int(address),
                str(name),
                string_table_address=ctx.binding.string_table_address,
                string_mask=ctx.binding.string_mask,
                string_seed=ctx.binding.string_seed,
            )
        if value is not None:
            return value
        return ctx.reader.metatable_index_string_field(
            int(address),
            str(name),
            string_table_address=ctx.binding.string_table_address,
            string_mask=ctx.binding.string_mask,
            string_seed=ctx.binding.string_seed,
        )
    except (FanxiuRuntimeMemoryError, AttributeError) as exc:
        raise FanxiuRuntimeMemoryError(
            f"活跃 UI 组件链接 {name} 无法完整读取",
            code="runtime_incomplete",
        ) from exc


def active_ui_component_objects(ctx: UiRuntimeContext) -> tuple[LuaRef, ...]:
    """Return the strict bounded active-component closure for ``UIShowMgr``.

    Roots are only the current values of ``UIShowMgr.V_M_compDic``.  From each
    discovered object the traversal follows exactly two live-verified links:
    ``m_panel`` (a single component/panel object) and that panel's immediate
    ``m_ChildCompList`` (a CList of component objects).  Addresses are
    globally de-duplicated; every child list is at most 64 and the whole
    closure at most 256 objects.  No arbitrary object field or Lua table is
    recursively read.

    Any malformed list, memory fault, or bound overflow raises
    ``FanxiuRuntimeMemoryError(code='runtime_incomplete')`` rather than
    returning a partial set.  Consumers still need independent business-level
    identity checks before treating any returned object as their panel.
    """

    try:
        root_values = ctx.reader.dictionary_fields(
            LuaRef("table", int(ctx.binding.components_address))
        ).values()
    except (FanxiuRuntimeMemoryError, AttributeError) as exc:
        raise FanxiuRuntimeMemoryError(
            "UIShowMgr 当前组件根集合无法完整读取",
            code="runtime_incomplete",
        ) from exc

    objects: dict[int, LuaRef] = {}
    def add(value: Any) -> None:
        component = table_ref(value)
        if component is None or component.address in objects:
            return
        if len(objects) >= _ACTIVE_UI_COMPONENT_MAX_NODES:
            raise FanxiuRuntimeMemoryError(
                "活跃 UI 组件树超过 256 个对象",
                code="runtime_incomplete",
            )
        objects[component.address] = component

    for value in root_values:
        add(value)
        # ``V_M_compDic`` normally stores a component, but the live activity
        # host can be wrapped in its own CList.  That wrapper is still part of
        # the registry root schema—not a recursive application-table walk.
        # Only a table explicitly declaring the CList pair ``_dt_`` + ``count``
        # is expanded, and its completeness/bounds remain strict.
        root = table_ref(value)
        if root is None:
            continue
        try:
            root_fields = ctx.reader.fields(root)
        except (FanxiuRuntimeMemoryError, AttributeError) as exc:
            raise FanxiuRuntimeMemoryError(
                "UIShowMgr 组件根无法完整读取",
                code="runtime_incomplete",
            ) from exc
        if table_ref(root_fields.get("_dt_")) is None:
            continue
        try:
            root_children, root_declared_count = ctx.reader.indexed_list_items(root)
        except (FanxiuRuntimeMemoryError, AttributeError) as exc:
            raise FanxiuRuntimeMemoryError(
                "UIShowMgr 组件根索引列表无法完整读取",
                code="runtime_incomplete",
            ) from exc
        if (
            (root_declared_count is not None and len(root_children) != root_declared_count)
            or (root_declared_count is not None and root_declared_count > _ACTIVE_UI_COMPONENT_MAX_LIST_ITEMS)
            or len(root_children) > _ACTIVE_UI_COMPONENT_MAX_LIST_ITEMS
        ):
            raise FanxiuRuntimeMemoryError(
                "UIShowMgr 组件根索引列表不完整或超过 64 个对象",
                code="runtime_incomplete",
            )
        for _index, child in root_children:
            add(child)

    # Resolve the host's panel first, then only that panel's immediate child
    # list.  Recursing through every child panel is not a more complete view:
    # the active registry also contains the permanent world HUD, whose own
    # descendants can exhaust a global cap before the target activity is
    # examined.  Consumers needing a tab's selected content must use that
    # host's verified ``tabPanelGroup`` schema, as Bothdraw does.
    root_components = tuple(objects.values())
    panels: list[LuaRef] = []
    for component in root_components:
        panel = table_ref(
            _required_active_component_link(ctx, component.address, "m_panel")
        )
        if panel is not None:
            panels.append(panel)
            add(panel)

    for panel in tuple(dict.fromkeys(panels)):
        child_list = table_ref(
            _required_active_component_link(ctx, panel.address, "m_ChildCompList")
        )
        if child_list is None:
            continue
        try:
            children, declared_count = ctx.reader.indexed_list_items(child_list)
        except (FanxiuRuntimeMemoryError, AttributeError) as exc:
            raise FanxiuRuntimeMemoryError(
                "活跃 UI 子组件列表无法完整读取",
                code="runtime_incomplete",
            ) from exc
        observed_count = len(children)
        if (
            (declared_count is not None and declared_count > _ACTIVE_UI_COMPONENT_MAX_LIST_ITEMS)
            or observed_count > _ACTIVE_UI_COMPONENT_MAX_LIST_ITEMS
        ):
            raise FanxiuRuntimeMemoryError(
                "活跃 UI 子组件列表超过 64 个对象",
                code="runtime_incomplete",
            )
        for _index, child in children:
            add(child)

    return tuple(objects.values())


_binding_cache: UiRuntimeBinding | None = None
_SnapshotResult = TypeVar("_SnapshotResult")


def _elapsed(started: float) -> float:
    return time.perf_counter() - started


def _fresh_memory(binding: UiRuntimeBinding) -> MumuProcessMemory:
    # Never reuse MumuProcessMemory._read_cache: UI fields and window membership
    # are mutable.  Only immutable process identity/maps and logical addresses
    # are retained.
    return MumuProcessMemory(
        pid=binding.pid,
        process_start_ticks=binding.process_start_ticks,
        adb_serial=binding.adb_serial,
        regions=binding.regions,
    )


def _root_field(
    reader: LuaJitReader,
    key_addresses: dict[str, int],
    address: int,
    name: str,
) -> Any:
    return reader.hashed_string_field(
        int(address),
        key_address=key_addresses[str(name)],
        expected_name=str(name),
    )


def _validate_process_identity(binding: UiRuntimeBinding) -> None:
    stat_bytes, stat_meta = mumu_control._mumu_adb_session_shell_bytes(
        f"cat /proc/{binding.pid}/stat", timeout_s=3
    )
    current_serial = str(stat_meta.get("adb_serial") or "")
    current_start_ticks = _parse_process_start_ticks(
        stat_bytes.decode("utf-8", errors="replace")
    )
    if (
        current_serial != binding.adb_serial
        or current_start_ticks != binding.process_start_ticks
    ):
        raise FanxiuRuntimeMemoryError("凡修 PID/start_ticks 已变化")


def _build_binding(
    memory: MumuProcessMemory,
    *,
    required_keys: frozenset[str],
    timings: dict[str, float],
) -> UiRuntimeBinding:
    started = time.perf_counter()
    state_address = int(_lua_addresses(memory)["state"], 16)
    environment_address = struct.unpack(
        "<Q", memory.read(state_address + 72, 8)
    )[0]
    timings["lua_addresses"] = _elapsed(started)

    started = time.perf_counter()
    _global, string_table, string_mask, string_seed = lua_jit_intern_state(
        memory, state_address
    )
    timings["intern_state"] = _elapsed(started)

    started = time.perf_counter()
    key_addresses = {
        name: resolve_interned_lua_string(
            memory,
            string_table_address=string_table,
            string_mask=string_mask,
            string_seed=string_seed,
            name=name,
        )
        # Only resolve keys the caller actually consumes.  Interned Lua strings
        # are created lazily by the game, so treating unrelated known UI keys as
        # mandatory makes an otherwise loaded panel look unavailable.
        for name in sorted(_ROOT_KEYS | required_keys)
    }
    timings["string_keys"] = _elapsed(started)
    reader = LuaJitReader(memory)

    started = time.perf_counter()
    package = table_ref(_root_field(reader, key_addresses, environment_address, "package"))
    loaded = table_ref(_root_field(reader, key_addresses, package.address, "loaded")) if package else None
    if package is None or loaded is None:
        raise FanxiuRuntimeMemoryError("Lua package.loaded 尚未加载")
    timings["package_loaded"] = _elapsed(started)

    started = time.perf_counter()
    module = table_ref(
        _root_field(
            reader,
            key_addresses,
            loaded.address,
            "Core.UIManager.Manager.UIShowMgr",
        )
    )
    if module is None:
        raise FanxiuRuntimeMemoryError("UIShowMgr module 尚未加载")
    metatable_address = struct.unpack_from(
        "<Q", memory.read(module.address, 40), 32
    )[0]
    index = table_ref(
        _root_field(reader, key_addresses, metatable_address, "__index")
    ) if metatable_address else None
    instance = table_ref(
        _root_field(reader, key_addresses, index.address, "inst")
    ) if index else None
    components = table_ref(
        _root_field(reader, key_addresses, instance.address, "V_M_compDic")
    ) if instance else None
    storage = table_ref(
        _root_field(reader, key_addresses, components.address, "_dt_")
    ) if components else None
    if instance is None or components is None or storage is None:
        raise FanxiuRuntimeMemoryError("UIShowMgr 当前窗口字典尚未加载")
    timings["ui_show_mgr"] = _elapsed(started)
    return UiRuntimeBinding(
        pid=memory.pid,
        process_start_ticks=memory.process_start_ticks,
        adb_serial=memory.adb_serial,
        regions=tuple(memory.regions),
        state_address=state_address,
        environment_address=environment_address,
        string_table_address=string_table,
        string_mask=string_mask,
        string_seed=string_seed,
        key_addresses=key_addresses,
        manager_module_address=module.address,
        manager_instance_address=instance.address,
        components_address=components.address,
        component_storage_address=storage.address,
    )


def _validate_binding(
    binding: UiRuntimeBinding,
    *,
    required_keys: frozenset[str],
    timings: dict[str, float],
) -> UiRuntimeContext:
    if not required_keys.issubset(binding.key_addresses):
        raise FanxiuRuntimeMemoryError("UI Runtime 字符串键集合已扩展")
    memory = _fresh_memory(binding)
    reader = LuaJitReader(memory)

    started = time.perf_counter()
    _validate_process_identity(binding)
    timings["process_identity"] = _elapsed(started)

    started = time.perf_counter()
    state_address = int(_lua_addresses(memory)["state"], 16)
    if state_address != binding.state_address:
        raise FanxiuRuntimeMemoryError("Lua state/environment 已变化")
    environment_address = struct.unpack(
        "<Q", memory.read(state_address + 72, 8)
    )[0]
    if environment_address != binding.environment_address:
        raise FanxiuRuntimeMemoryError("Lua state/environment 已变化")
    timings["lua_addresses"] = _elapsed(started)

    started = time.perf_counter()
    _global, string_table, string_mask, string_seed = lua_jit_intern_state(
        memory, state_address
    )
    if (
        string_table != binding.string_table_address
        or string_mask != binding.string_mask
        or string_seed != binding.string_seed
    ):
        raise FanxiuRuntimeMemoryError("Lua intern state 已变化")
    timings["intern_state"] = _elapsed(started)

    started = time.perf_counter()
    package = table_ref(
        _root_field(reader, binding.key_addresses, environment_address, "package")
    )
    loaded = table_ref(
        _root_field(reader, binding.key_addresses, package.address, "loaded")
    ) if package else None
    timings["package_loaded"] = _elapsed(started)

    started = time.perf_counter()
    module = table_ref(
        _root_field(
            reader,
            binding.key_addresses,
            loaded.address,
            "Core.UIManager.Manager.UIShowMgr",
        )
    ) if loaded else None
    metatable_address = (
        struct.unpack_from("<Q", memory.read(module.address, 40), 32)[0]
        if module
        else 0
    )
    index = table_ref(
        _root_field(reader, binding.key_addresses, metatable_address, "__index")
    ) if metatable_address else None
    manager_instance = table_ref(
        _root_field(reader, binding.key_addresses, index.address, "inst")
    ) if index else None
    components = table_ref(
        _root_field(
            reader,
            binding.key_addresses,
            manager_instance.address,
            "V_M_compDic",
        )
    ) if manager_instance else None
    storage = table_ref(
        _root_field(reader, binding.key_addresses, components.address, "_dt_")
    ) if components else None
    if (
        module is None
        or module.address != binding.manager_module_address
        or manager_instance is None
        or manager_instance.address != binding.manager_instance_address
        or components is None
        or components.address != binding.components_address
        or storage is None
        or storage.address != binding.component_storage_address
    ):
        raise FanxiuRuntimeMemoryError("UIShowMgr/window dictionary identity 已变化")
    timings["ui_show_mgr"] = _elapsed(started)
    return UiRuntimeContext(memory, reader, binding, timings, "hot")


def _validate_binding_fast(
    binding: UiRuntimeBinding,
    *,
    required_keys: frozenset[str],
    timings: dict[str, float],
) -> UiRuntimeContext:
    """Validate immutable UI roots without rediscovering package.loaded."""

    if not required_keys.issubset(binding.key_addresses):
        raise FanxiuRuntimeMemoryError("UI Runtime 字符串键集合已扩展")
    memory = _fresh_memory(binding)
    reader = LuaJitReader(memory)
    started = time.perf_counter()
    _validate_process_identity(binding)
    timings["process_identity"] = _elapsed(started)

    started = time.perf_counter()
    state_address = int(_lua_addresses(memory)["state"], 16)
    if state_address != binding.state_address:
        raise FanxiuRuntimeMemoryError("Lua state/environment 已变化")
    environment_address = struct.unpack(
        "<Q", memory.read(state_address + 72, 8)
    )[0]
    if environment_address != binding.environment_address:
        raise FanxiuRuntimeMemoryError("Lua state/environment 已变化")
    timings["lua_state"] = _elapsed(started)

    started = time.perf_counter()
    package = table_ref(
        _root_field(reader, binding.key_addresses, environment_address, "package")
    )
    loaded = table_ref(
        _root_field(reader, binding.key_addresses, package.address, "loaded")
    ) if package else None
    current_module = table_ref(
        _root_field(
            reader,
            binding.key_addresses,
            loaded.address,
            "Core.UIManager.Manager.UIShowMgr",
        )
    ) if loaded else None
    if (
        current_module is None
        or current_module.address != binding.manager_module_address
    ):
        raise FanxiuRuntimeMemoryError("UIShowMgr module identity 已变化")
    metatable_address = struct.unpack_from(
        "<Q", memory.read(current_module.address, 40), 32
    )[0]
    index = table_ref(
        _root_field(reader, binding.key_addresses, metatable_address, "__index")
    ) if metatable_address else None
    instance = table_ref(
        _root_field(reader, binding.key_addresses, index.address, "inst")
    ) if index else None
    components = table_ref(
        _root_field(
            reader,
            binding.key_addresses,
            instance.address,
            "V_M_compDic",
        )
    ) if instance else None
    storage = table_ref(
        _root_field(reader, binding.key_addresses, components.address, "_dt_")
    ) if components else None
    if (
        instance is None
        or instance.address != binding.manager_instance_address
        or components is None
        or components.address != binding.components_address
        or storage is None
        or storage.address != binding.component_storage_address
    ):
        raise FanxiuRuntimeMemoryError("UIShowMgr/window dictionary identity 已变化")
    timings["ui_roots"] = _elapsed(started)
    return UiRuntimeContext(memory, reader, binding, timings, "hot-fast")


def acquire_ui_runtime_context(required_keys: Iterable[str]) -> UiRuntimeContext:
    """Return a fresh reader over a validated process-bound logical root."""

    global _binding_cache
    keys = frozenset(str(key) for key in required_keys)
    timings: dict[str, float] = {}
    with _CACHE_LOCK:
        cached = _binding_cache
    if cached is not None:
        try:
            return _validate_binding(cached, required_keys=keys, timings=timings)
        except Exception:
            with _CACHE_LOCK:
                if _binding_cache is cached:
                    _binding_cache = None

    started = time.perf_counter()
    memory = MumuProcessMemory.discover()
    timings["discover_memory"] = _elapsed(started)
    binding = _build_binding(memory, required_keys=keys, timings=timings)
    with _CACHE_LOCK:
        _binding_cache = binding
    return UiRuntimeContext(memory, LuaJitReader(memory), binding, timings, "cold")


def acquire_ui_runtime_context_fast(required_keys: Iterable[str]) -> UiRuntimeContext:
    """Use the process-bound binding fast path, cold-rebinding at most once."""

    global _binding_cache
    keys = frozenset(str(key) for key in required_keys)
    timings: dict[str, float] = {}
    with _CACHE_LOCK:
        cached = _binding_cache
    if cached is not None:
        try:
            return _validate_binding_fast(
                cached,
                required_keys=keys,
                timings=timings,
            )
        except Exception:
            with _CACHE_LOCK:
                if _binding_cache is cached:
                    _binding_cache = None
    return acquire_ui_runtime_context(keys)


def read_ui_runtime_snapshot(
    required_keys: Iterable[str],
    reader_fn: Callable[[UiRuntimeContext], _SnapshotResult],
    *,
    fast: bool = False,
) -> _SnapshotResult:
    """Read one coherent UI snapshot with one cold rebind after a memory fault.

    UIShowMgr keeps a stable registry while panels below it can be pooled and
    replaced during a page transition.  A reader must therefore never retain
    a child address across attempts.  This helper lets it rebuild the entire
    context once after an *in-memory* failure; a second failure is surfaced to
    the caller unchanged, preserving failure-closed semantics.
    """

    keys = frozenset(str(key) for key in required_keys)
    acquire = acquire_ui_runtime_context_fast if fast else acquire_ui_runtime_context
    try:
        return reader_fn(acquire(keys))
    except FanxiuRuntimeMemoryError:
        clear_ui_runtime_context_cache()
        # ``acquire_ui_runtime_context`` is deliberately used here even when
        # the first pass was fast: it refreshes maps and rebuilds all roots.
        return reader_fn(acquire_ui_runtime_context(keys))


def clear_ui_runtime_context_cache() -> None:
    global _binding_cache
    with _CACHE_LOCK:
        _binding_cache = None


__all__ = [
    "UiRuntimeBinding",
    "UiRuntimeContext",
    "active_ui_component_objects",
    "acquire_ui_runtime_context",
    "acquire_ui_runtime_context_fast",
    "clear_ui_runtime_context_cache",
    "read_ui_object_field",
    "read_ui_runtime_snapshot",
]
