from __future__ import annotations

"""Explicit GUI loading of the current 炼体法相 personal rank page.

Runtime memory only exposes an activity leaderboard after the game client has
opened it, and only the currently loaded ``rankVOS`` window is readable at a
time.  This generator is the GUI half of the daily reconciliation: it selects
the exact scheduled occurrence, enters its introduction page, opens ``查看详情``
and switches to the personal tab, then scrolls the whole board and merges every
observed page into one complete fact.  The merge is persisted through the public
``store_runtime_activity_rank_fact`` API bound to ``occurrence.runtime_id`` so
later collectors keep the full board instead of the last page the client holds.
It never guesses a rank id; the caller seeds the occurrence through the public
``seed_ranking_occurrence`` API and passes ``activity.game_rank_activity_id``.
"""

from datetime import datetime
import time
from typing import Any, Iterator


SCHEDULE_SCENE_ID = 66
WORLD_SCENE_ID = 34
LIANTI_FAXIANG_INTRO_SCENE_ID = 754
LIANTI_FAXIANG_RANK_SCENE_ID = 755
LIANTI_FAXIANG_PERSONAL_TAB_INDEX = 0
LIANTI_FAXIANG_INTRO_DETAIL_SHAPE = "查看详情"
LIANTI_FAXIANG_PERSONAL_TAB_SHAPE = "个人"
LIANTI_FAXIANG_RETURN_SHAPE = "返回"
LIANTI_FAXIANG_ENTRY_TIMEOUT_SECONDS = 30.0
LIANTI_FAXIANG_CLICK_TIMEOUT_SECONDS = 20.0
LIANTI_FAXIANG_RANK_LIST_SHAPE = "排名列表"
# Runtime only exposes the loaded ``rankVOS`` window (the first read may hold
# 1..50 while rankListSize is 60).  The collector drags the proven live gesture
# (ratio 0.8 / duration 1.2 / cross-axis 0.08) and re-reads after a 0.7s settle;
# a short 0.35s centre drag is not reliable.  Bounds keep one Cell from drifting
# forever on a stuck or changing board.
LIANTI_FAXIANG_RANK_MAX_DRAGS = 40
LIANTI_FAXIANG_RANK_COLLECT_DEADLINE_SECONDS = 180.0
LIANTI_FAXIANG_RANK_DRAG_RATIO = 0.8
LIANTI_FAXIANG_RANK_DRAG_DURATION_SECONDS = 1.2
LIANTI_FAXIANG_RANK_DRAG_CROSS_AXIS_RATIO = 0.08
LIANTI_FAXIANG_RANK_DRAG_SETTLE_SECONDS = 0.7
LIANTI_FAXIANG_RANK_READ_RETRY_SECONDS = 0.5


def _normalized_now(now: datetime | None) -> datetime:
    current = now or datetime.now()
    if current.tzinfo is None:
        return current.astimezone()
    return current


def _occurrence_day_offset(occurrence: Any, now: datetime) -> int:
    """Locate the occurrence's calendar cell relative to ``now``.

    The panel's close time permits reading the settled leaderboard; it does
    not extend the event's calendar row. Locate a day inside start/end while
    checking panel access against the real clock.
    """

    start_at = occurrence.start_at
    close_at = occurrence.close_at
    if not (start_at <= now <= close_at):
        raise RuntimeError(
            "炼体法相榜单刷新：当前时刻不在实例有效窗口内，无法定位日历单元"
        )
    local_now = now.astimezone(start_at.tzinfo)
    calendar_day = min(local_now.date(), occurrence.end_at.date())
    return (calendar_day - local_now.date()).days


