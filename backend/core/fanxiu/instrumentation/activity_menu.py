from __future__ import annotations

"""Strictly read the loaded world activity menus without executing game code."""

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any, Literal

from backend.core.fanxiu.instrumentation.activity_runtime import (
    _load_activity_definitions,
)
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


ActivityMenuKind = Literal["world_left", "group_popup"]
ActivityMenuStatus = Literal["loaded", "not_loaded"]

# Menu-specific strings are lazy: requiring them while binding the shared UI
# root would make an unrelated page fail because (for example) ``ActContent``
# has not been interned yet.  Resolve each exact field only on the candidate
# object currently being inspected.
_MENU_KEYS = frozenset()
_KNOWN_GROUP_NAMES = {
    9000: "商店",
    100000: "社群",
    110001: "特惠",
    120010: "限时",
}
_CACHE_LOCK = threading.RLock()

# The group-popup controller is not consistently registered as a top-level
# UIShowMgr component.  It may be a child of the active panel, but we only
# follow the two fields verified in the live UI schema below.  These limits
# make that narrow traversal fail closed instead of becoming a heap walk.
_COMPONENT_TREE_MAX_DEPTH = 4
_COMPONENT_TREE_MAX_CHILDREN = 64
_COMPONENT_TREE_MAX_NODES = 256


@dataclass(frozen=True)
class ActivityMenuItem:
    index: int
    key: str
    name: str
    activity_id: int | None = None
    group_type: int | None = None
    base_id: int | None = None
    item_type: int | None = None
    sort: int | None = None
    is_custom: bool | None = None
    icon: str = ""
    atlas: str = ""


@dataclass(frozen=True)
class ActivityMenuReadTimings:
    binding_ms: float
    locate_ms: float
    decode_ms: float
    total_ms: float
    cache_mode: str


@dataclass(frozen=True)
class ActivityMenuSnapshot:
    kind: ActivityMenuKind
    status: ActivityMenuStatus
    complete: bool
    items: tuple[ActivityMenuItem, ...]
    pid: int
    process_start_ticks: int
    fingerprint: str
    reason: str
    timings: ActivityMenuReadTimings


@dataclass(frozen=True)
class _WorldLeftBinding:
    pid: int
    process_start_ticks: int
    owner_address: int
    list_address: int
    schema: Literal["legacy_v_btn_list", "main_ui_cur_list"]


_world_left_binding: _WorldLeftBinding | None = None


def clear_activity_menu_cache() -> None:
    """Clear process-local addresses without changing game state."""

    global _world_left_binding
    with _CACHE_LOCK:
        _world_left_binding = None


def _raw_field(ctx: UiRuntimeContext, address: int, name: str) -> Any:
    """Read one exact field from an ordinary Lua table or its prototype."""

    try:
        value = ctx.field(address, name)
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


