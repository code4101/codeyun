from __future__ import annotations

"""Strictly read the currently loaded main-world function menu."""

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
    as_int,
    table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    UiRuntimeContext,
    acquire_ui_runtime_context_fast,
)


_MENU_KEYS = frozenset(
    {
        "BottomContent",
        "m_panel",
        "FuncBtnList",
        "_BtnDataList",
        "_BtnList",
        "_CurFuncId",
        "data",
        "id",
        "name",
        "luaPath",
        "windowId",
        "sort",
    }
)
_CACHE_LOCK = threading.RLock()


@dataclass(frozen=True)
class WorldMenuItem:
    index: int
    function_id: int
    name: str
    lua_path: str = ""
    window_id: str = ""
    sort: int | None = None

    @property
    def key(self) -> str:
        return str(self.function_id)


@dataclass(frozen=True)
class WorldMenuReadTimings:
    binding_ms: float
    locate_ms: float
    decode_ms: float
    total_ms: float
    cache_mode: str


@dataclass(frozen=True)
class WorldMenuSnapshot:
    complete: bool
    items: tuple[WorldMenuItem, ...]
    pid: int
    process_start_ticks: int
    fingerprint: str
    timings: WorldMenuReadTimings


@dataclass(frozen=True)
class _MenuAddressBinding:
    pid: int
    process_start_ticks: int
    func_list_address: int
    data_list_address: int
    button_list_address: int


_menu_binding: _MenuAddressBinding | None = None


def clear_world_menu_cache() -> None:
    """Clear local object addresses without changing the game Runtime."""

    global _menu_binding
    with _CACHE_LOCK:
        _menu_binding = None


def _objects_in_component_value(ctx: UiRuntimeContext, value: Any) -> tuple[LuaRef, ...]:
    outer = table_ref(value)
    if outer is None:
        return ()
    result = [outer]
    try:
        indexed, _count = ctx.reader.indexed_list_items(outer)
    except FanxiuRuntimeMemoryError:
        indexed = []
    result.extend(ref for _index, item in indexed if (ref := table_ref(item)) is not None)
    # UIShowMgr stores windows as CList wrappers.  The actual WinMainUINew
    # instance carrying BottomContent is the wrapper's m_panel, not the
    # wrapper itself.  Keep both because other UI versions may expose the
    # panel directly.
    panels: list[LuaRef] = []
    for component in tuple(result):
        try:
            panel = table_ref(ctx.field(component.address, "m_panel"))
        except FanxiuRuntimeMemoryError:
            panel = None
        if panel is not None:
            panels.append(panel)
    result.extend(panels)
    return tuple(dict.fromkeys(result))


def _locate_menu(ctx: UiRuntimeContext) -> tuple[int, int, int]:
    candidates: set[tuple[int, int, int]] = set()
    component_values = ctx.reader.dictionary_fields(
        LuaRef("table", ctx.binding.components_address)
    ).values()
    for raw_value in component_values:
        for component in _objects_in_component_value(ctx, raw_value):
            try:
                bottom = table_ref(ctx.field(component.address, "BottomContent"))
                if bottom is None:
                    continue
                func_list = table_ref(ctx.field(bottom.address, "FuncBtnList"))
                if func_list is None:
                    continue
                data_list = table_ref(ctx.field(func_list.address, "_BtnDataList"))
                button_list = table_ref(ctx.field(func_list.address, "_BtnList"))
                if data_list is not None and button_list is not None:
                    candidates.add(
                        (func_list.address, data_list.address, button_list.address)
                    )
            except FanxiuRuntimeMemoryError:
                # UIShowMgr can retain a just-destroyed component for one frame.
                # Its stale LuaRef must not poison other live candidates; the
                # final zero/one/many menu cardinality check remains strict.
                continue
    if not candidates:
        raise FanxiuRuntimeMemoryError(
            "主世界菜单尚未自然加载 BottomContent.FuncBtnList",
            code="data_not_loaded",
        )
    if len(candidates) != 1:
        raise FanxiuRuntimeMemoryError(
            f"主世界菜单对象不唯一：{len(candidates)} 个候选",
            code="runtime_incomplete",
        )
    return next(iter(candidates))


def _validate_cached_menu(
    ctx: UiRuntimeContext, binding: _MenuAddressBinding
) -> tuple[int, int, int]:
    if (
        binding.pid != ctx.binding.pid
        or binding.process_start_ticks != ctx.binding.process_start_ticks
    ):
        raise FanxiuRuntimeMemoryError("主世界菜单地址缓存进程身份已变化")
    data_list = table_ref(ctx.field(binding.func_list_address, "_BtnDataList"))
    if data_list is None or data_list.address != binding.data_list_address:
        raise FanxiuRuntimeMemoryError("主世界菜单对象已替换")
    button_list = table_ref(ctx.field(binding.func_list_address, "_BtnList"))
    if button_list is None or button_list.address != binding.button_list_address:
        raise FanxiuRuntimeMemoryError("主世界菜单按钮对象已替换")
    return (
        binding.func_list_address,
        binding.data_list_address,
        binding.button_list_address,
    )


