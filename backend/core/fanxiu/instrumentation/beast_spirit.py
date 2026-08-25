from __future__ import annotations

"""Strictly read the already-loaded beast-soul inventory and configuration.

This adapter only walks the game's existing LuaJIT object graph through
``/proc/<pid>/mem``.  It never invokes a Lua method, initializes a manager, or
sends a game command.  All addresses are resolved per game-process identity.
"""

import struct
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    lua_jit_intern_state,
    manager_index_fields,
    resolve_lua_global_manager_root,
    table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    UiRuntimeContext,
    acquire_ui_runtime_context,
    acquire_ui_runtime_context_fast,
)


_MANAGER_METHODS = frozenset({"Inst_get"})
_CONFIG_NAMES = (
    "Pet.PetJadeAttr",
    "Pet.PetJadeBase",
    "Pet.PetJadeShape",
    "Pet.PetJadeSkill",
    "Pet.UpgradeProbability",
)
_BEAST_UI_KEYS = frozenset(
    {
        "m_panel",
        "tabPanelGroup",
        "curTabIndex",
        "panelShowComps",
        "v_showList",
        "scrollview",
        "ItemClassDic",
        "_dt_",
        "V_Data",
        "root",
        "id",
        "isEmpty",
    }
)
_BEAST_ORDER_CACHE_LOCK = threading.RLock()


@dataclass(frozen=True)
class _BeastOrderCache:
    pid: int
    process_start_ticks: int
    window_address: int
    window_component_address: int
    main_panel_address: int
    tab_group_address: int
    current_index: int
    panel_storage_address: int
    active_component_address: int
    panel_address: int
    show_list_address: int
    raw_signature: tuple[tuple[str, str], ...]
    item_ids: tuple[str | None, ...]


_beast_order_cache: _BeastOrderCache | None = None


def parse_beast_soul_shape(value: str) -> tuple[tuple[int, int], ...]:
    """Parse a PetJadeShape coordinate string into normalized zero-based cells."""

    cells: set[tuple[int, int]] = set()
    for row_spec in str(value or "").split(","):
        row_spec = row_spec.strip()
        if not row_spec:
            continue
        parts = row_spec.split("|", 1)
        if len(parts) != 2:
            raise ValueError(f"兽魂形状行格式错误：{row_spec}")
        try:
            row = int(parts[0])
            columns = [int(item) for item in parts[1].split("_") if item]
        except ValueError as exc:
            raise ValueError(f"兽魂形状坐标不是整数：{row_spec}") from exc
        if row <= 0 or not columns or any(column <= 0 for column in columns):
            raise ValueError(f"兽魂形状坐标必须从 1 开始：{row_spec}")
        cells.update((row - 1, column - 1) for column in columns)
    if not cells:
        raise ValueError("兽魂形状为空")
    min_row = min(row for row, _column in cells)
    min_column = min(column for _row, column in cells)
    return tuple(
        sorted((row - min_row, column - min_column) for row, column in cells)
    )


def synthesis_expected_material_cost(
    amount: int,
    success_probability: float,
    *,
    retained_on_failure: int = 1,
) -> float:
    """Expected same-level materials consumed per successful upgrade."""

    amount = int(amount)
    probability = float(success_probability)
    retained = int(retained_on_failure)
    if amount < 2:
        raise ValueError("每次合成材料数不能少于 2")
    if not 0 < probability <= 1:
        raise ValueError("合成成功率必须位于 (0, 1]")
    if not 0 <= retained <= amount:
        raise ValueError("失败保留数必须位于 [0, amount]")
    # A success consumes ``amount``.  Each expected failure consumes only the
    # non-retained materials; failures per success are (1-p)/p.
    return amount + ((1 - probability) / probability) * (amount - retained)


def beast_soul_bag_sort_key(item: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """Mirror ``BeastSpiritData.PartItemSortFuncNew`` for unequipped items.

    The main beast-soul bag intentionally omits equipped pieces.  Its remaining
    sort dimensions are new marker, favorite marker, level, score, and base ID.
    Stable item ID is not a game tie-breaker, so callers must treat equal keys
    as visually ambiguous instead of inventing an order.
    """

    return (
        -int(bool(item.get("is_new"))),
        -int(bool(item.get("favorite"))),
        -int(item.get("level") or 0),
        -int(item.get("score") or 0),
        int(item.get("base_id") or 0),
    )


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value)


def _row(reader: LuaJitReader, value: Any) -> list[Any]:
    if not isinstance(value, LuaRef) or value.kind != "table":
        return []
    return list(reader.table(value.address)["array"])


def _global_manager(
    memory: MumuProcessMemory,
    reader: LuaJitReader,
    *,
    global_name: str,
    manager_key: str,
) -> tuple[dict[Any, Any], int, bool]:
    def validate(candidate_reader: LuaJitReader, address: int) -> None:
        manager_index_fields(candidate_reader, address, _MANAGER_METHODS)

    root, cache_hit, _environment = resolve_lua_global_manager_root(
        memory,
        manager_key=manager_key,
        state_address=int(_lua_addresses(memory)["state"], 16),
        global_name=global_name,
        required_methods=_MANAGER_METHODS,
        validate=validate,
    )
    return manager_index_fields(reader, root, _MANAGER_METHODS), root, cache_hit


def _lua_long(reader: LuaJitReader, value: Any) -> int | None:
    return reader.long(value) if isinstance(value, LuaRef) else as_int(value)


def _truthy_id_map(reader: LuaJitReader, value: Any) -> set[int]:
    result: set[int] = set()
    for raw_id, raw_state in _fields(reader, value).items():
        if as_int(raw_state) != 1:
            continue
        try:
            result.add(int(str(raw_id)))
        except (TypeError, ValueError):
            continue
    return result


