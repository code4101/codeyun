from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from backend.db import engine


PIXIV_TERMINAL_CANDIDATE_STATES = frozenset({"downloaded", "curated", "deleted", "skipped"})


def _all_registered_paths_exist(rows: list[dict[str, Any]]) -> bool:
    """Return true only when every registered media page is still on disk."""

    paths = [Path(str(row.get("absolute_path") or "")) for row in rows]
    return bool(paths) and all(str(path) and path.is_file() for path in paths)


def enqueue_pixiv_url_migration(*, user_id: int, root_dir: str) -> dict[str, Any]:
    """Freeze all current URL-only Pixiv artwork IDs and queue their migration."""

    from backend.core.jobs.local_runtime import submit_local_job_once
    from backend.plugins.modules.media_sync.models import MediaSyncSourceItem

    with Session(engine) as session:
        rows = session.exec(
            select(MediaSyncSourceItem).where(
                MediaSyncSourceItem.user_id == int(user_id),
                MediaSyncSourceItem.platform == "pixiv",
                MediaSyncSourceItem.downloaded_at.is_(None),
                MediaSyncSourceItem.absolute_path.is_(None),
            )
        ).all()
    url_only_rows = [
        row
        for row in rows
        if (row.extra_json or {}).get("candidate_status") not in PIXIV_TERMINAL_CANDIDATE_STATES
        and (str(row.media_url or "").strip() or str(row.remote_url or "").strip())
    ]
    remote_ids = sorted(
        {str(row.remote_id or "").strip() for row in url_only_rows if str(row.remote_id or "").strip()},
        key=int,
    )
    if not remote_ids:
        return {"queued": False, "local_job_run_id": None, "frozen_remote_count": 0, "frozen_row_count": 0}

    run, queued = submit_local_job_once(
        job_type="media.pixiv-url-migration",
        payload={
            "user_id": int(user_id),
            "root_dir": str(root_dir),
            "remote_ids": remote_ids,
            "frozen_row_count": len(url_only_rows),
            "frozen_at": time.time(),
        },
        user_id=int(user_id),
        resource_key="resource:media-sync:curation:pixiv",
        dedup_key=f"pixiv-url-migration:{int(user_id)}",
    )
    return {
        "queued": bool(queued),
        "local_job_run_id": run.id,
        "frozen_remote_count": len(remote_ids),
        "frozen_row_count": len(url_only_rows),
    }


def _scrub_source_items_for_remote_id(
    *,
    user_id: int,
    remote_id: str,
    succeeded: bool,
    error: str = "",
) -> None:
    from backend.plugins.modules.media_sync.models import MediaSyncSourceItem

    now = time.time()
    with Session(engine) as session:
        rows = session.exec(
            select(MediaSyncSourceItem).where(
                MediaSyncSourceItem.user_id == user_id,
                MediaSyncSourceItem.platform == "pixiv",
                MediaSyncSourceItem.remote_id == remote_id,
            )
        ).all()
        for row in rows:
            row.remote_url = ""
            row.media_url = ""
            extra = dict(row.extra_json or {})
            missing_registered_file = bool(row.absolute_path) and not Path(str(row.absolute_path)).is_file()
            if missing_registered_file:
                row.absolute_path = None
                row.downloaded_at = None
                row.device_id = None
            if row.downloaded_at is None and not row.absolute_path:
                extra["candidate_status"] = "skipped" if succeeded else "error"
                extra["migration_reason"] = (
                    "remote_page_no_longer_exists" if succeeded else "historical_url_download_failed"
                )
                if error:
                    extra["last_error"] = error
            row.extra_json = extra
            row.updated_at = now
            session.add(row)
        session.commit()