def _required_text(value: Any, field: str, index: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise FanxiuRuntimeMemoryError(
            f"主世界菜单第 {index} 项缺少 {field}", code="runtime_incomplete"
        )
    return text


_KNOWN_FUNCTION_NAMES = {
    1000: "角色",
    210000: "装备",
    360001: "异火",
    3000: "功法书",
    1600000: "剑灵",
    1330000: "神器",
    1250000: "万灵",
    1190001: "灵器",
    200001: "家族",
    17000: "宗门",
    5000: "法宝",
    4000: "灵兽",
    22000: "仙缘",
    10000: "邮件",
    7000: "设置",
}


def _decode_items(
    ctx: UiRuntimeContext, data_list_address: int, button_list_address: int
) -> tuple[WorldMenuItem, ...]:
    data_rows, data_count = ctx.reader.indexed_list_items(
        LuaRef("table", data_list_address)
    )
    button_rows, button_count = ctx.reader.indexed_list_items(
        LuaRef("table", button_list_address)
    )
    if (
        data_count is None
        or data_count <= 0
        or len(data_rows) != data_count
        or button_count != data_count
        or len(button_rows) != button_count
    ):
        raise FanxiuRuntimeMemoryError("主世界菜单列表不完整", code="runtime_incomplete")
    items: list[WorldMenuItem] = []
    seen_ids: set[int] = set()
    for (index, _raw_config), (button_index, raw_button) in zip(
        data_rows, button_rows, strict=True
    ):
        if button_index != index:
            raise FanxiuRuntimeMemoryError(
                "主世界菜单配置与按钮顺序不一致", code="runtime_incomplete"
            )
        button = table_ref(raw_button)
        function_id = (
            as_int(ctx.field(button.address, "_CurFuncId")) if button is not None else None
        )
        if function_id is None or function_id in seen_ids:
            raise FanxiuRuntimeMemoryError(
                f"主世界菜单第 {index} 项功能 ID 无效或重复",
                code="runtime_incomplete",
            )
        seen_ids.add(function_id)
        items.append(
            WorldMenuItem(
                index=index,
                function_id=function_id,
                # Identity and order come from the live button instances.  The
                # label map is presentation only; unknown newly unlocked
                # functions remain visible instead of invalidating the list.
                name=_KNOWN_FUNCTION_NAMES.get(function_id, f"功能{function_id}"),
                sort=index,
            )
        )
    return tuple(items)


def read_world_menu_snapshot() -> WorldMenuSnapshot:
    """Read the authoritative current ordered menu, failing when it is not loaded."""

    global _menu_binding
    total_started = time.perf_counter()
    binding_started = total_started
    ctx = acquire_ui_runtime_context_fast(_MENU_KEYS)
    binding_done = time.perf_counter()
    locate_started = binding_done
    with _CACHE_LOCK:
        cached = _menu_binding
    cache_mode = f"{ctx.cache_mode}/relocated"
    if cached is not None:
        try:
            func_list_address, data_list_address, button_list_address = (
                _validate_cached_menu(ctx, cached)
            )
            cache_mode = f"{ctx.cache_mode}/object-hot"
        except FanxiuRuntimeMemoryError:
            func_list_address, data_list_address, button_list_address = _locate_menu(ctx)
    else:
        func_list_address, data_list_address, button_list_address = _locate_menu(ctx)
    locate_done = time.perf_counter()
    items = _decode_items(ctx, data_list_address, button_list_address)
    decode_done = time.perf_counter()
    with _CACHE_LOCK:
        _menu_binding = _MenuAddressBinding(
            pid=ctx.binding.pid,
            process_start_ticks=ctx.binding.process_start_ticks,
            func_list_address=func_list_address,
            data_list_address=data_list_address,
            button_list_address=button_list_address,
        )
    canonical = json.dumps([asdict(item) for item in items], ensure_ascii=False, sort_keys=True)
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return WorldMenuSnapshot(
        complete=True,
        items=items,
        pid=ctx.binding.pid,
        process_start_ticks=ctx.binding.process_start_ticks,
        fingerprint=fingerprint,
        timings=WorldMenuReadTimings(
            binding_ms=round((binding_done - binding_started) * 1000, 3),
            locate_ms=round((locate_done - locate_started) * 1000, 3),
            decode_ms=round((decode_done - locate_done) * 1000, 3),
            total_ms=round((decode_done - total_started) * 1000, 3),
            cache_mode=cache_mode,
        ),
    )


__all__ = [
    "WorldMenuItem",
    "WorldMenuReadTimings",
    "WorldMenuSnapshot",
    "clear_world_menu_cache",
    "read_world_menu_snapshot",
]