def _open_lianti_rank_scene(context: Any) -> Iterator[Any]:
    """Wait for the intro or rank scene and open ``查看详情`` when needed."""

    match = yield from context.wait_scene(
        [LIANTI_FAXIANG_INTRO_SCENE_ID, LIANTI_FAXIANG_RANK_SCENE_ID],
        wait=LIANTI_FAXIANG_ENTRY_TIMEOUT_SECONDS,
        label="炼体法相榜单刷新：等待活动介绍或榜单页",
    )
    scene_id = int(match.scene_id)
    if scene_id == LIANTI_FAXIANG_RANK_SCENE_ID:
        return LIANTI_FAXIANG_RANK_SCENE_ID
    if scene_id != LIANTI_FAXIANG_INTRO_SCENE_ID:
        raise RuntimeError(
            f"炼体法相榜单刷新：进入后停留于非预期场景 #{scene_id}"
        )
    yield from context.click_shape_center_then_scene(
        LIANTI_FAXIANG_INTRO_SCENE_ID,
        LIANTI_FAXIANG_INTRO_DETAIL_SHAPE,
        LIANTI_FAXIANG_RANK_SCENE_ID,
        timeout=LIANTI_FAXIANG_CLICK_TIMEOUT_SECONDS,
        label="炼体法相榜单刷新：活动介绍查看详情",
    )
    return LIANTI_FAXIANG_RANK_SCENE_ID