def _scrub_pixiv_url_storage(*, user_id: int, stores: list[Any]) -> dict[str, int]:
    from backend.plugins.modules.media_sync.models import MediaSyncSourceItem

    source_rows = 0
    with Session(engine) as session:
        rows = session.exec(
            select(MediaSyncSourceItem).where(
                MediaSyncSourceItem.user_id == user_id,
                MediaSyncSourceItem.platform == "pixiv",
            )
        ).all()
        for row in rows:
            if row.remote_url or row.media_url:
                source_rows += 1
            row.remote_url = ""
            row.media_url = ""
            session.add(row)
        session.commit()

    state_artworks = 0
    state_pages = 0
    manifests_removed = 0
    for store in stores:
        conn = store.connect_db()
        try:
            state_artworks += int(
                conn.execute(
                    "SELECT COUNT(*) FROM artworks WHERE artwork_url <> '' OR thumbnail_url <> ''"
                ).fetchone()[0]
            )
            state_pages += int(
                conn.execute("SELECT COUNT(*) FROM artwork_pages WHERE original_url <> ''").fetchone()[0]
            )
            conn.execute(
                """
                UPDATE artworks
                SET artwork_url = '', thumbnail_url = '',
                    download_status = CASE
                        WHEN download_status IN ('pending', 'error') THEN 'skipped'
                        ELSE download_status
                    END,
                    last_error = CASE
                        WHEN download_status IN ('pending', 'error')
                        THEN COALESCE(last_error, 'legacy URL queue retired')
                        ELSE last_error
                    END
                """
            )
            conn.execute(
                """
                UPDATE artwork_pages
                SET original_url = '',
                    status = CASE WHEN status IN ('pending', 'error') THEN 'skipped' ELSE status END,
                    last_error = CASE
                        WHEN status IN ('pending', 'error')
                        THEN COALESCE(last_error, 'legacy URL queue retired')
                        ELSE last_error
                    END
                """
            )
            conn.commit()
        finally:
            conn.close()
        if store.manifest_path.exists():
            store.manifest_path.unlink()
            manifests_removed += 1
    return {
        "source_rows_scrubbed": source_rows,
        "state_artworks_scrubbed": state_artworks,
        "state_pages_scrubbed": state_pages,
        "manifests_removed": manifests_removed,
    }