def _active_beast_bag_item_ids(
    memory: MumuProcessMemory,
    reader: LuaJitReader,
    *,
    expected_item_ids: set[str],
    context: UiRuntimeContext | None = None,
    timings: dict[str, float] | None = None,
    include_materialized: bool = True,
) -> tuple[list[str | None], dict[str, Any]] | None:
    """Read the active BeastSpiritSlotGridPanel.v_showList without invoking Lua."""

    try:
        if context is not None:
            memory = context.memory
            reader = context.reader
            field = context.field
            dictionary_storage = LuaRef(
                "table", context.binding.component_storage_address
            )
        else:
            state_address = int(_lua_addresses(memory)["state"], 16)
            environment_address = struct.unpack(
                "<Q", memory.read(state_address + 72, 8)
            )[0]
            _global, string_table, string_mask, string_seed = lua_jit_intern_state(
                memory, state_address
            )
            exact_kwargs = {
                "string_table_address": string_table,
                "string_mask": string_mask,
                "string_seed": string_seed,
            }

            def field(address: int, name: str) -> Any:
                return reader.interned_string_field(address, name, **exact_kwargs)

            package = table_ref(field(environment_address, "package"))
            loaded = table_ref(field(package.address, "loaded")) if package else None
            module = (
                table_ref(field(loaded.address, "Core.UIManager.Manager.UIShowMgr"))
                if loaded
                else None
            )
            instance = (
                table_ref(
                    reader.metatable_index_string_field(
                        module.address, "inst", **exact_kwargs
                    )
                )
                if module
                else None
            )
            component_dictionary = (
                table_ref(field(instance.address, "V_M_compDic")) if instance else None
            )
            dictionary_storage = (
                table_ref(field(component_dictionary.address, "_dt_"))
                if component_dictionary
                else None
            )
        if dictionary_storage is None:
            return None

        phase_started = time.perf_counter()
        dictionary_table = reader.table(dictionary_storage.address)
        window_values = [
            *dictionary_table["array"],
            *dictionary_table["fields"].values(),
        ]
        matching: dict[int, tuple[list[str | None], dict[str, Any]]] = {}
        window_count_seen = 0
        window_scan_seconds = time.perf_counter() - phase_started
        show_list_seconds = 0.0
        materialized_seconds = 0.0
        raw_signatures: dict[int, tuple[tuple[str, str], ...]] = {}

        def raw_signature(values: list[Any]) -> tuple[tuple[str, str], ...]:
            return tuple(
                (value.kind, f"0x{value.address:x}")
                if isinstance(value, LuaRef)
                else (type(value).__name__, repr(value))
                for value in values
            )

        def decode_order(
            show_list: LuaRef,
            *,
            known_values: list[Any] | None = None,
        ) -> list[str | None] | None:
            nonlocal show_list_seconds
            show_started = time.perf_counter()
            if known_values is None:
                values, count = reader.list_items(show_list)
            else:
                values, count = known_values, len(known_values)
            if count is None or count <= 0 or len(values) != count:
                show_list_seconds += time.perf_counter() - show_started
                return None
            raw_signatures[show_list.address] = raw_signature(values)
            item_refs = [table_ref(value) for value in values]
            if any(item_ref is None for item_ref in item_refs):
                return None
            if context is not None:
                prefetch = getattr(reader, "prefetch_hashed_string_fields", None)
                if callable(prefetch):
                    prefetch(
                        (item_ref.address for item_ref in item_refs if item_ref),
                        key_addresses=(
                            context.binding.key_addresses["isEmpty"],
                            context.binding.key_addresses["id"],
                        ),
                    )
            item_ids: list[str | None] = []
            for value, item_ref in zip(values, item_refs, strict=True):
                assert item_ref is not None
                if context is not None:
                    is_empty = field(item_ref.address, "isEmpty")
                    raw_id = field(item_ref.address, "id")
                else:
                    item = _fields(reader, value)
                    is_empty = item.get("isEmpty")
                    raw_id = item.get("id")
                if bool(is_empty):
                    item_ids.append(None)
                    continue
                item_id = _lua_long(reader, raw_id)
                if item_id is None:
                    show_list_seconds += time.perf_counter() - show_started
                    return None
                item_ids.append(str(item_id))
            show_list_seconds += time.perf_counter() - show_started
            present_ids = [item_id for item_id in item_ids if item_id is not None]
            if (
                not present_ids
                or len(set(present_ids)) != len(present_ids)
                or set(present_ids) != expected_item_ids
            ):
                return None
            return item_ids

        # Order-only validation is the hot path between every detail probe.
        # Validate process identity through UiRuntimeContext, active-window
        # membership through V_M_compDic, and panel->v_showList identity before
        # decoding the cached CList directly.  Any mismatch falls through to
        # exactly one cold window rebind below.
        global _beast_order_cache
        used_order_cache = False
        if not include_materialized and context is not None:
            with _BEAST_ORDER_CACHE_LOCK:
                cached = _beast_order_cache
            if cached is not None and (
                cached.pid,
                cached.process_start_ticks,
            ) == (memory.pid, memory.process_start_ticks):
                window_ref = next(
                    (
                        ref
                        for raw in window_values
                        if (ref := table_ref(raw)) is not None
                        and ref.address == cached.window_address
                    ),
                    None,
                )
                cached_windows, cached_window_count = (
                    reader.list_items(window_ref) if window_ref else ([], None)
                )
                current_window_component = (
                    table_ref(cached_windows[-1])
                    if cached_window_count is not None
                    and cached_window_count > 0
                    and len(cached_windows) == cached_window_count
                    else None
                )
                current_main_panel = (
                    table_ref(field(current_window_component.address, "m_panel"))
                    if current_window_component
                    else None
                )
                current_tab_group = (
                    table_ref(field(current_main_panel.address, "tabPanelGroup"))
                    if current_main_panel
                    else None
                )
                current_index = (
                    as_int(field(current_tab_group.address, "curTabIndex"))
                    if current_tab_group
                    else None
                )
                current_panel_components = (
                    table_ref(field(current_tab_group.address, "panelShowComps"))
                    if current_tab_group
                    else None
                )
                current_panel_storage = (
                    table_ref(field(current_panel_components.address, "_dt_"))
                    if current_panel_components
                    else None
                )
                current_active_component = None
                if current_panel_storage and current_index is not None:
                    current_storage = reader.table(current_panel_storage.address)
                    current_slot = current_index + 1
                    current_active_component = table_ref(
                        current_storage["array"][current_slot]
                        if current_slot < len(current_storage["array"])
                        else current_storage["fields"].get(current_slot)
                    )
                current_panel = (
                    table_ref(field(current_active_component.address, "m_panel"))
                    if current_active_component
                    else None
                )
                current_show = (
                    table_ref(field(current_panel.address, "v_showList"))
                    if current_panel
                    else None
                )
                identities_match = (
                    current_window_component is not None
                    and current_window_component.address == cached.window_component_address
                    and current_main_panel is not None
                    and current_main_panel.address == cached.main_panel_address
                    and current_tab_group is not None
                    and current_tab_group.address == cached.tab_group_address
                    and current_index == cached.current_index
                    and current_panel_storage is not None
                    and current_panel_storage.address == cached.panel_storage_address
                    and current_active_component is not None
                    and current_active_component.address == cached.active_component_address
                    and current_panel is not None
                    and current_panel.address == cached.panel_address
                    and current_show is not None
                    and current_show.address == cached.show_list_address
                )
                if identities_match:
                    raw_started = time.perf_counter()
                    current_values, current_count = reader.list_items(current_show)
                    current_raw_signature = (
                        raw_signature(current_values)
                        if current_count is not None
                        and current_count > 0
                        and len(current_values) == current_count
                        else None
                    )
                    show_list_seconds += time.perf_counter() - raw_started
                    fresh_ids = (
                        decode_order(current_show, known_values=current_values)
                        if current_raw_signature == cached.raw_signature
                        else None
                    )
                    if fresh_ids is not None:
                        cached_ids = fresh_ids
                        used_order_cache = True
                        if timings is not None:
                            timings["window_scan"] = 0.0
                            timings["v_show_list_decode"] = show_list_seconds
                            timings["materialized_read"] = 0.0
                            timings["window_projection"] = (
                                time.perf_counter() - phase_started
                            )
                            timings["view_cache_hit"] = 1.0
                        return cached_ids, {
                            "window_address": f"0x{cached.window_address:x}",
                            "panel_address": f"0x{cached.panel_address:x}",
                            "scrollview_address": None,
                            "show_list_address": f"0x{cached.show_list_address:x}",
                            "show_list_count": len(cached_ids),
                            "slot_count": len(cached_ids),
                            "item_count": sum(value is not None for value in cached_ids),
                            "materialized_bindings": [],
                        }
                with _BEAST_ORDER_CACHE_LOCK:
                    if _beast_order_cache == cached:
                        _beast_order_cache = None
        for window_value in window_values:
            scan_started = time.perf_counter()
            window_list = table_ref(window_value)
            if window_list is None:
                window_scan_seconds += time.perf_counter() - scan_started
                continue
            windows, window_count = reader.list_items(window_list)
            if window_count is None or window_count <= 0 or len(windows) != window_count:
                window_scan_seconds += time.perf_counter() - scan_started
                continue
            window_count_seen += 1
            window_component = table_ref(windows[-1])
            main_panel = (
                table_ref(field(window_component.address, "m_panel"))
                if window_component
                else None
            )
            tab_group = (
                table_ref(field(main_panel.address, "tabPanelGroup"))
                if main_panel
                else None
            )
            current_index = as_int(field(tab_group.address, "curTabIndex")) if tab_group else None
            panel_components = (
                table_ref(field(tab_group.address, "panelShowComps"))
                if tab_group
                else None
            )
            panel_storage = (
                table_ref(field(panel_components.address, "_dt_"))
                if panel_components
                else None
            )
            if current_index is None or panel_storage is None:
                window_scan_seconds += time.perf_counter() - scan_started
                continue
            storage = reader.table(panel_storage.address)
            slot = current_index + 1
            active_value = (
                storage["array"][slot]
                if slot < len(storage["array"])
                else storage["fields"].get(slot)
            )
            active_component = table_ref(active_value)
            panel = (
                table_ref(field(active_component.address, "m_panel"))
                if active_component
                else None
            )
            show_list = table_ref(field(panel.address, "v_showList")) if panel else None
            if show_list is None:
                window_scan_seconds += time.perf_counter() - scan_started
                continue
            window_scan_seconds += time.perf_counter() - scan_started
            item_ids = decode_order(show_list)
            if item_ids is None:
                continue
            present_ids = [item_id for item_id in item_ids if item_id is not None]
            materialized_started = time.perf_counter()
            scrollview = (
                table_ref(field(panel.address, "scrollview"))
                if include_materialized
                else None
            )
            materialized_bindings: list[dict[str, Any]] = []
            item_class_dictionary = (
                table_ref(field(scrollview.address, "ItemClassDic"))
                if scrollview
                else None
            )
            item_class_storage = (
                table_ref(field(item_class_dictionary.address, "_dt_"))
                if item_class_dictionary
                else None
            )
            if item_class_storage is not None:
                class_table = reader.table(item_class_storage.address)
                class_values = [
                    *class_table["array"],
                    *class_table["fields"].values(),
                ]
                seen_instances: set[int] = set()
                for class_value in class_values:
                    item_instance = table_ref(class_value)
                    if item_instance is None or item_instance.address in seen_instances:
                        continue
                    seen_instances.add(item_instance.address)
                    instance_fields = reader.string_fields(
                        item_instance.address,
                        frozenset({"V_Data", "root"}),
                    )
                    data = _fields(reader, instance_fields.get("V_Data"))
                    bound_id = _lua_long(reader, data.get("id"))
                    root = table_ref(instance_fields.get("root"))
                    materialized_bindings.append({
                        "instance_id": str(bound_id) if bound_id is not None else None,
                        "is_empty": bool(data.get("isEmpty")),
                        "root_address": f"0x{root.address:x}" if root else None,
                        "item_instance_address": f"0x{item_instance.address:x}",
                    })
            materialized_seconds += time.perf_counter() - materialized_started
            matching[show_list.address] = (item_ids, {
                "window_address": f"0x{window_list.address:x}",
                "window_component_address": f"0x{window_component.address:x}",
                "main_panel_address": f"0x{main_panel.address:x}",
                "tab_group_address": f"0x{tab_group.address:x}",
                "current_index": current_index,
                "panel_storage_address": f"0x{panel_storage.address:x}",
                "active_component_address": f"0x{active_component.address:x}",
                "panel_address": f"0x{panel.address:x}",
                "scrollview_address": (
                    f"0x{scrollview.address:x}" if scrollview else None
                ),
                "show_list_address": f"0x{show_list.address:x}",
                "show_list_count": len(item_ids),
                "slot_count": len(item_ids),
                "item_count": len(present_ids),
                "materialized_bindings": materialized_bindings,
            })
        if timings is not None:
            timings["window_scan"] = window_scan_seconds
            timings["v_show_list_decode"] = show_list_seconds
            timings["materialized_read"] = materialized_seconds
            timings["window_projection"] = time.perf_counter() - phase_started
            timings["window_candidates"] = float(window_count_seen)
            timings["view_cache_hit"] = float(used_order_cache)
        # Cached/inactive windows may retain a v_showList.  Accept only one
        # panel whose live IDs exactly match this coherent inventory snapshot.
        if len(matching) != 1:
            return None
        selected_ids, selected_evidence = next(iter(matching.values()))
        # A full snapshot is the strongest cache seed: it has just scanned all
        # currently loaded windows and proved that exactly one active panel
        # owns a complete v_showList for this inventory.  Seed the narrow
        # order cache here as well, so the first detail-close verification can
        # validate that exact window/tab/panel chain instead of cold-scanning
        # a transient closing window with a retained duplicate projection.
        # Every hot use still revalidates open membership and all identities,
        # then freshly decodes isEmpty/id for every slot.
        if (
            getattr(memory, "pid", None) is not None
            and getattr(memory, "process_start_ticks", None) is not None
        ):
            with _BEAST_ORDER_CACHE_LOCK:
                _beast_order_cache = _BeastOrderCache(
                    pid=memory.pid,
                    process_start_ticks=memory.process_start_ticks,
                    window_address=int(str(selected_evidence["window_address"]), 16),
                    window_component_address=int(
                        str(selected_evidence["window_component_address"]), 16
                    ),
                    main_panel_address=int(
                        str(selected_evidence["main_panel_address"]), 16
                    ),
                    tab_group_address=int(
                        str(selected_evidence["tab_group_address"]), 16
                    ),
                    current_index=int(selected_evidence["current_index"]),
                    panel_storage_address=int(
                        str(selected_evidence["panel_storage_address"]), 16
                    ),
                    active_component_address=int(
                        str(selected_evidence["active_component_address"]), 16
                    ),
                    panel_address=int(str(selected_evidence["panel_address"]), 16),
                    show_list_address=int(
                        str(selected_evidence["show_list_address"]), 16
                    ),
                    raw_signature=raw_signatures[
                        int(str(selected_evidence["show_list_address"]), 16)
                    ],
                    item_ids=tuple(selected_ids),
                )
        return selected_ids, selected_evidence
    except (FanxiuRuntimeMemoryError, KeyError, TypeError, ValueError, struct.error):
        return None