def _component_objects(
    ctx: UiRuntimeContext, *, include_descendants: bool = False
) -> tuple[LuaRef, ...]:
    result: list[LuaRef] = []
    values = ctx.reader.dictionary_fields(
        LuaRef("table", ctx.binding.components_address)
    ).values()
    for raw in values:
        outer = table_ref(raw)
        if outer is None:
            continue
        result.append(outer)
        try:
            indexed, count = ctx.reader.indexed_list_items(outer)
        except FanxiuRuntimeMemoryError:
            indexed = []
            count = 0
        if count > _COMPONENT_TREE_MAX_CHILDREN:
            raise FanxiuRuntimeMemoryError(
                "UIShowMgr 组件索引超过有界解析范围",
                code="runtime_incomplete",
            )
        result.extend(
            ref
            for _index, value in indexed
            if (ref := table_ref(value)) is not None
        )
    roots = tuple(dict.fromkeys(result))

    # Keep the original direct adapter fast and non-invasive.  The full
    # UIShowMgr registry is allowed to be large; only an explicit fallback is
    # permitted to inspect its verified child links.
    panels = tuple(
        panel
        for root in roots
        if (panel := table_ref(_raw_field(ctx, root.address, "m_panel"))) is not None
    )
    direct = tuple(dict.fromkeys((*roots, *panels)))
    if not include_descendants:
        return direct

    # The live schema is deliberately narrower than a generic UI-tree walk:
    # UIShowMgr active component -> its m_panel -> that panel's
    # m_ChildCompList.  The ActivityBtnGroup host is one of those immediate
    # children.  Descending again into every child also enters the world UI
    # trees and made an otherwise valid popup exceed an arbitrary node limit.
    # Do not turn this into recursive Lua-table traversal: a child controller
    # must itself expose the exact ActivityBtnGroup pair before it is accepted.
    result = list(direct)
    direct_addresses = {item.address for item in direct}
    children_by_address: dict[int, LuaRef] = {}
    for panel in panels:
        child_list = table_ref(_raw_field(ctx, panel.address, "m_ChildCompList"))
        if child_list is None:
            continue
        try:
            children, count = ctx.reader.indexed_list_items(child_list)
        except FanxiuRuntimeMemoryError:
            continue
        if count > _COMPONENT_TREE_MAX_CHILDREN:
            raise FanxiuRuntimeMemoryError(
                "活动窗口子组件超过有界解析范围", code="runtime_incomplete"
            )
        for _index, value in children:
            child = table_ref(value)
            if child is not None and child.address not in direct_addresses:
                children_by_address.setdefault(child.address, child)
    if len(children_by_address) > _COMPONENT_TREE_MAX_NODES:
        raise FanxiuRuntimeMemoryError(
            "活动窗口组件树超过有界解析范围", code="runtime_incomplete"
        )
    result.extend(children_by_address.values())
    return tuple(dict.fromkeys(result))


def active_ui_component_objects(
    ctx: UiRuntimeContext, *, include_descendants: bool = False
) -> tuple[LuaRef, ...]:
    """Return the bounded active UI component set for another UI reader.

    This is deliberately not a generic Lua-table traversal.  It exposes only
    the UIShowMgr registry and the two live-verified links used by this module:
    a component's ``m_panel`` and that panel's immediate
    ``m_ChildCompList``.  Consumers must still identify their own panel with
    independent business fields and fail on ambiguity.
    """

    return _component_objects(ctx, include_descendants=include_descendants)


def read_ui_object_field(ctx: UiRuntimeContext, address: int, name: str) -> Any:
    """Read one exact field from a bounded active UI object.

    The caller must obtain ``address`` from :func:`active_ui_component_objects`;
    this helper is not an address-discovery API.
    """

    return _raw_field(ctx, address, name)


def _world_left_candidate(
    ctx: UiRuntimeContext, component: LuaRef
) -> tuple[int, int, Literal["legacy_v_btn_list", "main_ui_cur_list"]] | None:
    # Current main-UI layout (live-verified 2026-08-14):
    # m_panel -> TopContent -> BtnNodeComp -> _CurList.  BtnNodeComp is the
    # controller for the expanded left activity strip; _CurList is its ordered
    # rendered row pool.  Require the controller's own UI identities so a
    # similarly named list on another component cannot become a candidate.
    top_content = table_ref(_raw_field(ctx, component.address, "TopContent"))
    if top_content is not None:
        controller = table_ref(
            _raw_field(ctx, top_content.address, "BtnNodeComp")
        )
        if controller is not None:
            current = table_ref(_raw_field(ctx, controller.address, "_CurList"))
            template = table_ref(
                _raw_field(ctx, controller.address, "ActivityBtnItem")
            )
            content = table_ref(_raw_field(ctx, controller.address, "Content"))
            change_button = table_ref(
                _raw_field(ctx, controller.address, "ChangeBtn")
            )
            is_open = _raw_field(ctx, controller.address, "_IsOpen")
            if (
                current is not None
                and template is not None
                and content is not None
                and change_button is not None
                and is_open is True
            ):
                return controller.address, current.address, "main_ui_cur_list"

    # Older supported layout retained as a finite schema adapter.
    owners = [component]
    try:
        act_content = table_ref(_raw_field(ctx, component.address, "ActContent"))
    except FanxiuRuntimeMemoryError:
        act_content = None
    if act_content is not None:
        owners.append(act_content)
    for owner in owners:
        try:
            button_list = table_ref(_raw_field(ctx, owner.address, "V_BtnList"))
            scroll = table_ref(_raw_field(ctx, owner.address, "ActivityContent"))
        except FanxiuRuntimeMemoryError:
            continue
        if button_list is not None and scroll is not None:
            return owner.address, button_list.address, "legacy_v_btn_list"
    return None


