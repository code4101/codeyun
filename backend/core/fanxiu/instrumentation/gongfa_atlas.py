from __future__ import annotations

"""Read and persist every learned GongFa book and its live progression."""

import time
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.catalog.inventory_snapshot_store import (
    load_inventory_hall_snapshot,
    upsert_inventory_hall_snapshot,
)
from backend.core.fanxiu.instrumentation.gongfa_equipment import (
    _GONGFA_MARKER,
    _GONGFA_METHODS,
    _book_catalog_index,
    _gongfa_data_fields,
    _gongfa_progression_index,
    _progression_view,
    read_gongfa_equipment_book_plan_snapshot,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    LuaJitReader,
    MumuProcessMemory,
    resolve_manager_root,
)
from backend.db import engine
from backend.core.fanxiu.instrumentation.gongfa_priority import (
    apply_gongfa_priority_to_books,
    load_gongfa_priority_book_ids,
    priority_book_ids_from_snapshot,
)


GONGFA_ATLAS_KEY = "gongfa_atlas"
GONGFA_BOOK_SKILL_TYPES = frozenset({"神通", "心法"})


def _project_gongfa_books(books: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich stored rows from the static catalog and exclude the 秘术 catalog."""

    catalog = _book_catalog_index()
    projected: list[dict[str, Any]] = []
    for raw_book in books:
        book = dict(raw_book)
        book_id = int(book.get("book_id") or 0)
        knowledge = catalog.get(book_id) or {}
        book.update(knowledge)
        if str(book.get("skill_type_name") or "") not in GONGFA_BOOK_SKILL_TYPES:
            continue
        max_jie = int(book.get("max_jie") or 0)
        jie = int(book.get("jie") or 0)
        progression = _progression_view(book)
        book["max_grade"] = int(progression.get("level_cap") or 0)
        book["wujing"] = max(0, int(book.get("pin") or 1) - 1)
        book["full"] = bool(max_jie and jie >= max_jie)
        book["remaining_fusion"] = max(0, max_jie - jie) if max_jie else None
        projected.append(book)
    projected.sort(key=lambda item: (
        # Use the current equipment dependency plan. Equipped books keep
        # their exact main/xian/side + slot priority; other learned books are
        # fallback candidates ordered by quality grade from high to low.
        0 if item.get("upgrade_priority") is not None else 1,
        int(item.get("upgrade_priority") or 0),
        -int(item.get("quality_grade_order") or 0),
        -int(item.get("grade") or 0),
        int(item.get("book_id") or 0),
    ))
    for upgrade_index, book in enumerate(projected, start=1):
        book["upgrade_index"] = upgrade_index
    return projected


def _attach_upgrade_plan(
    books: list[dict[str, Any]],
    plan_books: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach the same current-equipment priority consumed by 日常_经验."""

    plan_by_book = {
        int(item.get("book_id") or 0): item
        for item in plan_books
        if int(item.get("book_id") or 0) > 0
    }
    attached: list[dict[str, Any]] = []
    for source in books:
        book = dict(source)
        plan = plan_by_book.get(int(book.get("book_id") or 0))
        if plan is None:
            book["upgrade_priority"] = None
            book["upgrade_priority_pool"] = "fallback_learned"
            book["upgrade_first_usage"] = None
            book["upgrade_usages"] = []
        else:
            book["upgrade_priority"] = int(plan.get("priority") or 0)
            book["upgrade_priority_pool"] = "equipped_dependency"
            book["upgrade_first_usage"] = dict(plan.get("first_usage") or {})
            book["upgrade_usages"] = [
                dict(usage)
                for usage in plan.get("usages") or []
                if isinstance(usage, dict)
            ]
        attached.append(book)
    return attached


def _snapshot_summary(books: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "learned_count": len(books),
        "full_count": sum(bool(book.get("full")) for book in books),
        "upgradeable_count": sum(not bool(book.get("full")) for book in books),
        "wujing_count": sum(int(book.get("wujing") or 0) > 0 for book in books),
        "tongxuan_count": sum(int(book.get("tongxuan") or 0) > 0 for book in books),
    }


def _apply_atlas_priority(
    books: list[dict[str, Any]],
    *,
    refresh_priority_from_equipment: bool,
) -> tuple[list[dict[str, Any]], list[int]]:
    return apply_gongfa_priority_to_books(
        books,
        [] if refresh_priority_from_equipment else load_gongfa_priority_book_ids(),
    )


def read_gongfa_atlas_runtime(
    *,
    refresh_priority_from_equipment: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    memory = MumuProcessMemory.discover_cached(max_age_seconds=None)
    reader = LuaJitReader(memory)
    root, cache_hit = resolve_manager_root(
        memory,
        manager_key="gongfa-equipment-state",
        marker=_GONGFA_MARKER,
        required_methods=_GONGFA_METHODS,
        validate=_gongfa_data_fields,
    )
    progression = _gongfa_progression_index(reader, _gongfa_data_fields(reader, root))
    catalog = _book_catalog_index()
    books: list[dict[str, Any]] = []
    for book_id, values in progression.items():
        knowledge = catalog.get(book_id) or {}
        star = int(values.get("star") or 0)
        max_star = int(values.get("max_star") or 0)
        books.append({
            "book_id": book_id,
            "name": str(knowledge.get("name") or f"功法 {book_id}"),
            "skill_type": knowledge.get("skill_type"),
            "skill_type_name": str(knowledge.get("skill_type_name") or ""),
            "filter_category": str(knowledge.get("filter_category") or "其他"),
            "quality_type_name": str(knowledge.get("quality_type_name") or ""),
            "sub_type_names": list(knowledge.get("sub_type_names") or []),
            **values,
            "full": bool(max_star and star >= max_star),
            "remaining_star": max(0, max_star - star) if max_star else None,
            "catalog_href": f"/standalone/fanxiu/wiki?tab=gongfa&id={book_id}",
        })
    upgrade_plan = read_gongfa_equipment_book_plan_snapshot()
    if upgrade_plan.get("complete") is not True:
        raise RuntimeError(
            "当前功法搭配的升级优先级读取不完整，拒绝写入错误图鉴顺序："
            f"{upgrade_plan.get('reason') or upgrade_plan.get('evidence')}"
        )
    books = _project_gongfa_books(
        _attach_upgrade_plan(books, list(upgrade_plan.get("books") or []))
    )
    books, priority_book_ids = _apply_atlas_priority(
        books,
        refresh_priority_from_equipment=refresh_priority_from_equipment,
    )
    return {
        "books": books,
        "priority_book_ids": priority_book_ids,
        "runtime_complete": bool(books),
        "runtime_error": "",
        "runtime_updated_at": time.time(),
        "runtime_item_count": len(books),
        "summary": _snapshot_summary(books),
        "runtime_debug": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "gongfa_root": f"0x{root:x}",
            "gongfa_root_cache_hit": cache_hit,
            "elapsed_seconds": time.perf_counter() - started,
            "protocol": "GongFaNewData.gongFaDic -> GongFaItemVO",
            "upgrade_plan_protocol": upgrade_plan.get("protocol"),
            "upgrade_plan_captured_at": upgrade_plan.get("captured_at"),
            "upgrade_plan_book_count": upgrade_plan.get("book_count"),
            "upgrade_plan_elapsed_seconds": upgrade_plan.get("elapsed_seconds"),
        },
    }


def collect_gongfa_atlas_snapshot_once() -> dict[str, Any]:
    # The page's 更新 action is the sole refresh point for the shared order:
    # reread the current game equipment and overwrite the previous priority.
    snapshot = read_gongfa_atlas_runtime(refresh_priority_from_equipment=True)
    if not snapshot.get("runtime_complete"):
        raise RuntimeError("个人功法动态插桩数据尚未完整加载")
    with Session(engine) as session:
        upsert_inventory_hall_snapshot(
            session,
            GONGFA_ATLAS_KEY,
            snapshot,
            source_kind="dynamic_instrumentation",
            entity_name="个人功法",
            require_complete_runtime=True,
        )
    return snapshot


def load_gongfa_atlas_snapshot(session: Session) -> dict[str, Any]:
    snapshot = load_inventory_hall_snapshot(session, GONGFA_ATLAS_KEY)
    if snapshot:
        books = _project_gongfa_books(list(snapshot.get("books") or []))
        books, priority_book_ids = apply_gongfa_priority_to_books(
            books,
            priority_book_ids_from_snapshot(snapshot),
        )
        return {
            **snapshot,
            "books": books,
            "priority_book_ids": priority_book_ids,
            "runtime_item_count": len(books),
            "summary": _snapshot_summary(books),
        }
    return {
        "books": [],
        "priority_book_ids": [],
        "runtime_complete": False,
        "runtime_error": "尚未从游戏更新",
        "runtime_updated_at": 0,
        "runtime_item_count": 0,
        "summary": {},
        "runtime_debug": {},
    }


def load_gongfa_atlas_book_detail(
    session: Session,
    book_id: int,
) -> dict[str, Any]:
    from backend.core.fanxiu.instrumentation.gongfa_detail import (
        build_gongfa_book_detail,
    )

    snapshot = load_gongfa_atlas_snapshot(session)
    book = next(
        (
            item
            for item in snapshot.get("books") or []
            if int(item.get("book_id") or 0) == int(book_id)
        ),
        None,
    )
    if book is None:
        raise KeyError(f"功法图鉴中没有 book_id={book_id}")
    return build_gongfa_book_detail(session, book)