def read_active_beast_bag_projection(
    expected_item_ids: set[str],
    *,
    include_materialized: bool = True,
) -> dict[str, Any]:
    """Read only the loaded beast-bag UI order and materialized bindings.

    Unlike :func:`read_beast_spirit_snapshot`, this does not traverse the
    inventory managers, configuration tables, boards, or optimizer.  It is the
    bounded refresh primitive used while a target cell is being located.
    """

    started_at = time.perf_counter()
    context: UiRuntimeContext | None = None
    timings: dict[str, float] = {}
    try:
        context = (
            acquire_ui_runtime_context(_BEAST_UI_KEYS)
            if include_materialized
            else acquire_ui_runtime_context_fast(_BEAST_UI_KEYS)
        )
        timings.update(context.timings)
        projection_started = time.perf_counter()
        projection = _active_beast_bag_item_ids(
            context.memory,
            context.reader,
            expected_item_ids={str(item_id) for item_id in expected_item_ids},
            context=context,
            timings=timings,
            include_materialized=include_materialized,
        )
        timings["projection_total"] = time.perf_counter() - projection_started
        if projection is None:
            raise FanxiuRuntimeMemoryError(
                "active BeastSpiritSlotGridPanel projection is missing or ambiguous"
            )
        item_ids, evidence = projection
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory_ui_projection",
            "ui_bag_item_ids": item_ids,
            "ui_materialized_bindings": evidence.get(
                "materialized_bindings", []
            ),
            "elapsed_seconds": time.perf_counter() - started_at,
            "performance": {
                "cache_mode": context.cache_mode,
                "stages": timings,
            },
            "evidence": {
                "pid": context.memory.pid,
                "process_start_ticks": context.memory.process_start_ticks,
                "read_only": True,
                "active_ui_bag": evidence,
            },
        }
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
            "source": "runtime_memory_ui_projection",
            "reason": reason,
            "elapsed_seconds": time.perf_counter() - started_at,
            "performance": {
                "cache_mode": context.cache_mode if context else None,
                "stages": timings,
            },
            "evidence": {
                "pid": context.memory.pid if context else None,
                "process_start_ticks": (
                    context.memory.process_start_ticks if context else None
                ),
                "read_only": True,
            },
        }