def _locate_world_left(
    ctx: UiRuntimeContext,
) -> tuple[int, int, Literal["legacy_v_btn_list", "main_ui_cur_list"]]:
    candidates = {
        candidate
        for component in _component_objects(ctx)
        if (candidate := _world_left_candidate(ctx, component)) is not None
    }
    if not candidates:
        raise FanxiuRuntimeMemoryError(
            "#34 左侧活动菜单尚未自然加载",
            code="data_not_loaded",
        )
    if len(candidates) != 1:
        raise FanxiuRuntimeMemoryError(
            f"#34 左侧活动菜单对象不唯一：{len(candidates)} 个候选",
            code="runtime_incomplete",
        )
    return next(iter(candidates))


def _validate_world_left_binding(
    ctx: UiRuntimeContext, binding: _WorldLeftBinding
) -> tuple[int, int, Literal["legacy_v_btn_list", "main_ui_cur_list"]]:
    if (
        binding.pid != ctx.binding.pid
        or binding.process_start_ticks != ctx.binding.process_start_ticks
    ):
        raise FanxiuRuntimeMemoryError("#34 左侧活动菜单缓存进程身份已变化")
    if binding.schema == "main_ui_cur_list":
        current = table_ref(_raw_field(ctx, binding.owner_address, "_CurList"))
        identities_ok = all(
            table_ref(_raw_field(ctx, binding.owner_address, name)) is not None
            for name in ("ActivityBtnItem", "Content", "ChangeBtn")
        ) and _raw_field(ctx, binding.owner_address, "_IsOpen") is True
    else:
        current = table_ref(_raw_field(ctx, binding.owner_address, "V_BtnList"))
        identities_ok = (
            table_ref(
                _raw_field(ctx, binding.owner_address, "ActivityContent")
            )
            is not None
        )
    if current is None or current.address != binding.list_address or not identities_ok:
        raise FanxiuRuntimeMemoryError("#34 左侧活动菜单对象已替换")
    return binding.owner_address, binding.list_address, binding.schema


def _field(ctx: UiRuntimeContext, row: LuaRef, name: str) -> Any:
    value = _raw_field(ctx, row.address, name)
    if value is not None:
        return value
    reader = ctx.reader
    if not hasattr(reader, "metatable_index_string_field"):
        return None
    try:
        return reader.metatable_index_string_field(
            row.address,
            name,
            string_table_address=ctx.binding.string_table_address,
            string_mask=ctx.binding.string_mask,
            string_seed=ctx.binding.string_seed,
        )
    except FanxiuRuntimeMemoryError:
        return None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


@lru_cache(maxsize=1)
def _activity_definition_index() -> dict[int, dict[str, Any]]:
    """Cache the exported Activity config used by function-backed config rows."""

    return _load_activity_definitions()


def _configured_activity_name(
    activity_id: int | None,
    activity_type: int | None,
    base_id: int | None,
) -> str:
    if activity_id is None or activity_id <= 0:
        return ""
    try:
        definition = _activity_definition_index().get(activity_id)
    except (OSError, ValueError, json.JSONDecodeError, FanxiuRuntimeMemoryError):
        return ""
    if not definition:
        return ""
    configured_type = as_int(definition.get("activityId"))
    configured_base = as_int(definition.get("baseId"))
    if activity_type is not None and configured_type != activity_type:
        return ""
    if base_id is not None and configured_base != base_id:
        return ""
    return _text(definition.get("name_plain") or definition.get("name"))