def collect_lianti_rank_page(
    context: Any,
    *,
    activity_id: int,
    rank_activity_id: int,
    label: str,
    use_ui_rows: bool = True,
    loaded_page_only: bool = False,
    reload_first_page: Any = None,
    rank_scene_id: int = LIANTI_FAXIANG_RANK_SCENE_ID,
    rank_list_shape: str = LIANTI_FAXIANG_RANK_LIST_SHAPE,
) -> Iterator[Any]:
    """Collect the personal board from the live UI cache, then merge.

    The cross-server rank panel caches every row the client has loaded in
    ``V_RankDic``; that UI cache is preferred over the manager window, which only
    holds the current ``rankVOS`` tail.  While the UI cache is not complete the
    collector only scrolls down to load the next page, and gets no help from the
    manager.  Two consecutive complete reads with a score-inclusive signature
    are required before merging, and the bounds (40 drags / 180 s) stay in force.
    A local (server) small board still reads from the manager.
    """

    from backend.core.fanxiu.activity.rank_page_merge import (
        merge_activity_rank_pages,
    )
    from backend.core.fanxiu.instrumentation.activity_rank_page import (
        read_activity_rank_page_rows_snapshot,
        read_activity_rank_page_snapshot,
    )
    from backend.core.fanxiu.instrumentation.activity_rank_runtime import (
        prepare_activity_rank_runtime,
        read_activity_rank_runtime_snapshot,
    )

    if not use_ui_rows:
        # Rebinding is explicit preparation, separate from the collection budget.
        initial = read_activity_rank_runtime_snapshot(int(rank_activity_id))
        if initial.get("error_code") in {"process_cache_miss", "root_cache_miss"}:
            recovery = prepare_activity_rank_runtime([int(rank_activity_id)])
            if not recovery.get("ok"):
                raise RuntimeError(
                    f"{label}：榜单 Runtime 准备失败：{recovery.get('reason')}"
                )
    deadline = time.monotonic() + LIANTI_FAXIANG_RANK_COLLECT_DEADLINE_SECONDS
    pages: list[dict[str, Any]] = []
    seen_page_signatures: set[tuple[Any, ...]] = set()
    ranks_seen: set[int] = set()
    total: int | None = None
    coverage_signature: tuple[Any, ...] | None = None
    coverage_hits = 0
    drags = 0
    final_page: dict[str, Any] = {}
    final_snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        if use_ui_rows:
            snapshot = read_activity_rank_page_rows_snapshot(
                int(activity_id), int(rank_activity_id), loaded_page_only=loaded_page_only,
            )
            # Page identity and its cached rows share one Runtime observation.
            page = {key: snapshot.get(key) for key in
                    ('ok', 'activity_id', 'page_kind', 'tab_index', 'captured_at', 'evidence')}
            page['complete'] = bool(snapshot.get('ok'))
        else:
            page = read_activity_rank_page_snapshot()
            snapshot = read_activity_rank_runtime_snapshot(int(rank_activity_id))
            if snapshot.get("error_code") in {
                "process_cache_miss",
                "root_cache_miss",
            }:
                recovery = prepare_activity_rank_runtime(
                    [int(rank_activity_id)], allow_discovery=False,
                )
                if bool(recovery.get("ok")):
                    snapshot = read_activity_rank_runtime_snapshot(
                        int(rank_activity_id)
                    )
        page_complete = bool(
            page.get("ok")
            and page.get("complete")
            and int(page.get("activity_id") or 0) == int(activity_id)
            and page.get("tab_index") == LIANTI_FAXIANG_PERSONAL_TAB_INDEX
        )
        rank_ok = bool(
            snapshot.get("ok")
            and int(snapshot.get("rank_activity_id") or 0) == int(rank_activity_id)
            and int(snapshot.get("rank_list_size") or 0) > 0
            and int(snapshot.get("loaded_rank_count") or 0) > 0
        )
        if not (page_complete and rank_ok):
            yield from context.wait_action_settle(
                LIANTI_FAXIANG_RANK_READ_RETRY_SECONDS
            )
            continue
        rows = [
            (
                int(row.get("rank") or 0),
                str(row.get("role_key") or row.get("key") or ""),
                int(row.get("score") or 0),
            )
            for row in snapshot.get("rankings") or []
            if isinstance(row, dict)
        ]
        ranks = sorted(rank for rank, _role_key, _score in rows)
        if not ranks:
            yield from context.wait_action_settle(
                LIANTI_FAXIANG_RANK_READ_RETRY_SECONDS
            )
            continue
        # The signature includes score so a value change can never be mistaken
        # for a stable identical page.
        evidence = snapshot.get("evidence") or {}
        signature = (
            evidence.get("pid"), evidence.get("process_start_ticks"),
            tuple(sorted((snapshot.get("self_ranking") or {}).items())),
            tuple(sorted(rows)),
        )
        snapshot_total = int(snapshot["rank_list_size"])
        if total is None:
            total = snapshot_total
        elif snapshot_total != total:
            # An open board gains entrants while it is being read. Invalidate
            # old coverage and prove the new total within the original budget;
            # normal growth is not a broken Runtime contract.
            total = snapshot_total
            pages.clear()
            seen_page_signatures.clear()
            ranks_seen.clear()
            coverage_signature = None
            coverage_hits = 0
        # A partial board may still contain every row of its loaded page. Keep
        # those pages; the merger proves global coverage and identity coherence.
        if (snapshot.get("complete") is True or snapshot.get("partial") is True) and signature not in seen_page_signatures:
            seen_page_signatures.add(signature)
            pages.append(snapshot)
        ranks_seen.update(ranks)
        covered = set(range(1, total + 1)).issubset(ranks_seen) and bool(pages)
        if loaded_page_only and 1 not in ranks_seen and reload_first_page is not None:
            # Growth can invalidate the old head while the response is already
            # at the tail. Re-read the exact occurrence's head, retaining only
            # pages bound to the new total. Scrolling farther down cannot fill
            # that missing head.
            yield from reload_first_page()
            continue
        if covered:
            if signature == coverage_signature:
                coverage_hits += 1
            else:
                coverage_signature = signature
                coverage_hits = 1
            if coverage_hits >= 2:
                final_page = page
                final_snapshot = snapshot
                break
            yield from context.wait_action_settle(
                LIANTI_FAXIANG_RANK_READ_RETRY_SECONDS
            )
            continue
        coverage_signature = None
        coverage_hits = 0
        if drags >= LIANTI_FAXIANG_RANK_MAX_DRAGS:
            raise RuntimeError(
                f"{label}：榜单收集超过 {LIANTI_FAXIANG_RANK_MAX_DRAGS} 次拖动"
                f"仍未完整（已收 {len(ranks_seen)}/{total}）"
            )
        # The UI cache accumulates rows as the list is scrolled, so only load
        # the next page downward; never rewind through the manager tail.
        # Reading the full UI table costs ~13 s on the live client; one drag
        # moves only a few visible rows while the server loads batches of 50.
        # Let the proven UI cache accumulate across a bounded batch of drags,
        # then read it once. Manager windows are read after every drag because
        # they do not retain earlier batches.
        for _ in range(min(5 if use_ui_rows else 1,
                           LIANTI_FAXIANG_RANK_MAX_DRAGS - drags)):
            context.drag_shape_content(
                rank_scene_id,
                rank_list_shape,
                direction="down",
                ratio=LIANTI_FAXIANG_RANK_DRAG_RATIO,
                duration=LIANTI_FAXIANG_RANK_DRAG_DURATION_SECONDS,
                cross_axis_ratio=LIANTI_FAXIANG_RANK_DRAG_CROSS_AXIS_RATIO,
            )
            drags += 1
            yield from context.wait_action_settle(
                LIANTI_FAXIANG_RANK_DRAG_SETTLE_SECONDS
            )
    else:
        raise RuntimeError(
            f"{label}：{LIANTI_FAXIANG_RANK_COLLECT_DEADLINE_SECONDS:.0f} 秒内"
            f"未收集完整榜单（已收 {len(ranks_seen)}/{total or '?'}，"
            f"拖动 {drags} 次）"
        )
    merged = merge_activity_rank_pages(
        # A complete UI cache is already the whole board. Older complete reads
        # can contain former scores/ranks and must not be unioned with the
        # final stable observation. Manager windows still need page merging.
        [final_snapshot] if use_ui_rows and final_snapshot.get("complete") is True else pages,
        rank_activity_id=int(rank_activity_id),
        captured_at=str(final_snapshot.get("captured_at") or ""),
    )
    return {"page": final_page, "rank": merged}