def clear_beast_spirit_order_cache() -> None:
    global _beast_order_cache
    with _BEAST_ORDER_CACHE_LOCK:
        _beast_order_cache = None


def _apply_active_ui_bag_order(
    items: list[dict[str, Any]],
    item_ids: list[str | None] | None,
) -> tuple[bool, str | None]:
    """Attach exact UI indices only when v_showList covers the whole live bag."""

    bag_items = [item for item in items if not item["equipped"]]
    for item in items:
        item["ui_bag_index"] = None
    expected_ids = {str(item["item_id"]) for item in bag_items}
    ui_slots = list(item_ids or [])
    observed_ids = [item_id for item_id in ui_slots if item_id is not None]
    if not ui_slots or not observed_ids:
        return False, "active BeastSpiritSlotGridPanel.v_showList unavailable"
    if len(observed_ids) != len(set(observed_ids)):
        return False, "active v_showList contains duplicate item ids"
    if set(observed_ids) != expected_ids:
        return False, (
            "active v_showList does not exactly cover the unequipped inventory: "
            f"expected={len(expected_ids)}, observed={len(observed_ids)}"
        )
    index_by_id = {
        item_id: index
        for index, item_id in enumerate(ui_slots)
        if item_id is not None
    }
    for item in bag_items:
        item["ui_bag_index"] = index_by_id[str(item["item_id"])]
    return True, None