def _decode_activity_row(
    ctx: UiRuntimeContext, index: int, raw: Any
) -> ActivityMenuItem | None:
    row = table_ref(raw)
    if row is None:
        return None
    activity_id = as_int(_field(ctx, row, "activityId"))
    group_type = as_int(_field(ctx, row, "groupType"))
    base_id = as_int(_field(ctx, row, "baseId"))
    activity_type = as_int(_field(ctx, row, "activityType"))
    item_type = as_int(_field(ctx, row, "type"))
    sort = as_int(_field(ctx, row, "sort"))
    name = _text(_field(ctx, row, "name"))
    activity_config = table_ref(_field(ctx, row, "activitylo"))
    if not name and activity_config is not None:
        name = _text(_field(ctx, activity_config, "name"))
    if not name:
        name = _configured_activity_name(activity_id, activity_type, base_id)
    if activity_id is None and group_type is None and not name:
        return None
    if activity_id is not None and activity_id > 0:
        key = f"activity:{activity_id}"
        fallback = f"活动{activity_id}"
    elif group_type is not None:
        key = f"group:{group_type}"
        fallback = _KNOWN_GROUP_NAMES.get(group_type, f"分组{group_type}")
    else:
        # Preserve unknown custom entries instead of dropping or inventing an
        # activity identity.  The index remains part of the key, so ambiguous
        # duplicate names cannot silently collapse.
        key = f"custom:{item_type if item_type is not None else 'unknown'}:{index}"
        fallback = f"未知菜单{index}"
    custom_value = _field(ctx, row, "isCustom")
    return ActivityMenuItem(
        index=index,
        key=key,
        name=name or fallback,
        activity_id=activity_id,
        group_type=group_type,
        base_id=base_id,
        item_type=item_type,
        sort=sort,
        is_custom=custom_value if isinstance(custom_value, bool) else None,
        icon=_text(_field(ctx, row, "icon")),
        atlas=_text(_field(ctx, row, "alta")),
    )


def _validate_items(items: tuple[ActivityMenuItem, ...], label: str) -> None:
    if not items:
        raise FanxiuRuntimeMemoryError(
            f"{label}列表为空或尚未渲染", code="data_not_loaded"
        )
    keys = [item.key for item in items]
    if len(keys) != len(set(keys)):
        raise FanxiuRuntimeMemoryError(
            f"{label}存在重复业务身份", code="runtime_incomplete"
        )


def _decode_c_list(
    ctx: UiRuntimeContext, list_address: int, label: str
) -> tuple[ActivityMenuItem, ...]:
    rows, count = ctx.reader.indexed_list_items(LuaRef("table", list_address))
    if count is None or count <= 0 or len(rows) != count:
        raise FanxiuRuntimeMemoryError(
            f"{label}列表不完整", code="runtime_incomplete"
        )
    items: list[ActivityMenuItem] = []
    for logical_index, (_raw_index, raw) in enumerate(rows, start=1):
        item = _decode_activity_row(ctx, logical_index, raw)
        if item is None:
            raise FanxiuRuntimeMemoryError(
                f"{label}第 {logical_index} 项缺少活动身份",
                code="runtime_incomplete",
            )
        items.append(item)
    result = tuple(items)
    _validate_items(result, label)
    return result


