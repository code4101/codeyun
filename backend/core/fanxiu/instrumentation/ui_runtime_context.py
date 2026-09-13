from __future__ import annotations

"""Process-bound, read-only roots shared by loaded UI projections."""

import logging
import struct
import threading
import time
from dataclasses import dataclass, field, replace
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
from backend.core.fanxiu.client import mumu_control


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
_LOGGER = logging.getLogger(__name__)

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
    # Only this observation owns decoded values. Never promote this cache to
    # module/process scope: a pooled panel can keep its address but change ID.
    _active_components: tuple[LuaRef, ...] | None = field(default=None, repr=False)
    _direct_keys: dict[str, int | None] = field(default_factory=dict, repr=False)

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
    except FanxiuRuntimeMemoryError as exc:
        # 读取失败不等于字段不存在。尤其窗口池新分配的表必须把映射错误
        # 交回 shared snapshot 的有界刷新，不能伪装成“窗口数量为零”。
        if exc.code in {'memory_address_unmapped', 'memory_read_failed'}:
            raise
        value = None
    except (KeyError, AttributeError):
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
    except FanxiuRuntimeMemoryError as exc:
        if exc.code in {'memory_address_unmapped', 'memory_read_failed'}:
            raise
        return None
    except AttributeError:
        return None


def has_ui_object_fields(
    ctx: UiRuntimeContext, address: int, names: Iterable[str]
) -> bool:
    """Test direct schema keys without materializing every candidate's table.

    Equivalent to testing key presence in ``reader.fields(table)`` for string
    keys: False/0 are present, nil is absent, and prototypes are not consulted.
    This only narrows candidates; callers must still prove uniqueness/current
    membership and validate business values. It is NOT a cross-observation
    target cache. Live acceptance must exercise ambiguous/pooled panels before
    adding such a cache, rather than trusting a formerly matched address.
    """
    for name in sorted(set(names)):
        if name not in ctx._direct_keys:
            key_address = ctx.binding.key_addresses.get(name)
            if key_address is None:
                try:
                    key_address = resolve_interned_lua_string(
                        ctx.memory,
                        string_table_address=ctx.binding.string_table_address,
                        string_mask=ctx.binding.string_mask,
                        string_seed=ctx.binding.string_seed, name=name,
                    )
                except FanxiuRuntimeMemoryError as exc:
                    if exc.code != "string_key_not_loaded":
                        raise
            ctx._direct_keys[name] = key_address
        key_address = ctx._direct_keys[name]
        if key_address is None or ctx.reader.hashed_string_field(
            address, key_address=key_address, expected_name=name
        ) is None:
            return False
    return True


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

    if ctx._active_components is not None:
        return ctx._active_components
    started = time.perf_counter()
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

    ctx._active_components = tuple(objects.values())
    ctx.timings["active_components"] = _elapsed(started)
    return ctx._active_components


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
    # Intern-table growth is not a process/root change. Refresh its metadata;
    # GCstr identities are independently checked by exact field reads.
    binding = replace(binding, string_table_address=string_table,
                      string_mask=string_mask, string_seed=string_seed)
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
    return _extend_context_keys(
        UiRuntimeContext(memory, reader, binding, timings, "hot"), required_keys
    )


def _validate_binding_fast(
    binding: UiRuntimeBinding,
    *,
    required_keys: frozenset[str],
    timings: dict[str, float],
) -> UiRuntimeContext:
    """Validate current root membership, then lazily refresh intern metadata.

    ``fast`` does not permit skipping membership checks or reusing mutable
    bytes. Live acceptance must cover close/reopen, tab changes, pooled object
    reuse and Lua-state replacement; an address still readable proves none of
    those identities. Do not remove these guards to improve a benchmark.
    """

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
    # Lazy object-field readers also consume intern metadata, even when their
    # explicit required_keys is empty. It must belong to this observation.
    _global, string_table, string_mask, string_seed = lua_jit_intern_state(
        memory, state_address
    )
    binding = replace(binding, string_table_address=string_table,
                      string_mask=string_mask, string_seed=string_seed)
    return _extend_context_keys(
        UiRuntimeContext(memory, reader, binding, timings, "hot-fast"), required_keys
    )


def _extend_context_keys(
    ctx: UiRuntimeContext, required_keys: frozenset[str]
) -> UiRuntimeContext:
    """Extend only the requesting projection; never discard valid UI roots."""
    missing = required_keys.difference(ctx.binding.key_addresses)
    if not missing:
        return ctx
    started = time.perf_counter()
    addresses = dict(ctx.binding.key_addresses)
    for name in sorted(missing):
        addresses[name] = resolve_interned_lua_string(
            ctx.memory, string_table_address=ctx.binding.string_table_address,
            string_mask=ctx.binding.string_mask,
            string_seed=ctx.binding.string_seed, name=name,
        )
    ctx.binding = replace(ctx.binding, key_addresses=addresses)
    ctx.timings["extend_keys"] = _elapsed(started)
    return ctx