def _config_tables(
    reader: LuaJitReader,
    db_manager: dict[Any, Any],
) -> dict[str, dict[Any, Any]]:
    instance = _fields(reader, db_manager.get("inst"))
    config_wrapper = _fields(reader, instance.get("ConfigDic"))
    configs = _fields(reader, config_wrapper.get("_dt_"))
    result = {
        name: _fields(reader, configs.get(name))
        for name in _CONFIG_NAMES
    }
    missing = [name for name, table in result.items() if not table]
    if missing:
        raise FanxiuRuntimeMemoryError(
            f"兽魂配置尚未完整加载：{', '.join(missing)}"
        )
    return result


def _entry_snapshot(
    reader: LuaJitReader,
    value: Any,
    *,
    attr_rows: dict[Any, Any],
    skill_rows: dict[Any, Any],
) -> dict[str, Any] | None:
    entry = _fields(reader, value)
    skill_id = as_int(entry.get("skill")) or 0
    attr_config_id = as_int(entry.get("id")) or 0
    if skill_id:
        row = _row(reader, skill_rows.get(skill_id))
        if len(row) <= 8:
            raise FanxiuRuntimeMemoryError(f"兽魂技能配置缺失：{skill_id}")
        return {
            "kind": "skill",
            "config_id": skill_id,
            "name": str(row[3] or ""),
            "value": as_int(row[6]),
            "score": as_int(row[8]) or 0,
        }
    if attr_config_id:
        row = _row(reader, attr_rows.get(attr_config_id))
        if len(row) <= 8:
            raise FanxiuRuntimeMemoryError(f"兽魂属性配置缺失：{attr_config_id}")
        return {
            "kind": "attribute",
            "config_id": attr_config_id,
            "group": as_int(entry.get("group")),
            "attribute_id": as_int(entry.get("attrId")),
            "value": as_int(row[6]),
            # Main attributes deliberately have no score in the loaded table.
            "score": as_int(row[7]) or 0,
        }
    return None