def _decode_main_ui_row_pool(
    ctx: UiRuntimeContext, list_address: int
) -> tuple[ActivityMenuItem, ...]:
    """Decode the active prefix of BtnNodeComp._CurList.

    The controller keeps inactive pooled row views after the live menu rows.
    Their ``_DataIndex`` is ``-1`` and they carry no ``_Data``.  Only a
    contiguous, one-based active prefix is accepted; an active row after a
    pooled slot or a missing identity fails closed instead of being filtered
    into an invented sequence.
    """

    rows, count = ctx.reader.indexed_list_items(LuaRef("table", list_address))
    if count is None or count <= 0 or len(rows) != count:
        raise FanxiuRuntimeMemoryError(
            "#34 左侧活动菜单渲染池不完整", code="runtime_incomplete"
        )
    items: list[ActivityMenuItem] = []
    inactive_seen = False
    for _raw_index, raw in rows:
        view = table_ref(raw)
        if view is None:
            raise FanxiuRuntimeMemoryError(
                "#34 左侧活动菜单渲染槽不是对象", code="runtime_incomplete"
            )
        data_index = as_int(_field(ctx, view, "_DataIndex"))
        data = table_ref(_field(ctx, view, "_Data"))
        if data_index is None or data_index <= 0:
            inactive_seen = True
            if data is not None:
                raise FanxiuRuntimeMemoryError(
                    "#34 左侧活动菜单空闲槽仍绑定业务数据",
                    code="runtime_incomplete",
                )
            continue
        expected_index = len(items) + 1
        if inactive_seen or data_index != expected_index or data is None:
            raise FanxiuRuntimeMemoryError(
                "#34 左侧活动菜单有效槽不是连续有序前缀",
                code="runtime_incomplete",
            )
        item = _decode_activity_row(ctx, expected_index, data)
        if item is None:
            raise FanxiuRuntimeMemoryError(
                f"#34 左侧活动菜单第 {expected_index} 项缺少活动身份",
                code="runtime_incomplete",
            )
        items.append(item)
    result = tuple(items)
    _validate_items(result, "#34 左侧活动菜单")
    return result


def _ordered_table_values(ctx: UiRuntimeContext, table: LuaRef) -> tuple[Any, ...]:
    try:
        rows, count = ctx.reader.indexed_list_items(table)
    except FanxiuRuntimeMemoryError:
        rows, count = [], None
    if count is not None and count > 0 and len(rows) == count:
        return tuple(value for _index, value in rows)
    # ``table`` may be LuaUIScrollView's ordinary numeric view map.  It is not
    # a CDictionary wrapper, so reading it through ``dictionary_fields`` would
    # incorrectly require an ``_dt_`` field and silently lose the sequence.
    try:
        fields = ctx.reader.fields(table)
    except (FanxiuRuntimeMemoryError, AttributeError):
        return ()
    numeric = [
        (int(key), value)
        for key, value in fields.items()
        if as_int(key) is not None
    ]
    return tuple(value for _key, value in sorted(numeric))


def _unwrap_visible_row(ctx: UiRuntimeContext, raw: Any) -> Any:
    row = table_ref(raw)
    if row is None:
        return raw
    direct = _decode_activity_row(ctx, 1, row)
    if direct is not None:
        return row
    item_vo = table_ref(_field(ctx, row, "itemVo"))
    if item_vo is not None:
        data = _field(ctx, item_vo, "_Data")
        if table_ref(data) is not None:
            return data
    data = _field(ctx, row, "_Data")
    return data if table_ref(data) is not None else raw


def _candidate_sequence(
    ctx: UiRuntimeContext, source: LuaRef
) -> tuple[ActivityMenuItem, ...] | None:
    values = _ordered_table_values(ctx, source)
    if not values:
        return None
    items: list[ActivityMenuItem] = []
    for index, raw in enumerate(values, start=1):
        item = _decode_activity_row(ctx, index, _unwrap_visible_row(ctx, raw))
        if item is None:
            return None
        items.append(item)
    try:
        result = tuple(items)
        _validate_items(result, "活动分组弹层")
        return result
    except FanxiuRuntimeMemoryError:
        return None


