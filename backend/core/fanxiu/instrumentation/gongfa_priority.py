from __future__ import annotations

"""One durable GongFa priority shared by the atlas and automation jobs."""

from copy import deepcopy
from typing import Any, Iterable

from sqlmodel import Session

from backend.core.fanxiu.catalog.inventory_snapshot_store import (
    load_inventory_hall_snapshot,
)
from backend.db import engine


GONGFA_ATLAS_KEY = "gongfa_atlas"


def normalize_gongfa_priority_book_ids(values: Iterable[Any]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            book_id = int(value or 0)
        except (TypeError, ValueError):
            continue
        if book_id <= 0 or book_id in seen:
            continue
        seen.add(book_id)
        result.append(book_id)
    return result


def priority_book_ids_from_snapshot(snapshot: dict[str, Any] | None) -> list[int]:
    if not isinstance(snapshot, dict):
        return []
    configured = normalize_gongfa_priority_book_ids(
        snapshot.get("priority_book_ids") or []
    )
    if configured:
        return configured
    books = [book for book in snapshot.get("books") or [] if isinstance(book, dict)]
    books.sort(key=lambda book: int(book.get("upgrade_index") or 10**9))
    return normalize_gongfa_priority_book_ids(book.get("book_id") for book in books)


def load_gongfa_priority_book_ids() -> list[int]:
    with Session(engine) as session:
        snapshot = load_inventory_hall_snapshot(session, GONGFA_ATLAS_KEY)
    return priority_book_ids_from_snapshot(snapshot)


def apply_gongfa_priority_to_books(
    books: Iterable[dict[str, Any]],
    priority_book_ids: Iterable[Any],
) -> tuple[list[dict[str, Any]], list[int]]:
    copied = [dict(book) for book in books]
    configured = normalize_gongfa_priority_book_ids(priority_book_ids)
    rank = {book_id: index for index, book_id in enumerate(configured)}
    original_order = {
        int(book.get("book_id") or 0): index for index, book in enumerate(copied)
    }
    copied.sort(key=lambda book: (
        0 if int(book.get("book_id") or 0) in rank else 1,
        rank.get(int(book.get("book_id") or 0), 10**9),
        original_order.get(int(book.get("book_id") or 0), 10**9),
    ))
    for index, book in enumerate(copied, start=1):
        book["upgrade_index"] = index
    merged_ids = normalize_gongfa_priority_book_ids([
        *configured,
        *(book.get("book_id") for book in copied),
    ])
    return copied, merged_ids


def apply_saved_gongfa_priority_to_plan(
    snapshot: dict[str, Any],
    *,
    priority_book_ids: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Select the first trainable book using the atlas' durable order."""

    result = deepcopy(snapshot)
    if result.get("complete") is not True:
        return result
    configured = normalize_gongfa_priority_book_ids(
        priority_book_ids
        if priority_book_ids is not None
        else load_gongfa_priority_book_ids()
    )
    if not configured:
        result["priority_source"] = "runtime_equipment"
        return result

    candidates: dict[int, dict[str, Any]] = {}
    original_ids: list[int] = []
    for source, default_pool in (
        (result.get("books") or [], "equipped_dependency"),
        (result.get("fallback_candidates") or [], "fallback_learned"),
    ):
        for raw in source:
            if not isinstance(raw, dict):
                continue
            book_id = int(raw.get("book_id") or 0)
            if book_id <= 0 or book_id in candidates:
                continue
            book = dict(raw)
            book.setdefault("selection_pool", default_pool)
            candidates[book_id] = book
            original_ids.append(book_id)

    ordered_ids = normalize_gongfa_priority_book_ids([*configured, *original_ids])
    selected = next(
        (
            candidates[book_id]
            for book_id in ordered_ids
            if book_id in candidates
            and bool((candidates[book_id].get("progression") or {}).get("upgradeable"))
        ),
        None,
    )
    result["next_upgradable_book"] = selected
    result["all_books_full"] = selected is None
    result["priority_book_ids"] = ordered_ids
    result["priority_source"] = "gongfa_atlas"
    return result