def _entry_list(
    reader: LuaJitReader,
    value: Any,
    *,
    attr_rows: dict[Any, Any],
    skill_rows: dict[Any, Any],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for _position, raw_entry in sorted(
        _fields(reader, value).items(),
        key=lambda pair: str(pair[0]),
    ):
        entry = _entry_snapshot(
            reader,
            raw_entry,
            attr_rows=attr_rows,
            skill_rows=skill_rows,
        )
        if entry is not None:
            result.append(entry)
    return result


def _single_entry_list(
    reader: LuaJitReader,
    value: Any,
    *,
    attr_rows: dict[Any, Any],
    skill_rows: dict[Any, Any],
) -> list[dict[str, Any]]:
    """Normalize ext.mainEntry, which is one entry object rather than a list."""

    entry = _entry_snapshot(
        reader,
        value,
        attr_rows=attr_rows,
        skill_rows=skill_rows,
    )
    return [entry] if entry is not None else []


def _equipped_snapshot(
    reader: LuaJitReader,
    beast_data: dict[Any, Any],
) -> tuple[set[int], list[dict[str, Any]]]:
    equipped: set[int] = set()
    boards: list[dict[str, Any]] = []
    infos, _count = reader.list_items(beast_data.get("V_AllBeastSpiritInfoDic"))
    for raw_info in infos:
        info = _fields(reader, raw_info)
        server_data = _fields(reader, info.get("serverData"))
        jade_values, _jade_count = reader.list_items(server_data.get("jades"))
        jade_ids = [
            item_id
            for raw_id in jade_values
            if (item_id := _lua_long(reader, raw_id)) is not None
        ]
        equipped.update(jade_ids)
        shape = _fields(reader, server_data.get("shapeVO"))
        grid_values, _grid_count = reader.list_items(shape.get("grids"))
        cells: list[dict[str, Any]] = []
        for raw_cell in grid_values:
            cell = _fields(reader, raw_cell)
            raw_item_id = _lua_long(reader, cell.get("v")) or 0
            cells.append(
                {
                    "row": as_int(cell.get("r")),
                    "column": as_int(cell.get("c")),
                    # The live model uses integer 1 as its empty-cell sentinel.
                    # Only IDs also present in ``serverData.jades`` are occupied.
                    "item_id": raw_item_id if raw_item_id in equipped else 0,
                }
            )
        boards.append(
            {
                "soul_id": as_int(server_data.get("soulId")),
                "unlocked": bool(server_data.get("unlock")),
                "equipped_item_ids": [str(item_id) for item_id in jade_ids],
                "cells": [
                    {
                        **cell,
                        "item_id": str(cell["item_id"]) if cell["item_id"] else "",
                    }
                    for cell in cells
                ],
            }
        )
    return equipped, boards


def _snapshot(memory: MumuProcessMemory) -> dict[str, Any]:
    reader = LuaJitReader(memory)
    beast_manager, beast_root, beast_hit = _global_manager(
        memory,
        reader,
        global_name="BeastSpiritMgr",
        manager_key="beast-spirit-snapshot",
    )
    backpack_manager, backpack_root, backpack_hit = _global_manager(
        memory,
        reader,
        global_name="BackpackMgr",
        manager_key="beast-spirit-backpack-snapshot",
    )
    db_manager, db_root, db_hit = _global_manager(
        memory,
        reader,
        global_name="DBMgr",
        manager_key="beast-spirit-db-snapshot",
    )

    beast_instance = _fields(reader, beast_manager.get("inst"))
    beast_model = _fields(reader, beast_instance.get("Model"))
    beast_data = _fields(reader, beast_model.get("BeastSpiritData"))
    backpack_instance = _fields(reader, backpack_manager.get("inst"))
    backpack_model = _fields(reader, backpack_instance.get("Model"))
    backpack_data = _fields(reader, backpack_model.get("BackpackData"))
    configs = _config_tables(reader, db_manager)
    attr_rows = configs["Pet.PetJadeAttr"]
    skill_rows = configs["Pet.PetJadeSkill"]
    shape_rows = configs["Pet.PetJadeShape"]

    locked_ids = _truthy_id_map(reader, beast_instance.get("V_JadeLockedDic"))
    favorite_ids = _truthy_id_map(
        reader,
        beast_instance.get("V_JadeFavoritesDic"),
    )
    new_ids = _truthy_id_map(reader, beast_model.get("multipleNewRed"))
    excluded_values, _excluded_count = reader.list_items(
        beast_instance.get("V_ExcludesList")
    )
    excluded_ids = {
        item_id
        for raw_id in excluded_values
        if (item_id := _lua_long(reader, raw_id)) is not None
    }
    equipped_ids, boards = _equipped_snapshot(reader, beast_data)

    item_dictionary = _fields(reader, backpack_data.get("_BeastSpiritItemDic"))
    raw_items = _fields(reader, item_dictionary.get("_valueTable_"))
    if not raw_items:
        raise FanxiuRuntimeMemoryError("兽魂背包尚未加载")

    items: list[dict[str, Any]] = []
    for raw_item in raw_items.values():
        item = _fields(reader, raw_item)
        extension = _fields(reader, item.get("ext"))
        item_id = _lua_long(reader, item.get("id"))
        level = as_int(extension.get("level"))
        shape_id = as_int(extension.get("configId"))
        if item_id is None or level is None or shape_id is None:
            raise FanxiuRuntimeMemoryError("兽魂库存存在缺少身份或形状的条目")
        shape_row = _row(reader, shape_rows.get(shape_id))
        if len(shape_row) <= 3:
            raise FanxiuRuntimeMemoryError(f"兽魂形状配置缺失：{shape_id}")
        shape = parse_beast_soul_shape(shape_row[3])
        # Current item VOs expose the UI's basic-attribute rows as the plural
        # ``mainEntries`` table.  Older protocol fixtures used one
        # ``mainEntry`` object, so retain that as a strict compatibility path.
        if table_ref(extension.get("mainEntries")) is not None:
            main_entries = _entry_list(
                reader,
                extension.get("mainEntries"),
                attr_rows=attr_rows,
                skill_rows=skill_rows,
            )
        else:
            main_entries = _single_entry_list(
                reader,
                extension.get("mainEntry"),
                attr_rows=attr_rows,
                skill_rows=skill_rows,
            )
        vice_entries = _entry_list(
            reader,
            extension.get("viceEntries"),
            attr_rows=attr_rows,
            skill_rows=skill_rows,
        )
        items.append(
            {
                # Lua long IDs exceed JavaScript's safe integer range.  Keep
                # them as decimal strings at every JSON-facing boundary.
                "item_id": str(item_id),
                "base_id": as_int(item.get("baseId")),
                "level": level,
                "shape_id": shape_id,
                "shape": [list(cell) for cell in shape],
                "cell_count": len(shape),
                "runtime_cell": as_int(extension.get("cell")),
                "score": sum(entry["score"] for entry in main_entries + vice_entries),
                "main_entries": main_entries,
                "vice_entries": vice_entries,
                "equipped": item_id in equipped_ids,
                "locked": item_id in locked_ids,
                "favorite": item_id in favorite_ids,
                "is_new": item_id in new_ids,
                "excluded_from_quick_synthesis": item_id in excluded_ids,
            }
        )

    bag_items = sorted(
        (item for item in items if not item["equipped"]),
        key=beast_soul_bag_sort_key,
    )
    bag_key_counts = Counter(beast_soul_bag_sort_key(item) for item in bag_items)
    for bag_index, item in enumerate(bag_items):
        item["bag_index"] = bag_index
        item["bag_position_ambiguous"] = (
            bag_key_counts[beast_soul_bag_sort_key(item)] > 1
        )
    for item in items:
        if item["equipped"]:
            item["bag_index"] = None
            item["bag_position_ambiguous"] = False

    active_ui_projection = _active_beast_bag_item_ids(
        memory,
        reader,
        expected_item_ids={
            str(item["item_id"]) for item in items if not item["equipped"]
        },
    )
    ui_item_ids = active_ui_projection[0] if active_ui_projection else None
    ui_bag_complete, ui_bag_reason = _apply_active_ui_bag_order(items, ui_item_ids)

    items.sort(
        key=lambda item: (
            -item["score"],
            -item["level"],
            int(item["item_id"]),
        )
    )
    level_counts = Counter(item["level"] for item in items)
    complete = bool(items) and locked_ids == excluded_ids
    return {
        "ok": complete,
        "available": True,
        "complete": complete,
        "source": "runtime_memory",
        "protocol": (
            "BeastSpiritMgr.Model.BeastSpiritData + "
            "BackpackMgr.Model.BackpackData._BeastSpiritItemDic + "
            "DBMgr.ConfigDic"
        ),
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "inventory_count": len(items),
        "level_counts": dict(sorted(level_counts.items())),
        "equipped_count": len(equipped_ids),
        "locked_count": len(locked_ids),
        "favorite_count": len(favorite_ids),
        "new_count": len(new_ids),
        "quick_synthesis_excluded_count": len(excluded_ids),
        "lock_exclusion_consistent": locked_ids == excluded_ids,
        "ui_bag_available": active_ui_projection is not None,
        "ui_bag_complete": ui_bag_complete,
        "ui_bag_reason": ui_bag_reason,
        "ui_bag_item_ids": ui_item_ids or [],
        "ui_materialized_bindings": (
            active_ui_projection[1].get("materialized_bindings", [])
            if active_ui_projection
            else []
        ),
        "items": items,
        "boards": boards,
        "synthesis_options": [
            {
                "amount": amount,
                "success_probability": probability,
                "expected_cost_if_one_retained": synthesis_expected_material_cost(
                    amount,
                    probability,
                ),
                "expected_cost_if_none_retained": synthesis_expected_material_cost(
                    amount,
                    probability,
                    retained_on_failure=0,
                ),
            }
            for amount, probability in ((2, 0.55), (3, 0.70), (4, 0.85), (5, 1.0))
        ],
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "read_only": True,
            "roots": {
                "beast_spirit": f"0x{beast_root:x}",
                "backpack": f"0x{backpack_root:x}",
                "database": f"0x{db_root:x}",
            },
            "root_cache_hits": {
                "beast_spirit": beast_hit,
                "backpack": backpack_hit,
                "database": db_hit,
            },
            "active_ui_bag": active_ui_projection[1] if active_ui_projection else None,
        },
    }


