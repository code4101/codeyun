"""Read the active ActivityRankServerMainView identity, without invoking Lua."""

import time
from datetime import datetime
from typing import Any

from backend.core.fanxiu.instrumentation.activity_rank_runtime import (
    project_ranking_row,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    as_int,
    table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    active_ui_component_objects,
    has_ui_object_fields,
    read_ui_object_field,
    read_ui_runtime_snapshot,
)


# The cross-server view exposes the activity id directly.  The local (server)
# view instead nests it in ``activityInfo``/``params`` and has no personal or
# plane tabs; both share the same cfg/tab schema below these identity fields.
_SERVER_RANK_FIELDS = frozenset(
    {"activityId", "containRankId", "cfgTable", "tabPanelGroup", "FirstTabBtnItem"}
)
_LOCAL_RANK_FIELDS = frozenset(
    {
        "activityInfo",
        "activityRankCfg",
        "cfgTable",
        "tabPanelGroup",
        "FirstTabBtnItem",
        "params",
    }
)
PAGE_KIND_SERVER_RANK = "server_rank"
PAGE_KIND_LOCAL_RANK = "local_rank"


def _rank_page_kind(ctx, address: int) -> str | None:
    if has_ui_object_fields(ctx, address, _LOCAL_RANK_FIELDS):
        return PAGE_KIND_LOCAL_RANK
    if has_ui_object_fields(ctx, address, _SERVER_RANK_FIELDS):
        return PAGE_KIND_SERVER_RANK
    return None


def _snapshot(ctx) -> dict[str, Any]:
    candidates = []
    for obj in active_ui_component_objects(ctx):
        kind = _rank_page_kind(ctx, obj.address)
        if kind is not None:
            candidates.append((obj, kind))
    # Both schemas share the cfg/tab fields, so a pooled panel must never be
    # counted twice; the combined candidate set still has to be exactly one.
    if len(candidates) != 1:
        raise FanxiuRuntimeMemoryError("活动榜主面板未唯一加载", code="data_not_loaded")
    panel_ref, page_kind = candidates[0]
    panel = ctx.reader.fields(panel_ref)
    tabs = ctx.reader.fields(panel.get("tabPanelGroup"))
    tab_index = as_int(tabs.get("curTabIndex"))
    if tab_index is None or tab_index < 0:
        raise FanxiuRuntimeMemoryError("活动榜页签索引不完整", code="runtime_incomplete")
    if page_kind == PAGE_KIND_LOCAL_RANK:
        activity_info = ctx.reader.fields(panel.get("activityInfo"))
        params = ctx.reader.fields(panel.get("params"))
        info_id = as_int(activity_info.get("activityId"))
        params_id = as_int(params.get("activityId"))
        if (
            not info_id
            or info_id <= 0
            or not params_id
            or params_id <= 0
            or int(info_id) != int(params_id)
        ):
            raise FanxiuRuntimeMemoryError(
                "本服活动榜 activityInfo/params 身份不完整或不一致",
                code="runtime_incomplete",
            )
        activity_id = int(info_id)
    else:
        activity_id = as_int(panel.get("activityId"))
        if not activity_id or activity_id <= 0:
            raise FanxiuRuntimeMemoryError("活动榜面板 ID 不完整", code="runtime_incomplete")
        activity_id = int(activity_id)
    return {"ok": True, "complete": True, "activity_id": activity_id,
            "page_kind": page_kind,
            "tab_index": tab_index,
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "evidence": {"pid": ctx.memory.pid, "process_start_ticks": ctx.memory.process_start_ticks,
                         "source": "active_ui_registry", "page_kind": page_kind}}


def read_activity_rank_page_snapshot() -> dict[str, Any]:
    """Call after scene readiness; cached rank data never proves an open page."""
    try:
        return read_ui_runtime_snapshot((), _snapshot, fast=True)
    except FanxiuRuntimeMemoryError as exc:
        return {"ok": False, "complete": False, "activity_id": None,
                "page_kind": None, "tab_index": None,
                "error_code": exc.code, "reason": str(exc)}


# Field names consumed while walking the live rank panel.  They are interned so
# the direct hashed-field reads below stay exact and do not scan the table.
_ROW_READER_KEYS = frozenset(
    {
        "tabPanelGroup",
        "panelShowComps",
        "curTabIndex",
        "m_panel",
        "activityId",
        "activityRankId",
        "V_RankActivityId",
        "V_RankDic",
        "V_ActivityListVO",
        "V_RankList",
        "selfRankVO",
        "rankListSize",
        "rankVOS",
    }
)