def _discover_group_sequences(
    ctx: UiRuntimeContext, content: LuaRef
) -> tuple[ActivityMenuItem, ...]:
    """Read the current ``UpdateView(activityList)`` data without Lua calls.

    ``ActivityBtnGroup`` proves ``content`` is its ``activityContent``
    LuaUIScrollView.  LuaUIScrollView itself is an ordinary Lua table, not a
    CDictionary wrapper.  Its private storage names are not a stable business
    contract, so inspect only this already-verified host and one direct child
    level, with hard field/source limits.  Candidate sources must independently
    decode into one identical complete activity sequence.
    """

    try:
        fields = ctx.reader.fields(content)
    except (FanxiuRuntimeMemoryError, AttributeError) as exc:
        raise FanxiuRuntimeMemoryError(
            "活动分组弹层滚动列表尚未自然加载", code="data_not_loaded"
        ) from exc
    if len(fields) > 256:
        raise FanxiuRuntimeMemoryError(
            "活动分组弹层滚动组件字段超出有界解析范围",
            code="runtime_incomplete",
        )
    direct_sources = tuple(
        dict.fromkeys(
            ref
            for raw in fields.values()
            if (ref := table_ref(raw)) is not None
        )
    )
    if len(direct_sources) > 64:
        raise FanxiuRuntimeMemoryError(
            "活动分组弹层滚动组件子表超出有界解析范围",
            code="runtime_incomplete",
        )
    sources: list[LuaRef] = list(direct_sources)
    for source in direct_sources:
        try:
            nested = ctx.reader.fields(source)
        except (FanxiuRuntimeMemoryError, AttributeError):
            continue
        if len(nested) > 256:
            continue
        sources.extend(
            child
            for value in nested.values()
            if (child := table_ref(value)) is not None
        )
        if len(sources) > 128:
            raise FanxiuRuntimeMemoryError(
                "活动分组弹层候选数据源超出有界解析范围",
                code="runtime_incomplete",
            )
    canonical: dict[tuple[tuple[str, str], ...], tuple[ActivityMenuItem, ...]] = {}
    for source in dict.fromkeys(sources):
        sequence = _candidate_sequence(ctx, source)
        if sequence is None:
            continue
        signature = tuple((item.key, item.name) for item in sequence)
        canonical.setdefault(signature, sequence)
    if not canonical:
        raise FanxiuRuntimeMemoryError(
            "活动分组弹层列表尚未自然加载", code="data_not_loaded"
        )
    if len(canonical) != 1:
        raise FanxiuRuntimeMemoryError(
            f"活动分组弹层存在 {len(canonical)} 组不同的可见业务序列",
            code="runtime_incomplete",
        )
    return next(iter(canonical.values()))


def _locate_group_popup(
    ctx: UiRuntimeContext,
) -> tuple[int, int, tuple[ActivityMenuItem, ...]]:
    def collect(
        components: tuple[LuaRef, ...],
    ) -> dict[tuple[int, int], tuple[ActivityMenuItem, ...]]:
        candidates: dict[tuple[int, int], tuple[ActivityMenuItem, ...]] = {}
        for component in components:
            try:
                content = table_ref(_raw_field(ctx, component.address, "activityContent"))
                template = table_ref(_raw_field(ctx, component.address, "activityBtnItem"))
            except FanxiuRuntimeMemoryError:
                continue
            if content is None or template is None:
                continue
            candidates[(component.address, content.address)] = _discover_group_sequences(
                ctx, content
            )
        return candidates

    candidates = collect(_component_objects(ctx))
    if not candidates:
        # A real ActivityBtnGroup can be a child below the active panel.  Do
        # this bounded fallback only after the direct, previously proven path
        # has no candidate, so ordinary world reads do not traverse all UI.
        candidates = collect(_component_objects(ctx, include_descendants=True))
    if not candidates:
        raise FanxiuRuntimeMemoryError(
            "特惠等活动分组的次级弹层尚未自然加载",
            code="data_not_loaded",
        )
    if len(candidates) != 1:
        raise FanxiuRuntimeMemoryError(
            f"活动分组弹层对象不唯一：{len(candidates)} 个候选",
            code="runtime_incomplete",
        )
    (panel_address, content_address), items = next(iter(candidates.items()))
    return panel_address, content_address, items