def read_beast_spirit_snapshot() -> dict[str, Any]:
    """Return a coherent, strictly read-only beast-soul inventory snapshot."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        result = _snapshot(memory)
        result["elapsed_seconds"] = time.perf_counter() - started_at
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
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
                "read_only": True,
            },
        }


def diagnose_active_beast_scrollview_bindings() -> dict[str, Any]:
    """Inspect only the active panel's known scrollview and a field whitelist.

    This diagnostic never scans the heap and never invokes Lua.  Its sole
    purpose is to establish the deployed scrollview's materialized-item
    container before any binding is admitted to the production click path.
    """

    memory = MumuProcessMemory.discover_cached()
    snapshot = _snapshot(memory)
    active_ui = (snapshot.get("evidence") or {}).get("active_ui_bag") or {}
    address_text = active_ui.get("scrollview_address")
    if not address_text:
        return {
            "ok": False,
            "reason": "active BeastSpiritSlotGridPanel.scrollview unavailable",
            "read_only": True,
        }
    scrollview_address = int(str(address_text), 16)
    reader = LuaJitReader(memory)

    def fields(address: int, names: frozenset[str]) -> dict[str, Any]:
        return {
            str(key): value
            for key, value in reader.string_fields(address, names).items()
        }

    candidate_names = frozenset({
        "ItemClassDic",
        "ItemClassInst",
        "ItemClassList",
        "ItemInfoList",
        "ShowItemList",
        "itemClassDic",
        "itemClassInst",
    })
    candidates = fields(scrollview_address, candidate_names)
    result: dict[str, Any] = {}
    for name, value in candidates.items():
        ref = table_ref(value)
        entry: dict[str, Any] = {
            "type": value.kind if isinstance(value, LuaRef) else type(value).__name__,
            "address": f"0x{ref.address:x}" if ref else None,
            "bindings": [],
        }
        if ref is None:
            result[name] = entry
            continue
        direct = fields(ref.address, frozenset({"_dt_", "V_Data", "id", "isEmpty"}))
        storage = table_ref(direct.get("_dt_")) or ref
        table = reader.table(storage.address)
        values = [*table["array"], *table["fields"].values()]
        seen: set[int] = set()
        for item_value in values[:32]:
            item_ref = table_ref(item_value)
            if item_ref is None or item_ref.address in seen:
                continue
            seen.add(item_ref.address)
            item_fields = fields(
                item_ref.address,
                frozenset({"V_Data", "root", "index", "Index", "showIndex"}),
            )
            data_ref = table_ref(item_fields.get("V_Data"))
            if data_ref is None:
                continue
            data = fields(data_ref.address, frozenset({"id", "isEmpty"}))
            item_id = _lua_long(reader, data.get("id"))
            root_ref = table_ref(item_fields.get("root"))
            root_fields = (
                fields(
                    root_ref.address,
                    frozenset({
                        "FatherId",
                        "FatherComponentID",
                        "ComponentId",
                        "OrginComponentId",
                        "rectTransform",
                        "transform",
                    }),
                )
                if root_ref
                else {}
            )

            def diagnostic_value(raw: Any) -> Any:
                ref = table_ref(raw)
                if ref:
                    return {"type": "table", "address": f"0x{ref.address:x}"}
                scalar = _lua_long(reader, raw)
                if scalar is not None:
                    return scalar
                return raw if isinstance(raw, (str, int, float, bool)) else None

            entry["bindings"].append({
                "item_instance_address": f"0x{item_ref.address:x}",
                "data_address": f"0x{data_ref.address:x}",
                "instance_id": str(item_id) if item_id is not None else None,
                "is_empty": bool(data.get("isEmpty")),
                "root_address": f"0x{root_ref.address:x}" if root_ref else None,
                "item_indices": {
                    name: diagnostic_value(item_fields.get(name))
                    for name in ("index", "Index", "showIndex")
                    if item_fields.get(name) is not None
                },
                "root_fields": {
                    name: diagnostic_value(value)
                    for name, value in root_fields.items()
                },
            })
        result[name] = entry
    return {
        "ok": True,
        "read_only": True,
        "pid": memory.pid,
        "process_start_ticks": memory.process_start_ticks,
        "panel_address": active_ui.get("panel_address"),
        "scrollview_address": address_text,
        "candidate_fields": result,
    }


def diagnose_beast_scrollview_root_positions(
    *,
    scrollview_address: int,
    panel_address: int | None = None,
) -> dict[str, Any]:
    """Read one already-addressed scrollview's binding/root position fields.

    The caller supplies the address obtained from the prior active-panel probe.
    This deliberately avoids manager discovery, heap scans and full beast
    inventory/config decoding.  Only ``ItemClassDic`` and objects directly
    referenced by each binding's ``V_Data``/``root`` are traversed.
    """

    memory = MumuProcessMemory.discover_cached()
    reader = LuaJitReader(memory)

    def selected(address: int, names: frozenset[str]) -> dict[str, Any]:
        return {
            str(key): value
            for key, value in reader.string_fields(int(address), names).items()
        }

    def scalar(raw: Any) -> Any:
        ref = raw if isinstance(raw, LuaRef) else None
        if ref is not None:
            return {"type": ref.kind, "address": f"0x{ref.address:x}"}
        integer = _lua_long(reader, raw)
        if integer is not None:
            return integer
        return raw if isinstance(raw, (str, int, float, bool)) else None

    vector_names = frozenset({
        "anchoredPosition",
        "anchoredPosition3D",
        "localPosition",
        "position",
        "sizeDelta",
        "rect",
        "pivot",
        "anchorMin",
        "anchorMax",
        "x",
        "y",
        "z",
        "X",
        "Y",
        "Z",
        "_x",
        "_y",
        "_z",
    })
    root_names = frozenset({
        "FatherId",
        "FatherComponentID",
        "ComponentId",
        "OrginComponentId",
        "transform",
        "rectTransform",
        "TransformBridge",
        "RectTransformBridge",
    })
    scroll_fields = selected(
        int(scrollview_address),
        frozenset({"ItemClassDic", "ItemClassInst"}),
    )
    dictionary = table_ref(scroll_fields.get("ItemClassDic"))
    storage = (
        table_ref(selected(dictionary.address, frozenset({"_dt_"})).get("_dt_"))
        if dictionary
        else None
    )
    if dictionary is None or storage is None:
        return {
            "ok": False,
            "read_only": True,
            "reason": "addressed scrollview ItemClassDic/_dt_ is not loaded",
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "panel_address": f"0x{panel_address:x}" if panel_address else None,
            "scrollview_address": f"0x{scrollview_address:x}",
        }

    table = reader.table(storage.address)
    keyed_values: list[tuple[Any, Any]] = [
        (index, value) for index, value in enumerate(table["array"])
    ]
    keyed_values.extend(table["fields"].items())
    bindings: list[dict[str, Any]] = []
    seen: set[int] = set()
    for dictionary_key, raw_item in keyed_values:
        item = table_ref(raw_item)
        if item is None or item.address in seen:
            continue
        seen.add(item.address)
        item_fields = selected(
            item.address,
            frozenset({"V_Data", "root", "index", "Index", "showIndex"}),
        )
        data = table_ref(item_fields.get("V_Data"))
        root = table_ref(item_fields.get("root"))
        data_fields = (
            selected(data.address, frozenset({"id", "isEmpty"})) if data else {}
        )
        root_fields = selected(root.address, root_names) if root else {}
        referenced_objects: dict[str, Any] = {}
        for name, raw_ref in root_fields.items():
            ref = table_ref(raw_ref)
            if ref is None:
                continue
            first = selected(ref.address, vector_names | root_names)
            nested: dict[str, Any] = {
                key: scalar(value) for key, value in first.items()
            }
            for nested_name, nested_raw in first.items():
                nested_ref = table_ref(nested_raw)
                if nested_ref is None:
                    continue
                second = selected(nested_ref.address, vector_names)
                if second:
                    nested[f"{nested_name}.__fields__"] = {
                        key: scalar(value) for key, value in second.items()
                    }
            referenced_objects[name] = {
                "address": f"0x{ref.address:x}",
                "fields": nested,
            }
        item_id = _lua_long(reader, data_fields.get("id"))
        bindings.append({
            "dictionary_key": scalar(dictionary_key),
            "item_instance_address": f"0x{item.address:x}",
            "instance_id": str(item_id) if item_id is not None else None,
            "is_empty": bool(data_fields.get("isEmpty")),
            "root_address": f"0x{root.address:x}" if root else None,
            "item_indices": {
                key: scalar(item_fields[key])
                for key in ("index", "Index", "showIndex")
                if key in item_fields
            },
            "root_fields": {
                key: scalar(value) for key, value in root_fields.items()
            },
            "root_referenced_objects": referenced_objects,
        })
    return {
        "ok": True,
        "read_only": True,
        "pid": memory.pid,
        "process_start_ticks": memory.process_start_ticks,
        "panel_address": f"0x{panel_address:x}" if panel_address else None,
        "scrollview_address": f"0x{scrollview_address:x}",
        "item_class_dictionary_address": f"0x{dictionary.address:x}",
        "item_class_storage_address": f"0x{storage.address:x}",
        "binding_count": len(bindings),
        "bindings": bindings,
    }