def run_pixiv_url_migration(context: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Download every frozen legacy URL candidate once, then retire every Pixiv URL queue."""

    from backend.plugins.modules.media_sync.models import MediaSyncSourceItem
    from backend.plugins.modules.media_sync.runtime import pixiv_source_activity_lease
    from backend.plugins.modules.media_sync.sources import (
        PIXIV_AUTHOR_COLLECTION_URL,
        PIXIV_BOOKMARK_COLLECTION_URL,
        PIXIV_HOME_FOLLOWING_COLLECTION_URL,
        PIXIV_HOME_RECOMMEND_COLLECTION_URL,
        PIXIV_ILLUSTRATION_URL,
        PIXIV_RELATED_COLLECTION_URL,
        PixivStateStore,
        create_pixiv_home_following_store,
        create_pixiv_home_recommend_store,
        create_pixiv_related_store,
        create_pixiv_session,
        download_pixiv_artwork,
        fetch_pixiv_illust_detail,
        keep_one_domain_tab,
        normalize_pixiv_home_detail,
        open_browser,
        pixiv_remote_run_audit,
        pixiv_state_root,
        raise_if_browser_action_required,
        reconcile_candidate_storage_indexes,
        refill_candidate_review_batch,
        wait_for_pixiv_request_slot,
    )

    user_id = int(payload.get("user_id") or 0)
    root_dir = str(payload.get("root_dir") or "").strip()
    remote_ids = sorted(
        {str(value or "").strip() for value in payload.get("remote_ids") or [] if value},
        key=int,
    )
    if user_id <= 0 or not root_dir or not remote_ids:
        raise ValueError("Pixiv URL 历史迁移参数不完整。")

    with Session(engine) as session:
        source_rows = session.exec(
            select(MediaSyncSourceItem).where(
                MediaSyncSourceItem.user_id == user_id,
                MediaSyncSourceItem.platform == "pixiv",
                MediaSyncSourceItem.remote_id.in_(remote_ids),
            )
        ).all()
    rows_by_remote_id: dict[str, list[dict[str, Any]]] = {}
    for row in source_rows:
        rows_by_remote_id.setdefault(str(row.remote_id), []).append(
            {
                "source_kind": str(row.source_kind or ""),
                "collection_url": str(row.collection_url or ""),
                "absolute_path": str(row.absolute_path or ""),
            }
        )

    bookmark_store = PixivStateStore(pixiv_state_root(root_dir))
    related_store = create_pixiv_related_store(root_dir)
    following_store = create_pixiv_home_following_store(root_dir)
    recommend_store = create_pixiv_home_recommend_store(root_dir)
    stores = [bookmark_store, related_store, following_store, recommend_store]
    store_by_source_kind = {
        "bookmark": (bookmark_store, PIXIV_BOOKMARK_COLLECTION_URL),
        "related": (related_store, PIXIV_RELATED_COLLECTION_URL),
        "author": (related_store, ""),
        "home_following": (following_store, PIXIV_HOME_FOLLOWING_COLLECTION_URL),
        "home_recommend": (recommend_store, PIXIV_HOME_RECOMMEND_COLLECTION_URL),
    }
    connections = {id(store): store.connect_db() for store in stores}

    resolved: dict[str, tuple[Any, Any, dict[str, Any], str, str]] = {}
    missing_state_ids: list[str] = []
    for remote_id in remote_ids:
        source_kind = str((rows_by_remote_id.get(remote_id) or [{}])[0].get("source_kind") or "")
        preferred_store, default_collection_url = store_by_source_kind.get(
            source_kind,
            (following_store, PIXIV_HOME_FOLLOWING_COLLECTION_URL),
        )
        candidate_stores = [preferred_store, *[store for store in stores if store is not preferred_store]]
        artwork = None
        selected_store = None
        for store in candidate_stores:
            row = connections[id(store)].execute(
                "SELECT * FROM artworks WHERE artwork_id = ?",
                (remote_id,),
            ).fetchone()
            if row is not None:
                artwork = dict(row)
                selected_store = store
                break
        if artwork is None or selected_store is None:
            missing_state_ids.append(remote_id)
            continue
        collection_url = str((rows_by_remote_id.get(remote_id) or [{}])[0].get("collection_url") or "")
        if source_kind == "author" and not collection_url:
            collection_url = PIXIV_AUTHOR_COLLECTION_URL.format(author_id=artwork.get("user_id") or "unknown")
        resolved[remote_id] = (
            selected_store,
            connections[id(selected_store)],
            artwork,
            source_kind or "home_following",
            collection_url or default_collection_url,
        )

    succeeded_ids: list[str] = []
    already_present_ids: list[str] = []
    errors: dict[str, str] = {}
    browser = None
    nav_tab = None
    try:
        with pixiv_source_activity_lease(timeout=0):
            browser = open_browser()
            wait_for_pixiv_request_slot("page_navigation")
            nav_tab = browser.new_tab(PIXIV_ILLUSTRATION_URL)
            raise_if_browser_action_required(nav_tab, context="Pixiv 历史 URL 迁移")
            request_session = create_pixiv_session(nav_tab)
            for index, remote_id in enumerate(remote_ids, start=1):
                context.heartbeat(
                    stage="pixiv-url-migration",
                    message=f"正在迁移 Pixiv 历史候选 {index}/{len(remote_ids)}",
                    progress_current=index - 1,
                    progress_total=len(remote_ids),
                    metadata={"remote_id": remote_id, "failed_count": len(errors)},
                )
                context.raise_if_cancelled()
                source_rows_for_remote = rows_by_remote_id.get(remote_id, [])
                if _all_registered_paths_exist(source_rows_for_remote):
                    already_present_ids.append(remote_id)
                    _scrub_source_items_for_remote_id(
                        user_id=user_id,
                        remote_id=remote_id,
                        succeeded=True,
                    )
                    continue
                target = resolved.get(remote_id)
                if target is None:
                    try:
                        detail = fetch_pixiv_illust_detail(
                            request_session,
                            artwork_id=remote_id,
                            lang="zh",
                        )
                        artwork = normalize_pixiv_home_detail(
                            detail,
                            source_kind="home_following",
                        )
                        target = (
                            following_store,
                            connections[id(following_store)],
                            artwork,
                            "home_following",
                            PIXIV_HOME_FOLLOWING_COLLECTION_URL,
                        )
                    except Exception as exc:
                        errors[remote_id] = str(exc)
                        _scrub_source_items_for_remote_id(
                            user_id=user_id,
                            remote_id=remote_id,
                            succeeded=False,
                            error=str(exc),
                        )
                        continue
                store, conn, artwork, source_kind, collection_url = target
                artwork = dict(artwork)
                artwork["artwork_url"] = str(artwork.get("artwork_url") or "").strip() or (
                    f"https://www.pixiv.net/artworks/{remote_id}"
                )
                page_count = max(int(artwork.get("page_count") or 1), 1)
                try:
                    with pixiv_remote_run_audit(
                        source="legacy_url_migration",
                        max_remote_operations=min(page_count + 5, 500),
                    ):
                        download_pixiv_artwork(
                            conn,
                            store,
                            request_session,
                            artwork,
                            lambda _message: None,
                            user_id=user_id,
                            source_kind=source_kind,
                            collection_url=collection_url,
                        )
                    succeeded_ids.append(remote_id)
                    _scrub_source_items_for_remote_id(
                        user_id=user_id,
                        remote_id=remote_id,
                        succeeded=True,
                    )
                except Exception as exc:
                    errors[remote_id] = str(exc)
                    _scrub_source_items_for_remote_id(
                        user_id=user_id,
                        remote_id=remote_id,
                        succeeded=False,
                        error=str(exc),
                    )
    finally:
        for conn in connections.values():
            conn.close()
        if browser is not None:
            keep_one_domain_tab(browser, "pixiv.net", preferred_tab=nav_tab)

    context.heartbeat(
        stage="pixiv-url-migration-finalize",
        message="正在清理 Pixiv 历史 URL 存储并重建本地索引",
        progress_current=len(remote_ids),
        progress_total=len(remote_ids),
    )
    scrub = _scrub_pixiv_url_storage(user_id=user_id, stores=stores)
    refill = refill_candidate_review_batch(
        user_id=user_id,
        root_dir=root_dir,
        platform="pixiv",
        limit=200,
        log=lambda _message: None,
        full_reconcile=False,
    )
    indexes = reconcile_candidate_storage_indexes(root_dir=root_dir, platform="pixiv")

    with Session(engine) as session:
        final_rows = session.exec(
            select(MediaSyncSourceItem).where(
                MediaSyncSourceItem.user_id == user_id,
                MediaSyncSourceItem.platform == "pixiv",
            )
        ).all()
    remaining_url_rows = sum(
        1 for row in final_rows if str(row.remote_url or "").strip() or str(row.media_url or "").strip()
    )
    remaining_nonterminal_url_rows = sum(
        1
        for row in final_rows
        if row.downloaded_at is None
        and row.absolute_path is None
        and (row.extra_json or {}).get("candidate_status") not in PIXIV_TERMINAL_CANDIDATE_STATES
        and (str(row.remote_url or "").strip() or str(row.media_url or "").strip())
    )
    if remaining_url_rows or remaining_nonterminal_url_rows:
        raise RuntimeError(
            f"Pixiv URL 迁移未收敛：URL 行 {remaining_url_rows}，非终态 URL 行 {remaining_nonterminal_url_rows}。"
        )
    return {
        "frozen_remote_count": len(remote_ids),
        "frozen_row_count": int(payload.get("frozen_row_count") or 0),
        "downloaded_count": len(succeeded_ids),
        "already_present_count": len(already_present_ids),
        "failed_count": len(errors),
        "failed_ids": errors,
        "missing_state_count": len(missing_state_ids),
        "scrub": scrub,
        "remaining_url_rows": remaining_url_rows,
        "remaining_nonterminal_url_rows": remaining_nonterminal_url_rows,
        "refill": refill,
        "indexes": indexes,
    }
