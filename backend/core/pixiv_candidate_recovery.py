from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from backend.db import engine
from backend.models import DeviceFile


def enqueue_pixiv_candidate_recovery(
    *,
    user_id: int,
    root_dir: str,
    deleted_updated_at: float,
    expected_count: int,
) -> dict[str, Any]:
    """Freeze one exact deleted batch by hash, then submit its durable recovery job."""

    from backend.core.jobs.local_runtime import submit_local_job_once
    from backend.plugins.modules.media_sync.reservoir import media_review_root

    review_root = media_review_root(root_dir, "pixiv")
    lower = float(deleted_updated_at) - 0.01
    upper = float(deleted_updated_at) + 0.01
    with Session(engine) as session:
        records = session.exec(
            select(DeviceFile).where(
                DeviceFile.absolute_path.is_(None),
                DeviceFile.last_known_path.startswith(str(review_root)),
                DeviceFile.updated_at >= lower,
                DeviceFile.updated_at <= upper,
            )
        ).all()
    hashes = sorted({str(record.content_hash or "").strip() for record in records if record.content_hash})
    if len(records) != int(expected_count) or len(hashes) != int(expected_count):
        raise RuntimeError(
            f"恢复批次校验失败：期望 {expected_count}，记录 {len(records)}，唯一哈希 {len(hashes)}。"
        )

    run, queued = submit_local_job_once(
        job_type="media.pixiv-candidate-recovery",
        payload={
            "user_id": int(user_id),
            "root_dir": str(root_dir),
            "expected_count": int(expected_count),
            "content_hashes": hashes,
            "deleted_updated_at": float(deleted_updated_at),
        },
        user_id=int(user_id),
        resource_key="resource:media-sync:curation:pixiv",
        dedup_key=f"pixiv-candidate-recovery:{int(user_id)}:{float(deleted_updated_at):.6f}",
    )
    return {"local_job_run_id": run.id, "queued": bool(queued), "frozen_count": len(hashes)}


def run_pixiv_candidate_recovery(context: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Redownload one frozen deletion batch without consuming unrelated pending candidates."""

    from backend.plugins.modules.media_sync.models import MediaSyncSourceItem
    from backend.plugins.modules.media_sync.runtime import pixiv_source_activity_lease
    from backend.plugins.modules.media_sync.sources import (
        PIXIV_HOME_FOLLOWING_COLLECTION_URL,
        PIXIV_ILLUSTRATION_URL,
        create_pixiv_home_following_store,
        create_pixiv_session,
        download_pixiv_artwork,
        keep_one_domain_tab,
        open_browser,
        pixiv_remote_run_audit,
        raise_if_browser_action_required,
        reconcile_candidate_storage_indexes,
        refill_candidate_review_batch,
        wait_for_pixiv_request_slot,
    )

    user_id = int(payload.get("user_id") or 0)
    root_dir = str(payload.get("root_dir") or "").strip()
    expected_count = int(payload.get("expected_count") or 0)
    hashes = sorted({str(value or "").strip() for value in payload.get("content_hashes") or [] if value})
    if user_id <= 0 or not root_dir or expected_count <= 0 or len(hashes) != expected_count:
        raise ValueError("Pixiv 候选恢复参数不完整或冻结批次数量不一致。")
    if expected_count > 250:
        raise ValueError("Pixiv 候选单次恢复上限为 250 张。")

    with Session(engine) as session:
        source_items = session.exec(
            select(MediaSyncSourceItem).where(
                MediaSyncSourceItem.user_id == user_id,
                MediaSyncSourceItem.platform == "pixiv",
                MediaSyncSourceItem.content_hash.in_(hashes),
            )
        ).all()
    by_hash: dict[str, MediaSyncSourceItem] = {}
    for item in source_items:
        content_hash = str(item.content_hash or "").strip()
        if content_hash in by_hash:
            raise RuntimeError(f"恢复批次存在重复来源哈希：{content_hash}")
        by_hash[content_hash] = item
    if set(by_hash) != set(hashes):
        raise RuntimeError(f"恢复批次来源映射不完整：{len(by_hash)}/{expected_count}")

    already_restored = {
        str(item.remote_id)
        for item in by_hash.values()
        if item.absolute_path and Path(item.absolute_path).is_file()
    }
    target_ids = sorted(
        {str(item.remote_id) for item in by_hash.values() if str(item.remote_id) not in already_restored},
        key=int,
    )
    store = create_pixiv_home_following_store(root_dir)
    conn = store.connect_db()
    placeholders = ",".join("?" for _ in target_ids)
    artwork_rows = (
        conn.execute(
            f"SELECT * FROM artworks WHERE artwork_id IN ({placeholders})",
            target_ids,
        ).fetchall()
        if target_ids
        else []
    )
    artwork_by_id = {str(row["artwork_id"]): dict(row) for row in artwork_rows}
    missing_state_ids = sorted(set(target_ids) - set(artwork_by_id), key=int)
    if missing_state_ids:
        conn.close()
        raise RuntimeError(f"Pixiv 状态库缺少 {len(missing_state_ids)} 个作品：{missing_state_ids[:5]}")

    succeeded_ids: list[str] = []
    errors: dict[str, str] = {}
    browser = None
    nav_tab = None
    try:
        with pixiv_source_activity_lease(timeout=0):
            browser = open_browser()
            wait_for_pixiv_request_slot("page_navigation")
            nav_tab = browser.new_tab(PIXIV_ILLUSTRATION_URL)
            raise_if_browser_action_required(nav_tab, context="Pixiv 误删候选恢复")
            request_session = create_pixiv_session(nav_tab)
            with pixiv_remote_run_audit(
                source="candidate_recovery",
                max_remote_operations=min(500, max(len(target_ids) * 2 + 5, 5)),
            ) as audit:
                for index, artwork_id in enumerate(target_ids, start=1):
                    context.heartbeat(
                        stage="pixiv-candidate-recovery",
                        message=f"正在恢复 Pixiv 候选 {index}/{len(target_ids)}",
                        progress_current=index - 1,
                        progress_total=len(target_ids),
                        metadata={"artwork_id": artwork_id},
                    )
                    try:
                        download_pixiv_artwork(
                            conn,
                            store,
                            request_session,
                            artwork_by_id[artwork_id],
                            lambda _message: None,
                            user_id=user_id,
                            source_kind="home_following",
                            collection_url=PIXIV_HOME_FOLLOWING_COLLECTION_URL,
                        )
                        succeeded_ids.append(artwork_id)
                    except Exception as exc:
                        errors[artwork_id] = str(exc)
                audit_snapshot = audit.snapshot()
    finally:
        conn.close()
        if browser is not None:
            keep_one_domain_tab(browser, "pixiv.net", preferred_tab=nav_tab)

    context.heartbeat(
        stage="pixiv-candidate-refill",
        message="正在把恢复文件放回 Pixiv 待整理区",
        progress_current=len(target_ids),
        progress_total=len(target_ids),
    )
    refill = refill_candidate_review_batch(
        user_id=user_id,
        root_dir=root_dir,
        platform="pixiv",
        limit=expected_count,
        log=lambda _message: None,
        full_reconcile=False,
    )
    indexes = reconcile_candidate_storage_indexes(root_dir=root_dir, platform="pixiv")
    return {
        "expected_count": expected_count,
        "already_restored_count": len(already_restored),
        "downloaded_count": len(succeeded_ids),
        "failed_count": len(errors),
        "failed_ids": errors,
        "refill": refill,
        "indexes": indexes,
        "audit": audit_snapshot if target_ids else {},
    }