def _rank_id_entries(reader: Any, value: Any) -> dict[int, Any]:
    """Collect an ``activityRankId`` table keyed by numeric rank activity id.

    The live field is an ordinary table with large integer keys; a
    dictionary-backed variant is also accepted.  ``V_RankList`` (bare numbers)
    is deliberately never consulted.
    """

    ref = table_ref(value)
    if ref is None:
        return {}
    entries: dict[int, Any] = {}
    for key, item in reader.fields(ref).items():
        numeric = as_int(key)
        if numeric is not None:
            entries[numeric] = item
    for key, item in reader.dictionary_fields(ref).items():
        numeric = as_int(key)
        if numeric is not None:
            entries.setdefault(numeric, item)
    return entries


def _server_rank_panels(ctx: Any) -> list[Any]:
    return [
        obj
        for obj in active_ui_component_objects(ctx)
        if _rank_page_kind(ctx, obj.address) == PAGE_KIND_SERVER_RANK
    ]


def _rows_snapshot(ctx: Any, activity_id: int, rank_activity_id: int) -> dict[str, Any]:
    started = time.perf_counter()
    panels = _server_rank_panels(ctx)
    if len(panels) != 1:
        raise FanxiuRuntimeMemoryError(
            "活动榜跨服主面板未唯一加载", code="data_not_loaded"
        )
    panel = int(panels[0].address)
    if as_int(read_ui_object_field(ctx, panel, "activityId")) != int(activity_id):
        raise FanxiuRuntimeMemoryError("活动榜主面板身份不一致", code="snapshot_incoherent")
    tab_group = table_ref(read_ui_object_field(ctx, panel, "tabPanelGroup"))
    if tab_group is None:
        raise FanxiuRuntimeMemoryError(
            "活动榜主面板缺少页签组", code="runtime_incomplete"
        )
    show_comps = read_ui_object_field(ctx, tab_group.address, "panelShowComps")
    tab_index = as_int(read_ui_object_field(ctx, tab_group.address, "curTabIndex"))
    if tab_index is None or tab_index < 0:
        raise FanxiuRuntimeMemoryError("活动榜页签未加载", code="runtime_incomplete")
    children, tail_declared = ctx.reader.list_items(show_comps)
    rank_panels = []
    for child in children:
        child_ref = table_ref(child)
        if child_ref is None:
            continue
        child_panel = table_ref(
            read_ui_object_field(ctx, child_ref.address, "m_panel")
        )
        if child_panel is not None:
            rank_panels.append(child_panel)
    matched = [
        rank_panel
        for rank_panel in rank_panels
        if as_int(read_ui_object_field(ctx, rank_panel.address, "activityId"))
        == int(activity_id)
    ]
    if len(matched) != 1:
        raise FanxiuRuntimeMemoryError(
            f"活动榜跨服面板 activityId={int(activity_id)} 未唯一命中",
            code="data_not_loaded",
        )
    rank_panel = matched[0]
    rank_id_table = read_ui_object_field(ctx, rank_panel.address, "activityRankId")
    rank_value = _rank_id_entries(ctx.reader, rank_id_table).get(int(rank_activity_id))
    rank_ref = table_ref(rank_value)
    if rank_ref is None:
        raise FanxiuRuntimeMemoryError(
            f"活动榜 {int(rank_activity_id)} 的 UI 缓存未加载", code="data_not_loaded"
        )
    if as_int(read_ui_object_field(ctx, rank_ref.address, "V_RankActivityId")) != int(rank_activity_id):
        raise FanxiuRuntimeMemoryError("活动榜 UI 缓存身份不一致", code="snapshot_incoherent")
    activity_list = table_ref(
        read_ui_object_field(ctx, rank_ref.address, "V_ActivityListVO")
    )
    if activity_list is None:
        raise FanxiuRuntimeMemoryError("活动榜详情 VO 缺失", code="runtime_incomplete")
    vo = ctx.reader.fields(activity_list)
    if as_int(vo.get("activityId")) != int(rank_activity_id):
        raise FanxiuRuntimeMemoryError(
            "活动榜详情 VO 的 activityId 与目标不一致", code="snapshot_incoherent"
        )
    total = as_int(vo.get("rankListSize"))
    if total is None or int(total) <= 0:
        raise FanxiuRuntimeMemoryError("活动榜总人数无效", code="snapshot_incoherent")
    self_row = project_ranking_row(ctx.reader, vo.get("selfRankVO"))
    if self_row is None:
        raise FanxiuRuntimeMemoryError("活动榜自身排名无效", code="snapshot_incoherent")
    rank_dic_value = read_ui_object_field(ctx, rank_ref.address, "V_RankDic")
    rank_dic = (
        ctx.reader.dictionary_fields(rank_dic_value)
        if rank_dic_value is not None
        else {}
    )
    rankings: list[dict[str, Any]] = []
    for key, item in rank_dic.items():
        rank_key = as_int(key)
        if rank_key is None:
            continue
        row = project_ranking_row(ctx.reader, item)
        if row is None:
            continue
        if int(row["rank"]) != int(rank_key):
            raise FanxiuRuntimeMemoryError(
                f"活动榜 rank key {rank_key} 与行 rank {row['rank']} 不一致",
                code="snapshot_incoherent",
            )
        rankings.append(row)
    rankings.sort(key=lambda row: int(row["rank"]))
    loaded = len(rankings)
    declared = len(rank_dic)
    total_int = int(total)
    complete = loaded == total_int and [
        int(row["rank"]) for row in rankings
    ] == list(range(1, total_int + 1))
    return {
        "ok": True,
        "available": True,
        "complete": bool(complete),
        "partial": bool(0 < loaded < total_int),
        "source": "active_ui_rank_panel",
        "activity_id": int(activity_id),
        "page_kind": PAGE_KIND_SERVER_RANK,
        "tab_index": tab_index,
        "rank_activity_id": int(rank_activity_id),
        "rank_list_size": total_int,
        "loaded_rank_count": loaded,
        "declared_rank_count": declared,
        "self_ranking": self_row,
        "rankings": rankings,
        "runtime_object_identity": ":".join(
            str(address) for address in (panel, rank_panel.address, rank_ref.address)
        ),
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_seconds": time.perf_counter() - started,
        "evidence": {
            "pid": ctx.memory.pid,
            "process_start_ticks": ctx.memory.process_start_ticks,
            "source": "active_ui_rank_panel",
            "panel_address": f"0x{panel:x}",
            "rank_panel_address": f"0x{rank_panel.address:x}",
            "tail_declared": tail_declared,
        },
    }