def _fingerprint(kind: ActivityMenuKind, items: tuple[ActivityMenuItem, ...]) -> str:
    canonical = json.dumps(
        {"kind": kind, "items": [asdict(item) for item in items]},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_activity_menu_snapshot(kind: ActivityMenuKind) -> ActivityMenuSnapshot:
    """Read one currently loaded menu, returning explicit NotLoaded state."""

    global _world_left_binding
    if kind not in {"world_left", "group_popup"}:
        raise ValueError(f"unsupported activity menu kind: {kind}")
    total_started = time.perf_counter()
    ctx = acquire_ui_runtime_context_fast(_MENU_KEYS)
    binding_done = time.perf_counter()
    locate_started = binding_done
    cache_mode = f"{ctx.cache_mode}/relocated"
    try:
        if kind == "world_left":
            with _CACHE_LOCK:
                cached = _world_left_binding
            if cached is not None:
                try:
                    owner_address, list_address, schema = _validate_world_left_binding(
                        ctx, cached
                    )
                    cache_mode = f"{ctx.cache_mode}/object-hot"
                except FanxiuRuntimeMemoryError:
                    owner_address, list_address, schema = _locate_world_left(ctx)
            else:
                owner_address, list_address, schema = _locate_world_left(ctx)
            locate_done = time.perf_counter()
            if schema == "main_ui_cur_list":
                items = _decode_main_ui_row_pool(ctx, list_address)
            else:
                items = _decode_c_list(ctx, list_address, "#34 左侧活动菜单")
            with _CACHE_LOCK:
                _world_left_binding = _WorldLeftBinding(
                    pid=ctx.binding.pid,
                    process_start_ticks=ctx.binding.process_start_ticks,
                    owner_address=owner_address,
                    list_address=list_address,
                    schema=schema,
                )
        else:
            # The same popup instance can render a different group after every
            # UpdateView call.  Always rebind its current list instead of using
            # an old list address as authority.
            _panel, _content, items = _locate_group_popup(ctx)
            locate_done = time.perf_counter()
            cache_mode = f"{ctx.cache_mode}/current-view-rebound"
        decode_done = time.perf_counter()
    except FanxiuRuntimeMemoryError as exc:
        if exc.code != "data_not_loaded":
            raise
        done = time.perf_counter()
        timings = ActivityMenuReadTimings(
            binding_ms=round((binding_done - total_started) * 1000, 3),
            locate_ms=round((done - locate_started) * 1000, 3),
            decode_ms=0.0,
            total_ms=round((done - total_started) * 1000, 3),
            cache_mode=f"{ctx.cache_mode}/not-loaded",
        )
        return ActivityMenuSnapshot(
            kind=kind,
            status="not_loaded",
            complete=False,
            items=(),
            pid=ctx.binding.pid,
            process_start_ticks=ctx.binding.process_start_ticks,
            fingerprint="",
            reason=str(exc),
            timings=timings,
        )
    return ActivityMenuSnapshot(
        kind=kind,
        status="loaded",
        complete=True,
        items=items,
        pid=ctx.binding.pid,
        process_start_ticks=ctx.binding.process_start_ticks,
        fingerprint=_fingerprint(kind, items),
        reason="当前活动菜单已完整加载",
        timings=ActivityMenuReadTimings(
            binding_ms=round((binding_done - total_started) * 1000, 3),
            locate_ms=round((locate_done - locate_started) * 1000, 3),
            decode_ms=round((decode_done - locate_done) * 1000, 3),
            total_ms=round((decode_done - total_started) * 1000, 3),
            cache_mode=cache_mode,
        ),
    )


__all__ = [
    "ActivityMenuItem",
    "ActivityMenuKind",
    "ActivityMenuReadTimings",
    "ActivityMenuSnapshot",
    "ActivityMenuStatus",
    "clear_activity_menu_cache",
    "active_ui_component_objects",
    "read_ui_object_field",
    "read_activity_menu_snapshot",
]