def acquire_ui_runtime_context(required_keys: Iterable[str]) -> UiRuntimeContext:
    """Return a fresh reader over a validated process-bound logical root."""

    global _binding_cache
    keys = frozenset(str(key) for key in required_keys)
    timings: dict[str, float] = {}
    with _CACHE_LOCK:
        cached = _binding_cache
    if cached is not None:
        try:
            context = _validate_binding(cached, required_keys=keys, timings=timings)
        except FanxiuRuntimeMemoryError as exc:
            if exc.code == "string_key_not_loaded":
                raise
            with _CACHE_LOCK:
                if _binding_cache is cached:
                    _binding_cache = None
        else:
            with _CACHE_LOCK:
                if _binding_cache is cached:
                    _binding_cache = context.binding
            return context

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
            context = _validate_binding_fast(
                cached,
                required_keys=keys,
                timings=timings,
            )
        except FanxiuRuntimeMemoryError as exc:
            if exc.code == "string_key_not_loaded":
                raise
            with _CACHE_LOCK:
                if _binding_cache is cached:
                    _binding_cache = None
        else:
            with _CACHE_LOCK:
                if _binding_cache is cached:
                    _binding_cache = context.binding
            return context
    return acquire_ui_runtime_context(keys)


def read_ui_runtime_snapshot(
    required_keys: Iterable[str],
    reader_fn: Callable[[UiRuntimeContext], _SnapshotResult],
    *,
    fast: bool = False,
) -> _SnapshotResult:
    """Read current UI bytes, retrying a transient projection fault once.

    UIShowMgr keeps a stable registry while panels below it can be pooled and
    replaced during a page transition.  A reader must therefore never retain
    a child address across attempts without freshly proving parent membership
    and business identity. A retry drops observation bytes, not process roots;
    acquisition owns root validation/rebinding. Missing data is a terminal
    observation, not a reason to rediscover a process. Programming exceptions
    also propagate rather than being hidden by a cold retry.

    Runtime reads are not an atomic game snapshot. If a business operation
    needs a before/after guard, acquire a NEW context for the guard; rereading
    through the original reader only returns that observation's cached bytes.
    Live acceptance must measure hot and recovery paths separately and verify
    identity/value changes after real UI actions. Offline tests establish only
    retry/cache contracts, not panel correctness or game latency.
    """

    keys = frozenset(str(key) for key in required_keys)
    acquire = acquire_ui_runtime_context_fast if fast else acquire_ui_runtime_context
    for attempt in range(2):
        # Acquisition already owns one root recovery. Do not wrap that failure
        # in another retry and multiply expensive process discovery attempts.
        context = acquire(keys)
        started = time.perf_counter()
        failure = None
        try:
            return reader_fn(context)
        except FanxiuRuntimeMemoryError as exc:
            failure = exc
            if attempt or exc.code not in {
                "runtime_unavailable", "runtime_incomplete",
                "memory_address_unmapped", "memory_read_failed",
            }:
                raise
            # Component readers add business context with exception chaining.
            # Preserve the underlying recovery signal: retrying the same maps
            # cannot observe a panel allocated outside the cached regions.
            cause: BaseException | None = exc
            seen: set[int] = set()
            unmapped = False
            while cause is not None and id(cause) not in seen:
                seen.add(id(cause))
                if isinstance(cause, FanxiuRuntimeMemoryError) and cause.code == "memory_address_unmapped":
                    unmapped = True
                    break
                cause = cause.__cause__
            if unmapped:
                _refresh_context_maps(context)
        finally:
            _LOGGER.debug(
                "UI Runtime projection=%s attempt=%d mode=%s phases=%s "
                "projection_seconds=%.6f error=%s",
                getattr(reader_fn, "__qualname__", type(reader_fn).__name__),
                attempt + 1, context.cache_mode, context.timings,
                _elapsed(started), failure.code if failure else None,
            )
    raise AssertionError("unreachable")


def _refresh_context_maps(context: UiRuntimeContext) -> None:
    """Refresh newly allocated mappings without discarding valid roots.

    If this repeats in live use, investigate the first failing address/ABI;
    increasing retry counts or scanning the heap cannot establish freshness.
    """
    global _binding_cache
    memory = MumuProcessMemory.discover_cached(max_age_seconds=0.0)
    binding = context.binding
    with _CACHE_LOCK:
        if _binding_cache is not binding:
            return
        if (memory.pid, memory.process_start_ticks, memory.adb_serial) == (
            binding.pid, binding.process_start_ticks, binding.adb_serial
        ):
            _binding_cache = replace(binding, regions=tuple(memory.regions))
        else:
            _binding_cache = None


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
    "has_ui_object_fields",
    "read_ui_runtime_snapshot",
]