def refresh_lianti_faxiang_rank_page(
    context: Any,
    *,
    occurrence: Any,
    now: datetime,
    rank_activity_id: int | None = None,
) -> Iterator[Any]:
    """Explicitly load the occurrence's personal rank page and return world.

    The caller must seed ``occurrence`` through the public
    ``seed_ranking_occurrence`` API and pass the persisted public
    ``activity.game_rank_activity_id`` as ``rank_activity_id``.  No rank id is
    guessed here, and no public config resolver exists to derive it.
    """

    from backend.core.fanxiu.activity.lianti_faxiang import (
        LIANTI_FAXIANG_ACTIVITY_TYPE,
        LIANTI_FAXIANG_OFFICIAL_NAME,
    )
    from backend.core.fanxiu.data_annotation.schedule_navigation import (
        select_schedule_activity,
    )

    if str(occurrence.activity_type) != LIANTI_FAXIANG_ACTIVITY_TYPE:
        raise ValueError(
            "炼体法相榜单刷新收到非炼体法相实例："
            f"{occurrence.activity_type}"
        )
    if rank_activity_id is None or int(rank_activity_id) <= 0:
        raise RuntimeError(
            "炼体法相榜单刷新缺少个人榜绑定身份：公共配置 resolver 尚未提供，"
            "调用方须在 seed_ranking_occurrence 后传入 "
            "activity.game_rank_activity_id"
        )
    rank_id = int(rank_activity_id)
    current = _normalized_now(now)
    day_offset = _occurrence_day_offset(occurrence, current)

    yield from context.go_scene(WORLD_SCENE_ID)
    yield from context.go_scene(SCHEDULE_SCENE_ID)
    selected = yield from select_schedule_activity(
        context,
        LIANTI_FAXIANG_OFFICIAL_NAME,
        day_offset=day_offset,
        enter=True,
        require_runtime_alignment=True,
        expected_activity_id=int(occurrence.activity_id),
        expected_runtime_id=str(occurrence.runtime_id),
        expected_cross_count=int(occurrence.cross_count),
        now=current,
    )
    if not str(getattr(selected, "runtime_key", "") or ""):
        raise RuntimeError("炼体法相榜单刷新：#66 未回读精确 Runtime 实例标识")

    yield from _open_lianti_rank_scene(context)
    from backend.core.fanxiu.instrumentation.activity_rank_page import (
        PAGE_KIND_LOCAL_RANK,
        PAGE_KIND_SERVER_RANK,
        read_activity_rank_page_snapshot,
    )

    # Read the public page contract before touching any tab.  The local
    # (server) 炼体 board has only the bottom 榜单奖励 tab and no 个人/位面 tab,
    # so an unconditional 个人 click would fail on a real page that is already
    # correct.  Cross-server boards still switch through 个人, but only when the
    # real current index proves it is not already selected.
    page = read_activity_rank_page_snapshot()
    if not page.get("ok") or not page.get("complete"):
        raise RuntimeError(
            "炼体法相榜单刷新：活动榜页面未完整加载："
            f"{page.get('reason') or page.get('error_code') or 'unknown'}"
        )
    if int(page.get("activity_id") or 0) != int(occurrence.activity_id):
        raise RuntimeError(
            "炼体法相榜单刷新：活动榜页面活动身份与实例不一致："
            f"page={page.get('activity_id')}, occurrence={int(occurrence.activity_id)}"
        )
    page_kind = str(page.get("page_kind") or "")
    page_tab_index = int(page.get("tab_index") or 0)
    if page_kind == PAGE_KIND_LOCAL_RANK:
        # No 个人 tab exists here; the observed index must be the real 0.
        if page_tab_index != LIANTI_FAXIANG_PERSONAL_TAB_INDEX:
            raise RuntimeError(
                "炼体法相榜单刷新：本服榜单无个人页签，页签索引却为 "
                f"{page_tab_index}"
            )
    elif page_kind == PAGE_KIND_SERVER_RANK:
        if page_tab_index != LIANTI_FAXIANG_PERSONAL_TAB_INDEX:
            yield from context.wait_click(
                LIANTI_FAXIANG_RANK_SCENE_ID,
                LIANTI_FAXIANG_PERSONAL_TAB_SHAPE,
                timeout=LIANTI_FAXIANG_CLICK_TIMEOUT_SECONDS,
                label="炼体法相榜单刷新：切换到个人榜",
            )
    else:
        raise RuntimeError(f"炼体法相榜单刷新：活动榜页面类型未知：{page_kind!r}")
    settled = yield from collect_lianti_rank_page(
        context,
        activity_id=int(occurrence.activity_id),
        rank_activity_id=rank_id,
        label="炼体法相榜单刷新",
        use_ui_rows=page_kind == PAGE_KIND_SERVER_RANK,
    )
    merged = settled["rank"]
    # The client only holds one loaded page, so the complete merged board is
    # persisted here, bound to the exact occurrence.  This explicit load/sync is
    # why later collectors can read the full fact instead of the ~10 rows the
    # client still has on screen; nothing is written unless the merge is whole.
    from backend.core.fanxiu.activity.standard_observation import (
        store_runtime_activity_rank_fact,
    )
    from backend.db import engine
    from sqlmodel import Session

    with Session(engine) as session:
        store_runtime_activity_rank_fact(
            session,
            merged,
            occurrence_runtime_id=str(occurrence.runtime_id),
        )
        session.commit()
    yield from context.click_shape_center_then_scene(
        LIANTI_FAXIANG_RANK_SCENE_ID,
        LIANTI_FAXIANG_RETURN_SHAPE,
        SCHEDULE_SCENE_ID,
        timeout=LIANTI_FAXIANG_CLICK_TIMEOUT_SECONDS,
        label="炼体法相榜单刷新：返回日程",
    )
    yield from context.go_scene(WORLD_SCENE_ID)
    evidence_pages = merged.get("evidence", {}).get("pages") or []
    return {
        "status": "completed",
        "phase": "lianti_faxiang_rank_page_refresh",
        "activity_type": LIANTI_FAXIANG_ACTIVITY_TYPE,
        "activity_id": int(occurrence.activity_id),
        "runtime_id": str(occurrence.runtime_id),
        "rank_activity_id": rank_id,
        "day_offset": day_offset,
        "page_activity_id": int(settled["page"].get("activity_id") or 0),
        "tab_index": int(settled["page"].get("tab_index") or 0),
        "rank_list_size": int(merged.get("rank_list_size") or 0),
        "declared_rank_count": int(merged.get("declared_rank_count") or 0),
        "loaded_rank_count": int(merged.get("loaded_rank_count") or 0),
        "pages_collected": len(evidence_pages),
        "persisted": True,
    }


__all__ = [
    "collect_lianti_rank_page",
    "LIANTI_FAXIANG_INTRO_SCENE_ID",
    "LIANTI_FAXIANG_RANK_SCENE_ID",
    "refresh_lianti_faxiang_rank_page",
]