def read_activity_rank_page_rows_snapshot(
    activity_id: int, rank_activity_id: int
) -> dict[str, Any]:
    """Read the full rank rows cached by the live cross-server UI panel.

    Unlike the manager snapshot (which only exposes the loaded ``rankVOS``
    window), the UI rank panel keeps ``V_RankDic`` with every row the client has
    loaded, keyed by rank.  The walk is: active component with the server-rank
    schema -> ``tabPanelGroup.panelShowComps`` children -> each child's
    ``m_panel`` -> the rank panel whose ``activityId`` matches -> its
    ``activityRankId`` table -> ``V_ActivityListVO``/``V_RankDic``.  Identity,
    field completeness and rank-key/row.rank agreement are enforced; a partially
    loaded cache is returned with ``partial`` set rather than being padded or
    silently replaced by the manager tail.
    """

    started = time.perf_counter()
    base = {
        "scope": "rows",
        "source": "active_ui_rank_panel",
        "rank_activity_id": int(rank_activity_id),
        "rankings": [],
    }
    try:
        return read_ui_runtime_snapshot(
            _ROW_READER_KEYS,
            lambda ctx: _rows_snapshot(ctx, int(activity_id), int(rank_activity_id)),
            fast=True,
        )
    except Exception as exc:
        error_code = (
            exc.code if isinstance(exc, FanxiuRuntimeMemoryError) else "unexpected_error"
        )
        return {
            **base,
            "ok": False,
            "available": False,
            "complete": False,
            "partial": False,
            "error_code": error_code,
            "reason": str(exc),
            "recovery_required": error_code in {
                "process_cache_miss",
                "root_cache_miss",
                "data_not_loaded",
            },
            "rank_list_size": 0,
            "loaded_rank_count": 0,
            "declared_rank_count": 0,
            "self_ranking": None,
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "elapsed_seconds": time.perf_counter() - started,
            "evidence": {
                "pid": None,
                "process_start_ticks": None,
                "source": "active_ui_rank_panel",
            },
        }

